# -*- coding: utf-8 -*-
"""Stopping during asynchronous Drive startup must not resurrect resources."""
import os, pathlib, sys
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
KEY = os.environ["OPENAI_API_KEY"]

JS = r"""
async (phase) => {
  const video = document.getElementById("driveVideo");
  const realPlay = video.play.bind(video);
  const realStartRecording = window.startRecording;
  const realStopRecording = window.stopRecording;
  const realWatch = navigator.geolocation.watchPosition.bind(navigator.geolocation);
  const realClear = navigator.geolocation.clearWatch.bind(navigator.geolocation);
  const wakeOwn = Object.getOwnPropertyDescriptor(navigator, "wakeLock");
  let resolvePlay = null, resolveLock = null;
  let recordStarts = 0, recordStops = 0, watchStarts = 0;
  let wakeRequests = 0, wakeReleases = 0;

  window.startRecording = () => { recordStarts++; };
  window.stopRecording = () => { recordStops++; };
  navigator.geolocation.watchPosition = () => { watchStarts++; return 91; };
  navigator.geolocation.clearWatch = () => {};
  Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
    request: () => {
      wakeRequests++;
      return new Promise((resolve) => { resolveLock = () => resolve({
        release: () => { wakeReleases++; return Promise.resolve(); }
      }); });
    }
  }});
  video.play = phase === "play"
    ? () => new Promise((resolve) => { resolvePlay = resolve; })
    : () => Promise.resolve();

  const starting = startDrive();
  for (let i = 0; i < 200 && !drive; i++) await new Promise((r) => setTimeout(r, 10));
  if (!drive) throw new Error("Drive context was never created");
  if (phase === "wake") {
    for (let i = 0; i < 200 && !resolveLock; i++) await new Promise((r) => setTimeout(r, 10));
    if (!resolveLock) throw new Error("Wake-lock request was never reached");
  }
  stopDrive();
  if (resolvePlay) resolvePlay();
  if (resolveLock) resolveLock();
  await starting;
  await new Promise((r) => setTimeout(r, 20));

  const result = { recordStarts, recordStops, watchStarts, wakeRequests, wakeReleases,
                   drivePresent: !!drive, driveStarting };
  video.play = realPlay;
  window.startRecording = realStartRecording;
  window.stopRecording = realStopRecording;
  navigator.geolocation.watchPosition = realWatch;
  navigator.geolocation.clearWatch = realClear;
  if (wakeOwn) Object.defineProperty(navigator, "wakeLock", wakeOwn);
  else delete navigator.wakeLock;
  return result;
}
"""

fails = []
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-web-security",
        "--allow-running-insecure-content", "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream"])
    context = browser.new_context(viewport={"width": 390, "height": 844},
        permissions=["geolocation", "camera"],
        geolocation={"latitude": 12.9115, "longitude": 77.6427})
    for phase in ("play", "wake"):
        page = context.new_page()
        open_app(page, KEY)
        page.evaluate("window.alert = () => {}; window.confirm = () => false;")
        result = page.evaluate(JS, phase)
        page.close()
        if result["drivePresent"] or result["driveStarting"]:
            fails.append(f"{phase}: Drive state survived Stop: {result}")
        if result["watchStarts"]:
            fails.append(f"{phase}: installed a GPS watch after Stop: {result}")
        if phase == "play" and (result["recordStarts"] or result["wakeRequests"]):
            fails.append(f"play: startup continued past delayed video.play(): {result}")
        if phase == "wake" and result["wakeReleases"] != 1:
            fails.append(f"wake: late wake lock was not released: {result}")
    browser.close()

if fails:
    print("FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("DRIVE START/STOP TEST PASS")
