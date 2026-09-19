#!/usr/bin/env python3
"""Replay the shipped road-damage contract against labelled manual or drive images.

Production transforms and request semantics are mirrored here. Repetitions stay nested
under their source event; they are never presented as additional ground truth.
"""
import argparse, base64, hashlib, io, json, math, os, re, subprocess, sys
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "llm" / "generated" / "contract.json"
try:
    CONTRACT = json.loads(CONTRACT_PATH.read_bytes())
except (OSError, json.JSONDecodeError) as error:
    raise RuntimeError(
        "The generated LLM contract is missing or unreadable. "
        "Run `node llm/generate.mjs` from the repository root."
    ) from error

DETECTION = CONTRACT["prompts"]["detection"]
MODEL_CONFIG = CONTRACT["config"]["models"]
RUNTIME_CONFIG = CONTRACT["config"]["runtime"]
IMAGING_CONFIG = CONTRACT["config"]["imaging"]
LUMINANCE_CONFIG = IMAGING_CONFIG["adaptiveLuminance"]

API = RUNTIME_CONFIG["responsesUrl"]
DEFAULT_MODEL = MODEL_CONFIG["defaultModel"]
ALLOWED_MODELS = frozenset(MODEL_CONFIG["allowedModels"])
ALLOWED_DETAILS = frozenset(MODEL_CONFIG["allowedImageDetails"])
ORIGINAL_DETAIL_MODELS = frozenset(MODEL_CONFIG["originalDetailModels"])
DEFAULT_DETAIL = MODEL_CONFIG["defaultImageDetail"]
MAX_DETECTION_IMAGES = IMAGING_CONFIG["maxDetectionImages"]
PROMPT_VERSION = DETECTION["version"]
SCHEMA_VERSION = DETECTION["schemaVersion"]
SCHEMA_NAME = DETECTION["schemaName"]
SCHEMA = DETECTION["schema"]

if RUNTIME_CONFIG["storeResponses"] is not False:
    raise RuntimeError("The evaluator refuses to run while the canonical contract stores responses.")
if MAX_DETECTION_IMAGES != 1:
    raise RuntimeError("road-damage-v4 evaluation requires exactly one detection image.")


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def load_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("OPENAI_API_KEY not set (environment or .env)")


def client_template_constant(name):
    """Read an auditable template literal from the shipped pure-client runtime."""
    src = (ROOT / "static" / "standalone.js").read_text()
    found = re.search(rf"const {re.escape(name)} = `(.*?)`;", src, re.S)
    if not found:
        sys.exit(f"could not find {name} in static/standalone.js")
    return found.group(1)


def client_string_constant(name):
    """Read a quoted version identifier from the shipped pure-client runtime."""
    src = (ROOT / "static" / "standalone.js").read_text()
    found = re.search(rf'const {re.escape(name)} = "([^"]+)";', src)
    if not found:
        sys.exit(f"could not find {name} in static/standalone.js")
    return found.group(1)


def prompts():
    """Return only prompt arms registered by the canonical LLM contract."""
    registered = DETECTION.get("evaluationVariants", {})
    if "baseline" in registered:
        raise RuntimeError("The reserved baseline arm cannot be replaced by an evaluation variant.")
    return {"baseline": DETECTION["base"], **registered}


def effective_prompt(base_prompt, mode, layout_note=""):
    """Mirror the shipped mode-specific prompt assembly exactly."""
    return base_prompt + layout_note


def effective_prompt_version(mode):
    return PROMPT_VERSION


def normalise_config(model, detail):
    model = model if model in ALLOWED_MODELS else DEFAULT_MODEL
    detail = detail if detail in ALLOWED_DETAILS else DEFAULT_DETAIL
    if detail == MODEL_CONFIG["originalImageDetail"] and model not in ORIGINAL_DETAIL_MODELS:
        detail = DEFAULT_DETAIL
    return model, detail


DRIVE_DEFAULT_DETAIL = "original"
# Android hands the evaluator frames it already prepared. Replay downscales them to the
# same bound as a live Drive request and never upscales, so a small prepared frame is
# replayed exactly as the phone sent it.
MAX_PREPARED_FRAME_DIMENSION = IMAGING_CONFIG["drive"]["maxDimension"]
# The native Drive request budget, mirrored from NativeDetectionContract.kt.
NATIVE_DRIVE_MAX_OUTPUT_TOKENS = 1536


