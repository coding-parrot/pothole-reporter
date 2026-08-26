#!/usr/bin/env python3
"""Pull a bounded PMGSY road-maintenance index from official public eMARG JSON.

eMARG exposes public state, district, block and road lists plus per-road details.  The
detail response can identify the PMGSY package, contractor and stated maintenance dates.
It does not, by itself, prove an award document, DLP versus post-DLP classification, or a
geospatial road segment.  This tool preserves those distinctions and never calls a PMGSY
package number a tender number.

Live runs require explicit ``--state`` selections unless ``--all-states`` is supplied,
because each road needs a separate official detail request.  ``--input`` supports a fully
offline fixture/snapshot for deterministic tests.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any


BASE_URL = "https://emarg.gov.in"
HOME_URL = f"{BASE_URL}/"
STATES_URL = f"{BASE_URL}/public/getStateListForPublic.do"
DISTRICTS_URL = f"{BASE_URL}/public/getDistrictListByState.do"
BLOCKS_URL = f"{BASE_URL}/public/getBlockListByDistrict.do"
ROADS_URL = f"{BASE_URL}/public/getRoadListByDistrictAndBlockId.do"
DETAIL_URL = f"{BASE_URL}/public/getRoadDetailsByEncRoadDetailsId.do"
SOURCE_NAME = "eMARG public PMGSY road-maintenance records"
USER_AGENT = (
    "Pothole Reporter official-data builder/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)

STATE_CODES = {
    "andaman and nicobar islands": "AN",
    "andhra pradesh": "AP",
    "arunachal pradesh": "AR",
    "assam": "AS",
    "bihar": "BR",
    "chhattisgarh": "CG",
    "gujarat": "GJ",
    "haryana": "HR",
    "himachal pradesh": "HP",
    "jammu and kashmir": "JK",
    "jharkhand": "JH",
    "karnataka": "KA",
    "kerala": "KL",
    "keralam": "KL",
    "ladakh": "LA",
    "madhya pradesh": "MP",
    "maharashtra": "MH",
    "manipur": "MN",
    "meghalaya": "ML",
    "mizoram": "MZ",
    "nagaland": "NL",
    "odisha": "OD",
    "puducherry": "PY",
    "punjab": "PB",
    "rajasthan": "RJ",
    "sikkim": "SK",
    "tamil nadu": "TN",
    "telangana": "TG",
    "tripura": "TR",
    "uttar pradesh": "UP",
    "uttarakhand": "UK",
    "west bengal": "WB",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_portal_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d-%m-%Y").date()
    except ValueError:
        return None


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def nested_rows(payload: Any, label: str) -> list[dict[str, Any]]:
    rows = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else payload
    if not isinstance(rows, list):
        raise ValueError(f"eMARG {label} response was not a JSON list")
    return [row for row in rows if isinstance(row, dict)]


class LiveClient:
    def __init__(self, timeout: int = 60, delay: float = 0.2):
        cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
        self.timeout = timeout
        self.delay = max(0.0, delay)

    def _post(self, url: str, data: dict[str, str] | None = None) -> Any:
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data or {}).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": HOME_URL,
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            payload = json.loads(response.read())
        if self.delay:
            time.sleep(self.delay)
        return payload

    def states(self) -> list[dict[str, Any]]:
        return nested_rows(self._post(STATES_URL), "states")

    def districts(self, encoded_state_id: str) -> list[dict[str, Any]]:
        return nested_rows(
            self._post(DISTRICTS_URL, {"stateId": encoded_state_id}), "districts"
        )

    def blocks(self, encoded_district_id: str) -> list[dict[str, Any]]:
        return nested_rows(
            self._post(BLOCKS_URL, {"districtId": encoded_district_id}), "blocks"
        )

    def roads(self, encoded_district_id: str, encoded_block_id: str) -> list[dict[str, Any]]:
        return nested_rows(
            self._post(
                ROADS_URL,
                {"districtId": encoded_district_id, "blockId": encoded_block_id},
            ),
            "roads",
        )

    def detail(self, encoded_road_id: str) -> dict[str, Any]:
        payload = self._post(DETAIL_URL, {"roadDetailsId": encoded_road_id})
        if not isinstance(payload, dict):
            raise ValueError("eMARG road-detail response was not a JSON object")
        return payload


class FixtureClient:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def states(self) -> list[dict[str, Any]]:
        return list(self.payload.get("states", []))

    def districts(self, encoded_state_id: str) -> list[dict[str, Any]]:
        return list(self.payload.get("districts_by_state", {}).get(encoded_state_id, []))

    def blocks(self, encoded_district_id: str) -> list[dict[str, Any]]:
        return list(self.payload.get("blocks_by_district", {}).get(encoded_district_id, []))

    def roads(self, encoded_district_id: str, encoded_block_id: str) -> list[dict[str, Any]]:
        key = f"{encoded_district_id}|{encoded_block_id}"
        return list(self.payload.get("roads_by_district_block", {}).get(key, []))

    def detail(self, encoded_road_id: str) -> dict[str, Any]:
        detail = self.payload.get("details_by_road", {}).get(encoded_road_id)
        if not isinstance(detail, dict):
            raise KeyError(f"fixture has no detail for road {encoded_road_id}")
        return dict(detail)


def state_code(state_name: Any) -> str | None:
    return STATE_CODES.get(clean_text(state_name).casefold())


def select_states(
    available: list[dict[str, Any]], selectors: list[str] | None
) -> list[dict[str, Any]]:
    if not selectors:
        return available
    wanted = {clean_text(value).casefold() for value in selectors}
    selected = [
        row
        for row in available
        if clean_text(row.get("stateName")).casefold() in wanted
        or (state_code(row.get("stateName")) or "").casefold() in wanted
    ]
    matched = {
        clean_text(row.get("stateName")).casefold() for row in selected
    } | {(state_code(row.get("stateName")) or "").casefold() for row in selected}
    missing = sorted(value for value in wanted if value not in matched)
    if missing:
        raise ValueError(f"eMARG state selection not found: {', '.join(missing)}")
    return selected


def maintenance_status(start: date, end: date, as_of: date) -> str:
    if as_of < start:
        return "maintenance period scheduled"
    if as_of > end:
        return "maintenance period ended"
    return "maintenance period active"


def normalise_detail(
    detail: dict[str, Any],
    context: dict[str, Any],
    generated_at: str,
    include_inactive: bool = False,
) -> dict[str, Any] | None:
    as_of = parse_instant(generated_at).date()
    start = parse_portal_date(detail.get("strMaintenanceStartDate"))
    end = parse_portal_date(detail.get("strStipulatedDateOfCompletion"))
    if not start or not end:
        return None
    status = maintenance_status(start, end, as_of)
    if not include_inactive and status != "maintenance period active":
        return None

    package = detail.get("packageDetail") if isinstance(detail.get("packageDetail"), dict) else {}
    road_id = clean_text(detail.get("roadDetailsId"))
    encoded_road_id = clean_text(detail.get("encRoadDetailsId"))
    package_number = clean_text(package.get("packageNo"))
    package_id = clean_text(package.get("packageId"))
    contractor = clean_text(detail.get("contractorName")) or None
    agreement_number = clean_text(detail.get("agreementNo")) or None
    work_order_number = clean_text(package.get("workOrderNo")) or None
    if not road_id and not encoded_road_id:
        return None
    reference_label = "PMGSY package" if package_number else "eMARG road ID"
    reference_value = package_number or road_id or encoded_road_id
    return {
        "record_id": f"eMARG:{road_id or encoded_road_id}",
        "reference_label": reference_label,
        "reference_value": reference_value,
        "state_code": state_code(context.get("state_name")),
        "agency": "eMARG / NRIDA",
        "lifecycle": "maintenance_record",
        "lifecycle_status": status,
        "road_name": clean_text(detail.get("roadName")),
        "road_id": int(road_id) if road_id.isdigit() else None,
        "encoded_road_id": encoded_road_id or None,
        "package_number": package_number or None,
        "package_id": int(package_id) if package_id.isdigit() else None,
        "encoded_package_id": clean_text(package.get("packageIdEnc")) or None,
        "state_name": clean_text(context.get("state_name")),
        "district_name": clean_text(context.get("district_name")),
        "district_id": context.get("district_id"),
        "block_name": clean_text(context.get("block_name")),
        "block_id": context.get("block_id"),
        "contractor": contractor,
        "agreement_number": agreement_number,
        "work_order_number": work_order_number,
        "maintenance_start_date": start.isoformat(),
        "maintenance_end_date": end.isoformat(),
        "actual_length_km": detail.get("actualLength"),
        "bt_length_km": detail.get("btLength"),
        "cc_length_km": detail.get("ccLength"),
        "carriageway_width_m": detail.get("carriageWayWidth"),
        "road_status": clean_text(detail.get("averageMarks")) or None,
        "scope_verified": True,
        "segment_verified": False,
        "contractor_assignment_verified": contractor is not None,
        "maintenance_period_verified": True,
        "award_verified": contractor is not None and bool(agreement_number or work_order_number),
        "dlp_verified": False,
        "source_name": SOURCE_NAME,
        "source_url": DETAIL_URL,
        "retrieved_at": generated_at[:10],
    }


def build_pack(
    client: LiveClient | FixtureClient,
    generated_at: str,
    state_selectors: list[str] | None = None,
    max_details: int = 0,
    include_inactive: bool = False,
) -> dict[str, Any]:
    available_states = client.states()
    states = select_states(available_states, state_selectors)
    contracts: list[dict[str, Any]] = []
    seen_roads: set[str] = set()
    districts_scanned = blocks_scanned = roads_seen = details_fetched = 0
    truncated = False

    for state in states:
        state_name = clean_text(state.get("stateName"))
        encoded_state_id = clean_text(state.get("encStateId"))
        for district in client.districts(encoded_state_id):
            districts_scanned += 1
            district_name = clean_text(district.get("districtName"))
            district_id = district.get("districtId")
            encoded_district_id = clean_text(district.get("encDistrictId"))
            for block in client.blocks(encoded_district_id):
                blocks_scanned += 1
                block_name = clean_text(block.get("blockName"))
                block_id = block.get("blockId")
                encoded_block_id = clean_text(block.get("encBlockId"))
                roads = client.roads(encoded_district_id, encoded_block_id)
                roads_seen += len(roads)
                for road in roads:
                    encoded_road_id = clean_text(road.get("encRoadDetailsId"))
                    road_identity = clean_text(road.get("roadDetailsId")) or encoded_road_id
                    if not encoded_road_id or not road_identity or road_identity in seen_roads:
                        continue
                    if max_details and details_fetched >= max_details:
                        truncated = True
                        break
                    seen_roads.add(road_identity)
                    detail = client.detail(encoded_road_id)
                    details_fetched += 1
                    record = normalise_detail(
                        detail,
                        {
                            "state_name": state_name,
                            "district_name": district_name,
                            "district_id": district_id,
                            "block_name": block_name,
                            "block_id": block_id,
                        },
                        generated_at,
                        include_inactive=include_inactive,
                    )
                    if record:
                        contracts.append(record)
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break

    contracts.sort(
        key=lambda row: (
            row.get("state_code") or "",
            row.get("district_name") or "",
            row.get("block_name") or "",
            row.get("road_name") or "",
            row["record_id"],
        )
    )
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "sources": [
            {
                "name": SOURCE_NAME,
                "url": HOME_URL,
                "hierarchy_endpoints": [STATES_URL, DISTRICTS_URL, BLOCKS_URL, ROADS_URL],
                "detail_endpoint": DETAIL_URL,
                "retrieved_at": generated_at[:10],
                "access": "public unauthenticated JSON POST endpoints",
            }
        ],
        "coverage": {
            "available_states": len(available_states),
            "selected_states": [clean_text(row.get("stateName")) for row in states],
            "districts_scanned": districts_scanned,
            "blocks_scanned": blocks_scanned,
            "roads_seen": roads_seen,
            "details_fetched": details_fetched,
            "records_kept": len(contracts),
            "truncated_by_max_details": truncated,
        },
        "contracts": contracts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="offline hierarchy/detail fixture JSON")
    parser.add_argument("--output", type=Path, help="write JSON here; otherwise stdout")
    parser.add_argument("--state", action="append", default=[], help="state name/code; repeatable")
    parser.add_argument("--all-states", action="store_true", help="explicitly scan every eMARG state")
    parser.add_argument("--max-details", type=int, default=0, help="0 means no explicit cap")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--as-of", default=utc_now(), help="snapshot time in ISO 8601")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.2, help="seconds after every live request")
    args = parser.parse_args()
    if args.max_details < 0:
        parser.error("--max-details cannot be negative")
    if not args.input and not args.state and not args.all_states:
        parser.error("live runs require --state (repeatable) or explicit --all-states")
    if args.state and args.all_states:
        parser.error("use either --state or --all-states, not both")

    generated_at = parse_instant(args.as_of).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("offline eMARG input must be a JSON object")
        client: LiveClient | FixtureClient = FixtureClient(payload)
    else:
        client = LiveClient(timeout=args.timeout, delay=args.delay)
    selectors = args.state or None
    pack = build_pack(
        client,
        generated_at,
        state_selectors=selectors,
        max_details=args.max_details,
        include_inactive=args.include_inactive,
    )
    rendered = json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    print(
        "fetched {details_fetched} eMARG road details; kept {records_kept}; truncated={truncated_by_max_details}".format(
            **pack["coverage"]
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
