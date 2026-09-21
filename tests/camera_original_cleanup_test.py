# -*- coding: utf-8 -*-
"""A Photo's full-size original leaves the phone's Pictures folder once it is read.

Camera.getPhoto with resultType uri leaves the camera's full-size JPEG in the app's
files/Pictures folder. The app copies the bytes into its own store and never touched
the original again, so every photo a tester took stayed on disk twice until Delete all.
Both the live Photo path and a photo restored after Android killed the app now delete
the original once its bytes are read, and a file that is already gone is not an error.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import APP, error_failures, open_flow

ORIGINAL = "file:///data/user/0/dev.aiengg.potholereporter/files/Pictures/shot-1.jpg"
RESTORED = "file:///data/user/0/dev.aiengg.potholereporter/files/Pictures/shot-2.jpg"

# Registered after the native stub: a camera that returns the example photo, and a
# Filesystem that records deletions and says the second original is already gone.
CAMERA = """
(() => {
  window.__deleted = [];
  const plugins = window.Capacitor && window.Capacitor.Plugins;
  if (!plugins) return;
  plugins.Camera.getPhoto = async () => ({
    webPath: "%(app)sexample-pothole.jpg", path: "%(original)s", format: "jpeg", saved: false });
  plugins.Filesystem = {
    async deleteFile(options) {
      window.__deleted.push(options.path);
      if (options.path === "%(restored)s") throw new Error("File does not exist");
      return {};
    },
  };
})();
""" % {"app": APP, "original": ORIGINAL, "restored": RESTORED}

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=True)
    try:
        page.context.add_init_script(script=CAMERA)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        alerts = []
        page.on("dialog", lambda dialog: (
            alerts.append(dialog.message) if dialog.type == "alert" else None, dialog.accept()))
        page.locator("#captureBtn").click()
        page.locator("#detail").wait_for(state="visible", timeout=40_000)
        deleted = page.evaluate("window.__deleted")
        if deleted != [ORIGINAL]:
            fails.append(f"the live Photo original was not deleted once: {deleted}")

        page.evaluate("() => show('home')")
        page.evaluate("""(args) => window.__fireNative("appRestoredResult", {
          pluginId: "Camera", methodName: "getPhoto", success: true,
          data: { webPath: args[0], path: args[1], format: "jpeg", saved: false } })""",
                      [APP + "example-pothole.jpg", RESTORED])
        page.locator("#detail").wait_for(state="visible", timeout=40_000)
        deleted = page.evaluate("window.__deleted")
        if deleted != [ORIGINAL, RESTORED]:
            fails.append(f"the restored photo's original was not deleted: {deleted}")
        if alerts:
            fails.append(f"an original that was already gone raised {alerts}")
        fails += error_failures(errors, "camera original cleanup")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL camera original cleanup")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS camera original cleanup")
