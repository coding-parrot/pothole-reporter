# -*- coding: utf-8 -*-
"""Settings must say what debug mode costs, not re-save hidden state, and fail in the
tester's language.

Debug mode silently stopped repair verification (debug captures are never a repair
target and never check one), while its note only talked about disk space. The hidden
background-Drive box read a legacy "1" back on every open and wrote it straight back
on Save, so an old opt-in could never be cleared. The Save error was English only.
"""

import pathlib
import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    failures = []
    source = (ROOT / "static/index.html").read_text(encoding="utf-8")
    # Dead code the Settings screen no longer reaches: a keys list nothing reads, and a
    # welcome hint shown only on a first run that needsInitialSettings() never reports.
    for dead in ("LEGACY_SETTINGS_KEYS", "settingsHint", "welcome_hint"):
        if dead in source:
            failures.append(f"static/index.html still carries dead settings code: {dead}")

    with sync_playwright() as playwright:
        browser, page, errors = open_flow(
            playwright, storage={"native_background_drive": "1"})
        try:
            note = page.evaluate("() => t('debug_note')")
            if "repair" not in note.lower():
                failures.append(f"debug_note does not mention repair verification: {note!r}")

            page.click("#gearBtn")
            page.wait_for_selector("#setSave", state="visible")
            page.click("#setSave")
            page.wait_for_function("() => localStorage.getItem('initial_setup_complete') === '1'"
                                   " && !document.getElementById('setSave').disabled")
            stored = page.evaluate("() => localStorage.getItem('native_background_drive')")
            if stored != "0":
                failures.append("Save wrote the hidden background-Drive box back as "
                                f"{stored!r}; expected '0'")

            # A save that fails with no message must still speak the chosen language.
            page.evaluate("""() => {
              localStorage.setItem('app_lang', 'kn');
            }""")
            page.reload()
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            expected = page.evaluate("() => t('settings_apply_failed')")
            if not expected or expected == "settings_apply_failed":
                failures.append("settings_apply_failed has no translation")
            page.evaluate("""() => {
              const original = Storage.prototype.setItem;
              Storage.prototype.setItem = function (key, value) {
                if (key === 'vision_provider') throw new Error('');
                return original.call(this, key, value);
              };
            }""")
            messages = []
            page.on("dialog", lambda dialog: (messages.append(dialog.message), dialog.accept()))
            page.click("#gearBtn")
            page.click("#setSave")
            page.wait_for_function("() => !document.getElementById('setSave').disabled")
            page.wait_for_timeout(200)
            if not messages or messages[0].strip() != (expected or "").strip():
                failures.append(f"Save error in Kannada showed {messages!r}, expected {expected!r}")
            failures += error_failures(errors, "settings")
        finally:
            browser.close()

    if failures:
        print("FAIL settings hygiene")
        for failure in failures:
            print("  " + failure)
        sys.exit(1)
    print("ok   settings hygiene")


if __name__ == "__main__":
    main()
