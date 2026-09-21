# -*- coding: utf-8 -*-
"""On the phone, Drive stops at a refused camera permission and offers App info.

requestNativeCameraPermission threw its answer away, so a refused permission went on to
getUserMedia anyway. The WebView then failed with its own English error, and after two
refusals Android stops asking, so a tester had no path back. Drive now reads the answer,
never opens the camera without it, and offers the app's settings page in the tester's
language.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

# Registered after the native stub, so it overrides the stub's granted Camera.
DENY_NATIVE_CAMERA = """
(() => {
  window.__getUserMediaCalls = 0;
  window.__settingsCalls = 0;
  const media = navigator.mediaDevices;
  const real = media.getUserMedia.bind(media);
  media.getUserMedia = (constraints) => { window.__getUserMediaCalls++; return real(constraints); };
  const plugins = window.Capacitor && window.Capacitor.Plugins;
  if (!plugins) return;
  plugins.Camera.checkPermissions = async () => ({ camera: "denied", photos: "denied" });
  plugins.Camera.requestPermissions = async () => ({ camera: "denied", photos: "denied" });
  plugins.DriveMode.openAppSettings = async () => { window.__settingsCalls++; return { opened: true }; };
})();
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=True, storage={"app_lang": "kn"})
    try:
        page.context.add_init_script(script=DENY_NATIVE_CAMERA)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        messages = []
        page.on("dialog", lambda dialog: (messages.append(dialog.message), dialog.accept()))
        page.locator("#driveBtn").click()
        for _ in range(100):
            if messages:
                break
            page.wait_for_timeout(100)
        page.wait_for_timeout(800)
        state = page.evaluate("""() => ({ gum: window.__getUserMediaCalls,
          settings: window.__settingsCalls, drive: !!drive, starting: driveStarting,
          home: !document.getElementById('home').classList.contains('hidden') })""")
        if state["gum"]:
            fails.append(f"the camera was opened after the permission was refused: {state}")
        if not messages or "ಅನುಮತಿ" not in messages[0]:
            fails.append(f"no Kannada permission message: {messages}")
        if not state["settings"]:
            fails.append("accepting the message did not open the app's settings page")
        if state["drive"] or state["starting"] or not state["home"]:
            fails.append(f"Drive did not return cleanly to Home: {state}")
        fails += error_failures(errors, "native camera refused")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive camera permission denied")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive camera permission denied")
