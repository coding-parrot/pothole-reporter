# -*- coding: utf-8 -*-
"""Drive summaries show small footage sizes and single frames the way a person would.

A short drive's clips are one or two MB, and whole-number rounding told testers
"Video deleted, 0 MB freed." and "3 clips, 2 MB" for 2.1 MB. One failed frame read
"1 frames could not be checked". Sizes under 10 MB now keep one decimal, and the
failed-frame note has a singular form in every language.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

MB = 1048576
fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        sizes = page.evaluate(f"""() => [0.3, 2.1, 9.96, 25.4, 0].map((mb) =>
          typeof mbText === 'function' ? mbText(mb * {MB}) : null)""")
        if sizes != ["0.3", "2.1", "10", "25", "0"]:
            fails.append(f"MB sizes format as {sizes}, want ['0.3', '2.1', '10', '25', '0']")
        freed = page.evaluate(f"t('footage_freed', {{ mb: mbText(0.4 * {MB}) }})")
        if "0.4 MB" not in freed:
            fails.append(f"freed copy: {freed!r}")
        for lang in ("en", "kn", "mr", "bn"):
            one, many = page.evaluate(f"""() => {{
              localStorage.setItem('app_lang', '{lang}');
              return [I18N['{lang}'].drive_end_failed_one, I18N['{lang}'].drive_end_failed_many];
            }}""")
            if not one or not many or "{n}" not in one or "{n}" not in many:
                fails.append(f"{lang}: failed-frame note lacks singular/plural forms: {one!r} {many!r}")
        english_one = page.evaluate("I18N.en.drive_end_failed_one.replace('{n}', 1)")
        if "1 frames" in english_one or "1 frame " not in english_one:
            fails.append(f"English singular reads {english_one!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive summary copy")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive summary copy")
