#!/usr/bin/env python3
"""Pull source-reported in-progress PMGSY road agreements from the official dashboard.

The PMGSY dashboard exposes a public JSON response containing OMMAS road and package
identifiers, road names, scheme metadata, sanction data, agreement numbers/dates and a
source-reported work status.  It does not return award/contractor assignment evidence,
road geometry, completion/maintenance dates or DLP classification.  This tool preserves
those limits.

Live runs require one or more explicit ``--state`` selectors or ``--all-states``.
Nationwide pulls write one normalized source snapshot at a time with ``--output-dir``;
raw endpoint responses are never retained.  By default only rows whose official
``WORK_STATUS`` is exactly ``In Progress`` are retained; this is not an independent claim
that the portal status is current in the real world.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DASHBOARD_URL = "https://pmgsy.dord.gov.in/dbweb"
DETAIL_URL = (
    "https://pmgsy.dord.gov.in/dbweb/ChiefSecretary/GetRoadTenderDetails"
)
SOURCE_NAME = "PMGSY dashboard road tender/agreement details"
SOURCE_FORMAT = "pmgsy-current-road-agreement-source"
SCHEMA_VERSION = 1
SOURCE_STATUS = "In Progress"
FRESHNESS_WINDOW_YEARS = 5
SOURCE_LIMITATIONS = [
    "WORK_STATUS is source-reported and not independently freshness-verified",
    "catalog excludes missing, future, or more-than-five-year-old agreement dates",
    "road names are not road geometry and do not verify responsibility for a captured segment",
    "endpoint does not provide contractor, completion, maintenance, or DLP fields",
    "agreement number/date verifies those agreement fields, not an award or contractor assignment",
]
USER_AGENT = (
    "Pothole Reporter official-data builder/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)
DOTNET_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")

# These are the numeric state values published in the dashboard's own state selector.
STATE_SELECTORS: dict[str, tuple[int, str, str]] = {
    "an": (1, "AN", "Andaman And Nicobar"),
    "andaman and nicobar": (1, "AN", "Andaman And Nicobar"),
    "ap": (2, "AP", "Andhra Pradesh"),
    "andhra pradesh": (2, "AP", "Andhra Pradesh"),
    "ar": (3, "AR", "Arunachal Pradesh"),
    "arunachal pradesh": (3, "AR", "Arunachal Pradesh"),
    "as": (4, "AS", "Assam"),
    "assam": (4, "AS", "Assam"),
    "br": (5, "BR", "Bihar"),
    "bihar": (5, "BR", "Bihar"),
    "ch": (6, "CH", "Chandigarh"),
    "chandigarh": (6, "CH", "Chandigarh"),
    "cg": (7, "CG", "Chhattisgarh"),
    "chhattisgarh": (7, "CG", "Chhattisgarh"),
    "dh": (8, "DH", "Dadra And Nagar Haveli"),
    "dadra and nagar haveli": (8, "DH", "Dadra And Nagar Haveli"),
    "daman and diu": (9, "DH", "Daman And Diu"),
    "dl": (10, "DL", "Delhi"),
    "delhi": (10, "DL", "Delhi"),
    "ga": (11, "GA", "Goa"),
    "goa": (11, "GA", "Goa"),
    "gj": (12, "GJ", "Gujarat"),
    "gujarat": (12, "GJ", "Gujarat"),
    "hr": (13, "HR", "Haryana"),
    "haryana": (13, "HR", "Haryana"),
    "hp": (14, "HP", "Himachal Pradesh"),
    "himachal pradesh": (14, "HP", "Himachal Pradesh"),
    "jk": (15, "JK", "Jammu And Kashmir"),
    "jammu and kashmir": (15, "JK", "Jammu And Kashmir"),
    "jh": (16, "JH", "Jharkhand"),
    "jharkhand": (16, "JH", "Jharkhand"),
    "ka": (17, "KA", "Karnataka"),
    "karnataka": (17, "KA", "Karnataka"),
    "kl": (18, "KL", "Keralam"),
    "kerala": (18, "KL", "Keralam"),
    "keralam": (18, "KL", "Keralam"),
    "ld": (19, "LD", "Lakshadweep"),
    "lakshadweep": (19, "LD", "Lakshadweep"),
    "mp": (20, "MP", "Madhya Pradesh"),
    "madhya pradesh": (20, "MP", "Madhya Pradesh"),
    "mh": (21, "MH", "Maharashtra"),
    "maharashtra": (21, "MH", "Maharashtra"),
    "mn": (22, "MN", "Manipur"),
    "manipur": (22, "MN", "Manipur"),
    "ml": (23, "ML", "Meghalaya"),
    "meghalaya": (23, "ML", "Meghalaya"),
    "mz": (24, "MZ", "Mizoram"),
    "mizoram": (24, "MZ", "Mizoram"),
    "nl": (25, "NL", "Nagaland"),
    "nagaland": (25, "NL", "Nagaland"),
    "od": (26, "OD", "Odisha"),
    "odisha": (26, "OD", "Odisha"),
    "py": (27, "PY", "Puducherry"),
    "puducherry": (27, "PY", "Puducherry"),
    "pb": (28, "PB", "Punjab"),
    "punjab": (28, "PB", "Punjab"),
    "rj": (29, "RJ", "Rajasthan"),
    "rajasthan": (29, "RJ", "Rajasthan"),
    "sk": (30, "SK", "Sikkim"),
    "sikkim": (30, "SK", "Sikkim"),
    "tn": (31, "TN", "Tamil Nadu"),
    "tamil nadu": (31, "TN", "Tamil Nadu"),
    "tr": (32, "TR", "Tripura"),
    "tripura": (32, "TR", "Tripura"),
    "up": (33, "UP", "Uttar Pradesh"),
    "uttar pradesh": (33, "UP", "Uttar Pradesh"),
    "uk": (34, "UK", "Uttarakhand"),
    "uttarakhand": (34, "UK", "Uttarakhand"),
    "wb": (35, "WB", "West Bengal"),
    "west bengal": (35, "WB", "West Bengal"),
    "tg": (36, "TG", "Telangana"),
    "telangana": (36, "TG", "Telangana"),
    "la": (37, "LA", "Ladakh"),
    "ladakh": (37, "LA", "Ladakh"),
}
STATE_CODE_BY_ID = {value[0]: value[1] for value in STATE_SELECTORS.values()}
ALL_STATE_FEEDS = tuple(
    sorted({value for value in STATE_SELECTORS.values()}, key=lambda value: value[0])
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


def dotnet_date(value: Any) -> str | None:
    match = DOTNET_DATE_RE.match(clean_text(value))
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)) / 1000, timezone.utc).date().isoformat()


def resolve_states(selectors: list[str]) -> list[tuple[int, str, str]]:
    selected: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for selector in selectors:
        key = clean_text(selector).casefold()
        # The current Union Territory code DH combines two legacy PMGSY source feeds.
        # Fetch both for a code selector; the two source receipts stay separate and are
        # merged only by the content-addressed runtime-pack builder.
        items = (
            [item for item in ALL_STATE_FEEDS if item[1] == "DH"]
            if key == "dh"
            else [STATE_SELECTORS.get(key)]
        )
        item = items[0] if len(items) == 1 else None
        if len(items) == 1 and item is None:
            raise ValueError(f"PMGSY state selection not found: {selector}")
        if len(items) > 1:
            for selected_item in items:
                if selected_item[0] not in seen:
                    seen.add(selected_item[0])
                    selected.append(selected_item)
            continue
        if not item:
            raise ValueError(f"PMGSY state selection not found: {selector}")
        if item[0] not in seen:
            seen.add(item[0])
            selected.append(item)
    return selected


def fetch_state(state_id: int, timeout: int = 120) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        DETAIL_URL,
        data=json.dumps(
            {"StateID": state_id, "DistrictID": 0, "SchemeID": 0}
        ).encode(),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Referer": DASHBOARD_URL,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, list):
        raise ValueError("PMGSY road-tender response was not a JSON list")
    return [row for row in payload if isinstance(row, dict)]


def state_code(row: dict[str, Any]) -> str | None:
    try:
        source_id = int(row.get("MAST_STATE_CODE"))
    except (TypeError, ValueError):
        source_id = 0
    if source_id in STATE_CODE_BY_ID:
        return STATE_CODE_BY_ID[source_id]
    name = clean_text(row.get("MAST_STATE_NAME")).casefold()
    selected = STATE_SELECTORS.get(name)
    return selected[1] if selected else None


def nullable_identifier(value: Any) -> int | str | None:
    """Return a bounded JSON identifier without turning missing values into text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    text = clean_text(value)
    if not text:
        return None
    return int(text) if text.isdigit() else text[:200]