def detection_enhancement_plan(image):
    """Return the integer enhancement plan shared with Android and the Web runtime."""
    pixels = image.load()
    step = max(1, math.floor(math.sqrt((image.width * image.height) / 12000)))
    luminance_sum = sample_count = dark_count = bright_count = 0
    for y in range(0, image.height, step):
        for x in range(0, image.width, step):
            red, green, blue = pixels[x, y]
            luminance = 2126 * red + 7152 * green + 722 * blue
            luminance_sum += luminance
            sample_count += 1
            dark_count += luminance < 120000
            bright_count += luminance > 2450000

    enhanced = (luminance_sum < 720000 * sample_count
                and bright_count * 100 < 8 * sample_count)
    gain_numerator = gain_denominator = 1
    if enhanced:
        gain_numerator = 935000 * sample_count
        gain_denominator = max(luminance_sum, 350000 * sample_count)
        if gain_numerator * 1000 < 1265 * gain_denominator:
            gain_numerator, gain_denominator = 1265, 1000
        elif gain_numerator * 1000 > 1815 * gain_denominator:
            gain_numerator, gain_denominator = 1815, 1000

    return {
        "enhanced": enhanced,
        "sample_count": sample_count,
        "luminance_sum": luminance_sum,
        "dark_count": dark_count,
        "bright_count": bright_count,
        "gain_numerator": gain_numerator,
        "gain_denominator": gain_denominator,
        "luminance": luminance_sum / max(1, 10000 * sample_count),
        "dark": dark_count / max(1, sample_count),
        "bright": bright_count / max(1, sample_count),
    }


