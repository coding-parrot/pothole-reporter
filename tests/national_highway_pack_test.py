#!/usr/bin/env python3
"""The committed nationwide NH/NE catalog and every immutable tile must verify."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
result = subprocess.run(
    [sys.executable, "tools/build-national-highways.py", "--check"],
    cwd=ROOT,
    capture_output=True,
    text=True,
)
if result.returncode:
    print("FAIL")
    print(result.stdout, end="")
    print(result.stderr, end="")
    raise SystemExit(result.returncode)
print("NATIONAL HIGHWAY PACK TEST PASS")
