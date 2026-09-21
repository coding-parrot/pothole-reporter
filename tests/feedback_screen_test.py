# -*- coding: utf-8 -*-
"""The feedback screen speaks the tester's language, is honest about what it keeps,
and never leaves the tester waiting or guessing."""

import json
import sys

from playwright.sync_api import sync_playwright

from browser_test_utils import _central_service, open_app


SERVICE = "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com"
ENGLISH = ("Send feedback", "Tell us what worked", "How is the app so far?",
           "How are you testing?", "What happened?", "Your email", "On foot",
           "What broke, what confused you")

failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True,
                                  device_scale_factor=2.625)
    page = context.new_page()

    feedback_requests = []
    service_up = {"value": True}

    def central(route, request):
        if request.url.startswith(f"{SERVICE}/v1/feedback"):
            feedback_requests.append(json.loads(request.post_data or "{}"))
            if service_up["value"]:
                route.fulfill(status=201, headers={"content-type": "application/json"},
                              body=json.dumps({"accepted": True, "created_at": 1}))
            else:
                route.fulfill(status=503, headers={"content-type": "application/json"},
                              body=json.dumps({"error": "service_unavailable", "message": "down"}))
            return
        _central_service(route, request)

    open_app(page, "test-key-never-sent")
    page.unroute(f"{SERVICE}/**")
    page.route(f"{SERVICE}/**", central)

    # Every label on the screen, and the Settings button that opens it, follows the
    # chosen language. Only the status line used to.
    for lang in ("kn", "mr", "bn"):
        page.evaluate("(lang) => localStorage.setItem('app_lang', lang)", lang)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.evaluate("openSettings()")
        button = page.locator("#feedbackBtn").inner_text()
        page.evaluate("openFeedback('settings')")
        screen = page.evaluate("""() => {
          const root = document.getElementById("feedback");
          return root.innerText + "\\n" + document.getElementById("feedbackText").placeholder
            + "\\n" + [...root.querySelectorAll("option")].map((o) => o.textContent).join("\\n");
        }""")
        english = [text for text in ENGLISH if text in screen or text in button]
        if english:
            failures.append(f"{lang}: feedback screen still shows English {english}")
        for control, key in (("feedbackBack", "back"), ("feedbackSend", "feedback_send")):
            if page.evaluate(f"document.getElementById('{control}').textContent === t('{key}')") is False:
                failures.append(f"{lang}: {control} is not the {key} string")
    page.evaluate("localStorage.removeItem('app_lang')")
    page.reload()
    page.wait_for_load_state("networkidle")

    # Each star says which rating it is, and the group says what it rates.
    stars = page.evaluate("""() => ({
      labels: [...document.querySelectorAll("#feedbackStars button")]
        .map((b) => b.getAttribute("aria-label")),
      group: document.getElementById("feedbackStars").getAttribute("aria-labelledby"),
    })""")
    if len(set(filter(None, stars["labels"]))) != 5 or not all("5" in (x or "") for x in stars["labels"]):
        failures.append(f"star buttons have no distinct accessible names: {stars['labels']}")
    if stars["group"] != "feedbackRatingLabel":
        failures.append(f"star group is not labelled by its question: {stars['group']}")

    # Past the 2000-character limit the tester sees the count and is told the rest
    # was cut, instead of the tail silently disappearing.
    page.evaluate("openFeedback('settings')")
    page.locator("#feedbackText").focus()
    page.keyboard.insert_text("x" * 2100)
    long_text = page.evaluate("""() => ({
      length: document.getElementById("feedbackText").value.length,
      counter: (document.getElementById("feedbackCount") || {}).textContent || null,
      status: document.getElementById("feedbackStatus").textContent,
      expected: t("feedback_too_long"),
    })""")
    if long_text["length"] != 2000:
        failures.append(f"feedback text was not held at 2000 characters: {long_text['length']}")
    if not long_text["counter"] or "2000 / 2000" not in long_text["counter"]:
        failures.append(f"no live character counter: {long_text['counter']!r}")
    if long_text["status"] != long_text["expected"]:
        failures.append(f"cut text was not announced: {long_text['status']!r}")
    page.locator("#feedbackText").fill("short")
    if "5 / 2000" not in (page.evaluate("(document.getElementById('feedbackCount') || {}).textContent") or ""):
        failures.append("counter does not follow the text")

    # A successful send from Settings answers the nudge: the card does not ask again.
    page.evaluate("localStorage.removeItem('feedback_nudged')")
    page.locator("#feedbackStars button[data-rating='4']").click()
    page.locator("#feedbackSend").click()
    page.wait_for_function("document.getElementById('feedbackStatus').textContent === t('feedback_sent')",
                           timeout=10_000)
    page.evaluate("show('home'); maybeNudgeFeedback(3)")
    if page.locator("#feedbackNudge").is_visible():
        failures.append("nudge asked again after feedback was already sent from Settings")

    # Hardware Back from the nudge-opened screen returns where the tester came from.
    page.evaluate("localStorage.removeItem('feedback_nudged'); show('home'); maybeNudgeFeedback(3)")
    page.locator("#feedbackNudgeOpen").click()
    page.locator("#feedback").wait_for(state="visible")
    page.evaluate("handleAppBack()")
    if not page.locator("#home").is_visible() or page.locator("#settings").is_visible():
        failures.append("hardware Back from the nudge-opened feedback screen did not return Home")

    # A stalled connection: the entry is already safe on the phone, so the tester is
    # told so within a few seconds rather than after the old 15 s timeout.
    page.evaluate("""() => {
      const realFetch = window.fetch;
      window.__restoreFetch = () => { window.fetch = realFetch; };
      window.fetch = (url, init) => String(url).includes("/v1/feedback")
        ? new Promise((resolve, reject) => {
            if (init && init.signal) init.signal.addEventListener("abort",
              () => reject(new DOMException("aborted", "AbortError")));
          })
        : realFetch(url, init);
    }""")
    page.evaluate("openFeedback('settings')")
    page.locator("#feedbackText").fill("Stalled connection")
    page.locator("#feedbackSend").click()
    try:
        page.wait_for_function("document.getElementById('feedbackStatus').textContent === t('feedback_queued')",
                               timeout=8_000)
    except Exception:
        failures.append("a stalled send was not reported as saved within 8 s: "
                        + page.locator("#feedbackStatus").inner_text())
    page.evaluate("window.__restoreFetch(); localStorage.removeItem('pending_feedback')")

    # Offline: no request is attempted and the tester hears at once that it is saved.
    before = len(feedback_requests)
    page.evaluate("openFeedback('settings')")
    page.locator("#feedbackText").fill("Offline note")
    context.set_offline(True)
    page.locator("#feedbackSend").click()
    try:
        page.wait_for_function("document.getElementById('feedbackStatus').textContent === t('feedback_queued')",
                               timeout=1_500)
    except Exception:
        failures.append("offline send did not report saved at once: "
                        + page.locator("#feedbackStatus").inner_text())
    context.set_offline(False)
    if len(feedback_requests) != before:
        failures.append("offline send still attempted a request")
    page.evaluate("window.dispatchEvent(new Event('online'))")
    try:
        page.wait_for_function("!localStorage.getItem('pending_feedback')", timeout=20_000)
    except Exception:
        failures.append("feedback saved offline was not sent when the connection returned")
        page.evaluate("localStorage.removeItem('pending_feedback')")

    # Delete all data tries queued feedback first, and says so when it could not.
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    for up in (False, True):
        service_up["value"] = False
        page.evaluate("""() => api("/api/feedback",
          { method: "POST", body: JSON.stringify({ rating: 2, text: "Queued before wipe" }) })""")
        if not page.evaluate("localStorage.getItem('pending_feedback')"):
            failures.append("wipe setup: the 503 did not queue feedback")
            continue
        service_up["value"] = up
        before = len(feedback_requests)
        dialogs.clear()
        page.evaluate("openSettings()")
        page.locator("#wipeBtn").click()
        page.wait_for_function("document.getElementById('wipeBtn').disabled === false", timeout=10_000)
        page.wait_for_timeout(300)
        note = page.evaluate("t('confirm_wipe_feedback')")
        if len(feedback_requests) == before:
            failures.append(f"wipe (service {'up' if up else 'down'}) did not try to send queued feedback")
        if not dialogs:
            failures.append("wipe showed no confirmation")
        elif up and note in dialogs[0]:
            failures.append("wipe warned about feedback that was just sent")
        elif not up and note not in dialogs[0]:
            failures.append(f"wipe did not mention unsent feedback: {dialogs[0]!r}")
        page.evaluate("localStorage.removeItem('pending_feedback')")

    context.close()

    # Declining the data notice explains that nothing was captured, until the next screen.
    context = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True,
                                  device_scale_factor=2.625)
    page = context.new_page()
    open_app(page, "test-key-never-sent")
    page.evaluate("localStorage.removeItem('data_notice_version')")
    page.evaluate("void ensureDataConsent()")
    page.locator("#dataConsent").wait_for(state="visible")
    page.locator("#privacyDecline").click()
    declined = page.evaluate("""() => {
      const note = document.getElementById("consentDeclinedNote");
      return { home: !document.getElementById("home").classList.contains("hidden"),
               text: note && !note.classList.contains("hidden") ? note.textContent : "",
               expected: t("privacy_declined_note") };
    }""")
    if not declined["home"] or declined["text"] != declined["expected"]:
        failures.append(f"declining the notice gave no explanation: {declined}")
    page.evaluate("openSettings(); show('home')")
    if page.evaluate("""() => {
      const note = document.getElementById("consentDeclinedNote");
      return !!note && !note.classList.contains("hidden");
    }"""):
        failures.append("the decline note stayed after the tester moved on")
    context.close()
    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS feedback screen")
