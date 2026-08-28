#!/usr/bin/env python3
"""Replay the shipped road-damage contract against labelled manual or drive images.

Production transforms and request semantics are mirrored here. Repetitions stay nested
under their source event; they are never presented as additional ground truth.
"""
import argparse, base64, hashlib, io, json, math, os, random, re, subprocess, sys
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5-mini"
ALLOWED_MODELS = {DEFAULT_MODEL, "gpt-5.6"}
ALLOWED_DETAILS = {"high", "original"}
ROAD_REGION_RATIOS = {
    "portrait": {"top": 0.40, "bottom": 0.66},
    "landscape": {"top": 0.48, "bottom": 0.78},
    "square": {"top": 0.40, "bottom": 0.70},
}
ROAD_ORIENTATION_EPSILON = 0.10
MAX_PREPARED_ROAD_DIMENSION = 1280
NATIVE_DRIVE_MAX_OUTPUT_TOKENS = 1536
PROMPT_VERSION = "pothole-binary-v10"
SCHEMA_VERSION = 7

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type",
                 "on_drivable_surface", "has_localized_cavity", "has_broken_edge_or_rim",
                 "has_depth_or_surface_loss", "temporal_consistency", "size", "description"],
    "properties": {
        "is_pothole": {"type": "boolean"},
        "looks_like_speed_breaker": {"type": "boolean"},
        "image_quality": {"type": "string", "enum": ["usable", "unusable"]},
        "surface_type": {"type": "string", "enum": ["bituminous_asphalt",
            "cement_concrete", "mastic_asphalt", "paver_blocks",
            "temporary_drivable_surface",
            "unpaved_or_nonroad", "unknown"]},
        "on_drivable_surface": {"type": "boolean"},
        "has_localized_cavity": {"type": "boolean"},
        "has_broken_edge_or_rim": {"type": "boolean"},
        "has_depth_or_surface_loss": {"type": "boolean"},
        "temporal_consistency": {"type": "string", "enum": ["consistent", "single_view",
            "inconsistent", "not_applicable"]},
        "size": {"type": ["string", "null"], "enum": ["small", "medium", "large", None]},
        "description": {"type": "string"},
    },
}


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
    """Read the live prompt from the pure client so the control cannot drift."""
    variants = {"baseline": client_template_constant("DETECT_PROMPT")}
    extra = ROOT / "eval" / "prompts"
    if extra.is_dir():
        for path in sorted(extra.glob("*.txt")):
            variants[path.stem] = path.read_text().rstrip("\n")
    return variants


def effective_prompt(base_prompt, mode, layout_note=""):
    """Mirror the shipped mode-specific prompt assembly exactly."""
    photo_scope = client_template_constant("PHOTO_ONLY_PROMPT_SUFFIX") \
        if mode == "manual" else ""
    return base_prompt + photo_scope + layout_note


def effective_prompt_version(mode):
    return client_string_constant("PHOTO_PROMPT_VERSION") if mode == "manual" \
        else PROMPT_VERSION


def normalise_config(model, detail):
    model = model if model in ALLOWED_MODELS else DEFAULT_MODEL
    detail = detail if detail in ALLOWED_DETAILS else "high"
    if detail == "original" and model != "gpt-5.6":
        detail = "high"
    return model, detail


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
    """Enhance the already-cropped/resized view with the cross-runtime pixel kernel."""
    plan = detection_enhancement_plan(image)
    return apply_detection_enhancement(image, plan), plan


def positive_half_up(value):
    """Match Kotlin roundToInt/JavaScript Math.round for positive dimensions."""
    return math.floor(value + .5)


def select_road_region(frame_width, frame_height):
    """Mirror Android/WebView RoadRegionSelector pixel-for-pixel."""
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("Frame dimensions must be positive")
    aspect_ratio = frame_width / frame_height
    if aspect_ratio < 1 - ROAD_ORIENTATION_EPSILON:
        orientation = "portrait"
    elif aspect_ratio > 1 + ROAD_ORIENTATION_EPSILON:
        orientation = "landscape"
    else:
        orientation = "square"
    ratios = ROAD_REGION_RATIOS[orientation]
    # Kotlin roundToInt and JavaScript Math.round round a positive half upward;
    # Python's built-in round uses ties-to-even and would drift at .5 boundaries.
    top = min(frame_height - 1, max(0, positive_half_up(frame_height * ratios["top"])))
    bottom = min(frame_height, max(top + 1,
        positive_half_up(frame_height * ratios["bottom"])))
    return {"x": 0, "y": top, "width": frame_width, "height": bottom - top,
            "orientation": orientation, "top_ratio": ratios["top"],
            "bottom_ratio": ratios["bottom"]}


