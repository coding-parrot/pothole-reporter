#!/usr/bin/env python3
"""Pull current road-surface notices from Lakshadweep's official notice index.

The UT Administration page is public, paginated HTML with start/end dates and official
S3WaaS documents.  It does not consistently expose a tender ID, reference, publishing
timestamp, bid-opening timestamp or issuing department as structured fields.  Those
values remain null rather than being invented from filenames or prose.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tender_scope import is_road_surface_contract


SOURCE_ID = "in-ld-official-tender-notices"
SOURCE_NAME = "UT Administration of Lakshadweep tender notices"
SOURCE_URL = "https://lakshadweep.gov.in/notice_category/tenders/"
USER_AGENT = (
    "Pothole Reporter official-data builder/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)
IST = timezone(timedelta(hours=5, minutes=30))
TABLE_RE = re.compile(
    r"<caption>\s*Tender Notices\s*</caption>.*?<tbody>(.*?)</tbody>", re.I | re.S
)
TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
PDF_RE = re.compile(
    r"<a\b[^>]*class=['\"][^'\"]*pdf-download-link[^'\"]*['\"][^>]*"
    r"href=['\"]([^'\"]+)['\"]",
    re.I,
)
NEXT_RE = re.compile(
    r"<a\b[^>]*aria-label=['\"]Next page['\"][^>]*href=['\"]([^'\"]+)['\"]",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clean_text(value: Any) -> str:
    text = TAG_RE.sub(" ", str(value or ""))
    return " ".join(html.unescape(text).replace("\u00a0", " ").split())


def parse_portal_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_notice_page(page: str) -> tuple[list[dict[str, Any]], str | None]:
    table_match = TABLE_RE.search(page)
    if not table_match:
        raise ValueError("Lakshadweep page has no Tender Notices table")
    rows: list[dict[str, Any]] = []
    for row_html in TR_RE.findall(table_match.group(1)):
        cells = TD_RE.findall(row_html)
        if len(cells) < 5:
            continue
        document = PDF_RE.search(cells[4])
        rows.append(
            {
                "title": clean_text(cells[0]),
                "description": clean_text(cells[1]),
                "start_date_text": clean_text(cells[2]),
                "end_date_text": clean_text(cells[3]),
                "document_url": html.unescape(document.group(1)) if document else None,
            }
        )
    next_match = NEXT_RE.search(page)
    return rows, html.unescape(next_match.group(1)) if next_match else None


def fetch_rows(timeout: int = 60, max_pages: int = 25) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    page_url: str | None = SOURCE_URL
    while page_url:
        page_url = urllib.parse.urljoin(SOURCE_URL, page_url)
        if page_url in seen_pages:
            raise RuntimeError("Lakshadweep tender pagination looped")
        if len(seen_pages) >= max_pages:
            raise RuntimeError(f"Lakshadweep tender pagination exceeded {max_pages} pages")
        seen_pages.add(page_url)
        request = urllib.request.Request(
            page_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read().decode("utf-8", "replace")
        batch, page_url = parse_notice_page(page)
        rows.extend(batch)
    return rows


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("offline input must be a JSON list or an object with a rows list")
    return [row for row in rows if isinstance(row, dict)]


def normalise(rows: list[dict[str, Any]], retrieved_at: str) -> list[dict[str, Any]]:
    as_of = parse_instant(retrieved_at).astimezone(IST).date()
    notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        title = clean_text(row.get("title"))
        description = clean_text(row.get("description"))
        stated_scope = clean_text(f"{title} {description}")
        start_date = parse_portal_date(row.get("start_date_text") or row.get("start_date"))
        end_date = parse_portal_date(row.get("end_date_text") or row.get("end_date"))
        document_url = clean_text(row.get("document_url")) or None
        if not title or not end_date or not is_road_surface_contract(stated_scope):
            continue
        if end_date < as_of:
            continue
        identity_source = document_url or f"{title}|{end_date.isoformat()}"
        record_id = "LD:notice:" + hashlib.sha256(identity_source.encode("utf-8")).hexdigest()[:20]
        if record_id in seen:
            continue
        seen.add(record_id)
        notices.append(
            {
                "record_id": record_id,
                "state_code": "LD",
                "tender_id": None,
                "tender_reference": None,
                "title": title,
                "description": description or None,
                "organisation_chain": "UT Administration of Lakshadweep",
                "organisation_path": ["UT Administration of Lakshadweep"],
                "published_at": None,
                "start_date": start_date.isoformat() if start_date else None,
                "closing_date": end_date.isoformat(),
                "closing_at": None,
                "opening_at": None,
                "detail_url": document_url,
                "listing_url": SOURCE_URL,
                "source_name": SOURCE_NAME,
                "source_url": document_url or SOURCE_URL,
                "retrieved_at": retrieved_at,
                "lifecycle": "procurement_notice",
                "scope": "road_surface",
                "structured_field_warning": (
                    "The public index does not expose a structured tender ID/reference "
                    "or bid time for this notice."
                ),
            }
        )
    notices.sort(key=lambda row: (row["closing_date"], row["record_id"]))
    return notices


def build_snapshot(rows: list[dict[str, Any]], retrieved_at: str) -> dict[str, Any]:
    notices = normalise(rows, retrieved_at)
    return {
        "format": "official-road-surface-procurement-notices",
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "state_code": "LD",
        "retrieved_at": retrieved_at,
        "lifecycle": "procurement_notice",
        "rows_scanned": len(rows),
        "rows_excluded_by_scope": len(rows) - len(notices),
        "notices": notices,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="offline raw JSON list/fixture")
    parser.add_argument("--output", type=Path, help="write snapshot JSON here")
    parser.add_argument("--as-of", default=utc_now(), help="snapshot time in ISO 8601")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-pages", type=int, default=25)
    args = parser.parse_args()

    retrieved_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    rows = (
        load_rows(args.input)
        if args.input
        else fetch_rows(timeout=args.timeout, max_pages=args.max_pages)
    )
    snapshot = build_snapshot(rows, retrieved_at)
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        f"kept {len(snapshot['notices'])} current road-surface notices from "
        f"{snapshot['rows_scanned']} Lakshadweep notices",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
