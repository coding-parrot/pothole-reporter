#!/usr/bin/env python3
"""Pull current road-surface notices from Telangana's official eProcurement portal.

The portal's public ``Live Tenders`` page opens an anonymous server-side DataTables
listing.  This tool repeats that public flow without credentials, CAPTCHA handling or
document downloads.  The result is a notice snapshot, not an award/contract register:
contractor, road-segment, warranty and DLP fields are deliberately never inferred.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tender_scope import is_road_surface_contract


BASE_URL = "https://tender.telangana.gov.in"
HOME_URL = f"{BASE_URL}/login.html"
LISTING_URL = f"{BASE_URL}/TenderDetailsHome.html"
JSON_URL = f"{BASE_URL}/TenderDetailsHomeJson.html"
SOURCE_ID = "in-tg-eprocurement"
SOURCE_NAME = "Telangana State eProcurement Portal public live tenders"
FORMAT = "official-road-surface-procurement-notices"
STATE_CODE = "TG"
USER_AGENT = (
    "Pothole Reporter official-data builder/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
PAGE_SIZE = 100
IST = timezone(timedelta(hours=5, minutes=30))

SOURCE_COLUMNS = (
    "department_name",
    "tender_id",
    "tender_reference",
    "tender_category",
    "title",
    "estimated_contract_value",
    "published_date_time",
    "bid_submission_start_date_time",
    "bid_submission_closing_date_time",
    "action_html",
)
OFFICIAL_FIELDS = SOURCE_COLUMNS[:-1] + ("detail_action_arguments",)


class _CsrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "input":
            return
        values = {str(key).casefold(): value for key, value in attrs}
        if str(values.get("name") or "").casefold() == "csrftoken":
            value = clean_text(values.get("value"))
            if value:
                self.values.append(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def portal_datetime_to_iso(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%d/%m/%Y %I:%M %p").replace(tzinfo=IST)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def clean_text(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\u00a0", " ").split())


def detail_action_arguments(value: Any) -> list[int]:
    """Keep only the official numeric POST locator; never persist executable HTML."""

    match = re.search(
        r"\bviewBtn\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", str(value or "")
    )
    return [int(part) for part in match.groups()] if match else []


def _read_bounded(response: Any, maximum: int, label: str) -> bytes:
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError(f"{label} exceeded {maximum} bytes")
    return body


def _csrf(page: bytes, label: str) -> str:
    parser = _CsrfParser()
    parser.feed(page.decode("iso-8859-1", "replace"))
    if not parser.values:
        raise RuntimeError(f"{label} did not expose its anonymous CSRF token")
    value = parser.values[-1]
    if len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9-]+", value):
        raise RuntimeError(f"{label} exposed an invalid CSRF token")
    return value


def _official_response(response: Any, label: str) -> bytes:
    parsed = urllib.parse.urlparse(str(response.geturl()))
    if parsed.scheme != "https" or parsed.netloc != "tender.telangana.gov.in":
        raise RuntimeError(f"{label} redirected outside the official Telangana portal")
    return _read_bounded(response, MAX_HTML_BYTES, label)


def _post_form(opener: Any, url: str, fields: dict[str, str], timeout: int, label: str) -> bytes:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": HOME_URL,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with opener.open(request, timeout=timeout) as response:
        return _official_response(response, label)


def _datatable_query(offset: int, length: int) -> list[tuple[str, str]]:
    query: list[tuple[str, str]] = [
        ("nTenderID", ""),
        ("nDepartmentID", "0"),
        ("subDeptId", ""),
        ("ddlDistrict", ""),
        ("ddlMandal", ""),
        ("biddingType", ""),
        ("sProcurementType", ""),
        ("mECVValue1", ""),
        ("mECVValue2", ""),
        ("dtBidClosingselect", ""),
        ("dtBidClosing1", ""),
        ("dtBidClosing2", ""),
        ("dtTenderOpening1", ""),
        ("dtTenderOpening2", ""),
        ("hdnSearch4", ""),
        ("hdnSearch", ""),
        ("hdncorrigendumsDetails", ""),
        ("hdncorrigendumsDetails1", ""),
        ("hdnnoSearch", ""),
        ("hdncorrigendumsDetails2", ""),
        ("hdnPreviousPage", ""),
        ("hdnIndentID", ""),
        ("hdnTenderCategory", ""),
        ("hdnProcurementID", ""),
        ("hdnType", "current"),
        ("hdnPreviousPge", "TenderDetailsHome.html"),
        ("hdnadvsearch", ""),
        ("hdnFromStatus", ""),
        ("typeOfWorkFromConsolidation", ""),
        ("popUPRequestParameter", ""),
        ("selectedCircleDivison", ""),
        ("selectedDepartmentID", ""),
        ("selectedProcurementType", ""),
        ("selectedTypeofWork", ""),
        ("aid", ""),
        ("hdnEncryptNames", "hdnEncryptNames"),
        ("hdnEncryptValues", "hdnEncryptValues"),
        ("sEcho", "1"),
        ("iColumns", "10"),
        ("sColumns", ",,,,,,,,,"),
        ("iDisplayStart", str(offset)),
        ("iDisplayLength", str(length)),
    ]
    for index in range(10):
        query.extend(
            ((f"mDataProp_{index}", str(index)), (f"bSortable_{index}", "false" if index == 9 else "true"))
        )
    # Tender ID is a stable source column; using it avoids value-tie churn while paging.
    query.extend((("iSortCol_0", "1"), ("sSortDir_0", "asc"), ("iSortingCols", "1")))
    return query


def _fetch_json_page(opener: Any, offset: int, length: int, timeout: int) -> dict[str, Any]:
    url = JSON_URL + "?" + urllib.parse.urlencode(_datatable_query(offset, length))
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": LISTING_URL,
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with opener.open(request, timeout=timeout) as response:
        parsed = urllib.parse.urlparse(str(response.geturl()))
        if parsed.scheme != "https" or parsed.netloc != "tender.telangana.gov.in":
            raise RuntimeError("Telangana tender JSON redirected outside the official portal")
        body = _read_bounded(response, MAX_JSON_BYTES, "Telangana tender JSON")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Telangana live-tender endpoint did not return JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Telangana live-tender response was not a JSON object")
    return payload


def fetch_active_tenders(timeout: int = 60) -> list[list[Any]]:
    """Fetch every row from the portal's anonymous public Live Tenders table."""

    cookies = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    root_request = urllib.request.Request(
        f"{BASE_URL}/", headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    with opener.open(root_request, timeout=timeout) as response:
        root = _official_response(response, "Telangana portal root")
    login = _post_form(
        opener,
        HOME_URL,
        {
            "CSRFToken": _csrf(root, "Telangana portal root"),
            "hdnEncryptNames": "hdnEncryptNames",
            "hdnEncryptValues": "hdnEncryptValues",
        },
        timeout,
        "Telangana public home page",
    )
    listing = _post_form(
        opener,
        LISTING_URL,
        {
            "CSRFToken": _csrf(login, "Telangana public home page"),
            "hdnType": "current",
            "hdnEncryptNames": "hdnEncryptNames",
            "hdnEncryptValues": "hdnEncryptValues",
        },
        timeout,
        "Telangana Live Tenders page",
    )
    if b"TenderDetailsHomeJson.html" not in listing or not any(
        marker in listing for marker in (b"Published Date & Time", b"Published Date &amp; Time")
    ):
        raise RuntimeError("Telangana Live Tenders page contract changed")

    first = _fetch_json_page(opener, 0, PAGE_SIZE, timeout)
    total = first.get("iTotalDisplayRecords")
    if isinstance(total, str) and total.isascii() and total.isdigit():
        total = int(total)
    if type(total) is not int or total < 0 or total > MAX_ROWS:
        raise ValueError(
            f"Telangana live-tender response has an invalid total: {total!r}"
        )
    rows = first.get("aaData")
    if not isinstance(rows, list):
        raise ValueError("Telangana live-tender response has no row list")
    all_rows: list[list[Any]] = list(rows)
    offset = len(rows)
    while offset < total:
        payload = _fetch_json_page(opener, offset, min(PAGE_SIZE, total - offset), timeout)
        page_total = payload.get("iTotalDisplayRecords")
        if isinstance(page_total, str) and page_total.isascii() and page_total.isdigit():
            page_total = int(page_total)
        if page_total != total:
            raise RuntimeError("Telangana live-tender total changed during pagination; retry")
        page_rows = payload.get("aaData")
        if not isinstance(page_rows, list) or not page_rows:
            raise RuntimeError("Telangana live-tender pagination ended before its stated total")
        all_rows.extend(page_rows)
        offset += len(page_rows)
    if len(all_rows) != total:
        raise RuntimeError("Telangana live-tender row count differs from its stated total")
    if any(not isinstance(row, list) for row in all_rows):
        raise ValueError("Telangana live-tender response contains a non-row value")
    return all_rows


def normalise_with_accounting(
    rows: list[list[Any]], generated_at: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    as_of = parse_instant(generated_at)
    notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {
        "rows_scanned": len(rows),
        "rows_excluded_by_scope": 0,
        "rows_excluded_by_deadline": 0,
        "rows_excluded_invalid": 0,
    }
    for raw in rows:
        if len(raw) != len(SOURCE_COLUMNS):
            counts["rows_excluded_invalid"] += 1
            continue
        official = {
            field: raw[index] for index, field in enumerate(SOURCE_COLUMNS[:-1])
        }
        official["detail_action_arguments"] = detail_action_arguments(raw[-1])
        department = clean_text(official["department_name"])
        tender_id = clean_text(official["tender_id"])
        reference = clean_text(official["tender_reference"])
        title = clean_text(official["title"])
        published_at = portal_datetime_to_iso(official["published_date_time"])
        bid_start_at = portal_datetime_to_iso(official["bid_submission_start_date_time"])
        closing_at = portal_datetime_to_iso(official["bid_submission_closing_date_time"])
        if not all((department, tender_id, reference, title, published_at, bid_start_at, closing_at)):
            counts["rows_excluded_invalid"] += 1
            continue
        if tender_id in seen:
            counts["rows_excluded_invalid"] += 1
            continue
        seen.add(tender_id)
        if clean_text(official["tender_category"]).casefold() != "works" or not is_road_surface_contract(
            title, reference
        ):
            counts["rows_excluded_by_scope"] += 1
            continue
        if parse_instant(closing_at) < as_of:
            counts["rows_excluded_by_deadline"] += 1
            continue
        notices.append(
            {
                "bid_submission_start_at": bid_start_at,
                "closing_at": closing_at,
                # The public listing action is a session-bound POST, not a stable URL.
                "detail_url": None,
                "lifecycle": "procurement_notice",
                "listing_url": LISTING_URL,
                "opening_at": None,
                "organisation_chain": department,
                "organisation_path": [department],
                "official_fields": official,
                "published_at": published_at,
                "retrieved_at": generated_at,
                "scope": "road_surface",
                "source_name": SOURCE_NAME,
                "source_url": HOME_URL,
                "state_code": STATE_CODE,
                "tender_category": clean_text(official["tender_category"]),
                "tender_id": tender_id,
                "tender_reference": reference,
                "title": title,
            }
        )
    notices.sort(key=lambda row: (row["closing_at"], row["tender_reference"], row["tender_id"]))
    return notices, counts


def normalise(rows: list[list[Any]], generated_at: str) -> list[dict[str, Any]]:
    return normalise_with_accounting(rows, generated_at)[0]


def build_pack(rows: list[list[Any]], generated_at: str) -> dict[str, Any]:
    notices, counts = normalise_with_accounting(rows, generated_at)
    organisations = sorted({notice["organisation_chain"] for notice in notices})
    return {
        "format": FORMAT,
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "source_url": HOME_URL,
        "listing_url": LISTING_URL,
        "api_url": JSON_URL,
        "retrieved_at": generated_at,
        "state_code": STATE_CODE,
        "lifecycle": "procurement_notice",
        "source_limitations": [
            "The public list is a procurement-notice feed, not an award or contract register.",
            "Tender detail actions are session-bound POSTs, so no stable per-record detail URL is published.",
            "The public list does not expose a bid-opening timestamp, contractor, road geometry, DLP or warranty.",
        ],
        "organisations": organisations,
        **counts,
        "records_kept": len(notices),
        "notices": notices,
    }


def validate_pack(pack: dict[str, Any]) -> dict[str, Any]:
    expected_top = {
        "api_url", "format", "lifecycle", "listing_url", "notices", "organisations",
        "records_kept", "retrieved_at", "rows_excluded_by_deadline",
        "rows_excluded_by_scope", "rows_excluded_invalid", "rows_scanned", "schema_version",
        "source_id", "source_limitations", "source_name", "source_url", "state_code",
    }
    if not isinstance(pack, dict) or set(pack) != expected_top:
        raise ValueError("Telangana snapshot fields differ from the canonical contract")
    if (
        pack["format"] != FORMAT
        or pack["schema_version"] != 1
        or pack["source_id"] != SOURCE_ID
        or pack["source_name"] != SOURCE_NAME
        or pack["source_url"] != HOME_URL
        or pack["listing_url"] != LISTING_URL
        or pack["api_url"] != JSON_URL
        or pack["state_code"] != STATE_CODE
        or pack["lifecycle"] != "procurement_notice"
    ):
        raise ValueError("Telangana snapshot source identity is invalid")
    retrieved_at = parse_instant(pack["retrieved_at"])
    count_fields = (
        "rows_scanned", "rows_excluded_by_scope", "rows_excluded_by_deadline",
        "rows_excluded_invalid", "records_kept",
    )
    if any(type(pack[field]) is not int or pack[field] < 0 for field in count_fields):
        raise ValueError("Telangana snapshot row accounting is invalid")
    if pack["rows_scanned"] != sum(pack[field] for field in count_fields[1:]):
        raise ValueError("Telangana snapshot row accounting does not balance")
    if not isinstance(pack["notices"], list) or len(pack["notices"]) != pack["records_kept"]:
        raise ValueError("Telangana snapshot notice count is invalid")
    if (
        not isinstance(pack["organisations"], list)
        or pack["organisations"] != sorted(set(pack["organisations"]))
        or any(not isinstance(value, str) or not value for value in pack["organisations"])
    ):
        raise ValueError("Telangana snapshot organisation index is invalid")

    expected_notice = {
        "bid_submission_start_at", "closing_at", "detail_url", "lifecycle", "listing_url",
        "opening_at", "organisation_chain", "organisation_path", "official_fields",
        "published_at", "retrieved_at", "scope", "source_name", "source_url", "state_code",
        "tender_category", "tender_id", "tender_reference", "title",
    }
    forbidden = {
        "active_contract", "award", "award_verified", "contractor", "dlp", "dlp_verified",
        "segment_verified", "warranty", "winning_bidder",
    }
    seen: set[str] = set()
    for index, notice in enumerate(pack["notices"]):
        label = f"notices[{index}]"
        if not isinstance(notice, dict) or set(notice) != expected_notice:
            raise ValueError(f"{label} fields differ from the canonical contract")
        if set(notice) & forbidden:
            raise ValueError(f"{label} contains a forbidden contract inference")
        if (
            notice["state_code"] != STATE_CODE
            or notice["lifecycle"] != "procurement_notice"
            or notice["scope"] != "road_surface"
            or notice["source_name"] != SOURCE_NAME
            or notice["source_url"] != HOME_URL
            or notice["listing_url"] != LISTING_URL
            or notice["detail_url"] is not None
            or notice["opening_at"] is not None
            or notice["retrieved_at"] != pack["retrieved_at"]
        ):
            raise ValueError(f"{label} source truth is invalid")
        for field in ("tender_id", "tender_reference", "title", "organisation_chain"):
            value = notice[field]
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{label}.{field} is invalid")
        if notice["tender_id"] in seen:
            raise ValueError(f"{label}.tender_id is duplicated")
        seen.add(notice["tender_id"])
        if notice["organisation_path"] != [notice["organisation_chain"]]:
            raise ValueError(f"{label}.organisation_path is invalid")
        for field in ("published_at", "bid_submission_start_at", "closing_at"):
            parse_instant(notice[field])
        if parse_instant(notice["closing_at"]) < retrieved_at:
            raise ValueError(f"{label} is no longer current at retrieval")
        official = notice["official_fields"]
        if (
            not isinstance(official, dict)
            or tuple(official) != OFFICIAL_FIELDS
            or clean_text(official["department_name"]) != notice["organisation_chain"]
            or clean_text(official["tender_id"]) != notice["tender_id"]
            or clean_text(official["tender_reference"]) != notice["tender_reference"]
            or clean_text(official["title"]) != notice["title"]
            or clean_text(official["tender_category"]) != notice["tender_category"]
            or portal_datetime_to_iso(official["published_date_time"]) != notice["published_at"]
            or portal_datetime_to_iso(official["bid_submission_start_date_time"])
            != notice["bid_submission_start_at"]
            or portal_datetime_to_iso(official["bid_submission_closing_date_time"])
            != notice["closing_at"]
            or not isinstance(official["detail_action_arguments"], list)
            or any(type(value) is not int or value < 0 for value in official["detail_action_arguments"])
        ):
            raise ValueError(f"{label} does not preserve its official source fields")
        if not is_road_surface_contract(notice["title"], notice["tender_reference"]):
            raise ValueError(f"{label} lacks explicit carriageway-work scope")
    if pack["organisations"] != sorted({row["organisation_chain"] for row in pack["notices"]}):
        raise ValueError("Telangana organisation index differs from its notices")
    return pack


def load_rows(path: Path) -> list[list[Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise ValueError("offline input must be a JSON row list or an object containing one")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="offline raw JSON fixture")
    parser.add_argument("--output", type=Path, help="write JSON here; otherwise stdout")
    parser.add_argument("--as-of", default=utc_now(), help="snapshot time in ISO 8601")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    generated_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    rows = load_rows(args.input) if args.input else fetch_active_tenders(args.timeout)
    pack = validate_pack(build_pack(rows, generated_at))
    rendered = json.dumps(pack, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        f"kept {pack['records_kept']} current road-surface notices from {pack['rows_scanned']} live tenders",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