def apply_detection_enhancement(image, plan):
    """Apply Android's exact black-pivot rational gain through an integer RGB LUT."""
    if not plan["enhanced"]:
        return image
    numerator = plan["gain_numerator"]
    denominator = plan["gain_denominator"]
    lookup = [min(255, (2 * channel * numerator + denominator) // (2 * denominator))
              for channel in range(256)]
    return image.point(lookup * 3)


def adaptive_lift(image):
    """Enhance the already-resized full frame with the cross-runtime pixel kernel."""
    plan = detection_enhancement_plan(image)
    return apply_detection_enhancement(image, plan), plan


def positive_half_up(value):
    """Match Kotlin roundToInt and JavaScript Math.round for positive dimensions."""
    return math.floor(value + .5)


def encode_view(path, max_dim, quality, enhance):
    from PIL import Image
    image = Image.open(path).convert("RGB")
    source = {"width": image.width, "height": image.height}
    # Match native live analysis and WebView replay: preserve the complete frame and
    # downscale only. No spatial crop, tile, mask, or region of interest is permitted.
    scale = min(1.0, max_dim / max(image.size))
    if scale != 1:
        image = image.resize((positive_half_up(image.width * scale),
                              positive_half_up(image.height * scale)), Image.Resampling.LANCZOS)
    light = {"enhanced": False}
    if enhance:
        image, light = adaptive_lift(image)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    raw = buf.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode(), {
        "source": source, "output": {"width": image.width, "height": image.height},
        "max_dim": max_dim, "jpeg_quality": quality, "full_frame": True,
        **light, "bytes_sha256": sha(raw),
    }


def entry_image(entry):
    """Return the single frame selected for this labelled event."""
    paths = entry.get("frames") or [entry["path"]]
    primary = int(entry.get("primary_index", 0))
    primary = primary if 0 <= primary < len(paths) else 0
    return paths[primary]


def entry_mode(entry):
    if entry.get("mode") in {"manual", "drive"}:
        return entry["mode"]
    return "drive" if "dashcam" in str(entry.get("source", "")).lower() else "manual"


# The 2-of-3 temporary-surface policy the app runs on device. The evaluator has to
# execute the same attempt accounting or its numbers do not describe production.
TEMPORARY_SURFACE_MAX_ATTEMPTS = 3


def temporary_surface_vote_eligible(result, mode="drive"):
    """Whether one complete decision may participate in the bounded temporary vote."""
    return (mode == "drive"
            and result.get("looks_like_speed_breaker") is False
            and result.get("image_quality") == "usable"
            and result.get("surface_type") == "temporary_drivable_surface"
            and result.get("on_drivable_surface") is True
            and result.get("temporal_consistency") == "consistent")


def run_bounded_detection_policy(get_assessment, mode="drive", source_view_count=3):
    """Execute the native/Web 2-of-3 policy with exact attempt accounting.

    The first request is allowed to raise because no detector decision exists. Once
    an eligible temporary-surface decision exists, a failed confirmation is a
    conservative reject, exactly like the native service.
    """
    attempts_started = 1
    attempts = [get_assessment()]
    confirmation_failed = False
    while should_retry_temporary_surface(attempts, mode, source_view_count):
        attempts_started += 1
        try:
            attempts.append(get_assessment())
        except Exception:
            confirmation_failed = True
            break

    first_is_eligible = temporary_surface_vote_eligible(attempts[0], mode)
    if not first_is_eligible:
        final_decision = native_decision(attempts[0], mode, source_view_count)
    elif confirmation_failed:
        final_decision = "reject"
    elif confirms_temporary_surface(attempts, mode, source_view_count):
        final_decision = "accept"
    else:
        # This includes two NO votes, a 2-of-3 NO majority, and any subsequent
        # ineligible/safety-gate result. All are fail-closed in production.
        final_decision = "reject"
    assessment = _final_detection_policy_assessment(
        attempts, final_decision, mode, source_view_count)
    return DetectionPolicyOutcome(
        assessment=assessment,
        decision=final_decision,
        assessments=tuple(attempts),
        attempts_started=attempts_started,
        confirmation_failed=confirmation_failed,
    )


def prepare_event(entry, root, mode):
    config = IMAGING_CONFIG[mode]
    selected = entry_image(entry)
    view, meta = encode_view(
        root / selected, config["maxDimension"],
        round(config["jpegQuality"] * 100), config["adaptiveBrightness"])
    transform = {"selected_image": selected, **meta}
    return [view], [transform], DETECTION["captureLayouts"][mode]


def build_request(views, prompt, model, detail, mode="drive", *,
                  schema=None, max_output_tokens=None, reasoning_effort=None):
    model, detail = normalise_config(model, detail)
    content = [
        {"type": "input_image", "image_url": url, "detail": detail}
        for url in views
    ]
    if len(content) != 1:
        raise ValueError("road-damage-v5 requests must contain exactly one image")
    content.append({"type": "input_text", "text": prompt})
    # A native Drive replay carries the on-device contract's schema, effort and token
    # budget; everything else uses the generated shared contract. The request used to be
    # returned before these were applied, so a Drive replay was never the shipped shape.
    request = {
        "model": model,
        "store": RUNTIME_CONFIG["storeResponses"],
        "reasoning": {"effort": reasoning_effort or MODEL_CONFIG["reasoningEffortByModel"].get(
            model, MODEL_CONFIG["defaultReasoningEffort"])},
        "input": [{"role": DETECTION["role"], "content": content}],
        "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME,
                            "schema": schema if schema is not None else SCHEMA,
                            "strict": RUNTIME_CONFIG["strictStructuredOutputs"]},
                 "verbosity": RUNTIME_CONFIG["textVerbosity"]},
    }
    if mode == "drive":
        # Match the shipped native streaming request. An eval completion that needs
        # more output than production permits is not a valid production result.
        request["max_output_tokens"] = max_output_tokens or NATIVE_DRIVE_MAX_OUTPUT_TOKENS
        request["stream"] = True
    return request


