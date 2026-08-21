# -*- coding: utf-8 -*-
"""Persisted report fields must render as text, never executable markup."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
SECRET_KEY = "__stored_xss_secret"
SECRET = "local-storage-must-stay-private"
PIXEL = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="
)

HOOK = (
    "window.__storedXssRuns=(window.__storedXssRuns||0)+1;"
    f"window.__storedXssRead=localStorage.getItem('{SECRET_KEY}')"
)
ADDRESS = (
    'ADDRESS_XSS_MARKER</div><img src="xss-address-missing" '
    f'data-stored-xss="address-event" onerror="{HOOK}"><div>'
)
OFFICER = (
    'OFFICER_XSS_MARKER</div><svg data-stored-xss="officer-event" '
    f'onload="{HOOK}"></svg><div>'
)
MODEL = (
    'MODEL_XSS_MARKER</div><script data-stored-xss="model-script">'
    f"{HOOK}</script><img src=\"xss-model-missing\" "
    f'data-stored-xss="model-event" onerror="{HOOK}"><div>'
)

SEED = r"""
async ({overrides, secretKey, secret, pixel}) => {
  await StandaloneAPI.handle("/api/reports", {method: "DELETE"});
  localStorage.setItem(secretKey, secret);
  window.__storedXssRuns = 0;
  window.__storedXssRead = null;

  const record = {
    created_at: 1710000000,
    status: "draft",
    damage_type: "pothole_cavity",
    assessment: "clear",
    image_quality: "usable",
    size: "medium",
    description: "ordinary model description",
    address: "ordinary address",
    officer_name: "ordinary officer",
    officer_email: "road-officer@example.invalid",
    tender_note: "",
    email_subject: "Road damage report",
    email_body: "Please inspect this damage.",
    lat: 12.9115,
    lng: 77.6427,
    photo: pixel,
    photo_full: pixel,
    ...overrides,
  };

  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    tx.objectStore("reports").add(record);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("seed transaction aborted"));
    tx.onerror = () => {};
  });
  db.close();

  const stored = await StandaloneAPI.handle("/api/reports");
  if (stored.length !== 1) throw new Error(`expected one seeded report, got ${stored.length}`);
  return stored[0];
}
"""


def run_surface(browser, name, overrides, render, markers):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof StandaloneAPI !== 'undefined' && typeof loadReports === 'function'"
    )
    report = page.evaluate(
        SEED,
        {
            "overrides": overrides,
            "secretKey": SECRET_KEY,
            "secret": SECRET,
            "pixel": PIXEL,
        },
    )

    if render == "history":
        page.evaluate("loadReports()")
        root = "#list"
    else:
        page.evaluate("report => openDetail(report, [report])", report)
        root = "#detail"

    # Give error/load handlers time to fire. Escaped text must never create either node.
    page.wait_for_timeout(250)
    state = page.evaluate(
        """({root, markers, secretKey}) => {
          const el = document.querySelector(root);
          return {
            runs: window.__storedXssRuns || 0,
            stolen: window.__storedXssRead,
            secretStillStored: localStorage.getItem(secretKey),
            injectedNodes: el.querySelectorAll("[data-stored-xss]").length,
            markersPresent: markers.map((marker) => el.textContent.includes(marker)),
            visible: !el.classList.contains("hidden"),
          };
        }""",
        {"root": root, "markers": markers, "secretKey": SECRET_KEY},
    )
    context.close()
    return name, state


failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    results = [
        run_surface(
            browser,
            "history/address",
            {"address": ADDRESS},
            "history",
            ["ADDRESS_XSS_MARKER"],
        ),
        run_surface(
            browser,
            "detail/address+officer",
            {"address": ADDRESS, "officer_name": OFFICER},
            "detail",
            ["ADDRESS_XSS_MARKER", "OFFICER_XSS_MARKER"],
        ),
        run_surface(
            browser,
            "detail/model-description",
            {"status": "rejected", "description": MODEL},
            "detail",
            ["MODEL_XSS_MARKER"],
        ),
    ]
    browser.close()

for name, state in results:
    print(
        f"  {name:26s} runs={state['runs']} "
        f"injected_nodes={state['injectedNodes']} stolen={state['stolen']!r}"
    )
    if state["runs"]:
        failures.append(f"{name}: an injected event/script executed")
    if state["stolen"] is not None:
        failures.append(f"{name}: injected code read localStorage")
    if state["secretStillStored"] != SECRET:
        failures.append(f"{name}: the localStorage sentinel was altered")
    if state["injectedNodes"]:
        failures.append(f"{name}: hostile markup became live DOM")
    if not all(state["markersPresent"]):
        failures.append(f"{name}: the hostile field was not exercised by this renderer")
    if not state["visible"]:
        failures.append(f"{name}: the expected rendering surface was not visible")

if failures:
    print("\nFAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)

print("\nSTORED XSS TEST PASS")