def nullable_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def has_fresh_agreement(record: dict[str, Any], generated_at: str) -> bool:
    agreement_date = record.get("agreement_date")
    if not agreement_date or not record.get("agreement_number"):
        return False
    retrieved_date = parse_instant(generated_at).date()
    try:
        cutoff = retrieved_date.replace(year=retrieved_date.year - FRESHNESS_WINDOW_YEARS)
    except ValueError:
        cutoff = retrieved_date.replace(
            year=retrieved_date.year - FRESHNESS_WINDOW_YEARS, day=28
        )
    parsed_agreement_date = datetime.fromisoformat(agreement_date).date()
    return cutoff <= parsed_agreement_date <= retrieved_date


def normalise_row(row: dict[str, Any], generated_at: str) -> dict[str, Any] | None:
    road_id = clean_text(row.get("IMS_PR_ROAD_CODE"))
    package = clean_text(row.get("IMS_PACKAGE_ID"))
    road_name = clean_text(row.get("IMS_ROAD_NAME"))
    portal_status = clean_text(row.get("WORK_STATUS"))
    if not road_id or not package or not road_name or not portal_status:
        return None

    agreement_number = clean_text(row.get("TEND_AGREEMENT_NUMBER")) or None
    agreement_date = dotnet_date(row.get("TEND_DATE_OF_AGREEMENT"))
    agreement_verified = bool(agreement_number and agreement_date)
    return {
        "record_id": f"PMGSY:{row.get('MAST_STATE_CODE')}:{road_id}",
        "reference_label": "PMGSY package",
        "reference_value": package,
        "state_code": state_code(row),
        "agency": "NRIDA / OMMAS",
        "lifecycle": "current_project" if portal_status.casefold() == "in progress" else "project_record",
        "lifecycle_status": portal_status,
        "lifecycle_basis": "source-reported WORK_STATUS plus agreement date within five-year catalog window; segment responsibility unverified",
        "title": road_name,
        "road_id": int(road_id) if road_id.isdigit() else road_id,
        "package_number": package,
        "state_name": clean_text(row.get("MAST_STATE_NAME")) or None,
        "district_id": nullable_identifier(row.get("MAST_DISTRICT_CODE")),
        "district_name": clean_text(row.get("MAST_DISTRICT_NAME")) or None,
        "road_from": clean_text(row.get("IMS_ROAD_FROM")) or None,
        "road_to": clean_text(row.get("IMS_ROAD_TO")) or None,
        "scheme": clean_text(row.get("PMGSY_SCHEME_NAME")) or None,
        "scheme_id": nullable_identifier(row.get("MAST_PMGSY_SCHEME")),
        "project_year": nullable_identifier(row.get("IMS_YEAR")),
        "project_batch": nullable_identifier(row.get("IMS_BATCH")),
        "pavement_length_km": nullable_number(row.get("IMS_PAV_LENGTH")),
        "sanctioned_date": dotnet_date(row.get("IMS_SANCTIONED_DATE")),
        "sanctioned_cost": nullable_number(row.get("TOTAL_SANCTIONED_COST")),
        "agreement_number": agreement_number,
        "agreement_date": agreement_date,
        "agreement_amount": nullable_number(row.get("TEND_AGREEMENT_AMOUNT")),
        "days_sanction_to_agreement": nullable_number(
            row.get("DAYS_SANCTION_TO_AGREEMENT")
        ),
        "contractor": None,
        "completion_date": None,
        "maintenance_start_date": None,
        "maintenance_end_date": None,
        "scope_verified": True,
        "segment_verified": False,
        "agreement_verified": agreement_verified,
        "award_verified": False,
        "contractor_assignment_verified": False,
        "dlp_verified": False,
        "source_name": SOURCE_NAME,
        "source_url": DETAIL_URL,
        "retrieved_at": generated_at[:10],
    }