def native_decision(result, mode="drive", source_view_count=3):
    if not result or result.get("is_pothole") is not True:
        return "reject"
    if result.get("looks_like_speed_breaker") is not False:
        return "reject"
    surface_type = result.get("surface_type")
    if result.get("image_quality") != "usable" or surface_type not in {
            "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
            "temporary_drivable_surface"}:
        return "reject"
    if result.get("on_drivable_surface") is not True:
        return "reject"
    if result.get("has_localized_cavity") is not True:
        return "reject"
    if not isinstance(result.get("has_unambiguous_lower_interior"), bool):
        return "reject"
    if (surface_type == "temporary_drivable_surface" and
            result.get("has_unambiguous_lower_interior") is not True):
        return "reject"
    if result.get("has_broken_edge_or_rim") is not True or result.get("has_depth_or_surface_loss") is not True:
        return "reject"
    # A temporary traffic surface needs the corroborating chronology that separates a
    # discrete cavity from ordinary gravel texture, grading and wheel ruts.
    if surface_type == "temporary_drivable_surface" and mode != "drive":
        return "reject"
    if mode == "drive":
        if result.get("temporal_consistency") != "consistent" or source_view_count < 2:
            return "reject"
    elif result.get("temporal_consistency") not in {"consistent", "single_view"}:
        return "reject"
    if result.get("size") not in {"small", "medium", "large"}:
        return "reject"
    return "accept"


def temporary_surface_vote_eligible(result, mode="drive"):
    """Whether one complete decision may participate in the bounded temporary vote."""
    return (mode == "drive"
            and result.get("looks_like_speed_breaker") is False
            and result.get("image_quality") == "usable"
            and result.get("surface_type") == "temporary_drivable_surface"
            and result.get("on_drivable_surface") is True
            and result.get("temporal_consistency") == "consistent")


def should_retry_temporary_surface(attempts, mode="drive", source_view_count=3):
    """Stop as soon as two complete eligible decisions agree, with three calls maximum."""
    if (not attempts or len(attempts) >= TEMPORARY_SURFACE_MAX_ATTEMPTS
            or any(not temporary_surface_vote_eligible(item, mode) for item in attempts)):
        return False
    accepts = sum(native_decision(item, mode, source_view_count) == "accept" for item in attempts)
    rejects = len(attempts) - accepts
    return accepts < 2 and rejects < 2


def confirms_temporary_surface(attempts, mode="drive", source_view_count=3):
    """Require a strict two-YES majority from complete eligible temporary decisions."""
    return (len(attempts) >= 2
            and all(temporary_surface_vote_eligible(item, mode) for item in attempts)
            and sum(native_decision(item, mode, source_view_count) == "accept"
                    for item in attempts) >= 2)


@dataclass(frozen=True)
class DetectionPolicyOutcome:
    """Auditable result of the shipped bounded temporary-surface vote."""

    assessment: dict
    decision: str
    assessments: tuple
    attempts_started: int
    confirmation_failed: bool


def _final_detection_policy_assessment(attempts, final_decision, mode,
                                       source_view_count):
    matching = [item for item in attempts
                if native_decision(item, mode, source_view_count) == final_decision]
    if matching:
        return matching[-1]
    # A failed or ineligible confirmation after one or more YES results is the
    # native fail-closed path. Preserve the structured evidence but make the
    # representative result unambiguously negative.
    return {
        **attempts[-1],
        "is_pothole": False,
        "size": None,
        "description": "Temporary-surface pothole was not independently confirmed.",
    }


def decision(result):
    if not result or result.get("image_quality") == "rejected":
        return "review"
    if result.get("image_quality") != "acceptable":
        return "review"
    allowed_damage_types = {
        value for value in SCHEMA["properties"]["damage_type"]["enum"]
        if isinstance(value, str)
    }
    allowed_sizes = {
        value for value in SCHEMA["properties"]["size"]["enum"]
        if isinstance(value, str)
    }
    size = result.get("size")
    if (result.get("assessment") == "damaged"
            and result.get("damage_type") in allowed_damage_types
            and (size is None or size in allowed_sizes)):
        return "accept"
    if (result.get("assessment") == "undamaged"
            and result.get("damage_type") is None
            and result.get("size") is None):
        return "reject"
    # Any other field combination contradicts the canonical schema semantics.
    # It is not safe to turn a malformed result into a complaint decision.
    return "review"


