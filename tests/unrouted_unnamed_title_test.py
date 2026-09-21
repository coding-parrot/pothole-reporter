# -*- coding: utf-8 -*-
"""An unrouted title never starts with an empty subject.

The central resolver returns highway_name null for some state highways (Hubballi), and
KGIS NH features on ORR have no Name, so '{road} is a state highway' rendered as
' is a state highway' under a warning icon. A body without a name did the same to
'No published address for {body}.'
"""
import os
import sys

from playwright.sync_api import sync_playwright

APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

JS = r"""
(langs) => {
  const out = {};
  const saved = LANG;
  for (const lang of langs) {
    LANG = lang;
    out[lang] = {};
    for (const reason of ["national_highway", "state_highway", "district_highway",
                          "no_address_for_body"]) {
      out[lang][reason] = {
        unnamed: unroutedTitle({ unrouted_reason: reason, unrouted_body: null }),
        blank: unroutedTitle({ unrouted_reason: reason, unrouted_body: "  " }),
        named: unroutedTitle({ unrouted_reason: reason, unrouted_body: "NH 48" }),
      };
    }
  }
  LANG = saved;
  return out;
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_context(viewport={"width": 412, "height": 915}).new_page()
            page.goto(APP)
            page.wait_for_function("typeof unroutedTitle === 'function'", timeout=30000)
            result = page.evaluate(JS, ["en", "kn", "mr", "bn"])
        finally:
            browser.close()

    en = result["en"]
    if en["state_highway"]["unnamed"] != "This road is a state highway":
        failures.append(f"en unnamed state highway reads {en['state_highway']['unnamed']!r}")
    if en["national_highway"]["unnamed"] != "This road is a national highway":
        failures.append(f"en unnamed national highway reads {en['national_highway']['unnamed']!r}")
    if en["national_highway"]["named"] != "NH 48 is a national highway":
        failures.append("a named highway lost its name")
    for lang, reasons in result.items():
        for reason, titles in reasons.items():
            for kind in ("unnamed", "blank"):
                title = titles[kind]
                print(f"  {lang} {reason} {kind}: {title}")
                if not title or title[0].isspace() or "  " in title or " ." in title:
                    failures.append(f"{lang} {reason} {kind} has an empty subject: {title!r}")
                if title == titles["named"].replace("NH 48", ""):
                    failures.append(f"{lang} {reason} {kind} just drops the name: {title!r}")

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("UNROUTED UNNAMED TITLE TEST PASS")


if __name__ == "__main__":
    main()
