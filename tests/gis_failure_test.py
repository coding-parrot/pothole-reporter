# -*- coding: utf-8 -*-
"""When the state GIS cannot answer, the app must refuse, not fall back to Bengaluru.

The highway gate only runs inside kgisJurisdiction. Any path that routes without it can
address a municipal officer for a road NHAI owns, which is the bug the gate exists to
prevent. This blocks the KGIS host at the network layer and checks what comes out.
"""
import os, sys, base64, pathlib
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

KEY = os.environ["OPENAI_API_KEY"]
IMG = ROOT / "eval/images/seed/IMG20260720144404.jpg"

# Inside the app's Bengaluru bounding box, and on a national highway. Both must refuse.
CASES = [
    ("NH48 at Nelamangala", 13.094709, 77.389412),
    ("Bengaluru HSR",       12.9115,   77.6427),
]

POST = """async ([b64, lat, lng]) => {
  await StandaloneAPI.handle('/api/reports', {method:'DELETE'});
  const bin=atob(b64); const a=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)a[i]=bin.charCodeAt(i);
  const fd=new FormData();
  fd.append('photo', new Blob([a],{type:'image/jpeg'}),'p.jpg');
  fd.append('lat',String(lat)); fd.append('lng',String(lng));
  const r=await StandaloneAPI.handle('/api/report',{method:'POST',body:fd});
  return { status:r.status, reason:r.unrouted_reason, email:r.officer_email,
           subject:r.email_subject };
}"""

fails = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security","--allow-running-insecure-content"])
    ctx = b.new_context(viewport={"width":390,"height":844})
    # Every call to the state GIS fails, as it would on a bad connection.
    ctx.route("**://kgis.ksrsac.in/**", lambda route: route.abort())
    pg = ctx.new_page()
    open_app(pg, KEY)
    src = base64.standard_b64encode(IMG.read_bytes()).decode()
    for name, lat, lng in CASES:
        r = pg.evaluate(POST, [src, lat, lng])
        print(f"  {name:22} status={r['status']:9} reason={str(r['reason'] or '-'):20} email={r['email'] or '-'}")
        if r["email"]:
            fails.append(f"{name}: named {r['email']} with the GIS unreachable, so the highway check never ran")
        if r["subject"]:
            fails.append(f"{name}: drafted a sendable complaint with no verified jurisdiction")
        if r["status"] != "unrouted":
            fails.append(f"{name}: status {r['status']}, expected unrouted")
    b.close()

# ArcGIS reports failures as HTTP 200 with an error body and no features array. Defaulting
# that to an empty array reads as "no highway here", which is the gate failing open: the
# exact bug it exists to prevent, arriving through a different door.
print("\n  with the GIS answering 200 but with an error body:")
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    ctx.route("**://kgis.ksrsac.in/**", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"error":{"code":500,"message":"Unable to complete operation."}}'))
    pg = ctx.new_page()
    open_app(pg, KEY)
    src = base64.standard_b64encode(IMG.read_bytes()).decode()
    for name, lat, lng in CASES:
        r = pg.evaluate(POST, [src, lat, lng])
        print(f"    {name:22} status={r['status']:9} reason={str(r['reason'] or '-'):20} email={r['email'] or '-'}")
        if r["email"]:
            fails.append(f"{name}: named {r['email']} when the GIS returned an error body, so the highway check failed open")
        if r["status"] != "unrouted":
            fails.append(f"{name}: status {r['status']} on an error body, expected unrouted")
    b.close()

if fails:
    print("\nFAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("\nGIS FAILURE TEST PASS")