def call(key, body, cache_dir, cache_slot):
    # Each stochastic repetition has its own stable slot. Caching identical body bytes
    # into one file would make five "trials" five copies of the first response.
    body_hash = sha(json.dumps(body, sort_keys=True, separators=(",", ":")))
    cache_key = f"{body_hash}-{sha(cache_slot)[:12]}"
    cached = cache_dir / f"{cache_key}.json"
    if cached.exists():
        return json.loads(cached.read_text()), True, cache_key
    request = urllib.request.Request(API, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    result = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                    request, timeout=RUNTIME_CONFIG["timeoutsMs"]["personalOpenAI"] / 1000
            ) as response:
                payload = json.loads(response.read())
            message = next(o for o in payload.get("output", []) if o.get("type") == "message")
            text = next(c for c in message["content"] if c.get("type") == "output_text")["text"]
            result = json.loads(text)
            break
        except Exception as error:
            if attempt == 2:
                result = {"error": str(error)[:200]}
    cached.write_text(json.dumps(result, indent=1))
    return result, False, cache_key


def call_with_detection_policy(key, body, cache_dir, cache_slot, mode, source_view_count):
    """Apply the shipped bounded vote using a distinct fresh request per attempt."""
    calls = []

    def get_assessment():
        attempt_number = len(calls) + 1
        item = call(
            key, body, cache_dir,
            f"{cache_slot}|policy-attempt-{attempt_number}")
        calls.append(item)
        return item[0]

    outcome = run_bounded_detection_policy(
        get_assessment, mode, source_view_count)
    return (outcome.assessment, all(item[1] for item in calls),
            [item[2] for item in calls])


