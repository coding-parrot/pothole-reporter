#!/usr/bin/env python3
"""Label prepared drive frames with the production detector (the teacher).

The request is the one infra/aws-central/service/detectors.mjs sends for a drive
frame: same prompt and capture layout, schema, model, image detail, reasoning effort,
verbosity and store flag, all read from llm/generated/contract.json. Answers are cached
in work/teacher/ by the SHA-256 of the JPEG that was sent, so a rerun costs nothing.

The spend is counted from the token usage each response reports and the run stops
before it would pass --max-usd.
"""
import argparse
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import (DETECTION, FRAMES, MODEL_CONFIG, ROOT, RUNTIME_CONFIG, TEACHER,
                    sha256_hex)

MODEL = MODEL_CONFIG["defaultModel"]
DETAIL = MODEL_CONFIG["defaultImageDetail"]
EFFORT = MODEL_CONFIG["reasoningEffortByModel"].get(
    MODEL, MODEL_CONFIG["defaultReasoningEffort"])
PROMPT = DETECTION["base"] + DETECTION["captureLayouts"]["drive"]
# gpt-5-mini list price in USD per million tokens (OpenAI pricing page, October 2026).
USD_PER_M = {"input": 0.25, "cached_input": 0.025, "output": 2.00}
ESTIMATE_TOKENS = {"input": 1963, "output": 100}
CONTRACT_KEY = sha256_hex(json.dumps({
    "model": MODEL, "detail": DETAIL, "effort": EFFORT, "prompt": PROMPT,
    "schema": DETECTION["schema"], "verbosity": RUNTIME_CONFIG["textVerbosity"],
}, sort_keys=True).encode())[:16]


def api_key():
    """The OpenAI key, held in memory only. On the training instance it is read from the
    detector secret (DETECTOR_SECRET_ARN, field openai_api_key) with the instance role;
    it is never printed, logged or written to disk."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    secret = os.environ.get("DETECTOR_SECRET_ARN", "").strip()
    if secret:
        import boto3

        region = secret.split(":")[3]
        value = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret)
        key = json.loads(value["SecretString"]).get("openai_api_key", "").strip()
        if key:
            return key
        raise SystemExit("the detector secret has no openai_api_key")
    # On the Mac the key lives in the main checkout's .env, never in a worktree.
    for env in (ROOT / ".env", Path.home() / "Downloads" / "pothole-reporter" / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("OPENAI_API_KEY is not set")


def request_body(jpeg):
    return {
        "model": MODEL,
        "input": [{
            "role": DETECTION["role"],
            "content": [
                {"type": "input_image", "detail": DETAIL,
                 "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
                {"type": "input_text", "text": PROMPT},
            ],
        }],
        "text": {
            "format": {"type": "json_schema", "name": DETECTION["schemaName"],
                       "schema": DETECTION["schema"],
                       "strict": RUNTIME_CONFIG["strictStructuredOutputs"]},
            "verbosity": RUNTIME_CONFIG["textVerbosity"],
        },
        "reasoning": {"effort": EFFORT},
        "store": False,
    }


def output_text(data):
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    for item in data.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text")
    return None


def cost_usd(usage):
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    fresh = usage.get("input_tokens", 0) - cached
    return (fresh * USD_PER_M["input"] + cached * USD_PER_M["cached_input"]
            + usage.get("output_tokens", 0) * USD_PER_M["output"]) / 1e6


def cache_path(sha, trial):
    suffix = "" if trial == 0 else f".t{trial}"
    return TEACHER / CONTRACT_KEY / f"{sha}{suffix}.json"


def call(key, jpeg):
    body = json.dumps(request_body(jpeg)).encode()
    for attempt in range(6):
        request = urllib.request.Request(RUNTIME_CONFIG["responsesUrl"], data=body, headers={
            "authorization": f"Bearer {key}", "content-type": "application/json"})
        try:
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read())
            text = output_text(data)
            if not text:
                raise ValueError("no structured output")
            return {"verdict": json.loads(text), "usage": data.get("usage", {}),
                    "latency_ms": round((time.monotonic() - started) * 1000)}
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            if error.code not in (408, 409, 429) and error.code < 500:
                raise RuntimeError(f"OpenAI {error.code}: {detail}") from None
            last = f"{error.code}: {detail}"
        except Exception as error:  # noqa: BLE001 - network faults are retried
            last = repr(error)[:200]
        time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"gave up: {last}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-usd", type=float, default=20.0,
                        help="stop before the cached spend passes this")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--trial", type=int, default=0,
                        help="0 is the label; 1+ are repeats that measure teacher noise")
    parser.add_argument("--source-prefix", action="append", default=[],
                        help="only frames whose source starts with this (repeatable)")
    parser.add_argument("--paths-file", help="only the frame paths listed in this file")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = [json.loads(line) for line in (FRAMES / "index.jsonl").read_text().splitlines()]
    if args.source_prefix:
        rows = [row for row in rows
                if any(row["source"].startswith(p) for p in args.source_prefix)]
    if args.paths_file:
        wanted = set(Path(args.paths_file).read_text().split())
        rows = [row for row in rows if row["path"] in wanted]
    (TEACHER / CONTRACT_KEY).mkdir(parents=True, exist_ok=True)

    spent = 0.0
    for cached in TEACHER.glob("*/*.json"):
        spent += json.loads(cached.read_text()).get("usd", 0.0)
    todo = [row for row in rows if not cache_path(row["sha256"], args.trial).exists()]
    if args.limit:
        todo = todo[:args.limit]
    estimate = len(todo) * cost_usd({"input_tokens": ESTIMATE_TOKENS["input"],
                                     "output_tokens": ESTIMATE_TOKENS["output"]})
    print(f"contract {CONTRACT_KEY} model {MODEL} detail {DETAIL} effort {EFFORT}")
    print(f"{len(rows)} frames selected, {len(todo)} to label, already spent "
          f"USD {spent:.2f}, this run about USD {estimate:.2f}", flush=True)
    if spent + estimate > args.max_usd:
        raise SystemExit(f"refusing: would pass the USD {args.max_usd:.2f} limit")
    if not todo:
        return
    key = api_key()
    lock = threading.Lock()
    state = {"spent": spent, "done": 0, "failed": 0, "stop": False}

    def work(row):
        if state["stop"]:
            return
        jpeg = (FRAMES / row["path"]).read_bytes()
        if sha256_hex(jpeg) != row["sha256"]:
            raise RuntimeError(f"{row['path']} changed since it was indexed")
        try:
            answer = call(key, jpeg)
        except Exception as error:  # noqa: BLE001 - counted and reported, run continues
            with lock:
                state["failed"] += 1
                print("failed", row["path"], str(error)[:200], flush=True)
            return
        answer["usd"] = cost_usd(answer["usage"])
        answer.update({"sha256": row["sha256"], "model": MODEL, "detail": DETAIL,
                       "effort": EFFORT, "prompt_version": DETECTION["version"],
                       "trial": args.trial})
        cache_path(row["sha256"], args.trial).write_text(json.dumps(answer, sort_keys=True))
        with lock:
            state["spent"] += answer["usd"]
            state["done"] += 1
            if state["done"] % 200 == 0:
                print(f"{state['done']}/{len(todo)} USD {state['spent']:.3f}", flush=True)
            if state["spent"] > args.max_usd:
                state["stop"] = True

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(work, todo))
    print(f"done {state['done']} failed {state['failed']} total spent "
          f"USD {state['spent']:.3f}" + (" (stopped at the limit)" if state["stop"] else ""))


if __name__ == "__main__":
    main()
