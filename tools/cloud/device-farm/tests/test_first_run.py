#!/usr/bin/env python3
"""First run of the signed release on a real phone, driven through Appium.

Runs on the AWS Device Farm test host (see ../testspec.yml). It does what
tools/harness/emulator-smoke.sh does on the emulator, and a little more:

  fresh install, launch, Home, Drive, Continue on the camera and location notice, both
  system permission sheets, the live drive for 20 s with the camera running, Stop,
  Settings and back, the app's own record of JavaScript errors, then the logcat scan.

The release build is not debuggable, so there is no WebView context to attach to. The
page is read through the accessibility tree instead (Appium's native context): a
Chromium WebView publishes its buttons and text there once an accessibility client is
connected, which UiAutomator2 is. System permission sheets are found with the same rules
the emulator smoke uses (permission-button.py, copied in next to this file).

Only the Python standard library is used: the client speaks WebDriver to the Appium
server the test spec starts, so the test host installs nothing.

Output, all under $DEVICEFARM_LOG_DIR/pothole (collected as customer artifacts):
  result.json            one verdict per step, dialogs seen, drive samples, findings
  NN-<step>.png / .xml   screenshot and accessibility tree at each step
  logcat.txt.gz          the whole device log for the run
  logcat-findings.txt    the lines that failed the scan, with context
Exit status 1 when a step failed or the scan found a crash, an ANR or a JS error.
"""
import base64
import gzip
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

PACKAGE = "dev.aiengg.potholereporter"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.environ.get("DEVICEFARM_LOG_DIR", "/tmp"), "pothole")
UDID = os.environ.get("DEVICEFARM_DEVICE_UDID", "")
BASE = "http://127.0.0.1:4723" + os.environ.get("APPIUM_BASE_PATH", "")
STARTED = time.time()
# The Device Farm job is cut at 10 minutes. Stop starting new steps well before that so
# the artifacts are always written.
BUDGET_SECONDS = float(os.environ.get("POTHOLE_TEST_BUDGET_SECONDS", "440"))
DRIVE_SECONDS = 20

# The same patterns tools/harness/emulator-smoke.sh greps, plus the two ways Android
# itself reports a dead or hung process.
ERROR_LINE = re.compile(
    r"FATAL EXCEPTION|Uncaught|is not defined|ReferenceError|TypeError|SyntaxError", re.I)
NOISE = re.compile(
    r"AppsFilter|PreferenceController|BaseSearchIndex|Phenotype|BinderNative|uiautomator", re.I)
PROCESS_DEATH = re.compile(
    r"Process: %s\b|ANR in %s\b|>>> %s <<<" % ((re.escape(PACKAGE),) * 3))

result = {
    "package": PACKAGE, "device": {}, "steps": [], "dialogs": [], "permissions": [],
    "drive_samples": [], "notes": [], "logcat_findings": [], "passed": False,
}
state = {"session": None, "shot": 0, "size": (1080, 2400), "pids": []}


def note(text):
    print("  note:", text, flush=True)
    result["notes"].append(text)


# ---------- adb and WebDriver ----------

def adb(*args, timeout=30):
    command = ["adb"] + (["-s", UDID] if UDID else []) + list(args)
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout)
        return done.stdout.decode("utf-8", "replace")
    except Exception as error:  # a slow or missing adb must not end the run
        return "ADB-ERROR %s" % error


def call(method, path, body=None, timeout=90):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace")).get("value")
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        raise RuntimeError("%s %s -> %s %s" % (method, path, error.code, text[:400]))


def session(path=""):
    return "/session/%s%s" % (state["session"], path)


def source():
    return call("GET", session("/source")) or ""


BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def normal(label):
    """Lower-case letters, digits and single spaces: '\U0001F697  Drive' -> 'drive'."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (label or "").lower())).strip()


class Node:
    def __init__(self, attributes):
        self.package = attributes.get("package", "")
        self.kind = attributes.get("class", "")
        self.resource = attributes.get("resource-id", "")
        self.raw = (attributes.get("text") or attributes.get("content-desc") or "").strip()
        self.label = normal(self.raw)
        match = BOUNDS.match(attributes.get("bounds", ""))
        self.box = tuple(int(v) for v in match.groups()) if match else (0, 0, 0, 0)

    @property
    def centre(self):
        x0, y0, x1, y1 = self.box
        return (x0 + x1) // 2, (y0 + y1) // 2

    @property
    def on_screen(self):
        x0, y0, x1, y1 = self.box
        width, height = state["size"]
        x, y = self.centre
        return x1 > x0 and y1 > y0 and 0 <= x <= width and 0 <= y <= height


def tree(xml=None):
    xml = source() if xml is None else xml
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], xml
    return [Node(element.attrib) for element in root.iter()], xml


def app_nodes(nodes):
    return [node for node in nodes if node.package == PACKAGE]


def find(nodes, *labels, contains=False, package=PACKAGE):
    """First on-screen node whose normalised label is one of labels."""
    for wanted in labels:
        for node in nodes:
            if package and node.package != package:
                continue
            if not node.on_screen or not node.label:
                continue
            if node.label == wanted or (contains and wanted in node.label):
                return node
    return None


def tap(x, y):
    try:
        call("POST", session("/actions"), {"actions": [{
            "type": "pointer", "id": "finger", "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 90},
                {"type": "pointerUp", "button": 0},
            ]}]})
    except Exception as error:
        note("WebDriver tap failed (%s); using adb input tap" % str(error)[:120])
        adb("shell", "input", "tap", str(int(x)), str(int(y)))


def swipe_up(fraction=0.35):
    width, height = state["size"]
    x, y0 = width // 2, int(height * 0.62)
    y1 = int(y0 - height * fraction)
    call("POST", session("/actions"), {"actions": [{
        "type": "pointer", "id": "finger", "parameters": {"pointerType": "touch"},
        "actions": [
            {"type": "pointerMove", "duration": 0, "x": x, "y": y0},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 500, "x": x, "y": y1},
            {"type": "pointerUp", "button": 0},
        ]}]})
    time.sleep(0.8)


def capture(name, xml=None):
    """Screenshot plus accessibility tree, numbered in the order they were taken."""
    state["shot"] += 1
    stem = os.path.join(OUT, "%02d-%s" % (state["shot"], name))
    try:
        png = call("GET", session("/screenshot"))
        with open(stem + ".png", "wb") as handle:
            handle.write(base64.b64decode(png))
    except Exception as error:
        note("screenshot %s failed: %s" % (name, str(error)[:120]))
    try:
        with open(stem + ".xml", "w", encoding="utf-8") as handle:
            handle.write(source() if xml is None else xml)
    except Exception as error:
        note("tree dump %s failed: %s" % (name, str(error)[:120]))


def pid():
    return adb("shell", "pidof", PACKAGE).strip()


def remember_pid():
    current = pid()
    if current and current not in state["pids"]:
        state["pids"].append(current)
    return current


# ---------- the page itself, when the build allows it ----------
# A debuggable build (never the release) exposes its WebView to chromedriver. The test
# does not need it, but when it is there each drive sample also records what the page
# sees: the video element, the camera track and the drive's own watchdog fields. That is
# the evidence a release build cannot give.

DRIVE_PROBE = """
const v = document.getElementById('driveVideo');
const tr = v && v.srcObject && v.srcObject.getVideoTracks()[0];
const d = (typeof drive !== 'undefined' && drive) ? drive : null;
return {
  now: Date.now(), hud: (document.getElementById('driveStatus') || {}).textContent,
  video: v ? {currentTime: v.currentTime, readyState: v.readyState, paused: v.paused,
              width: v.videoWidth, ended: v.ended} : null,
  track: tr ? {muted: tr.muted, readyState: tr.readyState, enabled: tr.enabled} : null,
  hidden: document.hidden,
  drive: d ? {hasPos: !!d.pos, posAgeMs: d.posAt ? Date.now() - d.posAt : null,
              sinceProgressMs: d.videoProgressAt ? Date.now() - d.videoProgressAt : null,
              badForMs: d.cameraBadAt ? Date.now() - d.cameraBadAt : 0,
              camBad: d.camBad || 0, capBad: d.capBad || 0, recovering: !!d.cameraRecovering,
              captured: d.tally && d.tally.captured, checked: d.tally && d.tally.checked} : null,
};
"""


# Debuggable builds only: start a drive from inside the page and record, every half
# second for 16 s, what the watchdog sees, together with every drawImage(video) and
# canvas.toBlob the app makes (when it was called, when its callback came, what it gave),
# and whether the page is getting animation frames and idle time.
INSTRUMENTED_DRIVE = """
const done = arguments[arguments.length - 1];
const t0 = performance.now();
const at = () => Math.round(performance.now() - t0);
const log = [], samples = [], idle = [];
const canvasProto = HTMLCanvasElement.prototype, realToBlob = canvasProto.toBlob;
let calls = 0;
canvasProto.toBlob = function (callback, type, quality) {
  const id = ++calls, started = at();
  log.push({e: 'toBlob', id, t: started, w: this.width, h: this.height});
  return realToBlob.call(this, (blob) => {
    log.push({e: 'blob', id, t: at(), ms: at() - started, size: blob ? blob.size : null});
    callback(blob);
  }, type, quality);
};
const contextProto = CanvasRenderingContext2D.prototype, realDraw = contextProto.drawImage;
contextProto.drawImage = function (source, ...rest) {
  const started = performance.now();
  try { return realDraw.call(this, source, ...rest); }
  finally {
    if (source && source.tagName === 'VIDEO') {
      log.push({e: 'draw', t: at(), ms: +(performance.now() - started).toFixed(1),
                vw: source.videoWidth, rs: source.readyState});
    }
  }
};
// arguments[0] true: also keep an animation-frame loop and idle callbacks going, which
// makes the page produce frames it otherwise would not. false: only listen.
const animate = arguments[0] === true;
let frames = 0, running = true;
const onFrame = () => { frames += 1; if (running) requestAnimationFrame(onFrame); };
const askIdle = () => {
  const asked = at();
  requestIdleCallback(() => { idle.push([asked, at() - asked]); if (running) setTimeout(askIdle, 300); });
};
if (animate) { requestAnimationFrame(onFrame); askIdle(); }
document.getElementById('driveBtn').click();
const timer = setInterval(() => {
  const v = document.getElementById('driveVideo');
  const tr = v && v.srcObject && v.srcObject.getVideoTracks()[0];
  const d = (typeof drive !== 'undefined' && drive) ? drive : null;
  samples.push({
    t: at(), hud: (document.getElementById('driveStatus').textContent || '').slice(0, 60),
    ct: v ? +v.currentTime.toFixed(2) : null, rs: v ? v.readyState : null, vw: v ? v.videoWidth : null,
    muted: tr ? tr.muted : null, track: tr ? tr.readyState : null, frames, hidden: document.hidden,
    d: d ? {pos: !!d.pos, posAge: d.posAt ? Date.now() - d.posAt : null,
            prog: d.videoProgressAt ? Date.now() - d.videoProgressAt : null,
            bad: d.cameraBadAt ? Date.now() - d.cameraBadAt : 0, camBad: d.camBad || 0,
            capBad: d.capBad || 0, busy: !!d.stillBusy, rec: !!d.cameraRecovering,
            cap: d.tally ? d.tally.captured : null} : null,
  });
}, 500);
setTimeout(() => {
  running = false;
  clearInterval(timer);
  canvasProto.toBlob = realToBlob;
  contextProto.drawImage = realDraw;
  done({animate, samples, log, idle});
}, 16000);
"""


def instrumented_drive(animate):
    name = state["webview"]
    try:
        call("POST", session("/context"), {"name": name}, timeout=90)
        call("POST", session("/timeouts"), {"script": 40000}, timeout=30)
        return call("POST", session("/execute/async"), {"script": INSTRUMENTED_DRIVE, "args": [animate]}, timeout=60)
    finally:
        try:
            call("POST", session("/context"), {"name": "NATIVE_APP"}, timeout=60)
        except Exception:
            pass


def find_webview():
    try:
        contexts = call("GET", session("/contexts"), timeout=60) or []
    except Exception as error:
        note("could not list contexts: %s" % str(error)[:160])
        return None
    names = [name if isinstance(name, str) else name.get("id", "") for name in contexts]
    result["contexts"] = names
    for name in names:
        if name.startswith("WEBVIEW") and PACKAGE in name:
            return name
    return None


def page_eval(script):
    """Run script in the page when the build is debuggable; None otherwise."""
    name = state.get("webview")
    if not name:
        return None
    try:
        call("POST", session("/context"), {"name": name}, timeout=90)
        return call("POST", session("/execute/sync"), {"script": script, "args": []}, timeout=30)
    except Exception as error:
        note("the WebView context stopped answering: %s" % str(error)[:200])
        state["webview"] = None
        return None
    finally:
        try:
            call("POST", session("/context"), {"name": "NATIVE_APP"}, timeout=60)
        except Exception:
            pass


# ---------- things that get in front of the app ----------

def grant_button(xml):
    """Where permission-button.py says the grant button is, or None."""
    done = subprocess.run([sys.executable, os.path.join(HERE, "permission-button.py")],
                          input=xml.encode("utf-8"), capture_output=True, timeout=20)
    words = done.stdout.decode().split()
    return (int(words[0]), int(words[1])) if done.returncode == 0 and len(words) == 2 else None


def clear_the_way(nodes, xml):
    """Deal with one thing that is not the app's own page. Returns what it did, or None.

    A system permission sheet is granted. An app alert or confirm (a JavaScript dialog
    shown as an Android dialog) is recorded and dismissed: Cancel on a confirm, so the
    test never agrees to anything, OK on an alert. A system "isn't responding" dialog is
    recorded as an ANR and told to wait.
    """
    foreign = [node for node in nodes if node.package and node.package != PACKAGE]
    resources = {node.resource: node for node in nodes if node.resource}
    wait = resources.get("android:id/aerr_wait")
    if wait:
        text = " | ".join(node.raw for node in nodes if node.raw)[:300]
        result["dialogs"].append({"kind": "anr", "text": text, "at": elapsed()})
        capture("anr-dialog", xml)
        tap(*wait.centre)
        return "anr"
    positive = resources.get("android:id/button1")
    if positive and positive.package == PACKAGE:
        message = resources.get("android:id/message")
        negative = resources.get("android:id/button2")
        entry = {"kind": "confirm" if negative else "alert", "at": elapsed(),
                 "text": (message.raw if message else "")[:400],
                 "pressed": (negative or positive).raw}
        result["dialogs"].append(entry)
        print("  app dialog:", entry, flush=True)
        capture("app-dialog", xml)
        tap(*(negative or positive).centre)
        return "dialog"
    if foreign and not app_nodes(nodes):
        target = grant_button(xml)
        if target:
            texts = [node.raw for node in foreign if node.raw][:8]
            result["permissions"].append({"at": elapsed(), "package": foreign[0].package,
                                          "sheet": texts})
            print("  permission sheet:", texts, flush=True)
            capture("permission-sheet", xml)
            tap(*target)
            return "permission"
    return None


def elapsed():
    return round(time.time() - STARTED, 1)


def wait_for(description, test, seconds, allow_clearing=True):
    """Poll the tree until test(nodes) returns something truthy. Clears dialogs on the way."""
    deadline = time.time() + seconds
    last_xml = ""
    while time.time() < deadline:
        nodes, last_xml = tree()
        found = test(nodes)
        if found:
            return found, nodes, last_xml
        if allow_clearing and clear_the_way(nodes, last_xml):
            time.sleep(2.5)
            continue
        time.sleep(1.0)
    raise AssertionError("%s did not appear within %ss; on screen: %s" % (
        description, seconds, visible_text(tree(last_xml)[0])[:500]))


def visible_text(nodes):
    seen, parts = set(), []
    for node in nodes:
        if node.raw and node.on_screen and node.raw not in seen:
            seen.add(node.raw)
            parts.append(node.raw)
    return " | ".join(parts)


def is_home(nodes):
    return find(nodes, "drive") and find(nodes, "photo")


# ---------- steps ----------

def step(name, hard=True):
    def wrap(function):
        def run():
            if time.time() - STARTED > BUDGET_SECONDS:
                result["steps"].append({"name": name, "ok": False, "hard": hard,
                                        "detail": "not run: the time budget was used up"})
                return False
            began = time.time()
            print("STEP %s" % name, flush=True)
            entry = {"name": name, "ok": True, "hard": hard, "detail": ""}
            try:
                entry["detail"] = function() or ""
            except Exception as error:
                entry["ok"] = False
                entry["detail"] = str(error)[:900]
                print("  FAILED: %s" % entry["detail"], flush=True)
                if not isinstance(error, AssertionError):
                    traceback.print_exc()
                try:
                    capture("failed-" + re.sub(r"[^a-z0-9]+", "-", name.lower()))
                except Exception:
                    pass
            entry["seconds"] = round(time.time() - began, 1)
            entry["pid"] = remember_pid()
            result["steps"].append(entry)
            return entry["ok"]
        run.step_name = name
        return run
    return wrap


@step("install and launch")
def launch():
    adb("logcat", "-c")
    capabilities = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:appWaitActivity": "*",
        "appium:appWaitDuration": 60000,
        "appium:newCommandTimeout": 600,
        "appium:autoGrantPermissions": False,
        "appium:adbExecTimeout": 60000,
        "appium:uiautomator2ServerInstallTimeout": 120000,
        "appium:uiautomator2ServerLaunchTimeout": 120000,
        # The live camera preview never lets the UI go idle; do not wait for it.
        "appium:settings[waitForIdleTimeout]": 0,
    }
    drivers = os.environ.get("DEVICEFARM_CHROMEDRIVER_EXECUTABLE_DIR", "")
    if drivers and os.path.isdir(drivers):
        capabilities["appium:chromedriverExecutableDir"] = drivers
    value = call("POST", "/session",
                 {"capabilities": {"alwaysMatch": capabilities, "firstMatch": [{}]}},
                 timeout=300)
    state["session"] = value["sessionId"]
    rect = call("GET", session("/window/rect"))
    state["size"] = (int(rect["width"]), int(rect["height"]))
    prop = lambda key: adb("shell", "getprop", key).strip()
    webview = ""
    for candidate in ("com.google.android.webview", "com.android.chrome", "com.android.webview"):
        match = re.search(r"versionName=(\S+)", adb("shell", "dumpsys", "package", candidate))
        if match:
            webview += "%s %s; " % (candidate, match.group(1))
    current = re.search(r"Current WebView package[^:]*:\s*\(([^)]*)\)",
                        adb("shell", "dumpsys", "webviewupdate"))
    result["device"] = {
        "name": os.environ.get("DEVICEFARM_DEVICE_NAME", ""),
        "model": prop("ro.product.model"), "manufacturer": prop("ro.product.manufacturer"),
        "android": prop("ro.build.version.release"), "sdk": prop("ro.build.version.sdk"),
        "screen": "%dx%d" % state["size"],
        "webview_in_use": current.group(1) if current else "", "webview_packages": webview,
        "app_version": (re.search(r"versionName=(\S+)", adb("shell", "dumpsys", "package", PACKAGE))
                        or [None, ""])[1],
    }
    print("  device:", result["device"], flush=True)
    if not remember_pid():
        raise AssertionError("the app process is not running after launch")
    return "session %s" % state["session"][:8]


@step("Home is the first screen")
def home():
    _, nodes, xml = wait_for("Home (Drive and Photo buttons)", is_home, 45)
    capture("home", xml)
    state["webview"] = find_webview()
    if state["webview"]:
        agent = page_eval("return navigator.userAgent")
        result["webview_context"] = state["webview"] if agent else None
        print("  debuggable build: WebView context %s, %s" % (state["webview"], agent), flush=True)
    else:
        result["webview_context"] = None
    return visible_text(app_nodes(nodes))[:200]


@step("Drive opens the camera and location notice")
def drive_notice():
    for attempt in range(1, 5):
        nodes, _ = tree()
        button = find(nodes, "drive")
        if button:
            tap(*button.centre)
        try:
            _, nodes, xml = wait_for(
                "the notice with Continue", lambda n: find(n, "continue") and find(n, "not now"), 7)
            capture("notice", xml)
            return "after %d tap(s)" % attempt
        except AssertionError:
            if attempt == 4:
                raise


@step("Continue, then both permission sheets, without the process dying")
def permissions():
    before = pid()
    state["drive_tapped_at"] = device_clock()
    nodes, _ = tree()
    button = find(nodes, "continue")
    if not button:
        raise AssertionError("no Continue button on the notice")
    tap(*button.centre)
    # Camera first, then location, each in its own sheet; the second can follow the first
    # by several seconds (12 s was seen on a loaded emulator). The drive screen is drawn
    # before the sheets, so "Stop is visible" alone does not mean they are done: wait
    # until Stop is visible and no sheet has shown for a while.
    deadline = time.time() + 75
    quiet_since = None
    while True:
        if time.time() > deadline:
            raise AssertionError("the live drive screen (Stop button) did not settle in 75 s; on screen: %s"
                                 % visible_text(tree()[0])[:500])
        nodes, xml = tree()
        if clear_the_way(nodes, xml):
            quiet_since = None
            time.sleep(2.5)
            continue
        if find(nodes, "stop"):
            quiet_since = quiet_since or time.time()
            quiet = time.time() - quiet_since
            if (len(result["permissions"]) >= 2 and quiet >= 3) or quiet >= 12:
                break
        else:
            quiet_since = None
        time.sleep(1)
    capture("drive-started", xml)
    after = pid()
    if not after or after != before:
        raise AssertionError("the app process died or restarted (pid %s -> %s)" % (before, after or "none"))
    granted = len(result["permissions"])
    if granted < 2:
        note("only %d permission sheet(s) appeared" % granted)
    return "%d sheet(s) granted" % granted


def device_clock():
    """The phone's own clock, in the form logcat stamps its lines with."""
    return adb("shell", "date", "+%m-%d %H:%M:%S").strip()