def encode_view(path, max_dim, quality=85, road_crop=False, enhance=False):
    from PIL import Image
    image = Image.open(path).convert("RGB")
    source = {"width": image.width, "height": image.height}
    road_region = select_road_region(image.width, image.height) if road_crop else None
    if road_region:
        x, y = road_region["x"], road_region["y"]
        image = image.crop((x, y, x + road_region["width"], y + road_region["height"]))
    # Match both native live analysis and WebView saved-frame replay: crop first,
    # then downscale only. Upscaling cannot recover road detail and changes the request.
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
        "max_dim": max_dim, "jpeg_quality": quality, "road_region": road_region,
        **light, "bytes_sha256": sha(raw),
    }


def entry_paths(entry):
    return (entry.get("frames") or [entry["path"]])[:3]


def entry_mode(entry):
    if entry.get("mode") in {"manual", "drive"}:
        return entry["mode"]
    return "drive" if "dashcam" in str(entry.get("source", "")).lower() else "manual"


def prepare_event(entry, root, mode):
    paths = [root / path for path in entry_paths(entry)]
    primary = int(entry.get("primary_index", 0))
    primary = primary if 0 <= primary < len(paths) else 0
    views, transforms = [], []
    if mode == "manual":
        view, meta = encode_view(paths[primary], 2000, 85, False, True)
        views.append(view); transforms.append(meta)
        note = "\n- Capture layout: one user-framed full image."
    else:
        context, meta = encode_view(paths[primary], 768, 82, False, False)
        views.append(context); transforms.append({"role": "primary_context", **meta})
        for index, path in enumerate(paths):
            view, meta = encode_view(
                path, MAX_PREPARED_ROAD_DIMENSION, 85, True, True
            )
            views.append(view); transforms.append({"role": "chronological_road_crop", "frame_index": index, **meta})
        note = (f"\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. "
                f"Images 2-{len(views)} are orientation-aware road-region crops in chronological order; "
                f"the sharpest crop is chronological frame {primary + 1}.")
    return views, transforms, note


def build_request(views, prompt, model, detail, mode="drive"):
    model, detail = normalise_config(model, detail)
    content = [{"type": "input_image", "image_url": url, "detail": detail} for url in views[:4]]
    image_count = len(content)
    content.append({"type": "input_text", "text":
        f"{prompt}\n\nThe {image_count} supplied image(s) are ordered exactly as labelled by the capture pipeline."})
    request = {
        "model": model,
        "reasoning": {"effort": ("low" if mode == "drive" else "none")
                      if model == "gpt-5.6" else "minimal"},
        "store": False,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": "pothole_binary_assessment",
                              "schema": SCHEMA, "strict": True}, "verbosity": "low"},
    }
    if mode == "drive":
        # Match the shipped native streaming request. An eval completion that needs
        # more output than production permits is not a valid production result.
        request["max_output_tokens"] = NATIVE_DRIVE_MAX_OUTPUT_TOKENS
    return request


def decision(result, mode="drive", source_view_count=3):
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
            with urllib.request.urlopen(request, timeout=120) as response:
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


def binary_label(label):
    if label in {"pothole", "pothole_cavity"}:
        return True
    if label in {"not_pothole", "not_reportable", "none",
                 "surface_breakup", "rut_or_depression", "other_road_damage"}:
        return False
    # The retired failed_patch class did not record whether the failed repair contained
    # a distinct cavity, so it cannot be converted into binary truth without relabelling.
    if label == "failed_patch":
        return None
    return None


