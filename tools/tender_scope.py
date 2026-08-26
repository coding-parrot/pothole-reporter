#!/usr/bin/env python3
"""Conservative scope test for pothole-relevant road-work contracts.

Road names often appear in contracts for drains, footpaths, lights and buildings merely
to describe where that unrelated work will happen.  Those rows must never be offered as
the contract responsible for a damaged carriageway.  This module intentionally favours
omitting an uncertain contract over naming the wrong contractor.
"""

from __future__ import annotations

import re


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Work verbs which can govern a road/carriageway noun.  A verb by itself is never enough:
# the road must be its grammatical object, not a later location such as "at MG Road".
_ROAD_WORK_ACTIONS = {
    "asphalt", "asphalting", "construct", "construction", "develop", "development",
    "improve", "improvement", "improvements", "maintain", "maintenance", "patching",
    "recarpet", "recarpeting", "reconstruct", "reconstruction", "rehabilitate",
    "rehabilitation", "renew", "renewal", "repair", "repairs", "resurface", "resurfacing",
    "restore", "restoration", "strengthen", "strengthening", "tarring", "upgrade",
    "upgradation", "widen", "widening", "formation",
}
_ROAD_NOUNS = {"road", "roads", "carriageway", "carriageways"}

# If one of these occurs between a generic work verb and a later road name, the named
# road is probably the location of a different asset.  A direct list such as
# "construction of drains and roads" is handled separately as genuine mixed scope.
_NON_CARRIAGEWAY_ASSETS = {
    "arch", "arches", "barricade", "barricades", "bhavan", "bhavana", "borewell",
    "bridge", "bridges",
    "building", "buildings", "burial", "bus", "cable", "cables", "cattle", "cd",
    "camera", "cameras", "cctv", "center", "centre", "chamber", "chambers", "cistern",
    "college", "collage", "complex",
    "compound", "court", "courts", "culvert", "culverts", "deck", "dog", "dogsheltar", "drain", "drainage",
    "drains", "electrical", "fence", "fencing", "footpath", "footpaths", "garden",
    "facility", "facilities", "floor", "floors", "gantry", "gateway", "gateways",
    "graveyard", "hall", "helipad", "helipads", "hospital",
    "house", "houses",
    "kerb", "kerbs", "curb", "curbs",
    "lake", "lawn", "lawns", "light", "lighting", "lights", "machinehole", "machineholes", "manhole",
    "manholes", "mast", "masts", "median", "mh", "mhc", "network", "nursery", "park",
    "parking", "path", "paths", "pedestrian", "pipeline", "pipelines", "pipe", "pipes", "playground", "plaza", "pole", "poles",
    "pound",
    "pumphouse", "pump", "quarters", "roof", "roofs", "room", "rooms", "runway", "runways", "school",
    "sewer", "sewerage",
    "shed", "shelter", "shishuvihara", "sidewalk", "sidewalks", "sign", "signage",
    "signboard", "signboards", "slab", "sorting", "stand", "temple", "toilet", "toilets",
    "track", "tracks", "transformer", "transformers", "tree", "trees", "ugd", "unit", "urinal", "urinals",
    "utility", "utilities",
    "valve", "valves", "vending", "walkway", "walkways", "wall", "walls", "water",
}
_ROAD_MODIFIERS_THAT_ARE_NOT_SURFACE = {
    "divider", "dividers", "furniture", "light", "lighting", "lights", "marking",
    "markings", "median", "medians", "shoulder", "shoulders", "sign", "signage",
    "signboard", "signboards",
}
_ROAD_PREFIX_MODIFIERS = {
    "asphalt", "asphalted", "asphaltic", "bituminous", "bt", "cc", "cement", "concrete",
    "flexible", "internal", "link", "main", "metalled", "paver", "rigid",
}
_LOCATION_PREPOSITIONS = {
    "across", "along", "at", "behind", "beside", "in", "inside", "near", "on",
    "opposite", "within",
}

# These phrases identify carriageway treatment without relying on a generic occurrence
# of the word "road".  They are deliberately narrower than the old ingestion regex,
# which accepted drain, footpath, culvert, kerb and generic pavement work.
_EXPLICIT_ROAD_DAMAGE_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\b(?:repair(?:ing|s)?|fill(?:ing)?|patch(?:ing)?)\s+(?:of\s+)?"
    r"(?:pot\s*holes?|potholes?)\b",
    r"\b(?:pot\s*holes?|potholes?)\s+"
    r"(?:repair(?:s|ing)?|fill(?:ing)?|patch(?:ing)?|work|works)\b",
    r"\battend(?:ing)?\b.{0,48}\b(?:pot\s*holes?|potholes?)\b",
    r"\b(?:road|carriageway)\s+(?:patch(?:ing|work)?|surface\s+repair)\b",
    r"\b(?:patch(?:ing|work)?|surface\s+repair)\s+(?:of\s+)?(?:the\s+)?(?:road|carriageway)\b",
))
_SURFACE_TREATMENT_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\b(?:asphalting|re[ -]?asphalting|black[ -]?topping|tarring)\b",
    r"\b(?:resurfac(?:e|ing)|re[ -]?carpet(?:ing)?|recarpetting)\b",
    r"\b(?:dense\s+bituminous\s+macadam|bituminous\s+concrete|wet\s+mix\s+macadam)\b",
    r"\b(?:premix|pre\s*mix)\s+carpet\b|\bseal\s+coat\b",
))