def watch_drive(seconds, label, quiet=False):
    """Stay on the drive screen for the given time. Returns the problems seen.

    quiet: do not touch the phone at all while the drive runs, then read the screen
    once. Reading the accessibility tree makes the WebView do work it would not do for
    a driver, so the quiet drive is the one that shows what a driver gets; the watched
    drive (a read about every second) shows the HUD as it changes.
    """
    before = pid()
    began = time.time()
    window = {"drive": label, "quiet": quiet, "started": device_clock(),
              "opens_from": state.pop("drive_tapped_at", None) or device_clock()}
    result.setdefault("drives", []).append(window)
    problems, xml = [], ""
    if quiet:
        time.sleep(seconds)
    while True:
        nodes, xml = tree()
        if clear_the_way(nodes, xml):
            time.sleep(2)
            nodes, xml = tree()
        texts = [node.raw for node in app_nodes(nodes) if node.raw and node.on_screen]
        # Everything after the Stop button is the HUD: the count, the status, the tally.
        if any(normal(text) == "stop" for text in texts):
            texts = texts[max(i for i, text in enumerate(texts) if normal(text) == "stop") + 1:]
        hud = " | ".join(dict.fromkeys(texts))[:300]
        at = round(time.time() - began, 1)
        stop_visible = bool(find(nodes, "stop"))
        samples = result["drive_samples"]
        if samples and samples[-1]["drive"] == label and samples[-1]["hud"] == hud \
                and samples[-1]["stop_visible"] == stop_visible:
            samples[-1]["until"] = at
        else:
            samples.append({"drive": label, "t": at, "until": at, "stop_visible": stop_visible, "hud": hud})
            print("  drive sample:", samples[-1], flush=True)
        inside = page_eval(DRIVE_PROBE)
        if inside:
            inside["t"] = at
            inside["drive_label"] = label
            result.setdefault("drive_page_state", []).append(inside)
            print("  page:", json.dumps(inside), flush=True)
        if "camera paused" in hud.lower() and not any("Camera paused" in p for p in problems):
            problems.append("'Camera paused' on the HUD %ss into the watch" % at)
        if not stop_visible and not any("no Stop" in p for p in problems):
            problems.append("no Stop button %ss into the watch" % at)
        if time.time() - began >= seconds:
            break
        time.sleep(1.0)
    window["ended"] = device_clock()
    capture("%s-drive-after-%ss" % (label, seconds), xml)
    if pid() != before:
        problems.append("the app process died or restarted during the drive")
    return problems


