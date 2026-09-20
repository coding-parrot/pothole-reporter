#!/usr/bin/env python3
"""Drive, stop, analyse: the post-drive footage path on a real Android WebView.

The browser suites run this path in desktop Chromium, where MediaRecorder, the video
decoder and seeking all behave differently from a phone. The emulator smoke stops at the
live drive. This runs the whole thing where testers run it: the app's own WebView, over
the Chrome debugging protocol that a debug build exposes. Playwright cannot attach to a
WebView (no browser-context management), so this speaks the protocol directly.

The central service is intercepted and answered by the same mock the flow suites use, so
the run exercises the recorder, the decoder and the seek pipeline, not the network or
the detector key.

  python3 tools/harness/webview-drive-analyse.py --seconds 35

Needs a booted emulator or device with the debug build installed and in the foreground.
Exit 0 when every planned frame was checked; 1 with the app's own summary otherwise.
"""

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "tests"))
from flow_harness import DATA_NOTICE_VERSION, SERVICE, central_service  # noqa: E402
import websocket  # noqa: E402

ADB = os.path.expanduser(os.environ.get("ADB", "~/Library/Android/sdk/platform-tools/adb"))
PACKAGE = os.environ.get("POTHOLE_PACKAGE", "dev.aiengg.potholereporter")


class FakeRequest:
    def __init__(self, url, method, post_data):
        self.url, self.method, self.post_data = url, method, post_data


class FakeRoute:
    """What central_service expects: it calls route.fulfill(status=, headers=, body=)."""
    def __init__(self):
        self.response = None

    def fulfill(self, status=200, headers=None, body=""):
        self.response = (status, headers or {}, body)

    def abort(self, *_):
        self.response = (502, {}, "")


class Devtools:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, suppress_origin=True)
        self.ws.settimeout(0.25)
        self.next_id = 0
        self.console, self.errors, self.intercepted = [], [], []

    def call(self, method, params=None, timeout=120):
        self.next_id += 1
        msg_id = self.next_id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv()
            if message is None:
                continue
            if message.get("id") == msg_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._event(message)
        raise TimeoutError(method)

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            message = self._recv()
            if message is not None and "id" not in message:
                self._event(message)

    def _recv(self):
        try:
            return json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return None

    def _event(self, message):
        method, params = message.get("method"), message.get("params", {})
        if method == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", []))
            self.console.append(f"{params.get('type')}: {text}")
        elif method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails", {})
            exc = detail.get("exception", {})
            self.errors.append(exc.get("description") or detail.get("text") or str(detail)[:200])
        elif method == "Fetch.requestPaused":
            self._answer(params)

    def _answer(self, params):
        request = params["request"]
        # Capacitor's HTTP plugin rewrites page fetches to a local interceptor URL and
        # carries the real address in the query string. The mock must see the real one.
        url = request["url"]
        if "_capacitor_http_interceptor_" in url:
            from urllib.parse import parse_qs, urlparse
            real = parse_qs(urlparse(url).query).get("u", [""])[0]
            if real:
                request = {**request, "url": real}
        post = request.get("postData")
        if post is None and request.get("hasPostData"):
            try:
                post = self.call("Fetch.getRequestPostData", {"requestId": params["requestId"]}).get("postData")
            except Exception:
                post = None
        route = FakeRoute()
        central_service(route, FakeRequest(request["url"], request["method"], post))
        status, headers, body = route.response
        self.intercepted.append(f"{request['method']} {request['url'].split('.com', 1)[-1]} -> {status}")
        try:
            self.call("Fetch.fulfillRequest", {
                "requestId": params["requestId"], "responseCode": status,
                "responseHeaders": [{"name": k, "value": v} for k, v in headers.items()],
                "body": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            }, timeout=15)
        except (TimeoutError, RuntimeError) as error:
            # A request caught mid-navigation has no page to deliver to. Not fatal.
            self.intercepted.append(f"  (unanswerable: {error})")

    def evaluate(self, expression, await_promise=True):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": await_promise,
        })
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description", "evaluate failed"))
        return result.get("result", {}).get("value")


def app_pid():
    return subprocess.run([ADB, "shell", "pidof", PACKAGE], capture_output=True, text=True).stdout.strip()


