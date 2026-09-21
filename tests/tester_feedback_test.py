# -*- coding: utf-8 -*-
"""Tester feedback is signed, survives a failed send, and is asked for once."""

import json
import sys

from playwright.sync_api import sync_playwright

from browser_test_utils import _central_service, open_app


SERVICE = "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com"
# The server refuses feedback for good on bad input and after ten a day per install.
REJECTIONS = {
    "429": (429, {"error": "feedback_limit_reached", "details": {"limit": 10},
                  "message": "This installation has sent the maximum feedback for today."}),
    "400": (400, {"error": "bad_feedback", "message": "Feedback needs a whole 1 to 5 rating."}),
}

failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()

    feedback_requests = []
    service_up = {"value": False}

    def central(route, request):
        if request.url.startswith(f"{SERVICE}/v1/feedback"):
            feedback_requests.append({
                "body": json.loads(request.post_data or "{}"),
                "headers": {k.lower(): v for k, v in request.headers.items()},
            })
            if service_up["value"] in REJECTIONS:
                status, payload = REJECTIONS[service_up["value"]]
                route.fulfill(status=status, headers={"content-type": "application/json"},
                              body=json.dumps(payload))
            elif not service_up["value"]:
                route.fulfill(status=503, headers={"content-type": "application/json"},
                              body=json.dumps({"error": "service_unavailable", "message": "down"}))
            else:
                route.fulfill(status=201, headers={"content-type": "application/json"},
                              body=json.dumps({"accepted": True, "created_at": 1}))
            return
        _central_service(route, request)

    # Saving Settings checks a personal key is shaped like one, so this one has to be.
    open_app(page, "sk-test-key-never-sent-0000000")
    page.unroute(f"{SERVICE}/**")
    page.route(f"{SERVICE}/**", central)
    page.evaluate("openSettings()")
    page.locator("#feedbackBtn").click()
    page.locator("#feedback").wait_for(state="visible")

    # Nothing to send: no request and a clear message.
    page.locator("#feedbackSend").click()
    if feedback_requests:
        failures.append("empty feedback reached the service")
    if "star" not in page.locator("#feedbackStatus").inner_text().lower():
        failures.append(f"empty feedback did not explain itself: {page.locator('#feedbackStatus').inner_text()}")

    # A malformed optional email is caught on the phone.
    page.locator("#feedbackStars button[data-rating='4']").click()
    page.locator("#feedbackEmail").fill("not-an-email")
    page.locator("#feedbackSend").click()
    if feedback_requests:
        failures.append("malformed email reached the service")

    # Service down: the entry is kept on the phone, not lost.
    page.locator("#feedbackMode").select_option("car")
    page.locator("#feedbackText").fill("Drive mode froze after ten minutes.")
    page.locator("#feedbackEmail").fill("tester@example.com")
    page.locator("#feedbackSend").click()
    page.wait_for_function("document.getElementById('feedbackStatus').textContent.includes('Saved on this phone')",
                           timeout=15_000)
    queued = page.evaluate("JSON.parse(localStorage.getItem('pending_feedback') || '[]')")
    if len(queued) != 1:
        failures.append(f"failed feedback was not queued: {queued}")
    if len(feedback_requests) != 1:
        failures.append(f"expected one failed attempt, saw {len(feedback_requests)}")

    # Connection returns: the same signed entry is resent once and the queue empties.
    service_up["value"] = True
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_function("!localStorage.getItem('pending_feedback')", timeout=15_000)
    if len(feedback_requests) != 2:
        failures.append(f"expected a single retry, saw {len(feedback_requests)} requests")
    else:
        first, retry = feedback_requests
        body = retry["body"]
        expected = {"rating": 4, "text": "Drive mode froze after ten minutes.",
                    "test_mode": "car", "email": "tester@example.com"}
        for key, value in expected.items():
            if body.get(key) != value:
                failures.append(f"feedback {key} was {body.get(key)!r}, expected {value!r}")
        for header in ("x-install-id", "x-timestamp", "x-signature", "idempotency-key"):
            if not retry["headers"].get(header):
                failures.append(f"feedback request was not signed: missing {header}")
        if first["headers"].get("idempotency-key") != retry["headers"].get("idempotency-key"):
            failures.append("retry used a new idempotency key, so it could be counted twice")

    # A refusal is not a success: the tester is told, the text stays, nothing is queued.
    page.wait_for_timeout(1700)   # the queued send above closes its screen after 1.5 s
    for mode, wanted in (("429", "feedback_limit"), ("400", "feedback_rejected")):
        service_up["value"] = mode
        before = len(feedback_requests)
        page.evaluate("openFeedback('home')")
        page.locator("#feedbackText").fill(f"Refused feedback {mode}")
        page.locator("#feedbackSend").click()
        page.wait_for_function(
            "() => document.getElementById('feedbackStatus').textContent !== t('working')",
            timeout=15_000)
        state = page.evaluate("""(key) => ({
          status: document.getElementById("feedbackStatus").textContent,
          expected: t(key), text: document.getElementById("feedbackText").value,
          sendDisabled: document.getElementById("feedbackSend").disabled,
          queue: localStorage.getItem("pending_feedback"),
          visible: !document.getElementById("feedback").classList.contains("hidden"),
        })""", wanted)
        if state["status"] != state["expected"]:
            failures.append(f"{mode} refusal showed {state['status']!r}, expected {state['expected']!r}")
        if state["text"] != f"Refused feedback {mode}":
            failures.append(f"{mode} refusal cleared the tester's text: {state['text']!r}")
        if state["sendDisabled"] or not state["visible"]:
            failures.append(f"{mode} refusal left no way to try again: {state}")
        if state["queue"]:
            failures.append(f"{mode} refusal was queued for a retry that cannot succeed: {state['queue']}")
        if len(feedback_requests) != before + 1:
            failures.append(f"{mode} refusal sent {len(feedback_requests) - before} requests")
        page.wait_for_timeout(1700)
        if page.locator("#feedback").is_hidden():
            failures.append(f"{mode} refusal closed the feedback screen as if it had been sent")

    # A message sent while an earlier send is still in flight goes out in that same
    # flush. It used to be reported as queued and wait for the next app start.
    service_up["value"] = True
    before = len(feedback_requests)
    overlap = page.evaluate("""async () => {
      const realFetch = window.fetch;
      let held = false;
      window.fetch = async (url, init) => {
        if (!held && String(url).includes("/v1/feedback")) {
          held = true;
          await new Promise((resolve) => setTimeout(resolve, 1500));
        }
        return realFetch(url, init);
      };
      const post = (text) => api("/api/feedback",
        { method: "POST", body: JSON.stringify({ rating: 3, text }) });
      try {
        const first = post("In flight first");
        await new Promise((resolve) => setTimeout(resolve, 300));
        const second = await post("In flight second");
        return { first: await first, second, queue: localStorage.getItem("pending_feedback") };
      } finally { window.fetch = realFetch; }
    }""")
    texts = [request["body"].get("text") for request in feedback_requests[before:]]
    if overlap["second"].get("queued") or overlap["queue"] or texts != ["In flight first", "In flight second"]:
        failures.append(f"feedback sent during an in-flight send was parked: {overlap}, sent {texts}")

    # Queued after a 5xx, feedback goes out when the app returns to the foreground and
    # when Settings are saved, not only on a cold start or an online event.
    for trigger, script in (("resume", "window.handleNativeAppStateChange(true)"),
                            ("settings save", "openSettings(); document.getElementById('setSave').click()")):
        service_up["value"] = False
        page.evaluate("""(text) => api("/api/feedback",
          { method: "POST", body: JSON.stringify({ rating: 2, text }) })""", f"Queued before {trigger}")
        if not page.evaluate("localStorage.getItem('pending_feedback')"):
            failures.append(f"{trigger}: the 503 did not queue the feedback")
            continue
        service_up["value"] = True
        page.evaluate(f"() => {{ {script}; }}")
        try:
            page.wait_for_function("!localStorage.getItem('pending_feedback')", timeout=5000)
        except Exception:
            failures.append(f"{trigger}: queued feedback was not retried")
    page.locator("#home").wait_for(state="visible")

    # The one-time nudge appears from the third report and never again once answered.
    page.evaluate("localStorage.removeItem('feedback_nudged'); show('home')")
    page.evaluate("maybeNudgeFeedback(2)")
    if page.locator("#feedbackNudge").is_visible():
        failures.append("nudge appeared before the third report")
    page.evaluate("maybeNudgeFeedback(3)")
    if not page.locator("#feedbackNudge").is_visible():
        failures.append("nudge did not appear at the third report")
    page.locator("#feedbackNudgeDismiss").click()
    page.evaluate("maybeNudgeFeedback(7)")
    if page.locator("#feedbackNudge").is_visible():
        failures.append("nudge came back after it was dismissed")

    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS tester feedback")