@step("live drive holds for 20 s with the camera running")
def live_drive():
    problems = watch_drive(DRIVE_SECONDS, "first", quiet=True)
    if problems:
        raise AssertionError("; ".join(problems))
    return result["drive_samples"][-1]["hud"][:200]


@step("Stop ends the drive and returns to Home")
def stop_drive():
    nodes, _ = tree()
    button = find(nodes, "stop")
    if not button:
        raise AssertionError("no Stop button to press")
    tap(*button.centre)
    _, nodes, xml = wait_for("Home after Stop", is_home, 45)
    capture("home-after-stop", xml)
    return visible_text(app_nodes(nodes))[:300]


@step("a second drive, left alone for 12 s, is scanning with one camera open")
def second_drive():
    # The control for the first drive: no notice and no permission sheet this time, so
    # anything wrong here is not about first-run prompts.
    sheets = len(result["permissions"])
    if state.get("webview"):
        # A debuggable build: let the page record its own watchdog and capture calls.
        # Twice: once only listening, once with the page kept animating.
        paused = []
        result["instrumented_drives"] = []
        for animate in (False, True):
            recorded = instrumented_drive(animate)
            result["instrumented_drives"].append(recorded)
            huds = [sample["hud"] for sample in recorded["samples"]]
            print("  instrumented drive (animate=%s):" % animate, json.dumps(recorded)[:3000], flush=True)
            nodes, _ = tree()
            stop = find(nodes, "stop")
            if stop:
                tap(*stop.centre)
            wait_for("Home after the instrumented drive", is_home, 45)
            if any("camera paused" in hud.lower() for hud in huds):
                paused.append("animate=%s" % animate)
        if paused:
            raise AssertionError("'Camera paused' on the HUD during the instrumented drive (%s)" % ", ".join(paused))
        return "instrumented twice, no 'Camera paused'"
    nodes, _ = tree()
    button = find(nodes, "drive")
    if not button:
        raise AssertionError("no Drive button on Home")
    state["drive_tapped_at"] = device_clock()
    tap(*button.centre)
    # Not one read of the screen until the time is up: this drive is the app on its own.
    problems = watch_drive(12, "second", quiet=True)
    if len(result["permissions"]) != sheets:
        problems.append("a permission sheet appeared again")
    nodes, _ = tree()
    stop = find(nodes, "stop")
    if stop:
        tap(*stop.centre)
    wait_for("Home after the second Stop", is_home, 45)
    if problems:
        raise AssertionError("; ".join(problems))
    return result["drive_samples"][-1]["hud"][:200]