def binary_label(label):
    if label in {"pothole", "pothole_cavity", "failed_patch", "surface_breakup",
                 "rut_or_depression", "other_road_damage", "damaged"}:
        return True
    if label in {"not_pothole", "undamaged"}:
        return False
    # Anything else is an unverified category: it cannot be converted into binary truth
    # without relabelling, and guessing would quietly move the measured accuracy.
    return None


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def grouped_metrics(source_rows, suppress_precision_without_negatives=False):
    """Count each labelled event once, regardless of stochastic trial count."""
    grouped = defaultdict(list)
    for row in source_rows:
        # An event the owner marked ineligible, such as a phone recording of another
        # phone's screen, is kept in the corpus as evidence but must never move the
        # published accuracy. Default true: an ordinary labelled event still counts.
        if row.get("accuracy_eligible") is True and binary_label(row["label"]) is not None:
            grouped[row["event"]].append(row)

    counts = Counter(tp=0, fp=0, tn=0, fn=0)
    event_results = []
    for event, event_rows in sorted(grouped.items()):
        truth = binary_label(event_rows[0]["label"])
        decisions = Counter(row["decision"] for row in event_rows)
        # A complaint is the positive action. Require a strict majority of the
        # event's repetitions so ties and review-heavy events fail closed.
        predicted_positive = decisions["accept"] > len(event_rows) / 2
        if truth and predicted_positive:
            counts["tp"] += 1
        elif truth:
            counts["fn"] += 1
        elif predicted_positive:
            counts["fp"] += 1
        else:
            counts["tn"] += 1
        event_results.append({
            "event": event,
            "truth": "positive" if truth else "negative",
            "predicted": "positive" if predicted_positive else "negative",
            "decisions": dict(decisions),
            "accept_rate": decisions["accept"] / len(event_rows),
        })

    positive_events = counts["tp"] + counts["fn"]
    negative_events = counts["tn"] + counts["fp"]
    predicted_positive_events = counts["tp"] + counts["fp"]
    precision = ratio(counts["tp"], predicted_positive_events)
    recall = ratio(counts["tp"], positive_events)
    specificity = ratio(counts["tn"], negative_events)
    false_accept_rate = ratio(counts["fp"], negative_events)
    precision_note = None
    if suppress_precision_without_negatives and negative_events == 0:
        precision = None
        precision_note = "not estimable: no owner-verified negative events"
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return {
        "events": len(event_results),
        "positive_events": positive_events,
        "negative_events": negative_events,
        "tp": counts["tp"], "fp": counts["fp"],
        "tn": counts["tn"], "fn": counts["fn"],
        "precision": precision, "precision_note": precision_note,
        "recall": recall, "specificity": specificity,
        "false_accept_rate": false_accept_rate, "f1": f1,
        "event_results": event_results,
    }


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def select_events(entries, selector):
    """Select exact event IDs while preserving label-file order."""
    requested = [item.strip() for item in str(selector or "").split(",") if item.strip()]
    if not requested:
        return entries
    wanted = set(requested)
    found = {str(entry.get("event_id")) for entry in entries
             if entry.get("event_id") is not None and str(entry.get("event_id")) in wanted}
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"unknown event id(s) for selected mode: {', '.join(missing)}")
    return [entry for entry in entries
            if entry.get("event_id") is not None and str(entry.get("event_id")) in wanted]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--arms", default="baseline", help="comma-separated prompt arms")
    parser.add_argument("--models", default=DEFAULT_MODEL, help="comma-separated model IDs")
    parser.add_argument("--details", default=DEFAULT_DETAIL,
                        help="comma-separated image-detail values from the LLM contract")
    parser.add_argument("--mode", choices=["manual", "drive"], default="drive")
    parser.add_argument("--images-root", default=str(ROOT / "eval" / "images"))
    parser.add_argument("--labels", default=str(ROOT / "eval" / "labels.json"))
    parser.add_argument("--events", default="",
                        help="comma-separated exact event IDs; evaluates only those events")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="first N matching events; smoke tests only")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    parser.add_argument("--dry-run", action="store_true", help="validate and print requests without API calls")
    args = parser.parse_args()

    label_bytes = Path(args.labels).read_bytes()
    all_entries = json.loads(label_bytes)["images"]
    entries = [entry for entry in all_entries if entry_mode(entry) == args.mode]
    try:
        entries = select_events(entries, args.events)
    except ValueError as exc:
        sys.exit(str(exc))
    if args.limit > 0:
        entries = entries[:args.limit]
    if not entries:
        sys.exit(f"no {args.mode} entries in the selected label set")
    root = Path(args.images_root)
    missing = [entry_image(entry) for entry in entries if not (root / entry_image(entry)).exists()]
    if missing:
        sys.exit(f"{len(missing)} labelled images not found under {root}, first: {missing[0]}\n"
                 "Images are not committed; see eval/README.md.")

    variants = prompts()
    chosen = [x for x in args.arms.split(",") if x]
    unknown = [x for x in chosen if x not in variants]
    if unknown:
        sys.exit(f"unknown prompt arm(s): {unknown}; available: {sorted(variants)}")
    requested_models = args.models or (
        MANUAL_DEFAULT_MODEL if args.mode == "manual" else DRIVE_DEFAULT_MODEL
    )
    model_names = [value.strip() for value in requested_models.split(",") if value.strip()]
    unknown_models = [value for value in model_names if value not in ALLOWED_MODELS]
    if unknown_models:
        sys.exit(f"unsupported model(s): {unknown_models}; allowed: {sorted(ALLOWED_MODELS)}")
    configs = []
    for arm in chosen:
        for model in model_names:
            for detail in filter(None, args.details.split(",")):
                model, detail = normalise_config(model, detail)
                item = (f"{arm}|{model}|{detail}|{args.mode}", variants[arm], model, detail)
                if item not in configs:
                    configs.append(item)
    if not configs:
        sys.exit("no valid evaluation configuration")
    configs.append(("baseline_replicate|" + "|".join(configs[0][0].split("|")[1:]),
                    variants["baseline"], configs[0][2], configs[0][3]))

    prepared = {}
    for entry in entries:
        views, transforms, note = prepare_event(entry, root, args.mode)
        prepared[entry["path"]] = (views, transforms, note)

    jobs = []
    for name, prompt, model, detail in configs:
        for entry in entries:
            views, transforms, note = prepared[entry["path"]]
            body = build_request(views, effective_prompt(prompt, args.mode, note), model, detail,
                                 args.mode)
            for trial in range(args.trials):
                jobs.append((name, entry, trial, body, transforms))
    print(f"{len(jobs)} calls: {len(configs)} configurations x {len(entries)} events x {args.trials} trials")
    if args.dry_run:
        sample = jobs[0]
        images = [x for x in sample[3]["input"][0]["content"] if x["type"] == "input_image"]
        print(json.dumps({"arm": sample[0], "images": len(images), "model": sample[3]["model"],
                          "reasoning": sample[3]["reasoning"]["effort"],
                          "detail": images[0]["detail"], "store": sample[3]["store"],
                          "contract_source_sha256": CONTRACT["sourceHash"],
                          "schema_version": SCHEMA_VERSION,
                          "transform": sample[4]}, indent=1))
        return

    key = load_key()
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    cache_dir = outdir / "cache"; cache_dir.mkdir(exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = pool.map(lambda job: call_with_detection_policy(
            key, job[3], cache_dir,
            f"{job[0]}|{job[1].get('event_id') or job[1]['path']}|{job[2]}",
            args.mode, len(entry_paths(job[1]))), jobs)
        for index, (job, returned) in enumerate(zip(jobs, results), 1):
            name, entry, trial, body, transforms = job
            result, cached, cache_keys = returned
            rows.append({"arm": name, "event": entry.get("event_id") or entry["path"],
                         "image": entry["path"], "label": entry["label"],
                         "labelled_by": entry.get("labelled_by"), "trial": trial,
                         "accuracy_eligible": entry.get("accuracy_eligible", True),
                         "decision": native_decision(result, args.mode, len(entry_paths(entry))), "cached": cached,
                         "request_hash": cache_keys[-1], "request_hashes": cache_keys,
                         "attempts": len(cache_keys), "transforms": transforms, **result})
            if index % 25 == 0:
                print(f"  {index}/{len(jobs)}")

    (outdir / "raw.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    summary = {}
    print("\n=== event-clustered binary results ===")
    for name, _, _, _ in configs:
        arm_rows = [row for row in rows if row["arm"] == name and "error" not in row]
        verified_rows = [row for row in arm_rows
                         if str(row.get("labelled_by", "")).strip().lower() == "owner"]
        verified = grouped_metrics(verified_rows, suppress_precision_without_negatives=True)
        provisional = grouped_metrics(arm_rows)
        summary[name] = {
            "owner_verified": verified,
            "provisional_including_unverified": provisional,
        }
        def percent(value):
            return f"{value:.1%}" if value is not None else "n/a"

        print(f"  {name:48} OWNER VERIFIED "
              f"TP/FP/TN/FN {verified['tp']}/{verified['fp']}/{verified['tn']}/{verified['fn']} · "
              f"precision {percent(verified['precision'])} · recall {percent(verified['recall'])} · "
              f"specificity {percent(verified['specificity'])} · FAR {percent(verified['false_accept_rate'])} · "
              f"F1 {percent(verified['f1'])}")
        if verified["precision_note"]:
            print(f"    {verified['precision_note']}")
        print(f"  {name:48} PROVISIONAL    "
              f"TP/FP/TN/FN {provisional['tp']}/{provisional['fp']}/{provisional['tn']}/{provisional['fn']} · "
              f"precision {percent(provisional['precision'])} · recall {percent(provisional['recall'])} · "
              f"specificity {percent(provisional['specificity'])} · FAR {percent(provisional['false_accept_rate'])} · "
              f"F1 {percent(provisional['f1'])}")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(), "git_commit": git_commit(),
        "llm_contract_version": CONTRACT["contractVersion"],
        "llm_contract_source_sha256": CONTRACT["sourceHash"],
        "mode": args.mode, "trials_per_event": args.trials, "prompt_version": PROMPT_VERSION,
        "schema_name": SCHEMA_NAME, "schema_version": SCHEMA_VERSION,
        "schema_sha256": sha(json.dumps(SCHEMA, sort_keys=True)),
        "store_responses": RUNTIME_CONFIG["storeResponses"],
        "text_verbosity": RUNTIME_CONFIG["textVerbosity"],
        "max_detection_images": MAX_DETECTION_IMAGES,
        "imaging": IMAGING_CONFIG,
        "labels_sha256": sha(label_bytes), "configs": [config[0] for config in configs],
        "event_aggregation": "strict majority accept across repetitions; ties fail closed",
        "warning": "The seed set is not a release gate until it contains owner-verified positives and negatives.",
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\nwrote raw.jsonl, manifest.json, summary.json and response cache under {outdir}")


if __name__ == "__main__":
    main()
