# -*- coding: utf-8 -*-
"""The same pothole must name the same contract every time.

A complaint names a real company. Naming a different one on each run is not a cosmetic
issue: it means the app cannot justify the one it printed.

Two things are checked separately, because they have different fixes:
  1. The shortlist. Given the same address and body, the ranked candidate list must be
     byte-identical every time. This is entirely local and must be deterministic.
  2. The final pick, over repeated live runs.
"""
import os, sys, json, base64, collections, pathlib
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

KEY = os.environ["OPENAI_API_KEY"]
RUNS = int(os.environ.get("TENDER_RUNS", "6"))
ADDR = "17th Main Road, Sector 3, HSR Layout, Bengaluru, 560102"
LGD = "305852"   # Bengaluru South, which draws on the legacy BBMP pool

fails = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    open_app(pg, KEY)
    pg.wait_for_function("typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure", timeout=30000)

    # 1. The shortlist must be identical across repeated builds.
    lists = pg.evaluate("""async ([addr, lgd, n]) => {
      const out = [];
      for (let i = 0; i < n; i++) out.push(await StandaloneAPI.__pure.shortlistFor(addr, lgd));
      return out;
    }""", [ADDR, LGD, 5])
    sigs = {json.dumps(l) for l in lists}
    print(f"  shortlist built 5 times: {len(sigs)} distinct result(s)")
    if len(sigs) != 1:
        fails.append(f"the shortlist is not deterministic: {len(sigs)} different orderings")
    if lists and lists[0]:
        top = lists[0][0]
        tied = sum(1 for x in lists[0] if abs(x["score"] - top["score"]) < 1e-9)
        print(f"  {len(lists[0])} candidates, {tied} tied at the top score {top['score']:.3f}")
        print(f"  first three: {[x['tn'] for x in lists[0][:3]]}")

    # 2. The end-to-end pick over live runs.
    picks = []
    for i in range(RUNS):
        r = pg.evaluate("""async ([addr, lgd]) => {
          const t = await StandaloneAPI.__pure.matchTenderFor(addr, lgd);
          return t ? t.tender_number : null;
        }""", [ADDR, LGD])
        picks.append(r)
    b.close()

counts = collections.Counter(picks)
print(f"\n  {RUNS} live runs -> {len(counts)} distinct contract(s):")
for tn, n in counts.most_common():
    print(f"    {n}x  {tn}")
if len(counts) > 1:
    fails.append(f"the same pothole named {len(counts)} different contracts across {RUNS} runs")

print()
if fails:
    print("FAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("TENDER DETERMINISM TEST PASS")