@step("a third drive, read every second, shows the same")
def watched_drive():
    nodes, _ = tree()
    button = find(nodes, "drive")
    if not button:
        raise AssertionError("no Drive button on Home")
    state["drive_tapped_at"] = device_clock()
    tap(*button.centre)
    wait_for("the live drive screen", lambda n: find(n, "stop"), 20)
    problems = watch_drive(12, "watched")
    nodes, _ = tree()
    stop = find(nodes, "stop")
    if stop:
        tap(*stop.centre)
    wait_for("Home after the third Stop", is_home, 45)
    if problems:
        raise AssertionError("; ".join(problems))
    return result["drive_samples"][-1]["hud"][:200]


@step("Settings opens and Back returns to Home")
def settings():
    nodes, _ = tree()
    gear = find(nodes, "settings")
    if not gear:
        raise AssertionError("no Settings button on Home")
    tap(*gear.centre)
    _, nodes, xml = wait_for("Settings (Save and Back)",
                             lambda n: find(n, "save") and find(n, "back"), 15)
    capture("settings", xml)
    text = visible_text(app_nodes(nodes))
    tap(*find(nodes, "back").centre)
    wait_for("Home after Settings", is_home, 15)
    return text[:200]


@step("the app recorded no JavaScript errors (Feedback offers none to attach)", hard=True)
def recorded_errors():
    # A release build drops console output, so logcat cannot show an uncaught error. The
    # page keeps its own list (window.onerror, unhandledrejection) and the Feedback screen
    # offers "Include the last N app error messages" only when that list is not empty.
    nodes, _ = tree()
    tap(*find(nodes, "settings").centre)
    wait_for("Settings", lambda n: find(n, "save") and find(n, "back"), 15)
    opened = False
    for _ in range(8):
        nodes, _ = tree()
        target = find(nodes, "send feedback")
        row = find(nodes, "back")
        if target and (not row or target.box[3] < row.box[1] - 8):
            tap(*target.centre)
            opened = True
            break
        swipe_up()
    if not opened:
        raise AssertionError("could not bring 'Send feedback' into view in Settings")
    _, nodes, xml = wait_for("the Feedback screen", lambda n: find(n, "what happened", contains=True)
                             or find(n, "how is the app so far", contains=True), 15)
    for _ in range(3):
        swipe_up(0.25)
    nodes, xml = tree()
    capture("feedback", xml)
    offered = [node.raw for node in app_nodes(nodes) if "app error message" in node.label]
    # Leave the way it came: Feedback's Back, then Settings' Back.
    for _ in range(2):
        nodes, _ = tree()
        back = find(nodes, "back")
        if back:
            tap(*back.centre)
            time.sleep(1.5)
    wait_for("Home after Feedback", is_home, 15)
    if offered:
        raise AssertionError("the app recorded JavaScript errors: %s" % offered[0])
    return "Feedback offers no recorded errors"