def build_pack(
    rows: list[dict[str, Any]],
    generated_at: str,
    state_selectors: list[str] | None = None,
    include_noncurrent: bool = False,
    max_records: int = 0,
) -> dict[str, Any]:
    selected_ids = (
        {item[0] for item in resolve_states(state_selectors)} if state_selectors else set()
    )
    filtered = []
    for row in rows:
        try:
            row_state_id = int(row.get("MAST_STATE_CODE"))
        except (TypeError, ValueError):
            row_state_id = 0
        if selected_ids and row_state_id not in selected_ids:
            continue
        portal_status = clean_text(row.get("WORK_STATUS"))
        if not include_noncurrent and portal_status.casefold() != "in progress":
            continue
        record = normalise_row(row, generated_at)
        if record and (include_noncurrent or has_fresh_agreement(record, generated_at)):
            filtered.append(record)

    filtered.sort(
        key=lambda record: (
            record.get("state_code") or "",
            record.get("district_name") or "",
            record.get("package_number") or "",
            str(record.get("road_id") or ""),
        )
    )
    eligible = len(filtered)
    truncated = bool(max_records and eligible > max_records)
    if max_records:
        filtered = filtered[:max_records]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "sources": [
            {
                "name": SOURCE_NAME,
                "url": DASHBOARD_URL,
                "endpoint": DETAIL_URL,
                "retrieved_at": generated_at[:10],
                "access": "public unauthenticated JSON POST endpoint",
            }
        ],
        "coverage": {
            "source_rows": len(rows),
            "selected_states": sorted(
                {record.get("state_name") for record in filtered if record.get("state_name")}
            ),
            "source_status_filter": None if include_noncurrent else SOURCE_STATUS,
            "eligible_records": eligible,
            "records_kept": len(filtered),
            "truncated_by_max_records": truncated,
            "limitations": list(SOURCE_LIMITATIONS),
        },
        "contracts": filtered,
    }