def launch_app():
    """Start the app the way the launcher does and wait for its process. The activity's
    class lives under a different package from the application ID, so it is resolved
    rather than guessed."""
    resolved = subprocess.run([ADB, "shell", "cmd", "package", "resolve-activity", "--brief",
                               "-c", "android.intent.category.LAUNCHER", PACKAGE],
                              capture_output=True, text=True).stdout.strip().splitlines()
    activity = resolved[-1].strip() if resolved else ""
    if not activity.startswith(PACKAGE + "/"):
        sys.exit(f"no launchable activity for {PACKAGE}: {resolved}")
    subprocess.run([ADB, "shell", "am", "start", "-n", activity], capture_output=True)
    for _ in range(60):
        time.sleep(1)
        if app_pid():
            time.sleep(6)  # let the WebView and its devtools socket come up
            return
    sys.exit(f"{PACKAGE} did not start")


MOCK_JS = r"""(() => {
  const SERVICE = %(service)s;
  const real = window.fetch.bind(window);
  window.__mockCalls = [];
  const sha256 = async (text) => {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  };
  const reply = (payload, status = 200) => new Response(JSON.stringify({ request_id: "req-webview", ...payload }), {
    status, headers: { "content-type": "application/json", "x-request-id": "req-webview" },
  });
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (!url.startsWith(SERVICE)) return real(input, init);
    const path = new URL(url).pathname;
    const method = (init.method || "GET").toUpperCase();
    let body = {};
    try { body = init.body ? JSON.parse(init.body) : {}; } catch (e) {}
    window.__mockCalls.push(method + " " + path);
    if (path === "/v1/health") return reply({ ok: true, shared_vision_configured: true });
    if (path === "/v1/installations") return reply({ install_id: "webview-install" }, 201);
    if (path === "/v1/activity") return reply({ accepted: true, event: "vision_check" }, 202);
    if (path === "/v1/vision/detect") return reply({
      image_quality: "acceptable", assessment: "damaged", damage_type: "pothole_cavity",
      size: "medium", description: "A cavity with a broken rim is visible on the travelled surface.",
      detection_receipt: await sha256("receipt:" + body.client_observation_id),
      detector: { provider: "shared_server", model: body.model || "gpt-5-mini",
                  prompt_version: "road-damage-v5", schema_version: 4,
                  evidence_count: (body.images || []).length },
    });
    if (path === "/v1/tenders/resolve") return reply({
      jurisdiction: { lat: body.lat, lng: body.lng, address: "Test Road, Central Ward, Test City, 560001",
                      lgd: "999001", town: "Test City Corporation", source: "kgis",
                      address_source: "nominatim", road_ownership: "municipal" },
      tender: null, reason: "no_tenders_for_jurisdiction",
    });
    if (path === "/v1/potholes/report") return reply({
      duplicate: false, dedupe: null,
      pothole: { id: 4242, lat: body.lat, lng: body.lng, damage_type: body.damage_type, size: body.size,
                 first_seen_at: body.observed_at, last_seen_at: body.observed_at, seen_count: 1,
                 lgd: "999001", town: "Test City Corporation" },
    }, 201);
    if (path === "/v1/map") return reply({ type: "FeatureCollection", total: 0, features: [] });
    return reply({ error: "not_mocked", message: path }, 404);
  };
})();"""