def opens_unknown():
    return bool(result.get("drives")) and not result.get("camera_opens")


def scan_logcat():
    log = adb("logcat", "-d", "-v", "threadtime", timeout=90)
    with gzip.open(os.path.join(OUT, "logcat.txt.gz"), "wt", encoding="utf-8") as handle:
        handle.write(log)
    lines = log.splitlines()
    pids = set(state["pids"])
    hits = []
    for index, line in enumerate(lines):
        fields = line.split(None, 5)
        from_app = len(fields) >= 6 and fields[2] in pids
        if (from_app and ERROR_LINE.search(line) and not NOISE.search(line)) or PROCESS_DEATH.search(line):
            hits.append(index)
    findings, shown = [], set()
    with open(os.path.join(OUT, "logcat-findings.txt"), "w", encoding="utf-8") as handle:
        for index in hits:
            findings.append(lines[index][:500])
            for nearby in range(max(0, index - 3), min(len(lines), index + 25)):
                if nearby not in shown:
                    shown.add(nearby)
                    handle.write(lines[nearby] + "\n")
            handle.write("-----\n")
    # How often the app opened the camera. One per drive is normal; more means the app's
    # own watchdog decided the camera was lost and reopened it.
    opened = re.compile(r'CameraService::connect call \(PID -?\d+ "%s"' % re.escape(PACKAGE))
    opens = [" ".join(line.split()[:2])[:14] for line in lines if opened.search(line)]
    result["camera_opens"] = opens
    for window in result.get("drives", []):
        # Opened shortly before the watch began (the drive starts, then the sheets) up
        # to its end. More than one means the app reopened a camera it already had.
        inside = [at for at in opens if at <= window.get("ended", "99") and at >= window.get("opens_from", window["started"])]
        window["camera_opens"] = len(inside)
    result["permission_sheets_at"] = [line.split()[1] for line in lines
                                      if "START u0" in line and "REQUEST_PERMISSIONS" in line]
    result["logcat_findings"] = findings[:40]
    result["logcat_lines"] = len(lines)
    result["app_pids"] = sorted(pids)
    return findings


