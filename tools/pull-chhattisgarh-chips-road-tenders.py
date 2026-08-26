#!/usr/bin/env python3
"""Pull current road-surface notices from Chhattisgarh's official CHiPS portal.

The public landing page has a documented ``LOAD LIVE DATA`` button.  This importer
submits that anonymous form, parses the open-tender table, and keeps only explicit
travelled-road work.  The public table is a procurement-notice source; it does not prove
an award, contractor, exact road geometry, warranty or DLP, and none is inferred here.
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
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tender_scope import is_road_surface_contract


SOURCE_ID = "in-ct-chips"
SOURCE_NAME = "CHiPS e-Tendering public open tenders"
SOURCE_URL = "https://eproc.cgstate.gov.in/"
LISTING_URL = "https://eproc.cgstate.gov.in/CHEPS/security/getSignInAction.do"
DETAIL_URL = "https://eproc.cgstate.gov.in/CHEPS/business/rfq.action"
USER_AGENT = (
    "Pothole Reporter official-data builder/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)
IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_RE = re.compile(
    r"name\s*=\s*['\"]?OWASP_CSRFTOKEN['\"]?\s+value\s*=\s*['\"]?([A-Z0-9-]+)",
    re.I,
)
ORG_OR_ROW_RE = re.compile(
    r"<tr>\s*<td\b[^>]*class=['\"][^'\"]*col-md-12[^'\"]*['\"][^>]*"
    r"colspan=['\"]?3['\"]?[^>]*>(?P<organisation>.*?)</td>\s*</tr>"
    r"|<tr\b(?P<attrs>[^>]*class=['\"]clickable-row['\"][^>]*)>(?P<row>.*?)</tr>",
    re.I | re.S,
)
RFQ_RE = re.compile(r"viewRfq\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)
TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
TOOLTIP_RE = re.compile(
    r"<span\b[^>]*class=['\"][^'\"]*tooltiptext[^'\"]*['\"][^>]*>(.*?)</span>",
    re.I | re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
ATTRIBUTE_RE = re.compile(
    r"([A-Za-z_:][A-Za-z0-9_.:\[\]-]*)\s*=\s*(['\"])(.*?)\2", re.I | re.S
)
_BRIDGE_PACKAGE_RE = re.compile(
    r"\bconstruction\s*(?:&|and)\s*maintenance\s+(?:of\s+)?"
    r"(?:lsb|long\s+span\s+bridge|low\s+span\s+bridge)\b",
    re.I,
)
_PRIMARY_NON_ROAD_WORK_RE = re.compile(
    r"^\s*construction\s+of\s+(?:a\s+)?(?:multipurpose\s+)?sport(?:s)?\s+stadium\b",
    re.I,
)


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


def parse_portal_time(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for pattern in (
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%d %b, %Y %I:%M:%S %p IST",
    ):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=IST).isoformat()
        except ValueError:
            continue
    return None


def parse_amount(value: Any) -> float | None:
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_open_tender_rows(page: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    organisation = ""
    for match in ORG_OR_ROW_RE.finditer(page):
        if match.group("organisation") is not None:
            organisation = clean_text(match.group("organisation"))
            continue
        attrs = match.group("attrs") or ""
        identity = RFQ_RE.search(attrs)
        cells = TD_RE.findall(match.group("row") or "")
        if not identity or len(cells) < 7:
            continue
        tooltip = TOOLTIP_RE.search(cells[4])
        title = clean_text(tooltip.group(1) if tooltip else cells[4])
        rows.append(
            {
                "tender_id": identity.group(1),
                "tender_part": identity.group(2),
                "tender_reference": clean_text(cells[1]),
                "organisation": organisation,
                "bid_submission_start_at_text": clean_text(cells[2]),
                "closing_at_text": clean_text(cells[3]),
                "title": title,
                "estimated_value_text": clean_text(cells[5]),
                "corrigendum_issued": clean_text(cells[6]).upper() == "YES",
            }
        )
    return rows


def is_chips_road_surface_notice(title: Any, tender_reference: Any = None) -> bool:
    """Reject CHiPS packages where a road name is incidental to the primary asset."""

    if not is_road_surface_contract(title, tender_reference):
        return False
    text = clean_text(title)
    if _BRIDGE_PACKAGE_RE.search(text):
        return False
    if _PRIMARY_NON_ROAD_WORK_RE.search(text):
        return False
    return True


def input_value(page: str, element_id: str) -> str | None:
    for tag in INPUT_RE.findall(page):
        attributes = {
            name.casefold(): html.unescape(value)
            for name, _quote, value in ATTRIBUTE_RE.findall(tag)
        }
        if attributes.get("id") == element_id:
            return clean_text(attributes.get("value")) or None
    return None


def labelled_field(page: str, label: str) -> str | None:
    pattern = re.compile(
        r"<td\b[^>]*class=['\"][^'\"]*fieldLabel[^'\"]*['\"][^>]*>\s*"
        + re.escape(label)
        + r"\s*</td>\s*<td\b[^>]*class=['\"][^'\"]*field[^'\"]*['\"][^>]*>"
        r"(.*?)</td>",
        re.I | re.S,
    )
    match = pattern.search(page)
    return clean_text(match.group(1)) if match else None


def tender_reference_from_description(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    # ``Description`` is the only public CHiPS field carrying the department's NIT
    # reference.  It often appends the work title, so retaining the entire tail would
    # corrupt the identifier.  Preserve the first identifier token (plus a dotted/slash
    # suffix used by municipal bodies), and keep an explicit ``Sr no/...`` reference
    # verbatim.  Unknown description shapes stay in ``notice_description`` only.
    serial = re.fullmatch(r"sr\.?\s*no\s*/.+", text, re.I)
    if serial:
        return text
    match = re.search(r"\bNIT\b\s*(?:NO\.?|NUMBER)?\s*[:#-]?\s*(.+)$", text, re.I)
    if not match:
        return None
    tail = clean_text(match.group(1))
    tokens = tail.split()
    if not tokens:
        return None
    reference = tokens[0].rstrip(",;:")
    if (
        reference.isdigit()
        and len(tokens) > 1
        and re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]*[/._-][A-Z0-9._/-]+", tokens[1], re.I)
    ):
        reference += " " + tokens[1].rstrip(",;:")
    return reference or None


def parse_detail_page(page: str) -> dict[str, Any]:
    notice_description = input_value(page, "description")
    organisation = input_value(page, "organization.organizationName")
    return {
        "tender_reference": tender_reference_from_description(notice_description),
        "notice_description": notice_description,
        "title": labelled_field(page, "Detailed Description"),
        "organisation": organisation,
        "division_or_district": labelled_field(page, "Division / District Name"),
        "section_or_circle": labelled_field(page, "SECTION/CIRCLE(PWD) NAME"),
        "office_or_division": labelled_field(page, "OFFICE/DIVISION(PWD) NAME"),
        "opening_at_text": labelled_field(page, "Bid Open Date (Scheduled)"),
    }


def fetch_rows(timeout: int = 120, include_details: bool = True) -> list[dict[str, Any]]:
    cookies = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    request = urllib.request.Request(
        LISTING_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with opener.open(request, timeout=timeout) as response:
        landing = response.read().decode("utf-8", "replace")
    token_match = TOKEN_RE.search(landing)
    if not token_match:
        raise RuntimeError("CHiPS public landing page did not expose its CSRF form token")

    form = urllib.parse.urlencode(
        {
            "loadAlldata": "Y",
            "OWASP_CSRFTOKEN": token_match.group(1),
            "pkiEnabledOrg": "false",
        }
    ).encode()
    live_request = urllib.request.Request(
        LISTING_URL,
        data=form,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LISTING_URL,
        },
        method="POST",
    )
    with opener.open(live_request, timeout=timeout) as response:
        live_page = response.read().decode("utf-8", "replace")
    rows = parse_open_tender_rows(live_page)
    if not rows:
        raise RuntimeError("CHiPS LOAD LIVE DATA returned no parseable open-tender rows")
    if not include_details:
        return rows

    current_token = TOKEN_RE.search(live_page)
    if not current_token:
        raise RuntimeError("CHiPS live table did not expose its detail-page CSRF token")
    token = current_token.group(1)
    for row in rows:
        if not is_chips_road_surface_notice(
            row.get("title"), row.get("tender_reference")
        ):
            continue
        detail_form = urllib.parse.urlencode(
            {
                "rfqId": row["tender_id"],
                "rfqPartNumber": row["tender_part"],
                "printFlag": "N",
                "methodName": "viewRfq",
                "documentStatus": "AAS",
                "documentOwner": "",
                "openRfqFlag": "Y",
                "responseFlag": "",
                "OWASP_CSRFTOKEN": token,
                "pkiEnabledOrg": "false",
            }
        ).encode()
        detail_request = urllib.request.Request(
            DETAIL_URL,
            data=detail_form,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": LISTING_URL,
            },
            method="POST",
        )
        with opener.open(detail_request, timeout=timeout) as response:
            detail_page = response.read().decode("utf-8", "replace")
        parsed = parse_detail_page(detail_page)
        for key, value in parsed.items():
            if value:
                row[key] = value
        refreshed_token = TOKEN_RE.search(detail_page)
        if refreshed_token:
            token = refreshed_token.group(1)
    return rows


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("offline input must be a JSON list or an object with a rows list")
    return [row for row in rows if isinstance(row, dict)]


def normalise(rows: list[dict[str, Any]], retrieved_at: str) -> list[dict[str, Any]]:
    as_of = parse_instant(retrieved_at)
    notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        tender_id = clean_text(row.get("tender_id"))
        tender_reference = clean_text(row.get("tender_reference")) or tender_id
        tender_part = clean_text(row.get("tender_part")) or "1"
        title = clean_text(row.get("title"))
        organisation = clean_text(row.get("organisation"))
        closing_at = parse_portal_time(row.get("closing_at_text") or row.get("closing_at"))
        start_at = parse_portal_time(
            row.get("bid_submission_start_at_text") or row.get("bid_submission_start_at")
        )
        opening_at = parse_portal_time(row.get("opening_at_text") or row.get("opening_at"))
        if not tender_id or not title or not organisation or not closing_at:
            continue
        if not is_chips_road_surface_notice(title, tender_reference):
            continue
        if parse_instant(closing_at) < as_of:
            continue
        if tender_id in seen:
            continue
        seen.add(tender_id)
        organisation_path = [organisation]
        for key in ("division_or_district", "section_or_circle", "office_or_division"):
            value = clean_text(row.get(key))
            if value and value not in organisation_path:
                organisation_path.append(value)
        notices.append(
            {
                "state_code": "CG",
                "tender_id": tender_id,
                "tender_reference": tender_reference,
                "title": title,
                "organisation_chain": organisation,
                "organisation_path": organisation_path,
                "notice_description": clean_text(row.get("notice_description")) or None,
                "published_at": None,
                "bid_submission_start_at": start_at,
                "closing_at": closing_at,
                "opening_at": opening_at,
                "estimated_value": parse_amount(
                    row.get("estimated_value_text") or row.get("estimated_value")
                ),
                "corrigendum_issued": bool(row.get("corrigendum_issued", False)),
                "detail_url": DETAIL_URL,
                "detail_method": "POST",
                "detail_form": {
                    "rfqId": tender_id,
                    "rfqPartNumber": tender_part,
                    "methodName": "viewRfq",
                    "documentStatus": "AAS",
                    "openRfqFlag": "Y",
                },
                "listing_url": LISTING_URL,
                "source_name": SOURCE_NAME,
                "source_url": SOURCE_URL,
                "retrieved_at": retrieved_at,
                "lifecycle": "procurement_notice",
                "scope": "road_surface",
            }
        )
    notices.sort(key=lambda row: (row["closing_at"], row["tender_id"]))
    return notices


def build_snapshot(rows: list[dict[str, Any]], retrieved_at: str) -> dict[str, Any]:
    notices = normalise(rows, retrieved_at)
    return {
        "format": "official-road-surface-procurement-notices",
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "state_code": "CG",
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
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="skip anonymous detail pages (faster, but omits NIT reference/opening date)",
    )
    args = parser.parse_args()

    retrieved_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    rows = (
        load_rows(args.input)
        if args.input
        else fetch_rows(args.timeout, include_details=not args.skip_details)
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
        f"{snapshot['rows_scanned']} CHiPS open tenders",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