def forward_devtools(port):
    pid = app_pid()
    if not pid:
        launch_app()
        pid = app_pid()
    # After a cold boot the WebView can take most of a minute to open its devtools
    # socket, and the process can be replaced meanwhile, so the forward follows the pid.
    for _ in range(120):
        pid = app_pid() or pid
        subprocess.run([ADB, "forward", f"tcp:{port}", f"localabstract:webview_devtools_remote_{pid}"],
                       capture_output=True)
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/json", timeout=3) as response:
                pages = json.load(response)
            page = next((p for p in pages if p.get("type") == "page" and p.get("webSocketDebuggerUrl")), None)
            if page:
                return page["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    sys.exit("no debuggable page after 60s: is this a debug build, and is the app in the foreground?")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--seconds", type=int, default=35, help="drive length")
    parser.add_argument("--step", type=float, default=2.0, help="seconds between sampled frames")
    parser.add_argument("--json", help="write the outcome here")
    args = parser.parse_args()

    dt = Devtools(forward_devtools(args.port))
    dt.call("Runtime.enable")
    dt.call("Page.enable")
    dt.call("Page.addScriptToEvaluateOnNewDocument", {"source": f"""(() => {{
      window.__alerts = []; window.__confirms = [];
      window.alert = (m) => window.__alerts.push(String(m));
      window.confirm = (m) => {{ window.__confirms.push(String(m)); return true; }};
      try {{
        localStorage.removeItem("service_url");
        localStorage.setItem("vision_provider", "shared");
        localStorage.setItem("data_notice_version", {json.dumps(DATA_NOTICE_VERSION)});
        localStorage.setItem("debug_mode", "1");
        localStorage.setItem("record_video", "1");
        localStorage.setItem("keep_frames", "1");
        localStorage.setItem("app_lang", "en");
      }} catch (e) {{}}
    }})();"""})
    dt.call("Page.reload", {"ignoreCache": True})
    for _ in range(120):
        dt.pump(0.5)
        try:
            if dt.evaluate("typeof startDrive === 'function' && !!window.StandaloneAPI && !!window.__alerts", False):
                break
        except Exception:
            pass
    else:
        sys.exit("the app did not come up after reload")
    # Capacitor's HTTP plugin sends some requests through native code, where the
    # debugging protocol cannot see or answer them. The mock therefore wraps fetch inside
    # the page, after Capacitor has installed its own patch, and answers the service
    # itself. Only the detector and the map endpoints the analysis touches are modelled.
    service_url = dt.evaluate("CENTRAL_SERVICE_URL()", False)
    print(f"service: {service_url} (identity registers for real; detector and map calls are answered in-page)")
    dt.evaluate(MOCK_JS % {"service": json.dumps(service_url)}, False)
    dt.evaluate(f"VOD_STEP_S = {args.step}", False)
    mime = dt.evaluate("""(typeof RECORD_MIMES === 'undefined') ? null
        : RECORD_MIMES.find((t) => { try { return MediaRecorder.isTypeSupported(t); } catch (e) { return false; } })""", False)
    print(f"recorder mime: {mime}")

    dt.evaluate("void startDrive()", False)
    for _ in range(60):
        dt.pump(0.5)
        if dt.evaluate("!!drive", False):
            break
    else:
        sys.exit(f"drive did not start; alerts={dt.evaluate('window.__alerts', False)}")
    print(f"drive running; recording {args.seconds}s")
    started = time.time()
    while time.time() - started < args.seconds:
        dt.pump(5)
        print("  " + str(dt.evaluate("(document.getElementById('driveTip') || {textContent: ''}).textContent.trim().slice(0, 90)", False)))
    dt.evaluate("void stopDrive()", False)
    print("stopped; waiting for the analysis")

    deadline = time.time() + 420
    last_progress = ""
    while time.time() < deadline:
        dt.pump(2)
        state = dt.evaluate("""({
          alerts: window.__alerts, confirms: window.__confirms,
          progress: (document.getElementById('progressText') || {textContent: ''}).textContent.trim(),
        })""", False)
        if state["progress"] and state["progress"] != last_progress:
            last_progress = state["progress"]; print("  " + last_progress)
        if any(a.startswith("Footage analysed") or a.startswith("Could not finish") for a in state["alerts"]):
            break
    state = dt.evaluate("({alerts: window.__alerts, confirms: window.__confirms})", False)
    print("\nconfirms:", *state["confirms"], sep="\n  ")
    print("alerts:", *state["alerts"], sep="\n  ")
    noise = [c for c in dt.console if not c.startswith("log:")]
    print("console (non-log, last 30):", *noise[-30:], sep="\n  ")
    print("page errors:", *dt.errors, sep="\n  ")
    print("service calls:", *dt.evaluate("window.__mockCalls || []", False), sep="\n  ")
    # Complete means the analysis was offered, ran, and reported every planned frame
    # checked. A run that never reached the analysis is a failure of a different kind and
    # must not pass as a success.
    summary = next((a for a in reversed(state["alerts"])
                    if a.startswith("Footage analysed") or a.startswith("Could not finish")), "")
    offered = any("Analyse" in c or "analyse" in c for c in state["confirms"])
    ok = offered and summary.startswith("Footage analysed")
    # The log the app wrote beside its frames is the detailed account; show its summary.
    listing = subprocess.run([ADB, "shell", "ls -t /storage/emulated/0/Documents/pothole-frames/ 2>/dev/null | head -1"],
                             capture_output=True, text=True).stdout.strip()
    if listing:
        raw = subprocess.run([ADB, "shell", f"cat /storage/emulated/0/Documents/pothole-frames/{listing}/analysis-log.json"],
                             capture_output=True, text=True).stdout
        try:
            log = json.loads(raw)
            print("\nanalysis log:", json.dumps(log.get("summary")))
            print("clips:", log.get("clips"))
            for event in log.get("events", [])[:40]:
                if event.get("stage") != "clip":
                    print("  ", json.dumps(event)[:180])
        except ValueError:
            pass
    if not offered:
        print("\nthe analysis was never offered: the drive did not produce footage, or a preflight failed")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({"ok": ok, "mime": mime, "alerts": state["alerts"],
                                                        "console": noise, "errors": dt.errors}, indent=1))
    print("\nRESULT:", "complete" if ok else "INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
