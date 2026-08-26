#!/usr/bin/env python3
"""Pull current road-surface procurement notices from Gujarat nProcure.

nProcure's public home page renders an anonymous, server-paginated table of tenders in
progress.  Requests for that table are encrypted by the portal's own browser JavaScript,
so the supported live path here uses Playwright to read the same table a normal public
visitor sees.  No login, CAPTCHA, award record, contractor, road geometry or DLP is used
or inferred.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tender_scope import is_road_surface_contract


SOURCE_ID = "in-gj-nprocure"
SOURCE_NAME = "Gujarat nProcure public tenders in progress"
SOURCE_URL = "https://tender.nprocure.com/"
DETAIL_URL = "https://tender.nprocure.com/view-nit-home"
IST = timezone(timedelta(hours=5, minutes=30))

# nProcure mixes every procurement category into one table.  These are recurring
# Gujarat-title shapes where ``road`` or ``pothole`` describes a location, cause, or
# ancillary asset instead of work on a travelled road surface.  Keep this second gate
# local to the portal so the shared classifier remains independent of portal wording.
_STRONG_SURFACE_RE = re.compile(
    r"\b(?:road\s+(?:resurfac\w*|recarpet\w*|patch\w*|surface\s+repair)|"
    r"(?:resurfac\w*|recarpet\w*|asphalt\s+patch\w*|patch\s+work)\b.*\broad|"
    r"(?:asphalt|bitumen|bituminous|b\.?\s*t\.?|c\.?\s*c\.?|r\.?\s*c\.?\s*c\.?|"
    r"cement\s+concrete|p\.?\s*q\.?\s*c\.?)\s+road|road\s+layers?|"
    r"wearing\s+course|road\s+damage)\b",
    re.I,
)
_NON_SURFACE_ONLY_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\b(?:pothole\s+repairs?|repairs?\s+of\s+potholes?)\b.*\bplatform\s+areas?\b",
        r"\b(?:providing|fixing|installation)\b.*\b(?:safety\s+barrier|sign\s*boards?)\b",
        r"\b(?:shifting|relocation)\b.*\b(?:pipe|pipeline|utility|utilities)\b.*"
        r"\b(?:due\s+to|for)\s+road\s+widening\b",
        r"\b(?:construction|providing|fixing)\b.*\braill?ing\b.*\b(?:divider|median)\b",
        r"\bmaintenance\s+and\s+cleaning\b.*\broads?\b",
        r"\bbeautification\b.*\b(?:central\s+ver(?:ge|s)|traffic\s+junctions?)\b",
        r"\b(?:metro|viaduct|underground\s+station|twin\s+(?:cut\s*&\s*cover\s+)?tunnel)\b",
        r"\b(?:cement\s+concrete|concrete)\s+pavement\b.*\baround\b.*"
        r"\b(?:cooling\s+tower|power\s+station|plant)\b",
    )
)


def is_gujarat_road_surface_notice(title: Any, tender_reference: Any = None) -> bool:
    """Fail closed on nProcure titles whose road wording is only incidental."""

    if not is_road_surface_contract(title, tender_reference):
        return False
    text = clean_text(title).lower()
    strong_surface = bool(_STRONG_SURFACE_RE.search(text))

    if any(pattern.search(text) for pattern in _NON_SURFACE_ONLY_PATTERNS):
        return False
    if re.search(r"\b(?:gutter|drain|sewer|ugd)\s+(?:line\s+)?repair\w*\b", text):
        return strong_surface
    if re.search(r"\broad\s+furniture\b|\b(?:divider|median)\s+(?:work|works)\b", text):
        return strong_surface
    # ``Construction of Anganwadi Cinema Road`` is an Anganwadi/building notice on
    # Cinema Road.  Genuine roads leading towards an Anganwadi normally carry a road
    # material/treatment or chainage, which this exception preserves.
    if re.search(r"\bconstruction\s+of\s+(?:an\s+)?anganwadi\b", text):
        has_chainage = bool(re.search(r"\b(?:km|ch(?:ainage)?)[ .:/-]*\d", text))
        return strong_surface or has_chainage
    return True


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def parse_portal_time(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None
    return parsed.isoformat()


def parse_amount(value: Any) -> float | None:
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("offline input must be a JSON list or an object with a rows list")
    return [row for row in rows if isinstance(row, dict)]


def fetch_rows(timeout: int = 120) -> list[dict[str, Any]]:
    """Read every row from nProcure's anonymous public DataTable."""

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on live builder environment
        raise RuntimeError(
            "live nProcure pulls require Playwright (install the repository requirements)"
        ) from exc

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    timeout_ms = max(1, timeout) * 1000
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(SOURCE_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            page.locator("table.dataTable tbody tr").first.wait_for(
                state="attached", timeout=timeout_ms
            )

            length = page.locator("select[name$='_length']")
            if length.count():
                options = length.first.locator("option").evaluate_all(
                    "els => els.map(el => el.value)"
                )
                if "150" in options:
                    length.first.select_option("150")
                    page.wait_for_timeout(1500)

            previous_first_id = None
            while True:
                page.locator("table.dataTable tbody tr").first.wait_for(
                    state="attached", timeout=timeout_ms
                )
                batch = page.locator("table.dataTable tbody tr").evaluate_all(
                    r"""trs => trs.map(tr => {
                      const cells = [...tr.querySelectorAll(':scope > td')];
                      if (cells.length < 2) return null;
                      const idInput = tr.querySelector('input[name="tenderid"]');
                      const tenderId = idInput ? String(idInput.value || '').trim() : '';
                      const homeForms = [...tr.querySelectorAll('form[action="/view-nit-home"]')];
                      const titleLink = homeForms.length > 1
                        ? homeForms[1].querySelector('a')
                        : tr.querySelector('a strong')?.closest('a');
                      const title = (titleLink?.innerText || '').replace(/^Name Of Work\s*:\s*/i, '').trim();
                      const secondText = (cells[1].innerText || '').trim();
                      const organisation = secondText.split(/Tender Id\s*:/i)[0].trim();
                      const closing = (secondText.match(/Last Date\s*&\s*Time For Submission\s*:\s*([^\n]+)/i) || [])[1] || '';
                      const amount = (secondText.match(/Estimated Contract Value\s*:\s*([^\n]+)/i) || [])[1] || '';
                      return {
                        tender_reference: (cells[0].innerText || '').trim(),
                        tender_id: tenderId,
                        title,
                        organisation,
                        closing_at_text: closing.trim(),
                        estimated_value_text: amount.trim()
                      };
                    }).filter(Boolean)"""
                )
                for row in batch:
                    identity = clean_text(row.get("tender_id")) or clean_text(
                        row.get("tender_reference")
                    )
                    if identity and identity not in seen:
                        seen.add(identity)
                        rows.append(row)

                first_id = clean_text(batch[0].get("tender_id")) if batch else None
                if first_id and first_id == previous_first_id:
                    raise RuntimeError("nProcure pagination stopped advancing")
                previous_first_id = first_id

                next_container = page.locator("li.paginate_button.next")
                next_link = (
                    next_container.first.locator("a")
                    if next_container.count()
                    else page.get_by_text("Next", exact=True).first
                )
                if not next_link.count():
                    break
                class_name = (
                    next_container.first.get_attribute("class")
                    if next_container.count()
                    else next_link.get_attribute("class")
                ) or ""
                aria_disabled = next_link.get_attribute("aria-disabled") or ""
                if "disabled" in class_name.split() or aria_disabled == "true":
                    break
                next_link.click()
                if first_id:
                    page.wait_for_function(
                        r"""oldId => {
                          const input = document.querySelector('table.dataTable tbody tr input[name="tenderid"]');
                          return input && String(input.value) !== oldId;
                        }""",
                        arg=first_id,
                        timeout=timeout_ms,
                    )
        except PlaywrightTimeoutError as exc:  # pragma: no cover - live portal behavior
            raise RuntimeError("nProcure public tender table timed out") from exc
        finally:
            browser.close()
    return rows


def normalise(rows: list[dict[str, Any]], retrieved_at: str) -> list[dict[str, Any]]:
    as_of = parse_instant(retrieved_at)
    notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        tender_id = clean_text(row.get("tender_id"))
        tender_reference = clean_text(row.get("tender_reference"))
        title = clean_text(row.get("title"))
        organisation = clean_text(row.get("organisation"))
        closing_at = parse_portal_time(row.get("closing_at_text") or row.get("closing_at"))
        if not tender_id or not tender_reference or not title or not organisation or not closing_at:
            continue
        if not is_gujarat_road_surface_notice(title, tender_reference):
            continue
        if parse_instant(closing_at) < as_of:
            continue
        if tender_id in seen:
            continue
        seen.add(tender_id)
        notices.append(
            {
                "state_code": "GJ",
                "tender_id": tender_id,
                "tender_reference": tender_reference,
                "title": title,
                "organisation_chain": organisation,
                "organisation_path": [organisation],
                "published_at": None,
                "closing_at": closing_at,
                "opening_at": None,
                "estimated_value": parse_amount(
                    row.get("estimated_value_text") or row.get("estimated_value")
                ),
                "detail_url": DETAIL_URL,
                "detail_method": "POST",
                "detail_form": {"tenderid": tender_id},
                "listing_url": SOURCE_URL,
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
        "state_code": "GJ",
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
    args = parser.parse_args()

    retrieved_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    rows = load_rows(args.input) if args.input else fetch_rows(args.timeout)
    snapshot = build_snapshot(rows, retrieved_at)
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        f"kept {len(snapshot['notices'])} current road-surface notices from "
        f"{snapshot['rows_scanned']} nProcure tenders",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
