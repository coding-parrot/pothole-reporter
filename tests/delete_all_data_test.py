#!/usr/bin/env python3
"""Delete-all must clear every managed web store, or fail without claiming success."""

import pathlib
import sys

from playwright.sync_api import sync_playwright

from browser_test_utils import open_app


failures = []
with sync_playwright() as playwright:
    launch_options = {}
    system_chrome = pathlib.Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if system_chrome.is_file():
        launch_options["executable_path"] = str(system_chrome)
    browser = playwright.chromium.launch(**launch_options)
    page = browser.new_page()
    open_app(page, "test-key-never-sent")

    result = page.evaluate(
        """async () => {
          await StandaloneAPI.handle("/api/reports", {method: "DELETE"});
          const db = await new Promise((resolve, reject) => {
            const request = indexedDB.open("potholes", 6);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const tx = db.transaction(["reports", "drives", "footage", "state_packs"], "readwrite");
            tx.objectStore("reports").put({id: 9001, created_at: 1, photo: new Blob(["photo"])});
            tx.objectStore("drives").put({id: "wipe-drive"});
            tx.objectStore("footage").put({key: "wipe-drive#1", drive_id: "wipe-drive",
              blob: new Blob(["video"])});
            tx.objectStore("state_packs").put({cache_key: "wipe-pack", state_code: "XX",
              last_used_at: 1, blob: new Blob(["pack"])});
            tx.oncomplete = resolve;
            tx.onabort = () => reject(tx.error);
          });
          localStorage.setItem("wipe-setting", "secret");
          sessionStorage.setItem("wipe-session", "secret");
          const cache = await caches.open("wipe-cache");
          await cache.put("/wipe-probe", new Response("cached"));

          // Simulate a native sync whose last import transaction is already in flight
          // when the user wipes. The wipe must wait, clear that late row, and reject any
          // new sync request while its gate is closed.
          let releaseImport;
          const importGate = new Promise((resolve) => { releaseImport = resolve; });
          let importCompleted = false;
          nativeSyncPromise = importGate.then(() => new Promise((resolve, reject) => {
            const tx = db.transaction("reports", "readwrite");
            tx.objectStore("reports").put({id: 9003, created_at: 3,
              photo: new Blob(["late-native-import"])});
            tx.oncomplete = () => { importCompleted = true; resolve(); };
            tx.onabort = () => reject(tx.error);
          }));
          let releaseReplay;
          let replayCancelledAtRelease = false;
          const replayGate = new Promise((resolve) => { releaseReplay = resolve; });
          const replayState = {cancelled: false, interactive: false};
          replayState.done = replayGate.then(() => new Promise((resolve, reject) => {
            replayCancelledAtRelease = replayState.cancelled;
            const tx = db.transaction("reports", "readwrite");
            tx.objectStore("reports").put({id: 9004, created_at: 4,
              photo: new Blob(["late-saved-frame-replay"])});
            tx.oncomplete = () => { nativeKeyframeReplay = null; resolve(); };
            tx.onabort = () => reject(tx.error);
          }));
          nativeKeyframeReplay = replayState;
          nativeKeyframeAutoPromise = replayState.done;
          show("settings");
          let wipeSettled = false;
          const wipePromise = deleteAllAppData().then((value) => {
            wipeSettled = true;
            return value;
          });
          await new Promise((resolve) => setTimeout(resolve, 30));
          const wipeSettledBeforeImport = wipeSettled;
          const screenBeforeNavigation = $("settings").classList.contains("hidden") ? "other" : "settings";
          const navigationAllowed = show("home");
          const screenAfterNavigation = $("settings").classList.contains("hidden") ? "other" : "settings";
          const backConsumed = window.handleAppBack();
          await startDrive();
          const driveStartedDuringWipe = !!drive || driveStarting;
          let gatedSyncError = null;
          try { await syncNativeData(); }
          catch (error) { gatedSyncError = String(error && error.message || error); }
          releaseImport();
          releaseReplay();
          const completed = await wipePromise;
          const counts = await new Promise((resolve, reject) => {
            const tx = db.transaction(["reports", "drives", "footage", "state_packs"], "readonly");
            const values = {}, names = ["reports", "drives", "footage", "state_packs"];
            for (const name of names) {
              const req = tx.objectStore(name).count();
              req.onsuccess = () => { values[name] = req.result; };
            }
            tx.oncomplete = () => resolve(values);
            tx.onabort = () => reject(tx.error);
          });
          const localLengthAfterSuccess = localStorage.length;
          const sessionLengthAfterSuccess = sessionStorage.length;
          const cacheNamesAfterSuccess = await caches.keys();
          return {
            completed,
            counts,
            cacheNames: cacheNamesAfterSuccess,
            localLengthAfterSuccess,
            sessionLengthAfterSuccess,
            importCompleted,
            replayCancelledAtRelease,
            wipeSettledBeforeImport,
            gatedSyncError,
            screenBeforeNavigation,
            navigationAllowed,
            screenAfterNavigation,
            backConsumed,
            driveStartedDuringWipe,
          };
        }"""
    )

    # Use a fresh page because a successful production wipe intentionally keeps its gate
    # closed until reload. A native failure must happen before browser deletion and must
    # reopen the gate so the user can retry.
    failure_page = browser.new_page()
    open_app(failure_page, "test-key-never-sent")
    failure_result = failure_page.evaluate(
        """async () => {
          const db = await new Promise((resolve, reject) => {
            const request = indexedDB.open("potholes", 6);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const tx = db.transaction("reports", "readwrite");
            tx.objectStore("reports").put({id: 9002, created_at: 2, photo: new Blob(["keep"])});
            tx.oncomplete = resolve;
            tx.onabort = () => reject(tx.error);
          });
          localStorage.setItem("must-remain-on-failure", "yes");
          Object.defineProperty(window, "Capacitor", {configurable: true, value: {
            isNativePlatform: () => true,
            registerPlugin: () => ({clearNativeData: async () => {
              throw new Error("simulated native cleanup failure");
            }}),
            Plugins: {},
          }});
          let rejected = null;
          try { await deleteAllAppData(); }
          catch (error) { rejected = String(error && error.message || error); }
          const retained = await new Promise((resolve, reject) => {
            const tx = db.transaction("reports", "readonly");
            const req = tx.objectStore("reports").count();
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          return {
            rejected,
            retained,
            retainedSetting: localStorage.getItem("must-remain-on-failure"),
            gateReopened: nativeWipeInProgress === false,
          };
        }"""
    )
    browser.close()

