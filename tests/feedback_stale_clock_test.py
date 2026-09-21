# -*- coding: utf-8 -*-
"""Feedback refused for a wrong phone clock stays queued on the phone.

The flush dropped every 4xx as a refusal that could never succeed. A 401 stale_request
is not that: it says the phone's clock is off, and once the clock (or the server's
time) is right the same message goes through. Dropping it lost the one message a tester
with a broken clock could have sent about it.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, open_central, reply

SEND = """async () => {
  const result = await StandaloneAPI.handle('/api/feedback', { method: 'POST',
    body: JSON.stringify({ message: 'the clock test', mode: 'bug' }) });
  return { result, queued: StandaloneAPI.__pure.readFeedbackQueue().length };
}"""

fails = []
with sync_playwright() as playwright:
    # No server_time in the refusal, so the client cannot re-sign on the server's clock.
    state = {"stale": True}

    def script(route, request, path, central):
        if path == "/v1/feedback" and state["stale"]:
            return reply(route, 401, "stale_request", "The signed request is too old.")
        return False

    central = Central(script)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        sent = page.evaluate(SEND)
        if sent["queued"] != 1:
            fails.append(f"stale-clock feedback was dropped from the queue: {sent}")
        if sent["result"].get("rejected"):
            fails.append(f"stale-clock feedback was reported as refused: {sent['result']}")
        # With the clock fixed the kept message is delivered on the next flush.
        state["stale"] = False
        flushed = page.evaluate("async () => (await StandaloneAPI.__pure.flushFeedbackQueue())")
        if flushed.get("sent") != 1 or flushed.get("pending") != 0:
            fails.append(f"the kept message was not delivered once the clock was right: {flushed}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL feedback stale clock")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS feedback stale clock")
