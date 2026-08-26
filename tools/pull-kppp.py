#!/usr/bin/env python3
"""Pull official KPPP awarded-work records without inferring a live road contract.

Karnataka's public portal exposes an anonymous paginated search endpoint. Its
``AWARDED`` status is preserved exactly, but the response does not name a winning bidder,
award date, execution status, road segment, maintenance period or DLP. This tool keeps
those fields absent and labels every row a procurement record, not a current contract.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.parse
import urllib.request


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tender_scope import is_road_surface_contract


API = (
    "https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/"
    "portal-service/works/search-eproc-tenders"
)
SOURCE_NAME = "Karnataka Public Procurement Portal (KPPP) public awarded works"
SOURCE_ID = "in-ka-kppp-awarded-works"
FORMAT = "official-road-surface-procurement-records"
DEFAULT_PAGE_SIZE = 1_000
MAX_PAGE_SIZE = 2_000
MAX_PAGE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_ROWS = 500_000
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": "https://kppp.karnataka.gov.in/",
    "Post": "CONTRACTOR-EPROC-CONTRACTOR",
    "User-Agent": (
        "Pothole Reporter official-data builder/1.0 "
        "(+https://github.com/coding-parrot/pothole-reporter)"
    ),
}
QUERY = {"category": "WORKS", "status": "AWARDED"}
EXPECTED_OFFICIAL_FIELDS = {
    "canViewAddendum", "canViewCorrigendum", "category", "categoryText", "deptId",
    "deptName", "description", "ecv", "ecvtenderYn", "id", "invitingStrategy",
    "invitingStrategyText", "itemwise", "locationId", "locationName", "nitId",
    "publishedDate", "status", "statusText", "tenderClosureDate", "tenderNumber",
    "tenderType", "title", "workCategoryName",
}

# KPPP's awarded-WORKS index contains thousands of Forest Department plantation
# maintenance rows whose location happens to be written as ``<name> Road side``.
# That is vegetation work beside a road, not work on the travelled surface.  Keep
# this portal-local guard even though the shared classifier is also conservative:
# a future classifier relaxation must not turn these rows into road candidates.
_NON_CARRIAGEWAY_KPPP_SCOPE_RE = re.compile(
    r"\broad\s*side\s+(?:monsoon\s+)?plantations?\b|"
    r"\broadside\s+(?:monsoon\s+)?plantations?\b|"
    r"\bsocial\s+forestr(?:y|ies)\b|"
    r"(?:\w*plantation\w*|\w*forestr\w*).*\broad\s+side\w*\b|"
    r"\broad\s+side\w*\b.*(?:\w*plantation\w*|\w*forestr\w*)",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def is_kppp_road_surface_record(title: Any, tender_reference: Any = None) -> bool:
    """Fail closed for KPPP roadside vegetation records before shared scope checks."""

    text = clean_text(title)
    return bool(
        text
        and not _NON_CARRIAGEWAY_KPPP_SCOPE_RE.search(text)
        and is_road_surface_contract(text, tender_reference)
    )


def kppp_time(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%d-%m-%Y %H:%M:%S")
    except ValueError:
        return None
    # KPPP displays local Karnataka time. Preserve that offset explicitly.
    return parsed.isoformat() + "+05:30"


def _json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def fetch_page(page: int, size: int, timeout: int = 120) -> tuple[list[dict[str, Any]], int]:
    if page < 0 or size < 1 or size > MAX_PAGE_SIZE:
        raise ValueError("KPPP page/size is outside the safe range")
    query = urllib.parse.urlencode(
        {"page": page, "size": size, "order-by-tender-publish": "true"}
    )
    request = urllib.request.Request(
        f"{API}?{query}",
        data=json.dumps(QUERY, separators=(",", ":")).encode(),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if urllib.parse.urlparse(response.geturl()).netloc.casefold() != "kppp.karnataka.gov.in":
            raise RuntimeError("KPPP search redirected outside the official portal")
        if "json" not in response.headers.get("Content-Type", "").casefold():
            raise ValueError("KPPP search response was not JSON")
        raw = response.read(MAX_PAGE_BYTES + 1)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError("KPPP search page exceeded the byte safety limit")
        total = int(response.headers.get("x-total-count") or 0)
    payload = json.loads(raw)
    rows = payload if isinstance(payload, list) else payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("KPPP search response did not contain a row list")
    if len(rows) > size or total < 0 or total > MAX_TOTAL_ROWS:
        raise ValueError("KPPP search response violated pagination limits")
    return rows, total


def fetch_all(
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: int = 120,
    max_pages: int = 0,
    delay: float = 0.2,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    total_reported = 0
    page = 0
    while True:
        failure: Exception | None = None
        for attempt in range(3):
            try:
                batch, total = fetch_page(page, page_size, timeout)
                failure = None
                break
            except Exception as error:  # portal/network retry, then fail closed
                failure = error
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if failure is not None:
            raise RuntimeError(f"KPPP page {page} failed after three attempts: {failure}")
        total_reported = total if page == 0 else max(total_reported, total)
        rows.extend(batch)
        page += 1
        if not batch or page * page_size >= total_reported or (max_pages and page >= max_pages):
            break
        if delay > 0:
            time.sleep(delay)
    return rows, {"pages_fetched": page, "total_reported": total_reported}


def normalise_with_accounting(
    rows: list[dict[str, Any]], retrieved_at: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    parse_instant(retrieved_at)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {
        "rows_received": len(rows),
        "rows_excluded_by_scope": 0,
        "rows_excluded_invalid": 0,
    }
    for row in rows:
        if not EXPECTED_OFFICIAL_FIELDS.issubset(row) or not _json_value(row):
            counts["rows_excluded_invalid"] += 1
            continue
        tender_id = clean_text(row.get("id"))
        tender_reference = clean_text(row.get("tenderNumber"))
        title = clean_text(row.get("description") or row.get("title"))
        department = clean_text(row.get("deptName"))
        published_at = kppp_time(row.get("publishedDate"))
        closure_at = kppp_time(row.get("tenderClosureDate"))
        if (
            not tender_id or not tender_reference or not title or not department
            or not published_at or not closure_at
            or row.get("category") != "WORKS" or row.get("status") != "AWARDED"
            or tender_id in seen
        ):
            counts["rows_excluded_invalid"] += 1
            continue
        if not is_kppp_road_surface_record(title, tender_reference):
            counts["rows_excluded_by_scope"] += 1
            continue
        seen.add(tender_id)
        records.append(
            {
                "closure_at": closure_at,
                "detail_url": None,
                "department_id": row.get("deptId"),
                "department_name": department,
                "estimated_contract_value": row.get("ecv"),
                "lifecycle": "procurement_record",
                "listing_url": API,
                "location_id": row.get("locationId"),
                "location_name": clean_text(row.get("locationName")) or None,
                "official_fields": dict(row),
                "organisation_chain": department,
                "organisation_path": [department],
                "published_at": published_at,
                "retrieved_at": retrieved_at,
                "scope": "road_surface",
                "source_name": SOURCE_NAME,
                "source_status": row.get("status"),
                "source_status_text": clean_text(row.get("statusText")) or None,
                "source_url": API,
                "state_code": "KA",
                "tender_id": tender_id,
                "tender_reference": tender_reference,
                "title": title,
                "work_category": clean_text(row.get("workCategoryName")) or None,
            }
        )
    records.sort(
        key=lambda row: (row["published_at"], row["tender_reference"], row["tender_id"]),
        reverse=True,
    )
    return records, counts


def build_snapshot(
    rows: list[dict[str, Any]], retrieved_at: str, *, pages_fetched: int, total_reported: int
) -> dict[str, Any]:
    records, counts = normalise_with_accounting(rows, retrieved_at)
    return {
        "format": FORMAT,
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "source_url": API,
        "retrieved_at": retrieved_at,
        "state_code": "KA",
        "query": dict(QUERY),
        "anonymous_access": True,
        "pages_fetched": pages_fetched,
        "total_reported": total_reported,
        **counts,
        "records_kept": len(records),
        "records": records,
        "limitations": [
            "AWARDED is the source search status, not proof of current execution or DLP",
            "public search rows do not name the winning bidder or award date",
            "no road-segment geometry or maintenance/warranty dates are published here",
        ],
    }


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(snapshot, dict) or snapshot.get("format") != FORMAT
        or snapshot.get("schema_version") != 1 or snapshot.get("source_id") != SOURCE_ID
        or snapshot.get("source_name") != SOURCE_NAME or snapshot.get("source_url") != API
        or snapshot.get("state_code") != "KA" or snapshot.get("query") != QUERY
        or snapshot.get("anonymous_access") is not True
    ):
        raise ValueError("KPPP snapshot source identity is invalid")
    parse_instant(snapshot.get("retrieved_at"))
    count_fields = (
        "pages_fetched", "total_reported", "rows_received", "rows_excluded_by_scope",
        "rows_excluded_invalid", "records_kept",
    )
    if any(type(snapshot.get(field)) is not int or snapshot[field] < 0 for field in count_fields):
        raise ValueError("KPPP snapshot count fields are invalid")
    if snapshot["rows_received"] != (
        snapshot["rows_excluded_by_scope"] + snapshot["rows_excluded_invalid"]
        + snapshot["records_kept"]
    ):
        raise ValueError("KPPP snapshot row accounting does not balance")
    records = snapshot.get("records")
    if not isinstance(records, list) or len(records) != snapshot["records_kept"]:
        raise ValueError("KPPP snapshot records are invalid")
    forbidden = {
        "contractor", "winning_bidder", "award_date", "dlp", "warranty", "segment_verified",
    }
    seen: set[str] = set()
    for index, record in enumerate(records):
        field = f"records[{index}]"
        if not isinstance(record, dict) or set(record) & forbidden:
            raise ValueError(f"{field} contains a forbidden inference")
        if (
            record.get("state_code") != "KA" or record.get("lifecycle") != "procurement_record"
            or record.get("scope") != "road_surface" or record.get("source_status") != "AWARDED"
            or record.get("source_url") != API or record.get("listing_url") != API
            or record.get("detail_url") is not None or record.get("source_name") != SOURCE_NAME
            or record.get("retrieved_at") != snapshot["retrieved_at"]
        ):
            raise ValueError(f"{field} source truth is invalid")
        official = record.get("official_fields")
        if (
            not isinstance(official, dict) or not EXPECTED_OFFICIAL_FIELDS.issubset(official)
            or not _json_value(official) or clean_text(official.get("id")) != record.get("tender_id")
            or clean_text(official.get("tenderNumber")) != record.get("tender_reference")
            or clean_text(official.get("description") or official.get("title")) != record.get("title")
            or clean_text(official.get("deptName")) != record.get("department_name")
            or kppp_time(official.get("publishedDate")) != record.get("published_at")
            or kppp_time(official.get("tenderClosureDate")) != record.get("closure_at")
            or official.get("status") != record.get("source_status")
        ):
            raise ValueError(f"{field} does not preserve its official fields")
        if record["tender_id"] in seen:
            raise ValueError(f"{field}.tender_id is duplicated")
        seen.add(record["tender_id"])
        if not is_kppp_road_surface_record(record["title"], record["tender_reference"]):
            raise ValueError(f"{field} lacks explicit carriageway scope")
    if not isinstance(snapshot.get("limitations"), list) or len(snapshot["limitations"]) < 3:
        raise ValueError("KPPP snapshot limitations are missing")
    return snapshot


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("offline KPPP input must contain a row list")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="offline raw API response/fixture")
    parser.add_argument("--output", type=Path, help="write normalized JSON here")
    parser.add_argument("--as-of", default=utc_now(), help="retrieval time in ISO 8601")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=0, help="0 fetches the complete index")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    retrieved_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if args.input:
        rows = load_rows(args.input)
        meta = {"pages_fetched": 1, "total_reported": len(rows)}
    else:
        rows, meta = fetch_all(args.page_size, args.timeout, args.max_pages, args.delay)
    snapshot = validate_snapshot(build_snapshot(rows, retrieved_at, **meta))
    rendered = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        f"kept {snapshot['records_kept']} road-surface records from "
        f"{snapshot['rows_received']} KPPP awarded works "
        f"({snapshot['pages_fetched']} pages; source total {snapshot['total_reported']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
