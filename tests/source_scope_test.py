# -*- coding: utf-8 -*-
"""One routing source failing must never erase the answers the other sources can give.

Reported from Gandhinagar on 30 Sep 2026 as "could not check whether this road is a
national, state, or district highway, try again when you have a signal", on a phone with
full 5G. That was one symptom of a general fault, and the general fault is what this file
guards.

Every routing source in this app is SCOPED: the shared KGIS resolver to Karnataka, each
state pack to its state, each National Highway tile to its two-degree box. The discipline
that makes that safe, already implemented in pinnedStateRoute, is:

    decide scope from local geometry  ->  only then touch the network

Where that order is inverted, or where a source's failure is RETURNED rather than held,
the source's silence becomes indistinguishable from its verdict, and one transient CDN
blip refuses a report that four healthy sources could have routed. Measured before the
fix: aborting only the highway tiles refused Bengaluru, Ahmedabad, Chennai and Patna
alike, every one of them with the message telling the user to find a better signal.

The coordinates are derived from each routing pack's own geometry, so a pack added later
is exercised automatically. A pack whose interior cannot be derived fails the run rather
than being skipped quietly.
"""
import json, sys, pathlib
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from browser_test_utils import open_app, block_openai, OFFLINE_KEY
from state_pack_utils import load_manifest, pack_path

fails = []


def rings(geometry):
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    out = []
    for polygon in geometry["coordinates"]:
        out.extend(polygon)
    return out


def inside(x, y, ring):
    hit = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                hit = not hit
    return hit


def interior_point(geometry):
    """A point comfortably inside the largest ring, not merely inside its bounding box."""
    outer = max(rings(geometry), key=len)
    xs = [point[0] for point in outer]
    ys = [point[1] for point in outer]
    for fraction_y in (0.5, 0.4, 0.6, 0.35, 0.65, 0.45, 0.55):
        for fraction_x in (0.5, 0.4, 0.6, 0.45, 0.55, 0.35, 0.65):
            x = min(xs) + (max(xs) - min(xs)) * fraction_x
            y = min(ys) + (max(ys) - min(ys)) * fraction_y
            if inside(x, y, outer):
                return round(y, 5), round(x, 5)
    return None


def envelope_point(envelope):
    return (round((envelope["min_lat"] + envelope["max_lat"]) / 2, 5),
            round((envelope["min_lng"] + envelope["max_lng"]) / 2, 5))


def candidates(payload):
    """Every way a pack declares where it applies, best evidence first."""
    region = payload.get("region")
    if isinstance(region, dict) and region.get("geometry"):
        yield region.get("name") or "region", interior_point(region["geometry"])
    regions = payload.get("regions")
    values = regions.values() if isinstance(regions, dict) else (regions or [])
    for item in values:
        if not isinstance(item, dict):
            continue
        label = item.get("name") or item.get("id") or "region"
        if item.get("geometry"):
            yield label, interior_point(item["geometry"])
        elif isinstance(item.get("envelope"), dict):
            # Packs that route by an official point query or a structured geocode carry a
            # declared envelope instead of bundled geometry. The envelope is the scope.
            yield label, envelope_point(item["envelope"])


# in-ka-routing is the LGD address registry, keyed by the code KGIS returns rather than by
# a coordinate, so it has no scope of its own to derive a point from. It is listed here so
# a pack that loses its geometry by accident still fails the run.
NO_OWN_SCOPE = {"in-ka-routing"}


# Every routing pack in the shipped catalogue, with a point derived from its own geometry.
GRID = {}
uncovered = []
for pack_id, resource in sorted(load_manifest()["resources"].items()):
    if resource.get("kind") != "routing":
        continue
    if pack_id in NO_OWN_SCOPE:
        continue
    payload = json.loads(pack_path(pack_id).read_text(encoding="utf-8"))["payload"]
    found = None
    for name, point in candidates(payload):
        if point:
            found = (f"{pack_id} ({name})", point)
            break
    if found:
        GRID[found[0]] = found[1]
    else:
        uncovered.append(pack_id)

# A pack's interior point usually lands in open country, where the answer is the statewide
# handoff. The municipal recipients are the ones the road class check actually protects,
# and a veto that leaked past them went unseen until these were added: the KGIS answer for
# Bengaluru returned straight out of routeOfficer, past the point every other route
# converges on. Named cities, so the grid always exercises both kinds of recipient.
GRID.update({
    "Bengaluru (KGIS municipal)": (12.9716, 77.5946),
    "Ahmedabad (AMC)": (23.0225, 72.5714),
    "Chennai (GCC)": (13.0827, 80.2707),
    "Mumbai (MCGM)": (19.0760, 72.8777),
    "Delhi (PWD Sewa)": (28.6139, 77.2090),
    "Kolkata (KMC)": (22.5726, 88.3639),
    "Hyderabad (GHMC)": (17.3850, 78.4867),
    "Gandhinagar (the reported point)": (23.181854, 72.652801),
})

if uncovered:
    fails.append("no interior point could be derived for: %s" % ", ".join(uncovered))
if len(GRID) < 35:
    fails.append("the grid covers only %d routing packs; the catalogue has more" % len(GRID))

