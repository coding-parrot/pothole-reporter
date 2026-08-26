#!/usr/bin/env python3
"""Pull road-surface notices from every public State/UT GePNIC directory.

This is an orchestrator around ``pull-gepnic-tenders.py``. It only follows the public
"Tenders by Organisation" links exposed by each official portal, never a CAPTCHA form.
Jurisdictions using a different procurement product remain explicit gaps in the source
registry; they are not silently labelled complete.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "tender-sources-india.json"
DEFAULT_OUTPUT = ROOT / "data" / "gepnic-road-notices"
CRAWLER_PATH = ROOT / "tools" / "pull-gepnic-tenders.py"
STATE_CODE_OVERRIDES = {"CT": "CG", "UT": "UK"}
# GePNIC directories are not normalized: some portals expose descriptive road agencies,
# while Haryana exposes only the roots "Haryana Government" and "Haryana Board
# Corporation" and Dadra/Daman exposes district roots.  Selecting by organisation name
# therefore silently drops valid road notices.  Scan every public root organisation and
# let the strict title classifier decide scope; the crawler still follows only the public
# non-CAPTCHA listing links and records every excluded row in its receipt.
DEFAULT_ORGANISATION_PATTERNS = (r".*",)


def _load_crawler():
    spec = importlib.util.spec_from_file_location("gepnic_crawler", CRAWLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load crawler: {CRAWLER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_registry(path: Path = REGISTRY) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("jurisdictions"), list):
        raise RuntimeError("tender source registry has no jurisdictions")
    return value


def _runtime_state_code(jurisdiction_code: str) -> str:
    match = re.fullmatch(r"IN-([A-Z]{2})", jurisdiction_code)
    if not match:
        raise RuntimeError(f"invalid registry jurisdiction code: {jurisdiction_code!r}")
    return STATE_CODE_OVERRIDES.get(match.group(1), match.group(1))


def gepnic_sources(registry: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for jurisdiction in registry["jurisdictions"]:
        state_code = _runtime_state_code(jurisdiction["code"])
        for source in jurisdiction.get("sources", []):
            if source.get("portal_family") != "nic_gepnic":
                continue
            base = str(source.get("listing_url") or "").rstrip("?")
            separator = "&" if "?" in base else "?"
            sources.append({
                "source_id": source["id"],
                "source_name": f"{jurisdiction['name']} e-Procurement Portal",
                "state_code": state_code,
                "organisation_url": base + separator
                + "component=clear&page=FrontEndTendersByOrganisation&service=direct",
            })
    return sorted(sources, key=lambda item: (item["state_code"], item["source_id"]))


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--retrieved-at must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise argparse.ArgumentTypeError("--retrieved-at must use UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-code", action="append", default=[],
                        help="runtime two-letter State/UT code; repeatable")
    parser.add_argument("--organisation-regex", action="append", default=[],
                        help="case-insensitive full-match organisation allowlist; repeatable")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retrieved-at")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--request-delay", type=float, default=0.15)
    parser.add_argument("--allow-partial", action="store_true",
                        help="write successful receipts and a failure ledger instead of aborting")
    args = parser.parse_args()

    crawler = _load_crawler()
    retrieved_at = _timestamp(args.retrieved_at)
    wanted = {value.strip().upper() for value in args.state_code if value.strip()}
    invalid = sorted(value for value in wanted if not re.fullmatch(r"[A-Z]{2}", value))
    if invalid:
        parser.error(f"invalid --state-code values: {invalid}")
    sources = gepnic_sources(_read_registry())
    if wanted:
        sources = [source for source in sources if source["state_code"] in wanted]
    if not sources:
        parser.error("no public GePNIC sources matched the requested jurisdictions")

    allowlist = crawler.compile_allowlist(
        tuple(args.organisation_regex) or DEFAULT_ORGANISATION_PATTERNS
    )
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for source in sources:
        print(f"pull {source['source_id']} ({source['state_code']})", file=sys.stderr)
        try:
            result = crawler.crawl_live(
                source_url=source["organisation_url"],
                source_name=source["source_name"],
                state_code=source["state_code"],
                allowlist=allowlist,
                timeout=args.timeout,
                request_delay=args.request_delay,
                retrieved_at=retrieved_at,
            )
            result["source_id"] = source["source_id"]
            successes.append(result)
            _write_json(args.output_dir / "sources" / f"{source['source_id']}.json", result)
        except (crawler.CrawlerError, OSError) as error:
            failures.append({**source, "error": str(error)})
            print(f"FAIL {source['source_id']}: {error}", file=sys.stderr)
            if not args.allow_partial:
                return 2

    by_state: dict[str, list[dict[str, Any]]] = {}
    for receipt in successes:
        by_state.setdefault(receipt["state_code"], []).append(receipt)
    state_summaries: dict[str, dict[str, Any]] = {}
    for state_code, receipts in sorted(by_state.items()):
        by_id: dict[str, dict[str, Any]] = {}
        for receipt in receipts:
            for notice in receipt["notices"]:
                identity = notice["tender_id"]
                previous = by_id.get(identity)
                if previous is not None and previous != notice:
                    raise RuntimeError(f"conflicting duplicate {identity} in {state_code}")
                by_id[identity] = notice
        state_value = {
            "format": "india-gepnic-road-surface-procurement-notices",
            "schema_version": 1,
            "state_code": state_code,
            "retrieved_at": retrieved_at,
            "source_ids": sorted(receipt["source_id"] for receipt in receipts),
            "rows_scanned": sum(receipt["rows_scanned"] for receipt in receipts),
            "rows_excluded_by_scope": sum(
                receipt["rows_excluded_by_scope"] for receipt in receipts
            ),
            "notices": sorted(by_id.values(), key=lambda item: item["tender_id"]),
        }
        _write_json(args.output_dir / "states" / f"{state_code.lower()}.json", state_value)
        state_summaries[state_code] = {
            "sources": len(receipts),
            "rows_scanned": state_value["rows_scanned"],
            "rows_excluded_by_scope": state_value["rows_excluded_by_scope"],
            "notices": len(state_value["notices"]),
        }

    report = {
        "format": "india-gepnic-road-surface-crawl-report",
        "schema_version": 1,
        "retrieved_at": retrieved_at,
        "source_count_requested": len(sources),
        "source_count_succeeded": len(successes),
        "source_count_failed": len(failures),
        "states": state_summaries,
        "failures": failures,
    }
    _write_json(args.output_dir / "crawl-report.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())
