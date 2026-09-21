# -*- coding: utf-8 -*-
"""Every browser suite must open the app POTHOLE_TEST_APP names.

The documented way to run one suite is against the bundle already served on 8766. A
suite that hard-codes the full harness's port 8765 fails with ERR_CONNECTION_REFUSED
there, which reads as an app failure when nothing was tested at all.
"""

import pathlib
import sys

TESTS = pathlib.Path(__file__).resolve().parent
HARNESS_PORT = "localhost:" + "8765"

offenders = []
for path in sorted(TESTS.glob("*.py")):
    source = path.read_text(encoding="utf-8")
    if HARNESS_PORT in source and "POTHOLE_TEST_APP" not in source:
        offenders.append(path.name)

if offenders:
    print("TEST APP ENV TEST FAIL: these suites ignore POTHOLE_TEST_APP:")
    for name in offenders:
        print(" -", name)
    sys.exit(1)
print("TEST APP ENV TEST PASS")
