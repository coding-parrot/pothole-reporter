#!/usr/bin/env python3
"""Build or verify the hosted state-pack catalog used by the pure client."""

from __future__ import annotations

import argparse
import sys

from state_pack_tools import PackError, build_all, verify_all


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build immutable state packs and their checksum-pinned bundled manifest."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify only; never write files",
    )
    args = parser.parse_args()
    try:
        if args.check:
            verify_all()
            print("state packs OK")
        else:
            outputs = build_all()
            for output in outputs:
                print(output)
            print("state packs and bundled manifests updated")
    except PackError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
