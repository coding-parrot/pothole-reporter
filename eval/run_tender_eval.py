#!/usr/bin/env python3
"""Evaluate production tender shortlisting and model adjudication on labelled cases."""

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "llm" / "generated" / "contract.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text())
PROMPT = CONTRACT["prompts"]["tender"]
CONFIG = CONTRACT["config"]["tender"]
RUNTIME = CONTRACT["config"]["runtime"]
API = RUNTIME["responsesUrl"]

if RUNTIME["storeResponses"] is not False:
    raise RuntimeError("Tender eval refuses to run unless production storeResponses is false.")


def sha(value):
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def load_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key
    sys.exit("OPENAI_API_KEY not set in the environment or repository .env")


def production_shortlists(cases):
    """Call the Worker-exported shortlist function so the eval cannot drift."""
    script = """
import fs from 'node:fs';
import { __test } from './server/src/index.js';
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(rows.map((row) =>
  __test.tenderShortlist(row.address, row.candidates))));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("production tender shortlist failed: " + completed.stderr[:500])
    return json.loads(completed.stdout)


def aws_predictions(cases):
    """Invoke the actual AWS matcher, not the retired Worker's LLM shortlist."""
    script = """
import fs from 'node:fs';
import { matchTender } from './infra/aws-central/service/tenders.mjs';
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => matchTender(c.address, c.candidates))));
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script],
                            cwd=ROOT, input=json.dumps(cases), text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def run_aws(cases, args, fixture, fixture_bytes):
    jobs = []
    for case in cases:
        for trial in range(args.trials):
            candidates = list(case["candidates"])
            random.Random(f'{case["id"]}:{trial}').shuffle(candidates)
            jobs.append(({**case, "candidates": candidates}, trial))
    predictions = aws_predictions([case for case, _ in jobs])
    rows = []
    for (case, trial), result in zip(jobs, predictions):
        predicted = (result.get("tender") or {}).get("tender_number")
        rows.append({"case_id": case["id"], "trial": trial,
                     "expected_tender_number": case.get("expected_tender_number"),
                     "predicted_tender_number": predicted, "reason": result.get("reason"),
                     "backend": "aws_deterministic", "result": result})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    predicted = {key: majority_prediction(value) for key, value in grouped.items()}
    scores = metrics([(c.get("expected_tender_number"), predicted[c["id"]]) for c in cases])
    unstable = [key for key, group in grouped.items()
                if len({r["predicted_tender_number"] for r in group}) > 1]
    summary = {"backend": "aws_deterministic", "case_count": len(cases),
               "event_grouped_case_level": scores, "case_predictions": predicted,
               "candidate_order_instability": unstable,
               "warning": "Controlled candidate-pool regression, NOT live geographic coverage.",
               "all_null_baseline": metrics([(c.get("expected_tender_number"), None) for c in cases]),
               "failures": [r for r in rows if r["trial"] == 0 and r["expected_tender_number"] != r["predicted_tender_number"]]}
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "git_commit": git_commit(),
                "cases_sha256": sha(fixture_bytes), "backend": "aws_deterministic",
                "matcher_sha256": sha((ROOT / "infra/aws-central/service/tenders.mjs").read_bytes()),
                "scope_sha256": sha((ROOT / "infra/aws-central/service/tender-scope.mjs").read_bytes()),
                "trials_per_case": args.trials, "case_count": len(cases),
                "label_policy": fixture.get("label_policy"), "limitations": fixture.get("limitations", [])}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"cases": len(cases), "backend": "aws_deterministic", **scores}, indent=2))
    failed = (scores["precision"] is None or scores["precision"] < args.min_precision
              or scores["recall"] is None or scores["recall"] < args.min_recall or bool(unstable)
              or scores["wrong_contract"] > args.max_wrong_contracts
              or scores["negative_false_match"] > args.max_negative_false_matches)
    print("TENDER EVAL FAIL" if failed else "TENDER EVAL PASS")
    return 1 if failed and not args.no_gate else 0


def clipped(value, limit):
    return str(value or "")[:limit]


def tender_user_input(address, candidates):
    limits = CONFIG["stringLimits"]
    data = {
        "reverse_geocoded_address": clipped(address, limits["address"]),
        "candidates": [
            {
                "match_index": index,
                "work_description": clipped(item.get("title"), limits["workDescription"]),
                "division_or_location": clipped(
                    item.get("location"), limits["divisionOrLocation"]),
                "contractor": clipped(
                    item.get("contractor") or "not named", limits["contractor"]),
                "published": clipped(
                    item.get("published") or "unknown", limits["published"]),
            }
            for index, item in enumerate(candidates)
        ],
    }
    envelope = PROMPT["dataEnvelope"]
    return f'{envelope["begin"]}\n{json.dumps(data, separators=(",", ":"))}\n{envelope["end"]}'


def request_body(address, candidates, reasoning_effort=CONFIG["reasoningEffort"]):
    return {
        "model": CONFIG["model"],
        "store": RUNTIME["storeResponses"],
        "instructions": PROMPT["instructions"],
        "input": [{
            "role": PROMPT["dataRole"],
            "content": [{
                "type": "input_text",
                "text": tender_user_input(address, candidates),
            }],
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": PROMPT["schemaName"],
                "schema": PROMPT["schema"],
                "strict": RUNTIME["strictStructuredOutputs"],
            },
            "verbosity": RUNTIME["textVerbosity"],
        },
        "reasoning": {"effort": reasoning_effort},
    }


def response_text(payload):
    for output in payload.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text")
    raise ValueError("Responses API returned no output_text")


def call_openai(key, body, cache_dir, slot):
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    cache_key = sha(body_bytes + b"\0" + slot.encode())
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text()), True, cache_key
    request = urllib.request.Request(
        API,
        data=body_bytes,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    result = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request, timeout=RUNTIME["timeoutsMs"]["personalOpenAI"] / 1000
            ) as response:
                payload = json.loads(response.read())
            result = json.loads(response_text(payload))
            break
        except urllib.error.HTTPError as error:
            result = {"error": f"HTTP {error.code}"}
            if error.code < 500 and error.code != 429:
                break
        except Exception as error:  # network and malformed response are eval failures
            result = {"error": type(error).__name__ + ": " + str(error)[:160]}
        if attempt == 2:
            break
    cache_path.write_text(json.dumps(result, indent=2))
    return result, False, cache_key


def selected_tender(result, candidates, threshold):
    index = result.get("match_index") if isinstance(result, dict) else None
    confidence = result.get("confidence") if isinstance(result, dict) else None
    if (not isinstance(index, int) or isinstance(index, bool)
            or not isinstance(confidence, (int, float))
            or confidence < threshold or confidence > 1
            or index < 0 or index >= len(candidates)):
        return None
    return candidates[index]["tender_number"]


def apply_production_postgates(rows, cases, threshold):
    """Use the server's actual selection validator, not an eval-only approximation."""
    by_id = {case["id"]: case for case in cases}
    payload = []
    for row in rows:
        case = by_id[row["case_id"]]
        candidates = {c["tender_number"]: c for c in case["candidates"]}
        payload.append({"address": case["address"], "result": row, "threshold": threshold,
                        "candidates": [candidates[n] for n in row["shortlist"]]})
    script = """
import fs from 'node:fs';
import {__test} from './server/src/index.js';
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(rows.map(r => {
  const selection = __test.validateTenderSelection(r.address, r.candidates, r.result, r.threshold);
  return {number: selection.candidate?.tender_number || null, reason: selection.reason};
})));
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], cwd=ROOT,
                            input=json.dumps(payload), text=True, capture_output=True, check=True)
    for row, selected in zip(rows, json.loads(result.stdout)):
        row["raw_model_tender_number"] = row["predicted_tender_number"]
        row["predicted_tender_number"] = selected["number"]
        row["postgate_reason"] = selected["reason"]


def metrics(pairs):
    tp = fp = fn = tn = wrong_contract = negative_false_match = 0
    for expected, predicted in pairs:
        if expected is None:
            if predicted is None:
                tn += 1
            else:
                fp += 1
                negative_false_match += 1
        elif predicted == expected:
            tp += 1
        elif predicted is None:
            fn += 1
        else:
            fp += 1
            fn += 1
            wrong_contract += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + negative_false_match) if tn + negative_false_match else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "wrong_contract": wrong_contract,
        "negative_false_match": negative_false_match,
        "precision": precision, "recall": recall,
        "specificity": specificity,
        "false_match_rate": None if specificity is None else 1 - specificity,
        "f1": f1,
    }


def majority_prediction(rows):
    if any(row.get("error") for row in rows):
        return "__error__"
    counts = Counter(row["predicted_tender_number"] for row in rows)
    prediction, count = counts.most_common(1)[0]
    return prediction if count > len(rows) / 2 else "__unstable__"


def pct(value):
    return "n/a" if value is None else f"{value:.1%}"


def git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "eval" / "tender_cases_v2.json"))
    parser.add_argument("--backend", choices=["aws", "llm"], default="aws",
                        help="AWS deployed matcher (default), or legacy/personal LLM matcher")
    parser.add_argument("--trials", type=int, default=3,
                        help="candidate-order trials per case")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=CONFIG["minimumConfidence"])
    parser.add_argument("--reasoning", choices=["minimal", "low", "medium", "high"],
                        default=CONFIG["reasoningEffort"],
                        help="reasoning effort arm; defaults to the production contract")
    parser.add_argument("--min-precision", type=float, default=.80)
    parser.add_argument("--min-recall", type=float, default=.80)
    parser.add_argument("--max-wrong-contracts", type=int, default=0)
    parser.add_argument("--max-negative-false-matches", type=int, default=0,
                        help="Non-road/no-match fixtures must not name a tender")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results" / "tender"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gate", action="store_true")
    args = parser.parse_args()
    if args.trials < 1 or args.concurrency < 1:
        sys.exit("trials and concurrency must be positive")
    if not 0 <= args.threshold <= 1:
        sys.exit("threshold must be from 0 to 1")

    fixture_bytes = Path(args.cases).read_bytes()
    fixture = json.loads(fixture_bytes)
    cases = fixture["cases"]
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        sys.exit("tender case IDs must be unique")
    for case in cases:
        numbers = [item["tender_number"] for item in case["candidates"]]
        expected = case.get("expected_tender_number")
        if expected is not None and expected not in numbers:
            sys.exit(f'{case["id"]}: expected tender is absent from candidate pool')

    if args.backend == "aws":
        if args.dry_run:
            print(f"{len(cases)} cases; AWS deterministic matcher; no model calls")
            return 0
        return run_aws(cases, args, fixture, fixture_bytes)

    shortlists = production_shortlists(cases)
    if shortlists != production_shortlists(cases):
        sys.exit("production tender shortlist is not deterministic")
    shortlist_by_id = dict(zip(ids, shortlists))
    positive_cases = [case for case in cases if case.get("expected_tender_number")]
    shortlist_hits = sum(
        case["expected_tender_number"] in {
            item["tender_number"] for item in shortlist_by_id[case["id"]]
        }
        for case in positive_cases
    )
    shortlist_recall = shortlist_hits / len(positive_cases) if positive_cases else None

    jobs = []
    for case in cases:
        shortlist = shortlist_by_id[case["id"]]
        for trial in range(args.trials):
            candidates = list(shortlist)
            if trial:
                random.Random(f'{case["id"]}:{trial}').shuffle(candidates)
            if candidates:
                jobs.append((case, trial, candidates,
                             request_body(case["address"], candidates, args.reasoning)))

    print(f"{len(cases)} labelled cases; shortlist recall {pct(shortlist_recall)} "
          f"({shortlist_hits}/{len(positive_cases)})")
    print(f"{len(jobs)} model calls across {args.trials} candidate-order trial(s)")
    if args.dry_run:
        if jobs:
            case, trial, candidates, body = jobs[0]
            print(json.dumps({
                "case": case["id"], "trial": trial,
                "shortlist_size": len(candidates), "model": body["model"],
                "reasoning": body["reasoning"]["effort"],
                "store": body["store"], "prompt_version": PROMPT["version"],
                "schema_version": PROMPT["schemaVersion"],
                "contract_source_sha256": CONTRACT["sourceHash"],
            }, indent=2))
        return

    key = load_key()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        returned = pool.map(
            lambda job: call_openai(
                key, job[3], cache_dir, f'{job[0]["id"]}:{job[1]}'), jobs)
        for job, response in zip(jobs, returned):
            case, trial, candidates, _body = job
            result, cached, request_hash = response
            rows.append({
                "case_id": case["id"], "trial": trial,
                "expected_tender_number": case.get("expected_tender_number"),
                "predicted_tender_number": selected_tender(
                    result, candidates, args.threshold),
                "shortlist": [item["tender_number"] for item in candidates],
                "cached": cached, "request_hash": request_hash,
                **result,
            })

    # Cases with an empty production shortlist make no model call and predict null.
    called = {(row["case_id"], row["trial"]) for row in rows}
    for case in cases:
        for trial in range(args.trials):
            if (case["id"], trial) not in called:
                rows.append({
                    "case_id": case["id"], "trial": trial,
                    "expected_tender_number": case.get("expected_tender_number"),
                    "predicted_tender_number": None,
                    "shortlist": [], "cached": False,
                    "request_hash": None, "match_index": None,
                    "confidence": 1, "reason": "production shortlist was empty",
                })
    rows.sort(key=lambda row: (row["case_id"], row["trial"]))
    raw_model_metrics = metrics([(row["expected_tender_number"], row["predicted_tender_number"]) for row in rows])
    apply_production_postgates(rows, cases, args.threshold)

    trial_pairs = [(row["expected_tender_number"], row["predicted_tender_number"])
                   for row in rows]
    trial_metrics = metrics(trial_pairs)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    case_predictions = {
        case_id: majority_prediction(group_rows)
        for case_id, group_rows in grouped.items()
    }
    case_pairs = [(case.get("expected_tender_number"), case_predictions[case["id"]])
                  for case in cases]
    case_metrics = metrics(case_pairs)
    error_count = sum("error" in row for row in rows)

    sweep = {}
    for threshold in [0, .5, .6, .7, .8, .9]:
        pairs = []
        for row in rows:
            case = next(item for item in cases if item["id"] == row["case_id"])
            candidates_by_number = {
                item["tender_number"]: item for item in case["candidates"]
            }
            candidates = [candidates_by_number[number] for number in row["shortlist"]]
            predicted = selected_tender(row, candidates, threshold) if candidates else None
            pairs.append((row["expected_tender_number"], predicted))
        sweep[str(threshold)] = metrics(pairs)

    summary = {
        "shortlist": {
            "positive_cases": len(positive_cases), "hits": shortlist_hits,
            "recall": shortlist_recall,
        },
        "threshold": args.threshold,
        "trial_level": trial_metrics,
        "raw_model_trial_level": raw_model_metrics,
        "event_grouped_case_level": case_metrics,
        "case_predictions": case_predictions,
        "threshold_sweep_raw_model_trial_level": sweep,
        "api_error_count": error_count,
        "all_null_would_fail_recall_gate": bool(positive_cases and args.min_recall > 0),
        "candidate_order_instability": [case_id for case_id, group in grouped.items()
            if len({row["predicted_tender_number"] for row in group}) > 1],
    }
    manifest = {
        "backend": "legacy_personal_llm",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "contract_source_sha256": CONTRACT["sourceHash"],
        "contract_version": CONTRACT["contractVersion"],
        "prompt_version": PROMPT["version"],
        "schema_version": PROMPT["schemaVersion"],
        "model": CONFIG["model"],
        "reasoning_effort": args.reasoning,
        "store_responses": RUNTIME["storeResponses"],
        "trials_per_case": args.trials,
        "cases_sha256": sha(fixture_bytes),
        "label_policy": fixture.get("label_policy"),
        "warning": "These are curated project-policy cases, not a sealed population sample.",
    }
    (out_dir / "raw.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print("model exact-selection precision", pct(case_metrics["precision"]))
    print("model exact-selection recall   ", pct(case_metrics["recall"]))
    print("negative-case specificity      ", pct(case_metrics["specificity"]))
    print("wrong-contract cases           ", case_metrics["wrong_contract"])
    print("API errors                     ", error_count)
    print(f"wrote {out_dir / 'summary.json'}")

    failures = []
    if shortlist_recall is not None and shortlist_recall < args.min_recall:
        failures.append(f"shortlist recall {shortlist_recall:.3f} < {args.min_recall:.3f}")
    if case_metrics["precision"] is None or case_metrics["precision"] < args.min_precision:
        failures.append(
            f'model precision {case_metrics["precision"]} < {args.min_precision:.3f}')
    if case_metrics["recall"] is None or case_metrics["recall"] < args.min_recall:
        failures.append(f'model recall {case_metrics["recall"]} < {args.min_recall:.3f}')
    if error_count:
        failures.append(f"{error_count} API result(s) failed")
    # A user sees one response, not the majority of three retries. Never hide an
    # unsafe single-trial selection behind an apparently correct majority vote.
    if trial_metrics["wrong_contract"] > args.max_wrong_contracts:
        failures.append(f'{trial_metrics["wrong_contract"]} wrong-contract trial selections')
    if trial_metrics["negative_false_match"] > args.max_negative_false_matches:
        failures.append(f'{trial_metrics["negative_false_match"]} negative-case false-match trials')
    if summary["candidate_order_instability"]:
        failures.append(f'{len(summary["candidate_order_instability"])} cases unstable across trials')
    summary["gate_failures"] = failures
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if failures and not args.no_gate:
        print("TENDER EVAL FAIL")
        for failure in failures:
            print(" -", failure)
        return 1
    print("TENDER EVAL PASS" if not failures else "TENDER EVAL COMPLETE (gate disabled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
