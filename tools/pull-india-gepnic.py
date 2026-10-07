#!/usr/bin/env python3
"""Pull road-surface notices from every public State/UT GePNIC directory.

This is an orchestrator around ``pull-gepnic-tenders.py``. It only follows the public
"Tenders by Organisation" links exposed by each official portal, never a CAPTCHA form.
Jurisdictions using a different procurement product remain explicit gaps in the source
registry; they are not silently labelled complete.

A portal that stays down keeps the receipt of the crawl before it, byte for byte and
under its own ``retrieved_at``, while that receipt is inside the review window
(``--previous-sources``). The crawl report names every receipt kept this way.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
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


CRAWL_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 30


def crawl_with_retries(crawler: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Ask a portal up to three times.

    From GitHub's runners the State portals reset connections, time out and answer
    503 for a minute at a time; on 6 Oct 2026 seven of thirty did in one pass. One
    dropped connection used to cost that State its notices for the week.
    """
    for attempt in range(1, CRAWL_ATTEMPTS + 1):
        try:
            return crawler.crawl_live(**kwargs)
        except (crawler.CrawlerError, OSError):
            if attempt == CRAWL_ATTEMPTS:
                raise
            time.sleep(RETRY_PAUSE_SECONDS * attempt)
    raise AssertionError("unreachable")


# The review window of build-gepnic-road-notice-packs.py (REVIEW_DAYS). The builder
# refuses a carried receipt older than its own window, so a longer one here could only
# turn a dropped portal into a refused build; tests/gepnic_carry_over_test.py holds the
# two numbers together.
CARRY_OVER_DAYS = 7


def carry_over(
    source: dict[str, str], previous_sources: Path, output_dir: Path, retrieved_at: str
) -> dict[str, str] | None:
    """Keep a failed portal's last receipt as it was, if it is still inside the window.

    Run 37573977613 (7 Oct 2026) timed out on West Bengal's main portal and the State
    went from 874 notices to 55, although the receipt of 5 Oct had five days of its
    review window left. The receipt is copied back unchanged, so it still carries its
    own ``retrieved_at``, and the ledger entry returned here says from when it is and
    what its bytes hash to.

    The carry-over lives in the crawler, not in a step between crawl and build, because
    only the crawler knows a portal failed in this crawl, and it already owns the
    ledger. The pack builder can then stay strict with one author to check against: a
    failed source may have a receipt only if this ledger lists it, with the same bytes
    and a date inside the window. Anything else is still refused there.
    """
    path = previous_sources / f"{source['source_id']}.json"
    try:
        content = path.read_bytes()
    except OSError:
        return None
    try:
        previous = json.loads(content.decode("utf-8"))
        if not isinstance(previous, dict):
            raise ValueError("not a receipt")
        kept_at = str(previous.get("retrieved_at"))
        age = (
            datetime.strptime(retrieved_at, "%Y-%m-%dT%H:%M:%SZ")
            - datetime.strptime(kept_at, "%Y-%m-%dT%H:%M:%SZ")
        )
    except ValueError as error:
        print(f"DROP {source['source_id']}: the last receipt cannot be read: {error}",
              file=sys.stderr)
        return None
    if (previous.get("source_id"), previous.get("state_code")) != (
        source["source_id"], source["state_code"]
    ):
        print(f"DROP {source['source_id']}: the last receipt belongs to another source",
              file=sys.stderr)
        return None
    # Never republish a receipt past its review date, and never one that claims to be
    # as new as (or newer than) the crawl that failed to fetch it.
    if not timedelta(0) < age <= timedelta(days=CARRY_OVER_DAYS):
        print(f"DROP {source['source_id']}: the last receipt ({kept_at}) is outside "
              f"the {CARRY_OVER_DAYS}-day review window", file=sys.stderr)
        return None
    target = output_dir / "sources" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {
        "source_id": source["source_id"],
        "state_code": source["state_code"],
        "retrieved_at": kept_at,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


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
    parser.add_argument("--previous-sources", type=Path,
                        help="receipts of the previous crawl, staged outside --output-dir; "
                             "a portal that fails keeps its receipt from here while it is "
                             "inside the review window")
    args = parser.parse_args()
    # The output directory starts without the previous receipts, so one whose source
    # has left the registry cannot linger: only a declared source that failed in this
    # crawl is ever copied back.
    if args.previous_sources is not None:
        staged, output = args.previous_sources.resolve(), args.output_dir.resolve()
        if staged == output or output in staged.parents:
            parser.error("--previous-sources must be staged outside --output-dir")

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
    carried_over: list[dict[str, str]] = []
    for source in sources:
        print(f"pull {source['source_id']} ({source['state_code']})", file=sys.stderr)
        try:
            result = crawl_with_retries(crawler, dict(
                source_url=source["organisation_url"],
                source_name=source["source_name"],
                state_code=source["state_code"],
                allowlist=allowlist,
                timeout=args.timeout,
                request_delay=args.request_delay,
                retrieved_at=retrieved_at,
            ))
            result["source_id"] = source["source_id"]
            successes.append(result)
            _write_json(args.output_dir / "sources" / f"{source['source_id']}.json", result)
        except (crawler.CrawlerError, OSError) as error:
            failures.append({**source, "error": str(error)})
            print(f"FAIL {source['source_id']}: {error}", file=sys.stderr)
            if not args.allow_partial:
                return 2
            if args.previous_sources is not None:
                kept = carry_over(source, args.previous_sources, args.output_dir, retrieved_at)
                if kept is not None:
                    carried_over.append(kept)
                    print(f"::warning::{source['source_id']}: portal down, kept the "
                          f"receipt of {kept['retrieved_at']}", file=sys.stderr)

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
        # A carried-over source is still a failure of this crawl: it stays in the
        # counts and the failure ledger above, and "states" describes only what this
        # crawl fetched. This list says which failed sources kept an older receipt.
        "carried_over": carried_over,
    }
    _write_json(args.output_dir / "crawl-report.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())
