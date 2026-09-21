# -*- coding: utf-8 -*-
"""The feedback nudge counts real reports and speaks the tester's language.

It used to count every stored row, so three rejected frames from one drive (or a few
leftover junk rows) told a tester "You have made a few reports". And its three strings
were literal English markup that applyLang never touched.
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flow_harness  # noqa: E402

SEED = r"""
async (statuses) => {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes", 8);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    const store = tx.objectStore("reports");
    store.clear();
    const now = Math.floor(Date.now() / 1000);
    statuses.forEach((status, index) => store.add({
      status, created_at: now - index, lat: 12.97, lng: 77.59,
      damage_type: "pothole_cavity", size: "medium",
    }));
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}
"""

NUDGE_STATE = """() => ({
  shown: !document.getElementById("feedbackNudge").classList.contains("hidden"),
  text: document.getElementById("feedbackNudgeText").textContent,
  dismiss: document.getElementById("feedbackNudgeDismiss").textContent,
  open: document.getElementById("feedbackNudgeOpen").textContent,
})"""


def boot(page):
    page.goto(flow_harness.APP)
    page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
    page.wait_for_function("() => Array.isArray(loadReports.latest)", timeout=10_000)
    page.wait_for_timeout(300)


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for lang in ("en", "kn"):
                context = browser.new_context(viewport={"width": 412, "height": 915},
                                              is_mobile=True, device_scale_factor=2.625)
                context.add_init_script(script="(() => {" + "\n".join([
                    f'localStorage.setItem("service_url", {json.dumps(flow_harness.SERVICE)});',
                    f'localStorage.setItem("data_notice_version", {json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
                    'localStorage.setItem("initial_setup_complete", "1");',
                    'localStorage.setItem("vision_provider", "shared");',
                    f'localStorage.setItem("app_lang", {json.dumps(lang)});',
                ]) + "})();")
                context.route(f"{flow_harness.SERVICE}/**", flow_harness.central_service)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
                boot(page)

                page.evaluate(SEED, ["rejected", "rejected", "rejected", "review"])
                boot(page)
                if page.evaluate(NUDGE_STATE)["shown"]:
                    failures.append(f"{lang}: nudge shown for rejected and review frames only")

                page.evaluate(SEED, ["draft", "queued", "unrouted"])
                boot(page)
                state = page.evaluate(NUDGE_STATE)
                if not state["shown"]:
                    failures.append(f"{lang}: nudge hidden after three real reports")
                want = page.evaluate("""(lang) => [I18N[lang].nudge_text,
                  I18N[lang].nudge_dismiss, I18N[lang].nudge_open]""", lang)
                got = [state["text"], state["dismiss"], state["open"]]
                if not all(want) or got != want:
                    failures.append(f"{lang}: nudge copy is not the {lang} table: {got} vs {want}")
                if lang != "en":
                    english = page.evaluate("() => I18N.en.nudge_text")
                    if state["text"] == english:
                        failures.append(f"{lang}: nudge text is English")
                failures += [f"{lang}: {error}" for error in errors]
                context.close()
        finally:
            browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS feedback nudge counts real reports and is localized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
