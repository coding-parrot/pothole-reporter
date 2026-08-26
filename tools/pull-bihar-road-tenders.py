#!/usr/bin/env python3
"""Pull current pothole-relevant works notices from Bihar's official eProc2 portal.

The public tender-list page issues an anonymous, short-lived bearer token and its own
JavaScript uses that token to request the complete active-tender JSON list.  This tool
repeats only that public flow.  Rows are procurement notices, never awards: contractor,
road-segment and defect-liability fields are intentionally left unverified.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tender_scope import is_road_surface_contract


BASE_URL = "https://eproc2.bihar.gov.in/EPSV2Web"
LIST_PAGE_URL = f"{BASE_URL}/openarea/tenderListingPage.action"
ACTIVE_TENDERS_URL = f"{BASE_URL}/rest/openarea/getTenderList"
SOURCE_NAME = "Bihar eProc2.0 public active tenders"
USER_AGENT = (
    "Pothole Reporter official-data builder/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)
MAX_LIST_PAGE_BYTES = 4 * 1024 * 1024
MAX_API_BYTES = 64 * 1024 * 1024
MAX_API_ROWS = 100_000
FORMAT = "official-road-surface-procurement-notices"
SOURCE_ID = "in-br-eproc2"

OFFICIAL_FIELDS = (
    "currenttenderid",
    "currentOrgTenderId",
    "currenttenderrefno",
    "currentgroupid",
    "currentorgid",
    "currentdeptid",
    "currenttendertypeid",
    "currenttendercatid",
    "currentproccatid",
    "currentpacamt",
    "currentstatus",
    "currenttendercurrency",
    "currentbidcurrency",
    "currentminbidno",
    "currenttendercallno",
    "currentindentNo",
    "currentBidPartNo",
    "currentbidEndDate",
    "currentbidStartDate",
    "currentbidOpenDate",
    "currentDocSubmissionEndDate",
    "currentTenderPublishDate",
    "currentTenderCancelDate",
    "currentTenderCancelReason",
    "currentdescription",
)


class _AuthorizationInputParser(HTMLParser):
    """Extract the public-page token without depending on HTML attribute order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "input" or self.token is not None:
            return
        values = {str(key).casefold(): value for key, value in attrs}
        if str(values.get("id") or "").casefold() == "authorization":
            self.token = values.get("value")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def epoch_ms_to_iso(value: Any) -> str | None:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if milliseconds <= 0:
        return None
    try:
        return (
            datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError):
        return None


def clean_text(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\u00a0", " ").split())


def _read_bounded(response: Any, maximum: int, label: str) -> bytes:
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeded {maximum} bytes")
    return payload


def _official_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, bool)) or (
        isinstance(value, float) and math.isfinite(value)
    )


def fetch_active_tenders(timeout: int = 60) -> list[dict[str, Any]]:
    """Use the portal's anonymous public-page token to fetch active tender JSON."""

    cookies = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    page_request = urllib.request.Request(
        LIST_PAGE_URL,
        data=urllib.parse.urlencode({"Authorization": ""}).encode(),
        headers={"User-Agent": USER_AGENT, "Referer": f"{BASE_URL}/"},
        method="POST",
    )
    with opener.open(page_request, timeout=timeout) as response:
        if not str(response.geturl()).startswith(BASE_URL + "/"):
            raise RuntimeError("Bihar public listing redirected outside the official portal")
        page = _read_bounded(response, MAX_LIST_PAGE_BYTES, "Bihar public tender page").decode(
            "utf-8", "replace"
        )
    parser = _AuthorizationInputParser()
    parser.feed(page)
    token = html.unescape(str(parser.token or "")).strip()
    if not token:
        raise RuntimeError("Bihar public tender page did not issue its anonymous token")
    if len(token) > 4096 or any(ord(character) < 32 for character in token):
        raise RuntimeError("Bihar public tender page issued an invalid anonymous token")

    api_request = urllib.request.Request(
        ACTIVE_TENDERS_URL,
        data=b"{}",
        headers={
            "Authorization": token,
            "Auth-Token": "X-Requested-With",
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": LIST_PAGE_URL,
        },
        method="POST",
    )
    with opener.open(api_request, timeout=timeout) as response:
        if not str(response.geturl()).startswith(BASE_URL + "/"):
            raise RuntimeError("Bihar active-tender API redirected outside the official portal")
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.casefold():
            raise ValueError("Bihar active-tender response was not JSON")
        payload = json.loads(_read_bounded(response, MAX_API_BYTES, "Bihar active-tender API"))
    if not isinstance(payload, list):
        raise ValueError("Bihar active-tender response was not a JSON list")
    if len(payload) > MAX_API_ROWS:
        raise ValueError("Bihar active-tender response exceeded the row safety limit")
    if any(not isinstance(row, dict) for row in payload):
        raise ValueError("Bihar active-tender response contained a non-object row")
    return payload