if result["completed"] != {"cleared": True}:
    failures.append(f"delete-all did not return verified success: {result['completed']}")
if any(result["counts"].values()):
    failures.append(f"one or more IndexedDB stores survived: {result['counts']}")
if result["cacheNames"]:
    failures.append(f"Cache Storage survived: {result['cacheNames']}")
if result["localLengthAfterSuccess"] or result["sessionLengthAfterSuccess"]:
    failures.append("localStorage/sessionStorage survived a successful delete-all")
if not result["importCompleted"] or result["wipeSettledBeforeImport"]:
    failures.append(f"delete-all did not await the in-flight import: {result}")
if not result["replayCancelledAtRelease"]:
    failures.append(f"delete-all did not cancel and await saved-frame replay: {result}")
if "deletion is in progress" not in (result["gatedSyncError"] or ""):
    failures.append(f"delete-all allowed a new native sync: {result['gatedSyncError']}")
if result["screenBeforeNavigation"] != "settings" or result["screenAfterNavigation"] != "settings":
    failures.append(f"delete-all allowed navigation away from Settings: {result}")
if result["navigationAllowed"] is not False or result["backConsumed"] is not True:
    failures.append(f"delete-all did not consume direct/Back navigation: {result}")
if result["driveStartedDuringWipe"]:
    failures.append("Drive started while delete-all was in progress")
if "simulated native cleanup failure" not in (failure_result["rejected"] or ""):
    failures.append(f"native deletion failure was hidden: {failure_result['rejected']}")
if failure_result["retained"] != 1 or failure_result["retainedSetting"] != "yes":
    failures.append(f"web data was cleared after native deletion failed: {failure_result}")
if not failure_result["gateReopened"]:
    failures.append("a failed delete-all left all future sync and retry operations locked")

if failures:
    print("DELETE ALL DATA TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("DELETE ALL DATA TEST PASS")
