#!/usr/bin/env python3
"""Build a conservative national-highway project/tender pack from official sources.

The two public sources intentionally stay separate:

* MoRTH/NHAI Data Lake publishes projects under implementation and identifies them
  with a Unique Project Code (UPC).  A UPC is never relabelled as a tender number.
* NHIDCL publishes current tender notices.  Those rows are procurement notices, not
  awards; the CSV does not always expose a Tender ID or a winning contractor.

Only records whose title explicitly describes carriageway construction, upgrading,
rehabilitation or maintenance are retained.  Consultancy and structure/amenity-only
works are excluded.  This tool does not infer a road segment, award or defect-liability
period from dates or wording.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
NHAI_SOURCE_NAME = "MoRTH Data Lake: projects under implementation"
NHAI_SOURCE_URL = (
    "https://datalakem.nhai.gov.in/MoRTH/MISC/"
    "DroneVideosReportData?upc=0&state=0&mode=0"
)
NHIDCL_SOURCE_NAME = "NHIDCL current tenders CSV"
NHIDCL_SOURCE_URL = "https://www.nhidcl.com/en/tender/export?_format=csv&page="
USER_AGENT = "Pothole Reporter official-data builder/1.0 (+https://github.com/coding-parrot/pothole-reporter)"
IST = timezone(timedelta(hours=5, minutes=30))


STATE_CODES = {
    "andhra pradesh": "AP",
    "arunachal pradesh": "AR",
    "assam": "AS",
    "bihar": "BR",
    "chhattisgarh": "CG",
    "goa": "GA",
    "gujarat": "GJ",
    "haryana": "HR",
    "himachal pradesh": "HP",
    "jharkhand": "JH",
    "karnataka": "KA",
    "kerala": "KL",
    "madhya pradesh": "MP",
    "maharashtra": "MH",
    "manipur": "MN",
    "meghalaya": "ML",
    "mizoram": "MZ",
    "nagaland": "NL",
    "odisha": "OD",
    "punjab": "PB",
    "rajasthan": "RJ",
    "sikkim": "SK",
    "tamil nadu": "TN",
    "telangana": "TG",
    "tripura": "TR",
    "uttar pradesh": "UP",
    "uttarakhand": "UK",
    "west bengal": "WB",
    "andaman and nicobar islands": "AN",
    "chandigarh": "CH",
    "dadra and nagar haveli and daman and diu": "DH",
    "delhi": "DL",
    "jammu and kashmir": "JK",
    "ladakh": "LA",
    "lakshadweep": "LD",
    "puducherry": "PY",
}
STATE_ALIASES = {
    "andaman and nicobar": "andaman and nicobar islands",
    "andaman & nicobar": "andaman and nicobar islands",
    "dadra & nagar haveli and daman & diu": "dadra and nagar haveli and daman and diu",
    "dadra and nagar haveli & daman and diu": "dadra and nagar haveli and daman and diu",
    "jammu & kashmir": "jammu and kashmir",
    "orissa": "odisha",
    "pondicherry": "puducherry",
    "uttaranchal": "uttarakhand",
}


HIGHWAY_RE = re.compile(
    r"\b(?P<kind>N\.?\s*H\.?|N\.?\s*E\.?|NATIONAL\s+HIGHWAY|NATIONAL\s+EXPRESSWAY)"
    r"\s*(?:NO\.?\s*)?[-‐-―:]?\s*0*(?P<number>\d{1,4})(?P<suffix>[A-Z]?)\b",
    re.IGNORECASE,
)
TENDER_ID_RE = re.compile(r"\b20\d{2}_NHIDC[A-Z]*_\d+_\d+\b", re.IGNORECASE)
CHAINAGE_NUMBER = r"\d{1,4}(?:\s*(?:\+|/|\.)\s*\d{1,3})?"
PAIRED_CHAINAGE_RE = re.compile(
    rf"existing\s+(?:chainage\s+|ch\.?\s*|km\.?\s*)"
    rf"(?P<existing_start>{CHAINAGE_NUMBER})\s*"
    rf"\(\s*design\s+(?:chainage\s+|ch\.?\s*|km\.?\s*)"
    rf"(?P<design_start>{CHAINAGE_NUMBER})\s*\)\s*"
    rf"(?:to|[-‐-―])\s*existing\s+(?:chainage\s+|ch\.?\s*|km\.?\s*)"
    rf"(?P<existing_end>{CHAINAGE_NUMBER})\s*"
    rf"\(\s*design\s+(?:chainage\s+|ch\.?\s*|km\.?\s*)"
    rf"(?P<design_end>{CHAINAGE_NUMBER})\s*\)",
    re.IGNORECASE,
)
CHAINAGE_RANGE_RE = re.compile(
    rf"(?P<lead>(?:(?:from|between)\s+)?(?:(?:old\s+)?(?:design|existing)\s+)?"
    rf"(?:chainage\s+|ch(?:ainage)?\.?\s*|km\.?\s*))"
    rf"(?P<start>{CHAINAGE_NUMBER})\s*(?:km\.?\s*)?"
    rf"(?:to|up\s*to|upto|[-‐-―])\s*"
    rf"(?:(?:old\s+)?(?:design|existing)\s+)?"
    rf"(?:chainage\s+|ch(?:ainage)?\.?\s*|km\.?\s*)?"
    rf"(?P<end>{CHAINAGE_NUMBER})(?:\s*km\b)?",
    re.IGNORECASE,
)
CHAINAGE_TRAILING_UNIT_RE = re.compile(
    rf"(?P<lead>(?:(?:from|between)\s+)?(?:(?:old\s+)?(?:design|existing)\s+)?)"
    rf"(?P<start>{CHAINAGE_NUMBER})\s*km\.?\s*"
    rf"(?:to|up\s*to|upto|[-‐-―])\s*"
    rf"(?P<end>{CHAINAGE_NUMBER})\s*km\b",
    re.IGNORECASE,
)
CHAINAGE_POINT_RE = re.compile(
    rf"(?:at|near)\s+(?:(?:design|existing)\s+)?"
    rf"(?:chainage\s+|ch(?:ainage)?\.?\s*|km\.?\s*)"
    rf"(?P<point>{CHAINAGE_NUMBER})",
    re.IGNORECASE,
)

CONSULTANCY_RE = re.compile(
    r"\b(?:consultancy|consultants?|authority'?s? engineer|independent engineer|"
    r"supervision services|pre[- ]?feasibility|detailed project report|\bDPR\b|"
    r"feasibility study|transaction adviser|project management consultant)\b",
    re.IGNORECASE,
)
ADMINISTRATIVE_RE = re.compile(
    r"\b(?:providing|hiring|supply|selection)\b.{0,80}\b(?:vehicle|office staff|"
    r"manpower|security guard|accountant|data entry operator|computer|printer|"
    r"furniture|software|insurance|audit services|machinery|equipment)\b",
    re.IGNORECASE,
)
SURFACE_RE = re.compile(
    r"\b(?:carriageway|pavement|paved\s+shoulder|asphalt|bituminous|black\s*top|resurfac\w*|"
    r"overlay|road\s+patch\w*|pothole\w*|DBM\s*\+\s*BC)\b",
    re.IGNORECASE,
)
MAINTENANCE_RE = re.compile(
    r"\b(?:PBMC|STMC|periodic(?:al)?\s+renewal|routine\s+maintenance|"
    r"short[- ]term\s+(?:improvement|maintenance)|one[- ]time\s+improvement|"
    r"maintenance\s+(?:and|&)\s+(?:repair|operation)|operation\s+(?:and|&)\s+maintenance)\b",
    re.IGNORECASE,
)
ROAD_WORK_RE = re.compile(
    r"\b(?:widen\w*|up[- ]?grad\w*|rehabilitat\w*|strengthen\w*|"
    r"resurfac\w*|maintenan\w*|repair\w*|restor\w*|improv\w*|realign\w*|"
    r"reconstruct\w*|construct\w*|develop\w*)\b"
    r".{0,100}\b(?:road|highway|carriageway|pavement|corridor|lan(?:e|ing)s?|NH\s*[-:]?\s*\d+[A-Z]?)\b",
    re.IGNORECASE,
)
ROAD_WORK_REVERSE = re.compile(
    r"\b(?:road|highway|carriageway|pavement|corridor|lan(?:e|ing)s?)\b"
    r".{0,100}\b(?:widen\w*|up[- ]?grad\w*|rehabilitat\w*|strengthen\w*|"
    r"resurfac\w*|maintenan\w*|repair\w*|restor\w*|improv\w*|realign\w*|"
    r"reconstruct\w*|construct\w*|develop\w*)\b",
    re.IGNORECASE,
)
LANE_PROJECT_RE = re.compile(
    r"\b(?:construction|development|widening|up[- ]?gradation|improvement)\b"
    r".{0,80}\b(?:2|4|6|8|two|four|six|eight)[ -]?(?:lane|laning)\b",
    re.IGNORECASE,
)
NON_CARRIAGEWAY_RE = re.compile(
    r"\b(?:bridges?|viaducts?|flyovers?|underpass(?:es)?|"
    r"\bV\.?\s*U\.?\s*Ps?\.?\b|\bL\.?\s*V\.?\s*U\.?\s*Ps?\.?\b|"
    r"\bR\.?\s*O\.?\s*Bs?\.?\b|\bF\.?\s*O\.?\s*Bs?\.?\b|"
    r"culverts?|tunnels?|drains?|drainage|footpaths?|street\s+lights?|lighting|parking|"
    r"toll\s+plaza|wayside\s+amenit|plantation|landscap|slope\s+protection|"
    r"landslide(?:\s+(?:debris\s+)?clearance|\s+mitigation)?|riverbank\s+protection|retaining\s+wall|"
    r"road\s+furniture|signage|median\s+cut)\b",
    re.IGNORECASE,
)
MIXED_MINOR_RE = re.compile(
    r"\b(?:drains?|drainage|footpaths?|street\s+lights?|lighting)\b", re.IGNORECASE
)


def clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.replace("\u00a0", " ").split())


def state_code(value: Any, title: str = "") -> str | None:
    """Return a canonical two-letter state/UT code using explicit source text only."""

    field = clean_text(value).casefold().replace("&", "and")
    field = re.sub(r"\b(?:union\s+territory|ut)\s+of\s+", "", field)
    field = re.sub(r"[^a-z ]+", " ", field)
    field = " ".join(field.split())
    field = STATE_ALIASES.get(field, field)
    if field in STATE_CODES:
        return STATE_CODES[field]

    haystack = clean_text(title).casefold().replace("&", "and")
    candidates: list[tuple[int, str]] = []
    names = {**{name: name for name in STATE_CODES}, **STATE_ALIASES}
    for alias, canonical in names.items():
        match = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", haystack)
        if match:
            candidates.append((match.start(), STATE_CODES[canonical]))
    return min(candidates)[1] if candidates else None


def parse_highway_refs(title: str) -> list[str]:
    """Extract explicit NH/NE identifiers; never derive them from a UPC."""

    found: list[tuple[int, str]] = []
    for match in HIGHWAY_RE.finditer(clean_text(title)):
        number = str(int(match.group("number")))
        suffix = match.group("suffix").upper()
        raw_kind = re.sub(r"[^A-Z]", "", match.group("kind").upper())
        kind = "NE" if raw_kind in {"NE", "NATIONALEXPRESSWAY"} else "NH"
        found.append((match.start(), f"{kind}-{number}{suffix}"))

        # Official titles sometimes abbreviate "NH-13 & 15".  Continuations are
        # accepted only immediately after an explicit NH/NE token.
        tail = clean_text(title)[match.end() : match.end() + 35]
        offset = 0
        continuation = re.compile(r"\s*(?:&|,|/|and)\s*0*(\d{1,4})([A-Z]?)\b", re.I)
        while True:
            extra = continuation.match(tail, offset)
            if not extra:
                break
            found.append(
                (
                    match.end() + extra.start(),
                    f"{kind}-{int(extra.group(1))}{extra.group(2).upper()}",
                )
            )
            offset = extra.end()

    output: list[str] = []
    for _, reference in sorted(found):
        if reference not in output:
            output.append(reference)
    return output


def _chainage_value(raw: str) -> float:
    value = re.sub(r"\s+", "", raw)
    for separator in ("+", "/"):
        if separator in value:
            whole, fraction = value.split(separator, 1)
            return round(int(whole) + int(fraction) / (10 ** len(fraction)), 3)
    return round(float(value), 3)


def parse_chainages(title: str) -> list[dict[str, float]]:
    """Extract explicit chainage ranges/points without claiming map verification."""

    text = clean_text(title)
    candidates: list[tuple[int, float, float]] = []
    occupied: list[tuple[int, int]] = []

    for match in PAIRED_CHAINAGE_RE.finditer(text):
        candidates.append(
            (
                match.start(),
                _chainage_value(match.group("existing_start")),
                _chainage_value(match.group("existing_end")),
            )
        )
        candidates.append(
            (
                match.start() + 1,
                _chainage_value(match.group("design_start")),
                _chainage_value(match.group("design_end")),
            )
        )
        occupied.append(match.span())

    def overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < end and span[1] > start for start, end in occupied)

    for pattern in (CHAINAGE_RANGE_RE, CHAINAGE_TRAILING_UNIT_RE):
        for match in pattern.finditer(text):
            if overlaps(match.span()):
                continue
            start = _chainage_value(match.group("start"))
            end = _chainage_value(match.group("end"))
            if end < start:
                start, end = end, start
            candidates.append((match.start(), start, end))
            occupied.append(match.span())

    for match in CHAINAGE_POINT_RE.finditer(text):
        if overlaps(match.span()):
            continue
        point = _chainage_value(match.group("point"))
        candidates.append((match.start(), point, point))

    result: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    for _, start, end in sorted(candidates):
        pair = (start, end)
        if pair in seen:
            continue
        seen.add(pair)
        result.append({"start_km": start, "end_km": end})
    return result


def scope_evidence(title: str) -> list[str]:
    """Return carriageway evidence, or an empty list for unsafe/ambiguous scope."""

    text = clean_text(title)
    if not text or CONSULTANCY_RE.search(text) or ADMINISTRATIVE_RE.search(text):
        return []

    evidence: list[str] = []
    if SURFACE_RE.search(text):
        evidence.append("surface")
    if MAINTENANCE_RE.search(text):
        evidence.append("maintenance")
    if ROAD_WORK_RE.search(text) or ROAD_WORK_REVERSE.search(text):
        evidence.append("road-work")
    if LANE_PROJECT_RE.search(text):
        evidence.append("lane-project")

    if not evidence:
        return []

    non_carriageway = NON_CARRIAGEWAY_RE.search(text)
    if non_carriageway:
        # A mixed road-and-drain/footpath/lighting title does not prove that the
        # carriageway is the contracted asset.  Explicit pavement/surface work is
        # required before such a notice can enter the pothole responsibility pack.
        if MIXED_MINOR_RE.search(text) and not SURFACE_RE.search(text):
            return []

        # Structure-only titles often mention the highway or number of existing
        # lanes merely as location context.  Keep them only when the title separately
        # states surface work or a road/lane construction/upgrade project.
        network_maintenance = re.search(
            r"\b(?:PBMC|STMC|periodic(?:al)? renewal|routine maintenance|"
            r"operation (?:and|&) maintenance)\b",
            text,
            re.IGNORECASE,
        )
        explicit_road_project = re.search(
            r"\b(?:widening|up[- ]?gradation|rehabilitation|reconstruction|realignment|"
            r"strengthening|maintenance|repair|resurfacing|overlay)\b.{0,90}"
            r"\b(?:road|highway|carriageway|pavement|lanes?|laning|corridor)\b|"
            r"\bconstruction\b.{0,60}\b(?:2|4|6|8|two|four|six|eight)[ -]?lane\b"
            r".{0,60}\b(?:road|highway|carriageway|paved shoulder)\b",
            text,
            re.IGNORECASE,
        )
        structure_focused = re.search(
            r"\b(?:construction|reconstruction|widening|strengthening|providing|rectification|"
            r"rehabilitation|improvement|repair)\b"
            r"(?:(?!\b(?:road|highway|carriageway|pavement)\b).){0,70}"
            r"\b(?:bridges?|viaducts?|flyovers?|underpass(?:es)?|"
            r"V\.?\s*U\.?\s*Ps?\.?|L\.?\s*V\.?\s*U\.?\s*Ps?\.?|"
            r"R\.?\s*O\.?\s*Bs?\.?|F\.?\s*O\.?\s*Bs?\.?|culverts?|tunnels?|parking)\b",
            text,
            re.IGNORECASE,
        )
        protective_only = re.search(
            r"\b(?:slope protection|landslide(?:\s+(?:debris\s+)?clearance|\s+mitigation)?|riverbank protection|"
            r"retaining wall|road furniture|signage|median cut)\b",
            text,
            re.IGNORECASE,
        )
        protective_focus = re.search(
            r"^(?:RFP\s+for\s+)?(?:(?:special|urgent)\s+(?:repair|restoration)\s+(?:and|&)\s+)?"
            r"(?:hill\s+side\s+)?slope\s+protection\b|"
            r"^short[- ]term\s+maintenance(?:\s+contract)?\s+(?:for\s+)?"
            r"(?:slope\s+protection|landslide\b)|"
            r"^hiring\b.{0,80}\blandslide\b",
            text,
            re.IGNORECASE,
        )
        road_tunnel_only = re.search(r"\b(?:road\s+)?tunnel\b", text, re.IGNORECASE)

        if road_tunnel_only and not SURFACE_RE.search(text):
            return []
        if structure_focused and not SURFACE_RE.search(text):
            return []
        if protective_focus:
            return []
        if protective_only and not SURFACE_RE.search(text):
            return []
        if not (SURFACE_RE.search(text) or explicit_road_project or network_maintenance):
            return []

    return evidence


def is_strict_carriageway_scope(title: str) -> bool:
    return bool(scope_evidence(title))


def _iso_date(value: Any, formats: Iterable[str]) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for date_format in formats:
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            pass
    return None


def _nhidcl_due_at(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for date_format in ("%a, %m/%d/%Y - %H:%M", "%m/%d/%Y - %H:%M", "%d-%m-%Y %H:%M:%S"):
        try:
            # Drupal's CSV export renders its stored UTC value without a zone
            # suffix.  The public HTML adds 05:30 for the displayed IST deadline.
            parsed = datetime.strptime(text, date_format).replace(tzinfo=timezone.utc).astimezone(IST)
            return parsed.isoformat(timespec="minutes")
        except ValueError:
            pass
    return None


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _contractor(value: Any) -> str | None:
    text = clean_text(value)
    if text.casefold() in {
        "",
        "n/a",
        "na",
        "nil",
        "not available",
        "not applicable",
        "to be appointed",
        "state pwd",
        "pwd",
    }:
        return None
    return text


def _division(row: dict[str, Any]) -> str | None:
    parts = [clean_text(row.get("ro")), clean_text(row.get("name_piu"))]
    joined = " / ".join(part for part in parts if part)
    return joined or None


def _nhai_lifecycle(status: str, title: str) -> str:
    status_text = status.casefold()
    combined = f"{status} {title}".casefold()
    # A completion certificate wins over words such as "maintenance" in the historical
    # work title.  Keep a CC-issued row only when the status itself explicitly says the
    # construction agency is still performing O&M.
    ongoing_after_cc = "o&m" in status_text or "operation and maintenance" in status_text
    if (
        "archived" in status_text
        or "completed" in status_text
        or (("cc issued" in status_text or "pcc issued" in status_text)
            and not ongoing_after_cc)
    ):
        return "completed"
    if "maintenance" in combined or "o&m" in combined or "operation and maintenance" in combined:
        return "maintenance"
    if "awarded" in combined and "not appointed" in combined:
        return "awarded"
    if "under construction" in combined:
        return "construction"
    return "implementation"


def normalise_nhai(rows: Iterable[dict[str, Any]], retrieved_at: str) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()
    retrieved_on = _parse_instant(retrieved_at).astimezone(timezone.utc).date().isoformat()
    for row in rows:
        title = clean_text(row.get("project_name"))
        upc = clean_text(row.get("upc"))
        code = state_code(row.get("state"), title)
        if not title or not upc or not code or not is_strict_carriageway_scope(title):
            continue
        record_id = f"morth-upc:{upc}"
        if record_id in seen:
            continue
        status = clean_text(row.get("current_project_stage")) or "Project listed under implementation"
        # The Data Lake endpoint is named "projects under implementation", but it also
        # returns completed/archived rows.  Its own status is authoritative here: a
        # completed record must not be republished as a current project candidate.
        # "CC Issued & O&M by Construction Agency" remains eligible because the source
        # explicitly reports ongoing maintenance after completion certification.
        if _nhai_lifecycle(status, title) == "completed":
            continue
        seen.add(record_id)
        contractor = _contractor(row.get("name_of_concessionaire"))
        contracts.append(
            {
                "record_id": record_id,
                "reference_label": "UPC",
                "reference_value": upc,
                "state_code": code,
                "agency": "MoRTH",
                "lifecycle": "current_project",
                "lifecycle_status": status,
                "title": title,
                "highway_refs": parse_highway_refs(title),
                "chainages": parse_chainages(title),
                "contractor": contractor,
                "published_at": None,
                "start_date": _iso_date(row.get("start_date"), ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")),
                "likely_completion_date": _iso_date(
                    row.get("likely_completion_date"), ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")
                ),
                "bid_due_at": None,
                "division": _division(row),
                "source_name": NHAI_SOURCE_NAME,
                "source_url": NHAI_SOURCE_URL,
                "retrieved_at": retrieved_on,
                "scope_verified": True,
                "segment_verified": False,
                "award_verified": contractor is not None,
                "dlp_verified": False,
            }
        )
    return contracts


def _notice_fingerprint(title: str, code: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    return hashlib.sha256(f"{code}|{normalized}".encode("utf-8")).hexdigest()[:16]


def _tender_id(row: dict[str, Any], title: str) -> str | None:
    for field in ("field_tender_id", "tender_id", "Tender ID", "tenderId"):
        match = TENDER_ID_RE.search(clean_text(row.get(field)))
        if match:
            return match.group(0).upper()
    match = TENDER_ID_RE.search(title)
    return match.group(0).upper() if match else None


def normalise_nhidcl(rows: Iterable[dict[str, Any]], retrieved_at: str) -> list[dict[str, Any]]:
    """Normalize NHIDCL notices without treating them as awards.

    Exact-title duplicates in the export are usually revised document rows.  The row
    with the latest parseable bid due date is retained, while its stable fingerprint
    remains unchanged across date revisions.
    """

    selected: dict[str, tuple[str, dict[str, Any], str, str | None, str | None]] = {}
    for row in rows:
        title = clean_text(row.get("field_sub_title") or row.get("title"))
        code = state_code(row.get("field_select_state") or row.get("state"), title)
        if not title or not code or not is_strict_carriageway_scope(title):
            continue
        tender_id = _tender_id(row, title)
        fingerprint = _notice_fingerprint(title, code)
        key = tender_id or fingerprint
        due_at = _nhidcl_due_at(row.get("field_bid_due_date") or row.get("bid_due_date"))
        rank = due_at or ""
        if key not in selected or rank > selected[key][0]:
            selected[key] = (rank, row, code, tender_id, due_at)

    retrieved = _parse_instant(retrieved_at)
    snapshot_date = retrieved.astimezone(IST).date()
    retrieved_on = retrieved.astimezone(timezone.utc).date().isoformat()
    contracts: list[dict[str, Any]] = []
    for key, (_, row, code, tender_id, due_at) in selected.items():
        title = clean_text(row.get("field_sub_title") or row.get("title"))
        fingerprint = _notice_fingerprint(title, code)
        # The site's CSV export includes old notices despite being labelled
        # "current tenders".  A missing or past bid date cannot support an active
        # procurement claim, so retain only notices open on the snapshot date.
        if not due_at or _parse_instant(due_at).astimezone(IST).date() < snapshot_date:
            continue
        status = "active tender notice"
        contracts.append(
            {
                "record_id": f"nhidcl-tender:{tender_id}" if tender_id else f"nhidcl-notice:{fingerprint}",
                "reference_label": "Tender ID" if tender_id else "Official notice fingerprint",
                "reference_value": tender_id or fingerprint,
                "state_code": code,
                "agency": "NHIDCL",
                "lifecycle": "procurement_notice",
                "lifecycle_status": status,
                "title": title,
                "highway_refs": parse_highway_refs(title),
                "chainages": parse_chainages(title),
                "contractor": None,
                "published_at": None,
                "start_date": None,
                "likely_completion_date": None,
                "bid_due_at": due_at,
                "division": None,
                "source_name": NHIDCL_SOURCE_NAME,
                "source_url": NHIDCL_SOURCE_URL,
                "retrieved_at": retrieved_on,
                "scope_verified": True,
                "segment_verified": False,
                "award_verified": False,
                "dlp_verified": False,
            }
        )
    return contracts


def _read_bytes(source: str, accept: str) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = urllib.request.Request(source, headers={"Accept": accept, "User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read(), "live"
    return Path(source).read_bytes(), "offline_snapshot"


def _nhai_rows(payload: bytes) -> list[dict[str, Any]]:
    document = json.loads(payload.decode("utf-8-sig"))
    if isinstance(document, list):
        rows = document
    elif isinstance(document, dict):
        rows = next(
            (document.get(key) for key in ("data", "records", "content") if isinstance(document.get(key), list)),
            None,
        )
    else:
        rows = None
    if rows is None or not all(isinstance(row, dict) for row in rows):
        raise ValueError("NHAI/MoRTH input must be a JSON list, or an object with a data/records/content list")
    return rows


def _nhidcl_rows(payload: bytes) -> list[dict[str, Any]]:
    text = payload.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "field_sub_title" not in (rows[0].keys() if rows else []):
        raise ValueError("NHIDCL input is not the official current-tenders CSV export")
    return rows


def build_pack(
    nhai_payload: bytes,
    nhidcl_payload: bytes,
    retrieved_at: str,
    nhai_mode: str = "offline_snapshot",
    nhidcl_mode: str = "offline_snapshot",
) -> dict[str, Any]:
    nhai_rows = _nhai_rows(nhai_payload)
    nhidcl_rows = _nhidcl_rows(nhidcl_payload)
    contracts = normalise_nhai(nhai_rows, retrieved_at) + normalise_nhidcl(nhidcl_rows, retrieved_at)
    contracts.sort(
        key=lambda row: (
            row["state_code"],
            row["highway_refs"][0] if row["highway_refs"] else "ZZZ",
            row["record_id"],
        )
    )
    retrieved_on = _parse_instant(retrieved_at).astimezone(timezone.utc).date().isoformat()
    return {
        "schema_version": 1,
        "generated_at": retrieved_at,
        "sources": [
            {
                "source_name": NHAI_SOURCE_NAME,
                "source_url": NHAI_SOURCE_URL,
                "retrieved_at": retrieved_on,
                "input_mode": nhai_mode,
                "sha256": hashlib.sha256(nhai_payload).hexdigest(),
                "records_seen": len(nhai_rows),
                "records_kept": sum(1 for row in contracts if row["source_name"] == NHAI_SOURCE_NAME),
            },
            {
                "source_name": NHIDCL_SOURCE_NAME,
                "source_url": NHIDCL_SOURCE_URL,
                "retrieved_at": retrieved_on,
                "input_mode": nhidcl_mode,
                "sha256": hashlib.sha256(nhidcl_payload).hexdigest(),
                "records_seen": len(nhidcl_rows),
                "records_kept": sum(1 for row in contracts if row["source_name"] == NHIDCL_SOURCE_NAME),
            },
        ],
        "contracts": contracts,
    }


def _retrieved_at(value: str | None) -> str:
    if value:
        parsed = _parse_instant(value).astimezone(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nhai-input",
        default=NHAI_SOURCE_URL,
        help="Official NHAI/MoRTH JSON URL or an offline JSON snapshot path",
    )
    parser.add_argument(
        "--nhidcl-input",
        default=NHIDCL_SOURCE_URL,
        help="Official NHIDCL CSV URL or an offline CSV snapshot path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "tenders-national-highways.json",
        help="Output path (default: data/tenders-national-highways.json)",
    )
    parser.add_argument(
        "--retrieved-at",
        help="UTC retrieval timestamp for reproducible offline builds (ISO 8601)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print instead of compact JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    retrieved_at = _retrieved_at(args.retrieved_at)
    try:
        nhai_payload, nhai_mode = _read_bytes(args.nhai_input, "application/json")
        nhidcl_payload, nhidcl_mode = _read_bytes(args.nhidcl_input, "text/csv")
        pack = build_pack(nhai_payload, nhidcl_payload, retrieved_at, nhai_mode, nhidcl_mode)
    except (OSError, ValueError, json.JSONDecodeError, csv.Error) as exc:
        print(f"national-highway import failed: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        if args.pretty:
            json.dump(pack, handle, ensure_ascii=False, indent=2)
        else:
            json.dump(pack, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")

    counts = {source["source_name"]: source["records_kept"] for source in pack["sources"]}
    print(f"wrote {len(pack['contracts'])} strict carriageway records to {args.output}")
    for source_name, count in counts.items():
        print(f"  {source_name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