_NON_CARRIAGEWAY_TREATMENT_TARGET_RE = re.compile(
    r"\b(?:asphalting|re\s+asphalting|black\s+topping|tarring|resurfacing|re\s+carpeting|"
    r"recarpeting|recarpetting|dense\s+bituminous\s+macadam|bituminous\s+concrete|"
    r"wet\s+mix\s+macadam|(?:premix|pre\s*mix)\s+carpet|seal\s+coat|"
    r"(?:pot\s*holes?|potholes?)\s+(?:repair(?:s|ing)?|filling|patching)?)\b"
    r"(?:\s+work)?\s+(?:(?:of|to|on|at|in|for|with)\s+)?(?:the\s+)?"
    r"(?:(?!roads?\b|carriageways?\b)[a-z0-9]+\s+){0,3}"
    r"(?:bridge|court|culvert|drain|floor|footpath|garden|helipad|lawn|parking|path|"
    r"playground|roof|runway|sidewalk|track|walkway|wall)s?\b"
)

_MATERIAL_PAVEMENT_RE = re.compile(
    r"\b(?:asphalt(?:ic)?|bituminous|cement\s+concrete|concrete|flexible|rigid)\s+pavement\b"
)
_HIGHWAY_ROUTE_RE = re.compile(r"^(?:nh|sh|mdr|odr)\d+$")

# A consultancy can describe construction, strengthening or resurfacing in full detail
# without procuring any physical road work.  Those titles are particularly dangerous for
# this app because their road wording otherwise looks stronger than a genuine maintenance
# notice.  Reject explicit design/advisory/inspection assignments before considering the
# carriageway phrases below.  The patterns stay narrow so EPC/design-and-build works are
# not rejected merely because the contractor must also design the road.
_NON_WORKS_SERVICE_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\bconsult(?:ant|ancy|ants|ing)\b",
    r"\b(?:authority|independent)\s+engineer(?:ing)?\b",
    r"\bproject\s+management\s+(?:consult(?:ant|ancy|ing)|services?)\b",
    r"\b(?:preparation|prepare|preparing|revision|review)\s+of\s+(?:a\s+|the\s+)?"
    r"(?:detailed\s+project\s+report|dpr)\b",
    r"\b(?:detailed\s+project\s+report|dpr)\s+(?:preparation|consultancy|services?)\b",
    r"\b(?:feasibility|traffic)\s+(?:study|studies|survey|surveys)\b",
    r"\bsurvey\s+(?:and|&)\s+investigation\b",
    r"\bthird\s+party\s+(?:inspection|quality\s+(?:audit|monitoring))\b",
    r"\b(?:quality\s+control|proof\s+checking)\s+(?:consultancy|services?)\b",
    r"\bsupply(?:ing)?\s+of\b.*\b(?:aggregate|asphalt|bitumen|cold\s+mix|stone\s+dust)\b",
))

# Vegetation beside a road is not carriageway work. Keep this narrow enough that a
# genuine mixed contract such as "construction of CC road with plantation" still passes.
_ROADSIDE_VEGETATION_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\broad\s*side\s+(?:monsoon\s+)?plantations?\b",
    r"\broadside\s+(?:monsoon\s+)?plantations?\b",
    r"\bsocial\s+forestr(?:y|ies)\b",
    r"(?:\w*plantation\w*|\w*forestr\w*).*\broad\s+side\w*\b",
    r"\broad\s+side\w*\b.*(?:\w*plantation\w*|\w*forestr\w*)",
))


def _normalise(value: object) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


def _has_direct_mixed_scope(tokens: list[str], road_index: int) -> bool:
    """True when the road is explicitly the second item in a shared work list."""
    modifier_index = road_index - 1
    while modifier_index >= 0 and tokens[modifier_index] in _ROAD_PREFIX_MODIFIERS:
        modifier_index -= 1
    if modifier_index < 1 or tokens[modifier_index] != "and":
        return False
    # "drain and CC road" is work on two assets.  "drain at Foo and Bar Road" is not:
    # the token before "and" will be a place word rather than the asset itself.
    return tokens[modifier_index - 1] in _NON_CARRIAGEWAY_ASSETS


def _is_coordinated_road_noun(tokens: list[str], road_index: int) -> bool:
    """True for explicit additions such as ``pipeline with road development``."""
    modifier_index = road_index - 1
    while modifier_index >= 0 and tokens[modifier_index] in _ROAD_PREFIX_MODIFIERS:
        modifier_index -= 1
    return modifier_index >= 0 and tokens[modifier_index] in {"and", "plus", "with"}