def main():
    os.makedirs(OUT, exist_ok=True)
    steps = [launch, home, drive_notice, permissions, live_drive, stop_drive, second_drive,
             watched_drive, settings, recorded_errors]
    try:
        if launch():
            for run in steps[1:]:
                run()
    finally:
        try:
            findings = scan_logcat()
        except Exception as error:
            findings = ["logcat scan failed: %s" % error]
            result["logcat_findings"] = findings
        reopened = ["%s drive: the app opened the camera %d times" % (w["drive"], w["camera_opens"])
                    for w in result.get("drives", []) if w.get("camera_opens", 0) > 1]
        result["camera_reopened"] = reopened
        if opens_unknown():
            note("logcat on this phone does not show camera opens; reopening is judged from the screen only")
        if len(state["pids"]) > 1:
            note("the app ran under more than one pid: %s" % state["pids"])
        hard_failures = [entry for entry in result["steps"] if entry["hard"] and not entry["ok"]]
        ran = {entry["name"] for entry in result["steps"]}
        missing = [run.step_name for run in steps if run.step_name not in ran]
        result["not_run"] = missing
        result["passed"] = not hard_failures and not findings and not missing and not reopened
        result["seconds"] = elapsed()
        with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        try:
            if state["session"]:
                call("DELETE", session(), timeout=30)
        except Exception:
            pass
    print("\nRESULT %s in %ss" % ("PASSED" if result["passed"] else "FAILED", result["seconds"]))
    for entry in result["steps"]:
        print("  %-4s %s: %s" % ("ok" if entry["ok"] else "FAIL", entry["name"], entry["detail"][:160]))
    for line in result["logcat_findings"][:10]:
        print("  logcat:", line[:200])
    for line in result.get("camera_reopened", []):
        print("  FAIL", line)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