def cluster_interval(values, seed=17, samples=3000):
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        picked = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(picked) / len(picked))
    means.sort()
    return sum(values) / len(values), means[int(.025 * samples)], means[int(.975 * samples)]


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
    parser.add_argument("--details", default="high", help="comma-separated high/original")
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
    missing = [path for entry in entries for path in entry_paths(entry) if not (root / path).exists()]
    if missing:
        sys.exit(f"{len(missing)} labelled images not found under {root}, first: {missing[0]}\n"
                 "Images are not committed; see eval/README.md.")

    variants = prompts()
    chosen = [x for x in args.arms.split(",") if x]
    unknown = [x for x in chosen if x not in variants]
    if unknown:
        sys.exit(f"unknown prompt arm(s): {unknown}; available: {sorted(variants)}")
    configs = []
    for arm in chosen:
        for model in filter(None, args.models.split(",")):
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
                          "detail": images[0]["detail"], "schema_version": SCHEMA_VERSION,
                          "transform": sample[4]}, indent=1))
        return

    key = load_key()
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    cache_dir = outdir / "cache"; cache_dir.mkdir(exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = pool.map(lambda job: call(key, job[3], cache_dir,
                                            f"{job[0]}|{job[1].get('event_id') or job[1]['path']}|{job[2]}"), jobs)
        for index, (job, returned) in enumerate(zip(jobs, results), 1):
            name, entry, trial, body, transforms = job
            result, cached, cache_key = returned
            rows.append({"arm": name, "event": entry.get("event_id") or entry["path"],
                         "image": entry["path"], "label": entry["label"],
                         "labelled_by": entry.get("labelled_by"), "trial": trial,
                         "decision": decision(result, args.mode, len(entry_paths(entry))), "cached": cached,
                         "request_hash": cache_key, "transforms": transforms, **result})
            if index % 25 == 0:
                print(f"  {index}/{len(jobs)}")

    (outdir / "raw.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    summary = {}
    print("\n=== event-clustered binary results ===")
    for name, _, _, _ in configs:
        arm_rows = [row for row in rows if row["arm"] == name and "error" not in row]
        def rates(source_rows):
            event_rows = defaultdict(list)
            for row in source_rows:
                if binary_label(row["label"]) is not None:
                    event_rows[row["event"]].append(row)
            positives, negatives, confusion = [], [], Counter()
            for grouped in event_rows.values():
                truth = binary_label(grouped[0]["label"])
                rate = sum(row["decision"] == "accept" for row in grouped) / len(grouped)
                (positives if truth else negatives).append(rate)
                for row in grouped:
                    predicted = "pothole" if row["decision"] == "accept" else "not_pothole"
                    confusion[(grouped[0]["label"], predicted)] += 1
            recall = cluster_interval(positives)
            false_rate = cluster_interval(negatives)
            return positives, negatives, recall, false_rate, confusion

        provisional = rates(arm_rows)
        verified_rows = [row for row in arm_rows
                         if str(row.get("labelled_by", "")).strip().lower() == "owner"]
        verified = rates(verified_rows)
        vp, vn, vr, vf, vc = verified
        pp, pn, pr, pf, pc = provisional
        summary[name] = {
            "verified": {"positive_events": len(vp), "negative_events": len(vn),
                         "recall": vr[0] if vp else None,
                         "recall_cluster_95": list(vr[1:]) if vp else None,
                         "false_accept_rate": vf[0] if vn else None,
                         "false_accept_cluster_95": list(vf[1:]) if vn else None,
                         "confusion": {f"{a}->{b}": n for (a, b), n in vc.items()}},
            "provisional_including_unverified": {
                "positive_events": len(pp), "negative_events": len(pn),
                "recall": pr[0] if pp else None, "recall_cluster_95": list(pr[1:]) if pp else None,
                "false_accept_rate": pf[0] if pn else None,
                "false_accept_cluster_95": list(pf[1:]) if pn else None,
                "confusion": {f"{a}->{b}": n for (a, b), n in pc.items()}},
        }
        recall_text = f"{vr[0]:.1%} [{vr[1]:.1%}, {vr[2]:.1%}]" if vp else "n/a"
        false_text = f"{vf[0]:.1%} [{vf[1]:.1%}, {vf[2]:.1%}]" if vn else "n/a"
        print(f"  {name:48} VERIFIED recall {recall_text} · false accept {false_text} "
              f"({len(vp)} positive, {len(vn)} negative events)")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(), "git_commit": git_commit(),
        "mode": args.mode, "trials_per_event": args.trials,
        "prompt_version": effective_prompt_version(args.mode),
        "schema_version": SCHEMA_VERSION, "schema_sha256": sha(json.dumps(SCHEMA, sort_keys=True)),
        "labels_sha256": sha(label_bytes), "configs": [config[0] for config in configs],
        "warning": "The small set is not a release gate; collect diverse verified events and lock a held-out split.",
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\nwrote raw.jsonl, manifest.json, summary.json and response cache under {outdir}")


if __name__ == "__main__":
    main()
