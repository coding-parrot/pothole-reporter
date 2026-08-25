#!/usr/bin/env python3
"""Stamp each eligible municipal road contract with its awarding body's LGD code.

The app already knows which local body contains a pothole: the state GIS returns its LGD
code. Stamping the same code on each contract turns the shortlist into a lookup instead of
a scan of every municipal contract in Karnataka.

Resolution goes through the state's own roster of all 319 urban local bodies
(data/karnataka-towns.json), NOT through the 182 bodies we hold an address for. Matching
against the shorter list is what produced the Kanakapura bug: Kanakapura has no published
address, so it was absent from the list and the nearest available name was Khanapura, a
different town 500 km away in another district, and 45 of its contracts were stamped to it.
Against the full roster, Kanakapura matches Kanakapura and simply ends up unstamped.

A match must also beat the runner-up by a margin, so near-ties (Belur and Belluru,
Chincholi and Chinchali) are left unstamped rather than guessed.

Body matching and work-scope matching are independent gates.  A drain or footpath tender
can belong to the correct body and still be ineligible for a pothole complaint.
"""
import json, re, difflib, pathlib

from state_pack_tools import publish_resource
from tender_scope import is_road_surface_contract

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_RETRIEVED_AT = "2026-08-21"  # update when data/tenders-karnataka.json is refreshed
rows = json.load(open(ROOT / "data/tenders-karnataka.json"))
rows = rows if isinstance(rows, list) else rows.get("tenders", [])
towns = json.load(open(ROOT / "data/karnataka-towns.json"))["towns"]
bodies = json.load(open(ROOT / "data/karnataka-bodies.json"))["bodies"]

BLR = "BLR"
BLR_CODES = {c for c, b in bodies.items()
             if any(w in b["name"].lower() for w in ("bengaluru", "bangalore"))}

VAR = [("dharawada","dharwad"),("hubballi","hubli"),("bengaluru","bangalore"),
       ("mysuru","mysore"),("belagavi","belgaum"),("kalaburagi","gulbarga"),
       ("ballari","bellary"),("vijayapura","bijapur"),("shivamogga","shimoga"),
       ("tumakuru","tumkur"),("uu","u"),("oo","o"),("aa","a"),("ee","i"),
       ("th","t"),("dh","d"),("bh","b"),("kh","k"),("v","w"),("z","j")]
STRIP = ["dma","bbmp","city corporation","city municipal council","town municipal council",
         "town panchayat","municipal corporation","nagara panchayat","corporation","council"]
MIN_RATIO, MIN_MARGIN = 0.90, 0.03

def norm(s):
    s = re.sub(r"[^a-z]", "", (s or "").lower())
    for a, b in VAR: s = s.replace(a, b)
    return s.rstrip("u") or s          # a trailing -u is the commonest spelling difference

def body_part(loc):
    low = (loc or "").lower()
    for p in STRIP: low = low.replace(p, " ")
    return " ".join(low.split())

exact = {}
for t in towns: exact.setdefault(norm(t["name"]), []).append(t)
keys = list(exact)

def resolve(loc):
    """LGD code for this contract location, or None. Never guesses."""
    n = norm(body_part(loc))
    if n in exact:
        c = exact[n]
        return str(c[0]["lgd"]) if len(c) == 1 and c[0]["lgd"] else None
    near = difflib.get_close_matches(n, keys, n=2, cutoff=MIN_RATIO)
    if not near: return None
    best = difflib.SequenceMatcher(None, n, near[0]).ratio()
    if len(near) > 1:
        second = difflib.SequenceMatcher(None, n, near[1]).ratio()
        if best - second < MIN_MARGIN: return None      # too close to call
    c = exact[near[0]]
    return str(c[0]["lgd"]) if len(c) == 1 and c[0]["lgd"] else None

cache, stats = {}, {"mapped":0, "blr":0, "no_address":0, "unresolved":0}
for r in rows:
    agency = (r.get("tn") or "").split("/")[0].upper()
    if agency not in ("DMA", "BBMP"):
        r.pop("b", None); continue
    loc = (r.get("loc") or "").strip()
    if loc not in cache:
        cache[loc] = BLR if agency == "BBMP" else resolve(loc)
    code = cache[loc]
    if code == BLR:
        stats["blr"] += 1; r["b"] = BLR
    elif code and code in bodies and bodies[code].get("email"):
        stats["mapped"] += 1; r["b"] = code
    else:
        stats["no_address" if code else "unresolved"] += 1; r.pop("b", None)

with (ROOT / "data/tenders-karnataka.json").open("w", encoding="utf-8") as output:
    json.dump(rows, output, ensure_ascii=False, separators=(",", ":"))
    output.write("\n")
body_rows = [row for row in rows if row.get("b")]
published_rows = [
    row for row in body_rows
    if is_road_surface_contract(row.get("t"), row.get("tn"))
]
_, pack_output = publish_resource(
    "in-ka-tenders",
    published_rows,
    source_retrieved_at=SOURCE_RETRIEVED_AT,
)
print(f"stamped              {stats['mapped']}")
print(f"legacy BBMP ({BLR})   {stats['blr']}")
print(f"body has no address  {stats['no_address']}  (never citable, correctly unstamped)")
print(f"unresolved location  {stats['unresolved']}")
print(f"non-road scope       {len(body_rows) - len(published_rows)}")
print(f"published records    {len(published_rows)}")
print(pack_output.relative_to(ROOT))
print("static/pack-manifest-v1.31.json")
print("android-app/www/pack-manifest-v1.31.json")