def source_id(state_id: int, code: str) -> str:
    return f"in-{code.lower()}-pmgsy-{state_id:02d}"


def source_snapshot(
    rows: list[dict[str, Any]],
    generated_at: str,
    feed: tuple[int, str, str],
) -> dict[str, Any]:
    """Project one portal feed into the strict normalized source contract."""
    state_id, code, name = feed
    agreements: list[dict[str, Any]] = []
    excluded_by_status = 0
    excluded_by_freshness = 0
    excluded_invalid = 0
    for row in rows:
        try:
            row_state_id = int(row.get("MAST_STATE_CODE"))
        except (TypeError, ValueError):
            row_state_id = 0
        if row_state_id != state_id:
            excluded_invalid += 1
            continue
        if clean_text(row.get("WORK_STATUS")).casefold() != SOURCE_STATUS.casefold():
            excluded_by_status += 1
            continue
        record = normalise_row(row, generated_at)
        if record is None or record.get("state_code") != code:
            excluded_invalid += 1
            continue
        if not has_fresh_agreement(record, generated_at):
            excluded_by_freshness += 1
            continue
        agreements.append(record)
    agreements.sort(
        key=lambda record: (
            record.get("district_name") or "",
            record.get("package_number") or "",
            str(record.get("road_id") or ""),
        )
    )
    return {
        "format": SOURCE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id(state_id, code),
        "source_name": SOURCE_NAME,
        "source_url": DASHBOARD_URL,
        "endpoint": DETAIL_URL,
        "retrieved_at": generated_at,
        "source_state_id": state_id,
        "source_state_name": name,
        "state_code": code,
        "source_status_filter": SOURCE_STATUS,
        "freshness_window_years": FRESHNESS_WINDOW_YEARS,
        "rows_scanned": len(rows),
        "rows_excluded_by_status": excluded_by_status,
        "rows_excluded_by_freshness": excluded_by_freshness,
        "rows_excluded_invalid": excluded_invalid,
        "records_kept": len(agreements),
        "limitations": list(SOURCE_LIMITATIONS),
        "agreements": agreements,
    }