def normalise_with_accounting(
    rows: list[dict[str, Any]], generated_at: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep current road notices and account for every source row exactly once."""

    as_of = parse_instant(generated_at)
    notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {
        "rows_scanned": len(rows),
        "rows_excluded_by_scope": 0,
        "rows_excluded_by_deadline": 0,
        "rows_excluded_cancelled": 0,
        "rows_excluded_invalid": 0,
    }
    for row in rows:
        title = clean_text(row.get("currentdescription"))
        reference = clean_text(row.get("currenttenderrefno"))
        tender_id = clean_text(row.get("currenttenderid"))
        bid_due_at = epoch_ms_to_iso(row.get("currentbidEndDate"))
        official_fields = {field: row.get(field) for field in OFFICIAL_FIELDS}
        if (
            not title
            or not reference
            or not tender_id
            or not bid_due_at
            or any(not _official_scalar(value) for value in official_fields.values())
        ):
            counts["rows_excluded_invalid"] += 1
            continue
        if epoch_ms_to_iso(row.get("currentTenderCancelDate")) or clean_text(
            row.get("currentTenderCancelReason")
        ):
            counts["rows_excluded_cancelled"] += 1
            continue
        if not is_road_surface_contract(title, reference):
            counts["rows_excluded_by_scope"] += 1
            continue
        if parse_instant(bid_due_at) < as_of:
            counts["rows_excluded_by_deadline"] += 1
            continue
        if tender_id in seen:
            counts["rows_excluded_invalid"] += 1
            continue
        seen.add(tender_id)
        notices.append(
            {
                "bid_part_number": row.get("currentBidPartNo"),
                "bid_submission_start_at": epoch_ms_to_iso(row.get("currentbidStartDate")),
                "closing_at": bid_due_at,
                "department_id": row.get("currentdeptid"),
                "detail_url": None,
                "document_submission_end_at": epoch_ms_to_iso(
                    row.get("currentDocSubmissionEndDate")
                ),
                "lifecycle": "procurement_notice",
                "listing_url": LIST_PAGE_URL,
                "opening_at": epoch_ms_to_iso(row.get("currentbidOpenDate")),
                # The list response publishes numeric organisation/department IDs, not
                # names or a hierarchy. Preserve the IDs and leave these canonical name
                # fields unknown rather than turning an ID into an invented authority.
                "organisation_chain": None,
                "organisation_id": row.get("currentorgid"),
                "organisation_path": [],
                "organisation_tender_id": row.get("currentOrgTenderId"),
                "official_fields": official_fields,
                "procurement_category_id": row.get("currentproccatid"),
                "published_at": epoch_ms_to_iso(row.get("currentTenderPublishDate")),
                "retrieved_at": generated_at,
                "scope": "road_surface",
                "source_name": SOURCE_NAME,
                "source_status_code": row.get("currentstatus"),
                "source_url": LIST_PAGE_URL,
                "state_code": "BR",
                "tender_category_id": row.get("currenttendercatid"),
                "tender_id": tender_id,
                "tender_reference": reference,
                "tender_type_id": row.get("currenttendertypeid"),
                "title": title,
            }
        )
    notices.sort(key=lambda row: (row["closing_at"], row["tender_reference"], row["tender_id"]))
    return notices, counts


def normalise(rows: list[dict[str, Any]], generated_at: str) -> list[dict[str, Any]]:
    """Compatibility helper returning only normalized current road notices."""

    return normalise_with_accounting(rows, generated_at)[0]


def build_pack(rows: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    notices, counts = normalise_with_accounting(rows, generated_at)
    organisations = sorted(
        {
            int(notice["organisation_id"])
            for notice in notices
            if isinstance(notice.get("organisation_id"), int)
            and not isinstance(notice.get("organisation_id"), bool)
        }
    )
    return {
        "format": FORMAT,
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "source_url": LIST_PAGE_URL,
        "api_url": ACTIVE_TENDERS_URL,
        "retrieved_at": generated_at,
        "state_code": "BR",
        "lifecycle": "procurement_notice",
        # Only numeric IDs are present in the active-list API; no names are invented.
        "organisations": [],
        "organisation_ids": organisations,
        **counts,
        "records_kept": len(notices),
        "notices": notices,
    }


def validate_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a normalized snapshot loses source truth or gains inferences."""

    top_fields = {
        "api_url",
        "format",
        "lifecycle",
        "notices",
        "organisation_ids",
        "organisations",
        "records_kept",
        "retrieved_at",
        "rows_excluded_by_deadline",
        "rows_excluded_by_scope",
        "rows_excluded_cancelled",
        "rows_excluded_invalid",
        "rows_scanned",
        "schema_version",
        "source_id",
        "source_name",
        "source_url",
        "state_code",
    }
    if not isinstance(pack, dict) or set(pack) != top_fields:
        raise ValueError("Bihar snapshot fields differ from the canonical contract")
    if (
        pack["format"] != FORMAT
        or pack["schema_version"] != 1
        or pack["source_id"] != SOURCE_ID
        or pack["source_name"] != SOURCE_NAME
        or pack["source_url"] != LIST_PAGE_URL
        or pack["api_url"] != ACTIVE_TENDERS_URL
        or pack["state_code"] != "BR"
        or pack["lifecycle"] != "procurement_notice"
        or pack["organisations"] != []
    ):
        raise ValueError("Bihar snapshot source identity is invalid")
    retrieved_at = parse_instant(pack["retrieved_at"])
    count_fields = (
        "rows_scanned",
        "rows_excluded_by_scope",
        "rows_excluded_by_deadline",
        "rows_excluded_cancelled",
        "rows_excluded_invalid",
        "records_kept",
    )
    if any(type(pack[field]) is not int or pack[field] < 0 for field in count_fields):
        raise ValueError("Bihar snapshot row accounting is invalid")
    if pack["rows_scanned"] != sum(pack[field] for field in count_fields[1:]):
        raise ValueError("Bihar snapshot row accounting does not balance")
    if not isinstance(pack["notices"], list) or len(pack["notices"]) != pack["records_kept"]:
        raise ValueError("Bihar snapshot notice count is invalid")
    organisation_ids = pack["organisation_ids"]
    if (
        not isinstance(organisation_ids, list)
        or organisation_ids != sorted(set(organisation_ids))
        or any(type(value) is not int for value in organisation_ids)
    ):
        raise ValueError("Bihar snapshot organisation IDs are invalid")

    notice_fields = {
        "bid_part_number",
        "bid_submission_start_at",
        "closing_at",
        "department_id",
        "detail_url",
        "document_submission_end_at",
        "lifecycle",
        "listing_url",
        "opening_at",
        "organisation_chain",
        "organisation_id",
        "organisation_path",
        "organisation_tender_id",
        "official_fields",
        "procurement_category_id",
        "published_at",
        "retrieved_at",
        "scope",
        "source_name",
        "source_status_code",
        "source_url",
        "state_code",
        "tender_category_id",
        "tender_id",
        "tender_reference",
        "tender_type_id",
        "title",
    }
    forbidden = {
        "active_contract",
        "award",
        "award_verified",
        "contractor",
        "dlp",
        "dlp_verified",
        "segment_verified",
        "warranty",
        "winning_bidder",
    }
    seen: set[str] = set()
    for index, notice in enumerate(pack["notices"]):
        field = f"notices[{index}]"
        if not isinstance(notice, dict) or set(notice) != notice_fields:
            raise ValueError(f"{field} fields differ from the canonical contract")
        if set(notice) & forbidden:
            raise ValueError(f"{field} contains a forbidden contract inference")
        if (
            notice["state_code"] != "BR"
            or notice["lifecycle"] != "procurement_notice"
            or notice["scope"] != "road_surface"
            or notice["source_name"] != SOURCE_NAME
            or notice["source_url"] != LIST_PAGE_URL
            or notice["listing_url"] != LIST_PAGE_URL
            or notice["detail_url"] is not None
            or notice["organisation_chain"] is not None
            or notice["organisation_path"] != []
            or notice["retrieved_at"] != pack["retrieved_at"]
        ):
            raise ValueError(f"{field} source truth is invalid")
        for text_field in ("tender_id", "tender_reference", "title"):
            value = notice[text_field]
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{field}.{text_field} is invalid")
        if notice["tender_id"] in seen:
            raise ValueError(f"{field}.tender_id is duplicated")
        seen.add(notice["tender_id"])
        closing_at = parse_instant(notice["closing_at"])
        if closing_at < retrieved_at:
            raise ValueError(f"{field} is no longer current at retrieval")
        for date_field in (
            "published_at",
            "bid_submission_start_at",
            "opening_at",
            "document_submission_end_at",
        ):
            if notice[date_field] is not None:
                parse_instant(notice[date_field])
        official = notice["official_fields"]
        if (
            not isinstance(official, dict)
            or tuple(official) != OFFICIAL_FIELDS
            or any(not _official_scalar(value) for value in official.values())
            or clean_text(official["currenttenderid"]) != notice["tender_id"]
            or clean_text(official["currenttenderrefno"]) != notice["tender_reference"]
            or clean_text(official["currentdescription"]) != notice["title"]
            or epoch_ms_to_iso(official["currentbidEndDate"]) != notice["closing_at"]
            or epoch_ms_to_iso(official["currentTenderPublishDate"]) != notice["published_at"]
            or epoch_ms_to_iso(official["currentbidStartDate"])
            != notice["bid_submission_start_at"]
            or epoch_ms_to_iso(official["currentbidOpenDate"]) != notice["opening_at"]
            or epoch_ms_to_iso(official["currentDocSubmissionEndDate"])
            != notice["document_submission_end_at"]
            or notice["organisation_id"] != official["currentorgid"]
            or notice["department_id"] != official["currentdeptid"]
            or notice["organisation_tender_id"] != official["currentOrgTenderId"]
            or notice["source_status_code"] != official["currentstatus"]
            or notice["tender_type_id"] != official["currenttendertypeid"]
            or notice["tender_category_id"] != official["currenttendercatid"]
            or notice["procurement_category_id"] != official["currentproccatid"]
            or notice["bid_part_number"] != official["currentBidPartNo"]
        ):
            raise ValueError(f"{field} does not preserve its official source fields")
        if not is_road_surface_contract(notice["title"], notice["tender_reference"]):
            raise ValueError(f"{field} lacks explicit carriageway-work scope")
    expected_organisations = sorted(
        {
            notice["organisation_id"]
            for notice in pack["notices"]
            if type(notice["organisation_id"]) is int
        }
    )
    if organisation_ids != expected_organisations:
        raise ValueError("Bihar snapshot organisation index differs from its notices")
    return pack


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("offline input must be a JSON list or an object with a rows list")
    return [row for row in rows if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="offline raw JSON list/fixture")
    parser.add_argument("--output", type=Path, help="write JSON here; otherwise stdout")
    parser.add_argument("--as-of", default=utc_now(), help="snapshot time in ISO 8601")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    generated_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = load_rows(args.input) if args.input else fetch_active_tenders(args.timeout)
    pack = validate_pack(build_pack(rows, generated_at))
    rendered = json.dumps(pack, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        f"kept {len(pack['notices'])} current road-surface notices from {len(rows)} active tenders",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
