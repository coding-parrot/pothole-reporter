# -*- coding: utf-8 -*-
"""Routing must name the right officer, and refuse for the right reason.

Runs against live KGIS: these are the answers the state actually gives today, which is
the only version that matters. Needs a local server on 8765 serving android-app/www.
"""
import os, sys, base64, pathlib
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

KEY = os.environ["OPENAI_API_KEY"]
IMG = ROOT / "eval/images/seed/IMG20260720144404.jpg"

# name, lat, lng, expected status, expected refusal reason
CASES = [
    ("Bengaluru HSR",          12.9115,  77.6427,  "draft",    None),
    ("Mysuru city",            12.2958,  76.6394,  "draft",    None),
    ("Hubballi-Dharwad",       15.3647,  75.1240,  "draft",    None),
    ("Chikkaballapur CMC",     13.4310,  77.7270,  "draft",    None),
    # 13.4355,77.7315 is on NH69. It used to be addressed to the town's Chief Officer.
    ("NH69 at Chikkaballapur", 13.4355,  77.7315,  "unrouted", "national_highway"),
    ("rural Magadi taluk",     13.0000,  77.2000,  "unrouted", "rural_road"),
    ("Chennai, out of state",  13.0827,  80.2707,  "unrouted", "outside_area"),
    ("no GPS",                 None,     None,     "unrouted", "no_location"),
]

POST = """async ([b64, lat, lng]) => {
  await StandaloneAPI.handle('/api/reports', {method:'DELETE'});
  const bin = atob(b64); const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const fd = new FormData();
  fd.append('photo', new Blob([arr], {type:'image/jpeg'}), 'p.jpg');
  if (lat !== null) { fd.append('lat', String(lat)); fd.append('lng', String(lng)); }
  const r = await StandaloneAPI.handle('/api/report', {method:'POST', body: fd});
  let blocked = null;
  if (r.status === 'unrouted') {
    try { await StandaloneAPI.handle('/api/reports/' + r.id + '/send', {method:'POST'}); blocked = 'NOT BLOCKED'; }
    catch (e) { blocked = e.message; }
  }
  return { status: r.status, reason: r.unrouted_reason, officer: r.officer_name,
           email: r.officer_email, tender: r.tender_number, blocked };
}"""

fails = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    open_app(pg, KEY)
    src = base64.standard_b64encode(IMG.read_bytes()).decode()
    for name, lat, lng, want, reason in CASES:
        r = pg.evaluate(POST, [src, lat, lng])
        print(f"  {name:24} {r['status']:9} {str(r['reason'] or ''):20} {str(r['officer'] or '')[:34]}")
        if r["status"] != want:
            fails.append(f"{name}: expected {want}, got {r['status']}")
        if reason and r["reason"] != reason:
            fails.append(f"{name}: expected reason {reason}, got {r['reason']}")
        if want == "unrouted":
            if r["email"]:  fails.append(f"{name}: named a recipient it should have refused")
            if r["tender"]: fails.append(f"{name}: named a contract outside coverage")
            if r["blocked"] == "NOT BLOCKED": fails.append(f"{name}: send was not blocked")
            # the block message must name the actual reason, not a generic one
            if r["blocked"] and "outside the area" in r["blocked"] and reason != "outside_area":
                fails.append(f"{name}: block message blames coverage for a {reason}")
        else:
            if not r["email"]: fails.append(f"{name}: routable point named no officer")
    # A Chief Officer for a council, a Commissioner for a corporation.
    b.close()

if fails:
    print("\nFAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("\nROUTING TEST PASS")
