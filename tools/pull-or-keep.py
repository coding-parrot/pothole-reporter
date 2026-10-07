#!/usr/bin/env python3
"""Run one State portal puller; if the portal stays down, keep the last snapshot.

One portal with an expired certificate or a timeout used to abort the whole catalogue
refresh, so no State's notices were renewed (runs of 5 and 6 Oct 2026). This wrapper
retries the puller, and when every attempt fails it restores the committed snapshot and
lets the refresh go on, as long as that snapshot is still inside the review window. A
snapshot older than the window is never republished: the wrapper fails instead.

Usage: pull-or-keep.py --output PATH [--attempts N] [--max-age-days D] -- COMMAND...
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_ATTEMPTS = 3
DEFAULT_MAX_AGE_DAYS = 7
RETRY_SECONDS = 20


def snapshot_age(path: Path, now: datetime) -> timedelta:
    value = json.loads(path.read_text(encoding="utf-8")).get("retrieved_at")
    retrieved = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return now - retrieved


def restore(path: Path) -> bool:
    return subprocess.run(["git", "checkout", "HEAD", "--", str(path)]).returncode == 0


def pull_or_keep(
    output: Path,
    command: list[str],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    max_age: timedelta = timedelta(days=DEFAULT_MAX_AGE_DAYS),
    run=lambda command: subprocess.run(command).returncode,
    restore=restore,
    sleep=time.sleep,
    now=lambda: datetime.now(timezone.utc),
    log=lambda line: print(line, flush=True),
) -> int:
    for attempt in range(1, attempts + 1):
        status = run(command)
        if status == 0:
            return 0
        log(f"::warning::{output}: attempt {attempt} of {attempts} exited {status}")
        if attempt < attempts:
            sleep(RETRY_SECONDS * attempt)
    if not restore(output):
        log(f"::error::{output}: the portal is down and there is no committed snapshot to keep")
        return 1
    try:
        age = snapshot_age(output, now())
    except (OSError, ValueError) as error:
        log(f"::error::{output}: the kept snapshot cannot be dated: {error}")
        return 1
    if age > max_age:
        log(f"::error::{output}: the kept snapshot is {age.days} days old, past the review window")
        return 1
    log(f"::warning::{output}: portal down, kept the snapshot from {age.days} day(s) ago")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a puller command is required after --")
    return pull_or_keep(
        args.output, command,
        attempts=args.attempts, max_age=timedelta(days=args.max_age_days),
    )


if __name__ == "__main__":
    raise SystemExit(main())