def _road_is_non_surface_modifier(tokens: list[str], road_index: int) -> bool:
    following = tokens[road_index + 1 : road_index + 4]
    if not following:
        return False
    if following[0] in _ROAD_MODIFIERS_THAT_ARE_NOT_SURFACE:
        return True
    # "Siddaiah Road referral hospital" and "Gadag Road graveyard" name the
    # non-road asset.  Stop at a preposition because "road from School A" still
    # describes road work with a landmark as its endpoint.
    for token in following:
        if token in {"and", "at", "from", "in", "near", "of", "on", "to", "via"}:
            break
        if token in _NON_CARRIAGEWAY_ASSETS:
            return True
    return len(following) >= 2 and following[0] == "side" and following[1] in {
        "drain", "drains", "light", "lights", "shoulder", "shoulders",
    }


def is_road_surface_contract(title: object, tender_number: object = None) -> bool:
    """Return whether the stated work clearly includes the travelled road surface.

    ``tender_number`` is accepted for a stable row-oriented API, but is intentionally not
    used as evidence.  KPPP category fragments such as ``/RD/`` have appeared on drain-only
    work, so the decision must come from the stated scope.
    """
    del tender_number
    text = _normalise(title)
    if not text:
        return False

    if any(pattern.search(text) for pattern in _NON_WORKS_SERVICE_PATTERNS):
        return False

    if any(pattern.search(text) for pattern in _ROADSIDE_VEGETATION_PATTERNS):
        return False

    tokens = text.split()
    non_surface_assets = set(tokens) & _NON_CARRIAGEWAY_ASSETS

    # A bare treatment word is not an asset classification. Real notices include
    # "resurfacing tennis court", "asphalting garden path" and "pothole repairs to
    # footpath". The old early return admitted those before seeing their actual object.
    # Mixed work must independently make the road/carriageway an explicit object below.
    if (
        any(pattern.search(text) for pattern in _EXPLICIT_ROAD_DAMAGE_PATTERNS)
        and not _NON_CARRIAGEWAY_TREATMENT_TARGET_RE.search(text)
    ):
        return True

    if (
        any(pattern.search(text) for pattern in _SURFACE_TREATMENT_PATTERNS)
        and not _NON_CARRIAGEWAY_TREATMENT_TARGET_RE.search(text)
    ):
        return True

    # Material-qualified pavement is useful evidence only when the title does not say
    # that the actual asset is a footpath, drain, bridge, etc.  Explicit road work below
    # can still admit a genuine mixed road-and-drain contract.
    if (
        _MATERIAL_PAVEMENT_RE.search(text)
        and not _NON_CARRIAGEWAY_TREATMENT_TARGET_RE.search(text)
    ):
        return True

    for road_index, token in enumerate(tokens):
        if token not in _ROAD_NOUNS or _road_is_non_surface_modifier(tokens, road_index):
            continue

        # Direct forms such as "road repair", "road development" and "road work".
        after = tokens[road_index + 1 : road_index + 4]
        if after and (after[0] in _ROAD_WORK_ACTIONS or after[0] in {"work", "works"}):
            # "pipeline due to road improvement" and "poles across road widening"
            # describe why the non-road work is happening; they do not award the road
            # work itself.  An explicit conjunction ("UGD with road development") does.
            prior_assets = set(tokens[:road_index]) & _NON_CARRIAGEWAY_ASSETS
            if not prior_assets or _is_coordinated_road_noun(tokens, road_index):
                return True

        # Work verb before the road noun, permitting names such as "Kotekani Inner Road".
        # The finite window and competing-asset check stop "construction of drain and
        # footpath at Binny Crescent Cross Road" from treating its location as its scope.
        start = max(0, road_index - 12)
        action_index = next(
            (index for index in range(road_index - 1, start - 1, -1)
             if tokens[index] in _ROAD_WORK_ACTIONS),
            None,
        )
        if action_index is not None:
            gap = tokens[action_index + 1 : road_index]
            competing = set(gap) & _NON_CARRIAGEWAY_ASSETS
            direct_scope = len(gap) <= 3
            action_object_before = (
                set(tokens[max(0, action_index - 6) : action_index])
                & _NON_CARRIAGEWAY_ASSETS
            )
            coordinated_action = action_index > 0 and tokens[action_index - 1] in {
                "and", "plus", "with",
            }
            is_location = bool(set(gap) & _LOCATION_PREPOSITIONS)
            if _has_direct_mixed_scope(tokens, road_index) or (
                not competing and not is_location
                and (not action_object_before or coordinated_action)
                and (direct_scope or not non_surface_assets)
            ):
                return True

    # Highway maintenance titles often name only an official route reference (SH-255,
    # NH 48) instead of spelling out "road".  Require a road-work action as well.
    route_tokens = {
        token for index, token in enumerate(tokens)
        if _HIGHWAY_ROUTE_RE.fullmatch(token)
        or (token in {"nh", "sh", "mdr", "odr"}
            and index + 1 < len(tokens) and tokens[index + 1].isdigit())
    }
    if route_tokens and any(token in _ROAD_WORK_ACTIONS for token in tokens):
        return not non_surface_assets

    return False


__all__ = ["is_road_surface_contract"]
