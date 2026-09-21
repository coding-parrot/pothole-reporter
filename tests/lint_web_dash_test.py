# -*- coding: utf-8 -*-
"""lint-web must reject em and en dashes, and the Drive tip must be translated.

An aria-label, a Drive tip and three translated verdicts shipped with dashes while
lint-web still printed "Static web checks passed". The native Drive tip was also a
hardcoded English line in a four-language app. Plant a dash and watch the gate fail.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LINT = ROOT / "tools/harness/lint-web.mjs"


def lint(paths):
    return subprocess.run(["node", str(LINT), *map(str, paths)],
                          capture_output=True, text=True, cwd=ROOT)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        html = pathlib.Path(tmp) / "index.html"
        js = pathlib.Path(tmp) / "standalone.js"
        shutil.copy(ROOT / "static/index.html", html)
        shutil.copy(ROOT / "static/standalone.js", js)
        clean = lint([html, js])
        if clean.returncode != 0:
            failures.append("lint-web fails on the clean sources:\n" + clean.stdout)
        for dash in ("\u2014", "\u2013"):
            planted = pathlib.Path(tmp) / "planted.js"
            planted.write_text(js.read_text(encoding="utf-8")
                               + f"\n// planted {dash} dash\n", encoding="utf-8")
            result = lint([html, planted])
            if result.returncode == 0 or "no-dash-characters" not in result.stdout:
                failures.append(f"lint-web passed a planted U+{ord(dash):04X}")

    source = (ROOT / "static/index.html").read_text(encoding="utf-8")
    # Every driveTip string has to come from the translation table.
    for line in source.splitlines():
        if re.search(r'\$\("driveTip"\)\.textContent\s*=\s*"', line):
            failures.append("driveTip set from a hardcoded English string: " + line.strip())

    if failures:
        print("FAIL lint-web dashes and Drive tip")
        for failure in failures:
            print("  " + failure)
        sys.exit(1)
    print("ok   lint-web dashes and Drive tip")


if __name__ == "__main__":
    main()
