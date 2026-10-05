#!/usr/bin/env python3
"""Build a source-backed, explicitly provisional tender regression set.

Labels come from explicit work clauses, NOT matcher output. Addresses are derived
from procurement text, not sampled GPS reports; this is not geographic coverage.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/tenders-karnataka.json"
POSITIVE = re.compile(r"^(?:Construction of (?:CC |Cement Concrete )?roads? (?:from|at|in)|(?:Asphalting|Resurfacing|Re-asphalting|Re-surfacing) of (?:the )?roads? (?:at|in|from)|(?:Improvement|Improvements) of roads? (?:at|in|from))\b", re.I)
NEGATIVE = re.compile(r"^(?:Construction (?:of )?(?:(?:CC|RCC|concrete|storm water|box|earthern|a) )?drain|(?:Providing and laying|Laying of).{0,40}(?:water|pipeline)|(?:Construction of Public Toilet|Renovation Of Existing Shed|Repair and Repainting of RCC GLSR Water Tank))", re.I)
MIXED_OR_UNCLEAR = re.compile(r"asphalt|paver|interlock|paving|(?:and|&) roads?\b|(?:CC|BT|concrete) roads?|(?:construction|improvement|development|repair|maintenance) of roads?", re.I)


def town(row):
    return re.sub(r"^(?:DMA|BBMP)\s*(?:City Corporation|City Municipal Council|Town Municipal Council|Town Panchayat|TP)?\s*", "", row["loc"], flags=re.I).strip()


def address(row, positive):
    clause = row["t"]
    if positive:
        clause = POSITIVE.sub("", clause).strip()
        clause = re.split(r"\b(?:construction|laying|providing)\b|\s*&\s*compound", clause, maxsplit=1, flags=re.I)[0].rstrip(" ,")
        place = clause
    else:
        parts = re.split(r"\b(?:at|from|near|behind|in|opposite)\b", clause, maxsplit=1, flags=re.I)
        place = parts[-1].strip() if len(parts) > 1 else town(row)
    place = re.sub(r"\([^)]*\)|\([^)]*$", "", place).strip(" .,;")
    # Keep a location-sized query, not the work-description sentence.
    place = " ".join(place.split()[:12])
    ward = re.search(r"\b(?:ward\s*(?:no\.?|number)?|wn)\s*[-.]?\s*(\d+)", clause, re.I)
    return ", ".join(x for x in [place, f"Ward {ward[1]}" if ward else None, town(row), "Karnataka"] if x)


def candidate(row):
    return {"tender_number": row["tn"], "title": row["t"], "location": row["loc"],
            "contractor": "", "published": row.get("d", ""), "body_lgd": row["b"]}


def choose(rows, predicate, count):
    selected, bodies = [], set()
    for row in rows:
        if row.get("b") and row["b"] not in bodies and predicate(row):
            selected.append(row)
            bodies.add(row["b"])
            if len(selected) == count:
                break
    if len(selected) != count:
        raise ValueError(f"Only {len(selected)} distinct bodies; need {count}")
    return selected


def build():
    raw = SOURCE.read_bytes()
    rows = json.loads(raw)
    positives = choose(rows, lambda r: bool(POSITIVE.search(r["t"])) and not re.search(r"\bSWM\b", r["t"], re.I), 60)
    negatives = choose(rows, lambda r: bool(NEGATIVE.search(r["t"])) and not MIXED_OR_UNCLEAR.search(r["t"]), 50)
    cases = list(json.loads((ROOT / "eval/tender_cases.json").read_text())["cases"])
    for positive, targets in [(True, positives), (False, negatives)]:
        for row in targets:
            # Same-body non-surface distractors preserve the production LLM premise.
            distractors = [r for r in rows if r.get("b") == row["b"] and r["tn"] != row["tn"]
                           and NEGATIVE.search(r["t"]) and not MIXED_OR_UNCLEAR.search(r["t"])][:2]
            cases.append({
                "id": ("source-road-" if positive else "source-nonroad-") + row["tn"].replace("/", "-"),
                "address": address(row, positive), "body_lgd": row["b"],
                "expected_tender_number": row["tn"] if positive else None,
                "labelled_by": "assistant_source_clause_review",
                "label_status": "provisional_not_human_sealed",
                "rationale": "Explicit road-work clause covers the derived location." if positive else "The stated work is only a drain, water utility, toilet, shed or tank; a road name is its location, not road-work scope.",
                "tags": ["positive" if positive else "negative", "source_backed", "derived_address", "same_body_candidates"],
                "source_evidence": {"path": "data/tenders-karnataka.json", "sha256": hashlib.sha256(raw).hexdigest(), "tender_number": row["tn"], "title": row["t"], "location": row["loc"]},
                "candidates": [candidate(row), *map(candidate, distractors)],
            })
    assert len(cases) == 120
    return {"version": 2, "label_policy": "Explicit road-work scope and stated location must both agree. No output-derived labels; contractor identities are deliberately blanked because the mirror does not verify awards.",
            "limitations": ["110 distinct source-backed target records plus 10 original regressions", "Derived addresses and controlled pools; not random places or live GPS lookups", "Assistant-reviewed provisional labels; no population accuracy or verified contractor-liability claim"], "cases": cases}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
