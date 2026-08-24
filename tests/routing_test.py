# -*- coding: utf-8 -*-
"""Routing must name a recipient, select the right delivery path, and refuse safely.

Runs against live OpenAI, Nominatim, KGIS, and hosted state packs: these are the answers
the production dependencies actually give today. Needs a local server on 8765 serving
android-app/www.
"""
import os, sys, base64, pathlib, json
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

KEY = os.environ["OPENAI_API_KEY"]
IMG = ROOT / "eval/images/seed/IMG20260720144404.jpg"

# name, lat, lng, expected status, expected refusal reason, delivery, authority
CASES = [
    ("Bengaluru HSR",          12.9115,  77.6427,  "draft",    None,               "email",            None),
    ("Mysuru city",            12.2958,  76.6394,  "draft",    None,               "email",            None),
    ("Hubballi-Dharwad",       15.3647,  75.1240,  "draft",    None,               "email",            None),
    ("Chikkaballapur CMC",     13.4310,  77.7270,  "draft",    None,               "email",            None),
    # 13.4355,77.7315 is on NH69. It used to be addressed to the town's Chief Officer.
    ("NH69 at Chikkaballapur", 13.4355,  77.7315,  "unrouted", "national_highway", None,               None),
    ("rural Magadi taluk",     13.0000,  77.2000,  "unrouted", "rural_road",       None,               None),
    ("Chennai GCC",            13.0827,  80.2707,  "draft",    None,               "official_handoff", "tn-gcc"),
    ("Hyderabad CURE",         17.3616,  78.4747,  "draft",    None,               "official_handoff", "tg-cure-shared"),
    # Ahmedabad routing is a local point-in-polygon check against the pinned 48-ward union.
    ("Ahmedabad AMC",          23.0225,  72.5714,  "draft",    None,               "official_handoff", "gj-amc"),
    ("no GPS",                 None,     None,     "unrouted", "no_location",       None,               None),
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
           authority: r.authority_id, channel: r.delivery_channel,
           email: r.officer_email, handoff: r.handoff_url,
           tender: r.tender_number, blocked };
}"""

fails = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    context = b.new_context(viewport={"width": 390, "height": 844})
    # Hyderabad's official TGRAC service is deliberately native-only. Simulate the
    # Capacitor shell while keeping immutable state packs local to the release fixture.
    context.add_init_script("window.Capacitor={isNativePlatform:()=>true,Plugins:{}};")
    manifest = json.loads((ROOT / "static" / "pack-manifest-v1.27.json").read_text())
    local_packs = {
        resource["url"]: (ROOT / "docs" / resource["path"]).read_bytes()
        for resource in manifest["resources"].values()
    }

    def serve_state_pack(route):
        body = local_packs.get(route.request.url)
        if body is None:
            route.continue_()
        else:
            route.fulfill(status=200, content_type="application/json", body=body)

    context.route(
        "https://coding-parrot.github.io/pothole-reporter/packs/v1/states/**",
        serve_state_pack,
    )
    pg = context.new_page()
    open_app(pg, KEY)
    src = base64.standard_b64encode(IMG.read_bytes()).decode()
    for name, lat, lng, want, reason, delivery, authority in CASES:
        r = pg.evaluate(POST, [src, lat, lng])
        print(f"  {name:24} {r['status']:9} {str(r['reason'] or ''):20} "
              f"{str(r['channel'] or ''):18} {str(r['officer'] or '')[:34]}")
        if r["status"] != want:
            fails.append(f"{name}: expected {want}, got {r['status']}")
        if reason and r["reason"] != reason:
            fails.append(f"{name}: expected reason {reason}, got {r['reason']}")
        if want == "unrouted":
            if r["email"]:  fails.append(f"{name}: named a recipient it should have refused")
            if r["handoff"]: fails.append(f"{name}: offered a handoff it should have refused")
            if r["tender"]: fails.append(f"{name}: named a contract outside coverage")
            if r["blocked"] == "NOT BLOCKED": fails.append(f"{name}: send was not blocked")
            # the block message must name the actual reason, not a generic one
            if r["blocked"] and "outside the area" in r["blocked"] and reason != "outside_area":
                fails.append(f"{name}: block message blames coverage for a {reason}")
        else:
            if not r["officer"]: fails.append(f"{name}: routable point named no recipient")
            if r["channel"] != delivery:
                fails.append(f"{name}: expected delivery {delivery}, got {r['channel']}")
            if authority and r["authority"] != authority:
                fails.append(f"{name}: expected authority {authority}, got {r['authority']}")
            if delivery == "email" and not r["email"]:
                fails.append(f"{name}: email route has no verified address")
            if delivery == "official_handoff":
                if r["email"]:
                    fails.append(f"{name}: official handoff unexpectedly named an email recipient")
                if not str(r["handoff"] or "").startswith("https://"):
                    fails.append(f"{name}: official handoff has no verified HTTPS URL")
                if r["tender"]:
                    fails.append(f"{name}: non-Karnataka handoff unexpectedly named a contract")
    b.close()

if fails:
    print("\nFAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("\nROUTING TEST PASS")
