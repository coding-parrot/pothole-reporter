# -*- coding: utf-8 -*-
"""Camera/location permissions must stay behind the versioned data disclosure."""
import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

INIT_NATIVE_PROBE = r"""
(() => {
  // Keep the test on Home instead of the unrelated first-run API-key Settings screen.
  // No request is made: prewarm and the capture picker are replaced below.
  localStorage.setItem("vision_provider", "personal");
  localStorage.setItem("openai_key", "test-key-never-sent");
  const probe = window.__privacyProbe = {
    events: [],
    pending: {camera: [], location: []},
    fileClicks: 0,
  };
  const permission = (kind, args) => {
    probe.events.push({kind, args});
    return new Promise((resolve) => probe.pending[kind].push(resolve));
  };
  Object.defineProperty(window, "Capacitor", {configurable: true, writable: true, value: {
    isNativePlatform: () => true,
    Plugins: {
      Camera: {requestPermissions: (args) => permission("camera", args)},
      Geolocation: {requestPermissions: (args) => permission("location", args)},
    },
  }});
  Object.defineProperty(navigator, "mediaDevices", {configurable: true, value: {
    getUserMedia: async (constraints) => {
      probe.events.push({kind: "media", args: constraints});
      throw new Error("test camera stops after the consent gate");
    },
  }});
})();
"""


def open_native_page(browser, viewport=None, lang=None):
    context = browser.new_context(viewport=viewport or {"width": 390, "height": 844})
    context.add_init_script(INIT_NATIVE_PROBE)
    if lang:
        context.add_init_script(f"localStorage.setItem('app_lang', {lang!r});")
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof StandaloneAPI !== 'undefined' && typeof ensureDataConsent === 'function'"
    )
    page.evaluate(
        """() => {
          window.alert = () => {};
          document.getElementById("fileInput").click = () => {
            window.__privacyProbe.fileClicks++;
            window.__privacyProbe.events.push({kind: "file"});
          };
          StandaloneAPI.prewarm = () => window.__privacyProbe.events.push({kind: "prewarm"});
        }"""
    )
    return context, page


def snapshot(page):
    return page.evaluate(
        """() => ({
          events: window.__privacyProbe.events.map((event) => event.kind),
          fileClicks: window.__privacyProbe.fileClicks,
          consentVisible: !document.getElementById("dataConsent").classList.contains("hidden"),
          homeVisible: !document.getElementById("home").classList.contains("hidden"),
          version: localStorage.getItem("data_notice_version"),
          currentVersion: DATA_NOTICE_VERSION,
          driveStarting,
          drivePresent: !!drive,
          disclosure: ["privacyBody", "privacyLocal", "privacyGovernment"]
            .map((id) => document.getElementById(id).textContent).join(" "),
          privacyHref: document.getElementById("consentPrivacyLink").href,
        })"""
    )


def wait_for_event(page, kind, count=1):
    page.wait_for_function(
        """({kind, count}) => window.__privacyProbe.events
          .filter((event) => event.kind === kind).length >= count""",
        arg={"kind": kind, "count": count},
    )


def release_permission(page, kind):
    released = page.evaluate(
        """kind => {
          const resolve = window.__privacyProbe.pending[kind].shift();
          if (!resolve) return false;
          resolve({state: "granted"});
          return true;
        }""",
        kind,
    )
    if not released:
        raise AssertionError(f"no pending {kind} permission request")


failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])

    # Capture lifecycle: no startup prompt; decline is non-persistent; accepting stores
    # the current notice version before asking Android; the accepted version skips UI.
    context, page = open_native_page(browser)
    initial = snapshot(page)
    if initial["events"]:
        failures.append(f"capture: native permission/API calls happened on startup: {initial['events']}")
    if initial["version"] is not None:
        failures.append("capture: consent was persisted without an affirmative action")

    page.locator("#captureBtn").click()
    page.locator("#dataConsent").wait_for(state="visible")
    disclosed = snapshot(page)
    if disclosed["events"] or disclosed["fileClicks"]:
        failures.append(f"capture: work started before disclosure acceptance: {disclosed}")
    disclosure = disclosed["disclosure"].lower()
    if not all(term in disclosure for term in (
        "camera", "location", "background", "not visible", "recording",
        "openai", "government", "github pages", "state", "ip"
    )):
        failures.append("capture: visible disclosure omits a core data-use/government fact")
    if not disclosed["privacyHref"].startswith("https://"):
        failures.append("capture: visible disclosure has no HTTPS privacy-policy link")

    page.locator("#privacyDecline").click()
    page.locator("#dataConsent").wait_for(state="hidden")
    declined = snapshot(page)
    if declined["version"] is not None or declined["events"] or not declined["homeVisible"]:
        failures.append(f"capture: Decline did not return cleanly without persistence: {declined}")

    page.locator("#captureBtn").click()
    page.locator("#dataConsent").wait_for(state="visible")
    page.locator("#privacyAccept").click()
    wait_for_event(page, "camera")
    camera_held = snapshot(page)
    if camera_held["version"] != camera_held["currentVersion"]:
        failures.append(f"capture: acceptance did not store the current notice version: {camera_held}")
    if camera_held["events"] != ["camera"] or camera_held["fileClicks"]:
        failures.append(f"capture: permission/file ordering is wrong before camera resolves: {camera_held}")

    # Manual capture needs camera permission to open the picker. Location is requested
    # only after a real photo comes back, so it cannot add seconds to camera startup or
    # ask for precise location when the user cancels the picker.
    release_permission(page, "camera")
    wait_for_event(page, "file")
    accepted = snapshot(page)
    if accepted["events"] != ["camera", "prewarm", "file"]:
        failures.append(f"capture: work after camera permission is in the wrong order: {accepted}")
    if accepted["fileClicks"] != 1 or accepted["consentVisible"]:
        failures.append(f"capture: accepted action did not resume exactly once: {accepted}")

    # The accepted version skips the disclosure but still goes through Android's idempotent
    # permission checks before opening the file picker. Photo is a one-tap pothole flow;
    # there is no issue/category screen between the button and the camera.
    page.locator("#captureBtn").click()
    wait_for_event(page, "camera", 2)
    repeated = snapshot(page)
    if repeated["consentVisible"]:
        failures.append("capture: current accepted notice was shown again")
    release_permission(page, "camera")
    wait_for_event(page, "file", 2)
    repeated_done = snapshot(page)
    if "location" in repeated_done["events"]:
        failures.append(f"capture: location was requested before a photo existed: {repeated_done}")

    # A stale notice version must disclose again. Android Back is a decline: it must not
    # upgrade the stored version or continue the interrupted capture action.
    page.evaluate("localStorage.setItem('data_notice_version', 'obsolete-notice')")
    page.locator("#captureBtn").click()
    page.locator("#dataConsent").wait_for(state="visible")
    before_back = snapshot(page)
    handled = page.evaluate("handleAppBack()")
    page.locator("#dataConsent").wait_for(state="hidden")
    after_back = snapshot(page)
    if not handled or after_back["version"] != "obsolete-notice":
        failures.append(f"capture: Back did not behave like a non-persistent decline: {after_back}")
    if after_back["events"] != before_back["events"] or after_back["fileClicks"] != 2:
        failures.append(f"capture: stale-notice action continued through Back: {after_back}")
    context.close()

    # Drive Mode has its own entry point and must use the same gate before native
    # permissions and getUserMedia. Declining must also release its startup guard.
    context, page = open_native_page(browser)
    page.locator("#driveBtn").click()
    page.locator("#dataConsent").wait_for(state="visible")
    drive_disclosed = snapshot(page)
    if drive_disclosed["events"]:
        failures.append(f"drive: permission/camera work began before acceptance: {drive_disclosed}")
    page.locator("#privacyDecline").click()
    page.wait_for_function("driveStarting === false")
    drive_declined = snapshot(page)
    if drive_declined["version"] is not None or drive_declined["drivePresent"]:
        failures.append(f"drive: Decline persisted or left Drive state behind: {drive_declined}")

    page.locator("#driveBtn").click()
    page.locator("#privacyAccept").click()
    wait_for_event(page, "camera")
    release_permission(page, "camera")
    wait_for_event(page, "media")
    page.wait_for_function("driveStarting === false")
    drive_accepted = snapshot(page)
    expected_prefix = ["camera", "media"]
    capture_order = [event for event in drive_accepted["events"] if event in expected_prefix]
    if (capture_order != expected_prefix or "location" in drive_accepted["events"]
            or drive_accepted["drivePresent"]):
        failures.append(f"drive: accepted action did not preserve permission/camera order: {drive_accepted}")
    context.close()

    # Leaving the notice through the Settings gear is a decline. It once left
    # driveStarting set and the consent waiter pending, so every later Drive tap was
    # rejected by the startup guard until the app restarted.
    context, page = open_native_page(browser)
    page.locator("#driveBtn").click()
    page.locator("#dataConsent").wait_for(state="visible")
    page.locator("#gearBtn").click()
    page.locator("#settings").wait_for(state="visible")
    page.locator("#setBack").click()
    page.locator("#home").wait_for(state="visible")
    escaped = page.evaluate("({driveStarting, waiting: !!consentWaiter})")
    if escaped["driveStarting"] or escaped["waiting"]:
        failures.append(f"drive: leaving the notice through Settings left Drive guarded: {escaped}")
    page.locator("#driveBtn").click()
    try:
        page.locator("#dataConsent").wait_for(state="visible", timeout=3000)
    except Exception:
        failures.append("drive: a Drive tap after the Settings escape did not show the notice again")
    if snapshot(page)["version"] is not None:
        failures.append("drive: the Settings escape persisted consent")
    context.close()

    # The only two actions on the notice stay on screen on a small phone, in every
    # language, without scrolling past three long paragraphs first.
    stale_flows = ("Nominatim", "handoff", "Telangana GIS")
    DISCLOSURE = {
        "en": (("installation ID", "image hash", "queued"), stale_flows),
        "kn": (("ಸ್ಥಾಪನೆ ID", "ಹ್ಯಾಶ್", "ಬಾಕಿ"), stale_flows),
        "mr": (("इन्स्टॉलेशन ID", "hash", "प्रलंबित"), stale_flows),
        "bn": (("ইনস্টলেশন ID", "hash", "অপেক্ষমাণ"), stale_flows),
    }
    for lang in ("en", "kn", "mr", "bn"):
        context, page = open_native_page(browser, {"width": 360, "height": 780}, lang)
        page.locator("#driveBtn").click()
        page.locator("#dataConsent").wait_for(state="visible")
        fit = page.evaluate("""() => {
          const rects = ["privacyAccept", "privacyDecline"]
            .map((id) => document.getElementById(id).getBoundingClientRect());
          return {bottoms: rects.map((rect) => Math.round(rect.bottom)),
                  tops: rects.map((rect) => Math.round(rect.top)),
                  height: window.innerHeight};
        }""")
        if max(fit["bottoms"]) > fit["height"] or min(fit["tops"]) < 0:
            failures.append(f"layout {lang}: notice buttons are off screen at 360x780: {fit}")
        # The rendered notice, not just the dictionary, names what reaches the project
        # service and that a failed upload waits on the phone. Sentence-stripping once
        # dropped exactly that from English and Kannada, while Marathi and Bengali
        # still described Nominatim lookups and complaint handoffs the app no longer has.
        disclosure = snapshot(page)["disclosure"]
        wanted, stale = DISCLOSURE[lang]
        missing = [term for term in wanted if term.lower() not in disclosure.lower()]
        if missing:
            failures.append(f"disclosure {lang}: rendered notice omits {missing}")
        present = [term for term in stale if term in disclosure]
        if present:
            failures.append(f"disclosure {lang}: rendered notice describes removed flows {present}")
        if "\u2014" in disclosure or "\u2013" in disclosure:
            failures.append(f"disclosure {lang}: rendered notice contains a dash character")
        page.locator("#privacyDecline").click()
        page.wait_for_function("driveStarting === false")
        context.close()

    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)

print("PRIVACY CONSENT TEST PASS")