# Far outside every source's scope. No source can answer for these, so no source may be
# blamed for not answering, and no amount of network failure may change the verdict.
OUTSIDE = {
    "Colombo, Sri Lanka": (6.9271, 79.8612),
    "Kathmandu, Nepal": (27.7172, 85.3240),
    "Indian Ocean": (2.0, 75.0),
}

# Reasons that tell the user to try again. Using one of these for a point that no source
# was ever going to answer is the fault this file exists to prevent.
RETRYABLE = {"road_class_unknown", "jurisdiction_unavailable", "road_class_unavailable"}


def verdicts(page, points):
    return page.evaluate(
        """async (points) => {
          const P = StandaloneAPI.__pure;
          const out = {};
          for (const [label, [lat, lng]] of Object.entries(points)) {
            try {
              const route = await P.routeOfficer(
                null, lat, lng, 12, null, null, "road_damage");
              out[label] = { routed: !!route.routed,
                             reason: route.unrouted_reason || null,
                             who: route.officer_email || route.authority_id || null };
            } catch (error) { out[label] = { threw: String(error && error.message) }; }
          }
          return out;
        }""", points)


def scenario(browser, points, wire=None):
    page = browser.new_page()
    leaks = []
    open_app(page, OFFLINE_KEY)
    block_openai(page, leaks)
    fetched = []
    page.route("**/packs/v1/**", lambda route: (fetched.append(route.request.url),
                                                route.continue_()))
    if wire:
        wire(page)
    try:
        return verdicts(page, points), fetched
    finally:
        page.close()


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    try:
        # A: every source healthy. This is the answer the user is entitled to.
        healthy, _ = scenario(browser, GRID)
        for label, result in healthy.items():
            if result.get("threw"):
                fails.append("%s threw with every source healthy: %s"
                             % (label, result["threw"]))
        municipal = [label for label, result in healthy.items()
                     if result.get("routed")
                     and not str(result.get("who") or "").endswith("-statewide-unverified")]
        if len(municipal) < 5:
            fails.append("only %d municipal recipients in the grid; the veto below would "
                         "pass without ever being exercised" % len(municipal))

        # B: only the National Highway tiles are unreachable. Every other source is up,
        # so every verdict that did not depend on a tile must be unchanged.
        def kill_highways(page):
            page.route("**/packs/v1/highways/**", lambda route: route.abort())

        degraded, _ = scenario(browser, GRID, kill_highways)
        for label, expected in healthy.items():
            actual = degraded.get(label, {})
            is_municipal = expected.get("routed") and not str(
                expected.get("who") or "").endswith("-statewide-unverified")
            if is_municipal:
                # SAFETY. Naming a city officer for what may be a national highway is the
                # one harm the road class check exists to prevent, so a map that will not
                # download must veto a municipal recipient. This must be REQUIRED, not
                # merely permitted: written the other way round, the check passed while
                # the KGIS answer for Bengaluru returned straight past the veto.
                if actual.get("routed"):
                    fails.append(
                        "%s still named a municipal recipient (%s) while the highway map "
                        "was unreachable" % (label, actual.get("who")))
                elif actual.get("reason") != "road_class_unavailable":
                    fails.append(
                        "%s refused with %r rather than naming the map that was missing"
                        % (label, actual.get("reason")))
                continue
            # AVAILABILITY. Every other verdict did not depend on a tile, so it must not
            # have moved. This is what refused all of India before the fix.
            if actual != expected:
                fails.append(
                    "%s changed when only the highway tiles were unreachable: "
                    "healthy=%s degraded=%s" % (label, expected, actual))

        # C: one transient blip per remote pack, then success. A single dropped request is
        # the commonest thing that happens on a phone and must cost nothing.
        def blip_once(page):
            seen = set()
            def handler(route):
                url = route.request.url
                if url not in seen:
                    seen.add(url)
                    route.abort()
                    return
                route.continue_()
            page.route("**/packs/v1/**", handler)

        flaky, _ = scenario(browser, GRID, blip_once)
        for label, expected in healthy.items():
            actual = flaky.get(label, {})
            if actual != expected:
                fails.append("%s changed after a single dropped pack request: "
                             "healthy=%s flaky=%s" % (label, expected, actual))

        # D and E: outside every source's scope, with every remote source dead. The
        # verdict must be definitive and must not send the user back to their signal, and
        # no source may be contacted about a point it does not cover.
        def kill_everything(page):
            page.route("**/packs/v1/**", lambda route: route.abort())

        outside, fetched_outside = scenario(browser, OUTSIDE, kill_everything)
        for label, result in outside.items():
            if result.get("routed"):
                fails.append("%s was routed to an authority: %s" % (label, result))
            if result.get("reason") in RETRYABLE:
                fails.append("%s blamed a source that does not cover it: %s"
                             % (label, result["reason"]))
        for url in fetched_outside:
            if "/highways/" in url:
                continue  # the tile index legitimately spans the whole country
            fails.append("a pack was requested for a point outside every scope: %s" % url)
    finally:
        browser.close()

if fails:
    print("FAIL")
    for line in fails[:25]:
        print("   ", line)
    if len(fails) > 25:
        print("    ... and %d more" % (len(fails) - 25))
    sys.exit(1)
print("PASS %d routing packs: no source failure erases another source's answer"
      % len(GRID))