def render_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_content = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    content = gzip.compress(json_content, compresslevel=9, mtime=0) if path.suffix == ".gz" else json_content
    if path.exists() and path.read_bytes() == content:
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pull_to_directory(
    feeds: list[tuple[int, str, str]],
    output_directory: Path,
    generated_at: str,
    timeout: int,
) -> list[Path]:
    """Fetch, normalize and release one source feed before loading the next."""
    outputs: list[Path] = []
    for feed in feeds:
        state_id, code, _name = feed
        rows = fetch_state(state_id, timeout=timeout)
        snapshot = source_snapshot(rows, generated_at, feed)
        path = output_directory / f"{source_id(state_id, code)}.json.gz"
        write_json_atomic(path, snapshot)
        outputs.append(path)
        print(
            f"{snapshot['source_id']}: scanned {snapshot['rows_scanned']}; "
            f"kept {snapshot['records_kept']}",
            file=sys.stderr,
        )
        del rows, snapshot
    return outputs


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("offline input must be a JSON list or an object with a rows list")
    return [row for row in rows if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="offline raw endpoint response/fixture")
    parser.add_argument("--output", type=Path, help="write JSON here; otherwise stdout")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write one normalized source snapshot per live PMGSY source feed",
    )
    parser.add_argument("--state", action="append", default=[], help="state name/code; repeatable")
    parser.add_argument(
        "--all-states",
        action="store_true",
        help="pull all 37 portal feeds (36 current State/UT codes)",
    )
    parser.add_argument("--include-noncurrent", action="store_true")
    parser.add_argument("--max-records", type=int, default=0, help="0 means no output cap")
    parser.add_argument("--as-of", default=utc_now(), help="snapshot time in ISO 8601")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.max_records < 0:
        parser.error("--max-records cannot be negative")
    if args.all_states and args.state:
        parser.error("--all-states cannot be combined with --state")
    if args.input and (args.all_states or args.output_dir):
        parser.error("--input cannot be combined with --all-states or --output-dir")
    if args.output and args.output_dir:
        parser.error("--output and --output-dir are mutually exclusive")
    if args.include_noncurrent and args.output_dir:
        parser.error("normalized source snapshots intentionally keep only In Progress rows")
    if args.max_records and args.output_dir:
        parser.error("normalized source snapshots cannot be truncated")
    if not args.input and not (args.state or args.all_states):
        parser.error("live runs require --state or --all-states")
    if args.all_states and not args.output_dir:
        parser.error("--all-states requires --output-dir")

    generated_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if args.output_dir:
        feeds = list(ALL_STATE_FEEDS) if args.all_states else resolve_states(args.state)
        outputs = pull_to_directory(
            feeds, args.output_dir, generated_at, timeout=args.timeout
        )
        print(
            f"wrote {len(outputs)} normalized PMGSY source snapshots",
            file=sys.stderr,
        )
        return 0
    if args.input:
        rows = load_rows(args.input)
    else:
        states = resolve_states(args.state)
        rows = []
        for state_id, _code, _name in states:
            rows.extend(fetch_state(state_id, timeout=args.timeout))
    pack = build_pack(
        rows,
        generated_at,
        state_selectors=args.state or None,
        include_noncurrent=args.include_noncurrent,
        max_records=args.max_records,
    )
    rendered = render_json(pack)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        "read {source_rows} PMGSY rows; kept {records_kept}; truncated={truncated_by_max_records}".format(
            **pack["coverage"]
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
