# -*- coding: utf-8 -*-
"""A photo the phone cannot decode says what to do, in the tester's language.

The picker accepts any image, and Android galleries offer HEIF photos that the WebView
cannot always decode. Such a file ended with the platform's own English text ("The
source image could not be decoded.") and no hint that a JPEG or the app camera works.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

# A HEIF header (ftypheic) followed by bytes no decoder accepts.
IMPORT_HEIC = """async () => {
  const bytes = new Uint8Array(4096);
  bytes.set([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x68, 0x65, 0x69, 0x63]);
  const file = new File([bytes], "IMG_0001.heic", { type: "image/heic" });
  await handleFile(file, { locationConfirmed: false });
}"""

fails = []
with sync_playwright() as playwright:
    for lang in ("en", "kn"):
        browser, page, errors = open_flow(playwright, storage={"app_lang": lang})
        dialogs = []

        def on_dialog(dialog, dialogs=dialogs):
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", on_dialog)
        try:
            page.locator("#home").wait_for(state="visible", timeout=30_000)
            detects = []
            page.on("request", lambda request, detects=detects: detects.append(request.url)
                    if "/v1/vision/detect" in request.url else None)
            page.evaluate(IMPORT_HEIC)
            page.wait_for_timeout(500)
            expected = page.evaluate("() => t('photo_unreadable')")
            if expected == "photo_unreadable":
                fails.append(f"{lang}: no photo_unreadable string")
            if dialogs != [expected]:
                fails.append(f"{lang}: alerts were {dialogs!r}, expected [{expected!r}]")
            if any("decoded" in message for message in dialogs):
                fails.append(f"{lang}: the platform's decoder text reached the tester")
            if not page.locator("#home").is_visible():
                fails.append(f"{lang}: an unreadable photo did not return Home")
            if detects:
                fails.append(f"{lang}: an unreadable photo still called the detector")
            fails += error_failures(errors, f"undecodable photo ({lang})")
        except Exception as error:
            fails.append(f"{lang}: flow broke: {str(error)[:300]}")
        finally:
            browser.close()

if fails:
    print("FAIL photo undecodable")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS photo undecodable")
