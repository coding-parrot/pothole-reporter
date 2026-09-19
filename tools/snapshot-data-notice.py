#!/usr/bin/env python3
"""Record the data notice text so its version can never fall behind its wording.

    python3 tools/snapshot-data-notice.py            # check
    python3 tools/snapshot-data-notice.py --write    # re-record after a review

Consent is only meaningful if an install that accepted the old wording is asked again
when the wording changes. The app decides that by comparing the stored
DATA_NOTICE_VERSION with the current one, so a disclosure edit that forgets the bump
silently keeps stale consent. This records a digest of every disclosure string next to
the version that was shipped with it.
"""

import argparse
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "static" / "index.html"
SNAPSHOT = ROOT / "eval" / "data-notice-snapshot.json"
KEYS = ("privacy_body", "privacy_local", "privacy_government", "privacy_accept",
        "privacy_decline", "privacy_title")


def current() -> dict:
    source = APP.read_text(encoding="utf-8")
    version = re.search(r'const DATA_NOTICE_VERSION = "([^"]+)"', source)
    if not version:
        raise SystemExit("FAIL DATA_NOTICE_VERSION not found in static/index.html")
    strings = []
    for key in KEYS:
        strings.extend(re.findall(rf'^\s+{key}: "([^"]*)"', source, re.MULTILINE))
    if not strings:
        raise SystemExit("FAIL no disclosure strings found in static/index.html")
    digest = hashlib.sha256("␟".join(strings).encode("utf-8")).hexdigest()
    return {"version": version.group(1), "strings": len(strings), "sha256": digest}


parser = argparse.ArgumentParser()
parser.add_argument("--write", action="store_true",
                    help="re-record the snapshot after reviewing the new wording")
args = parser.parse_args()

live = current()
if args.write:
    SNAPSHOT.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
    print(f"recorded data notice snapshot at version {live['version']}")
    raise SystemExit(0)

if not SNAPSHOT.is_file():
    raise SystemExit("FAIL no data notice snapshot; run with --write after reviewing")

recorded = json.loads(SNAPSHOT.read_text())
if recorded == live:
    print(f"data notice matches its recorded snapshot ({live['version']})")
    raise SystemExit(0)

if recorded.get("version") == live["version"]:
    raise SystemExit(
        "FAIL the data notice text changed but DATA_NOTICE_VERSION did not.\n"
        f"  version: {live['version']}\n"
        f"  recorded digest: {recorded.get('sha256')}\n"
        f"  current digest:  {live['sha256']}\n"
        "  Bump DATA_NOTICE_VERSION so accepted installs are asked again, then rerun "
        "with --write.")
raise SystemExit(
    f"FAIL the data notice moved from {recorded.get('version')} to {live['version']} "
    "without re-recording. Review the new wording, then rerun with --write.")
