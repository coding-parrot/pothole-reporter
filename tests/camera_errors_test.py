# -*- coding: utf-8 -*-
"""A camera that is busy or refused says what to do, in the tester's language.

With another app holding the camera, Drive alerted "Camera access failed: Could not
start video source", the platform's English text, in every language. A refused
permission already said where to fix it but offered no way there, although Android
stops asking after two refusals. Now a busy camera says to close the other app, and on
the phone a refusal offers to open this app's settings page. Only the refusal offers it.
"""

import re
import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


def reject_get_user_media(name, message):
    return f"""
navigator.mediaDevices.getUserMedia = async () => {{
  throw new DOMException({message!r}, {name!r});
}};
"""


DENY_NATIVE_PHOTO = """
(() => {
  const plugins = window.Capacitor && window.Capacitor.Plugins;
  if (!plugins) return;
  window.__settingsOpened = 0;
  plugins.DriveMode.openAppSettings = async () => { window.__settingsOpened += 1; return { opened: true }; };
  plugins.Camera.getPhoto = async () => {
    const error = new Error("User denied access to camera");
    error.code = "OS-PLUG-CAMR-0003";
    throw error;
  };
})();
"""


def latin_words(text):
    return re.findall(r"[A-Za-z]{2,}", text.replace("Pothole Reporter", ""))


def dialogs_after(page, button, accept):
    seen = []
    page.on("dialog", lambda dialog: (seen.append((dialog.type, dialog.message)),
                                      dialog.accept() if accept else dialog.dismiss()))
    page.locator(button).click()
    for _ in range(100):
        if seen:
            break
        page.wait_for_timeout(100)
    page.wait_for_timeout(300)
    return seen


def opened(lang, native, extra):
    browser, page, errors = open_flow(playwright, native=native, storage={"app_lang": lang})
    page.context.add_init_script(script=extra)
    page.reload()
    page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    return browser, page, errors


fails = []
with sync_playwright() as playwright:
    # Busy camera, Kannada: a translated message, no platform text, no settings offer.
    browser, page, errors = opened(lang="kn", native=False, extra=reject_get_user_media(
        "NotReadableError", "Could not start video source"))
    try:
        seen = dialogs_after(page, "#driveBtn", accept=False)
        busy = page.evaluate("t('camera_busy')")
        if not seen:
            fails.append("busy camera: no message at all")
        else:
            kind, text = seen[0]
            if text != busy:
                fails.append(f"busy camera: expected {busy!r}, got {text!r}")
            if latin_words(text):
                fails.append(f"busy camera: Kannada message carries English {latin_words(text)}")
            if kind != "alert":
                fails.append(f"busy camera offered a {kind}; only a refusal has a settings page to open")
        fails += error_failures(errors, "busy camera")
    finally:
        browser.close()

    # Refused on the phone: the message and an offer to open this app's settings.
    browser, page, errors = opened(lang="en", native=True, extra=DENY_NATIVE_PHOTO)
    try:
        seen = dialogs_after(page, "#captureBtn", accept=True)
        denied = page.evaluate("t('camera_denied')")
        if not seen or seen[0][0] != "confirm" or denied not in seen[0][1]:
            fails.append(f"refused camera on the phone did not offer the settings page: {seen}")
        elif page.evaluate("window.__settingsOpened") != 1:
            fails.append("accepting the offer did not open the app's settings page")
        fails += error_failures(errors, "refused camera")
    finally:
        browser.close()

if fails:
    print("FAIL camera errors")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS camera errors")
