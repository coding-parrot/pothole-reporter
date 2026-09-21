# -*- coding: utf-8 -*-
"""A denied camera permission says, in the tester's language, where to turn it back on.

Both camera entry points once alerted t("camera_failed") followed by the platform's own
message, so a Kannada tester who had refused the permission read
"ಕ್ಯಾಮೆರಾ ಪ್ರವೇಶ ವಿಫಲ: Permission denied": English, and no next step. Drive (the
WebView getUserMedia path) and Photo (the native Camera plugin) are both checked with the
permission refused and the app in Kannada.
"""

import re
import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

DENY_GET_USER_MEDIA = """
navigator.mediaDevices.getUserMedia = async () => {
  throw new DOMException("Permission denied", "NotAllowedError");
};
"""
# Registered after the native stub, so it adds getPhoto to the stub's Camera.
DENY_NATIVE_PHOTO = """
(() => {
  const camera = window.Capacitor && window.Capacitor.Plugins.Camera;
  if (camera) camera.getPhoto = async () => {
    const error = new Error("User denied access to camera");
    error.code = "OS-PLUG-CAMR-0003";
    throw error;
  };
})();
"""


def latin_words(text):
    """Latin-script words left once the app's own name is removed."""
    return re.findall(r"[A-Za-z]{2,}", text.replace("Pothole Reporter", ""))


def alert_after(page, button):
    messages = []
    page.on("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
    page.locator(button).click()
    for _ in range(100):
        if messages:
            break
        page.wait_for_timeout(100)
    return messages


fails = []
with sync_playwright() as playwright:
    for where, button, native, extra in (
        ("Drive", "#driveBtn", False, DENY_GET_USER_MEDIA),
        ("Photo", "#captureBtn", True, DENY_NATIVE_PHOTO),
    ):
        browser, page, errors = open_flow(
            playwright, native=native, storage={"app_lang": "kn"})
        try:
            page.context.add_init_script(script=extra)
            page.reload()
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            page.locator("#home").wait_for(state="visible", timeout=30_000)
            messages = alert_after(page, button)
            if not messages:
                fails.append(f"{where}: no alert after the camera permission was refused")
            else:
                text = messages[0]
                if "ಅನುಮತಿ" not in text:
                    fails.append(f"{where}: alert does not say the permission is off: {text!r}")
                if latin_words(text):
                    fails.append(f"{where}: Kannada alert carries English {latin_words(text)}: {text!r}")
            fails += error_failures(errors, where)
        finally:
            browser.close()

if fails:
    print("FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("CAMERA DENIED TEST PASS")
