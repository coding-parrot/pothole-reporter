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
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
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
TOKEN_RE = re.compile(r'id=["\']Authorization["\']\s+value=["\']([^"\']+)["\']')


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
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return (
        datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def clean_text(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\u00a0", " ").split())


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
        page = response.read().decode("utf-8", "replace")
    token_match = TOKEN_RE.search(page)
    if not token_match:
        raise RuntimeError("Bihar public tender page did not issue its anonymous token")

    api_request = urllib.request.Request(
        ACTIVE_TENDERS_URL,
        data=b"{}",
        headers={
            "Authorization": html.unescape(token_match.group(1)),
            "Auth-Token": "X-Requested-With",
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": LIST_PAGE_URL,
        },
        method="POST",
    )
    with opener.open(api_request, timeout=timeout) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, list):
        raise ValueError("Bihar active-tender response was not a JSON list")
    return [row for row in payload if isinstance(row, dict)]


def normalise(rows: list[dict[str, Any]], generated_at: str) -> list[dict[str, Any]]:
    """Keep only explicit road-surface notices whose bid deadline has not passed."""

    as_of = parse_instant(generated_at)
    notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        title = clean_text(row.get("currentdescription"))
        reference = clean_text(row.get("currenttenderrefno"))
        tender_id = clean_text(row.get("currenttenderid"))
        bid_due_at = epoch_ms_to_iso(row.get("currentbidEndDate"))
        if not title or not is_road_surface_contract(title, reference):
            continue
        if not bid_due_at or parse_instant(bid_due_at) < as_of:
            continue
        identity = tender_id or reference
        if not identity or identity in seen:
            continue
        seen.add(identity)
        reference_label = "Tender reference number" if reference else "System tender ID"
        reference_value = reference or tender_id
        notices.append(
            {
                "record_id": f"BR:eProc2:{identity}",
                "reference_label": reference_label,
                "reference_value": reference_value,
                "state_code": "BR",
                "agency": "Bihar eProc2.0",
                "lifecycle": "procurement_notice",
                "lifecycle_status": "active tender notice",
                "title": title,
                "system_tender_id": tender_id or None,
                "organisation_tender_id": clean_text(row.get("currentOrgTenderId")) or None,
                "department_id": clean_text(row.get("currentdeptid")) or None,
                "published_at": epoch_ms_to_iso(row.get("currentTenderPublishDate")),
                "bid_submission_start_at": epoch_ms_to_iso(row.get("currentbidStartDate")),
                "bid_due_at": bid_due_at,
                "bid_open_at": epoch_ms_to_iso(row.get("currentbidOpenDate")),
                "contractor": None,
                "scope_verified": True,
                "segment_verified": False,
                "award_verified": False,
                "dlp_verified": False,
                "source_name": SOURCE_NAME,
                "source_url": LIST_PAGE_URL,
                "retrieved_at": generated_at[:10],
            }
        )
    notices.sort(key=lambda row: (row["bid_due_at"], row["reference_value"]))
    return notices


def build_pack(rows: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "sources": [
            {
                "name": SOURCE_NAME,
                "url": ACTIVE_TENDERS_URL,
                "listing_url": LIST_PAGE_URL,
                "retrieved_at": generated_at[:10],
                "access": "anonymous bearer token issued by the public listing page",
            }
        ],
        "notices": normalise(rows, generated_at),
    }


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
    pack = build_pack(rows, generated_at)
    rendered = json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
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
