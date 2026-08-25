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
ROAD_BAND = 0.60
PROMPT_VERSION = "road-damage-v4"
SCHEMA_VERSION = 4

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["looks_like_speed_breaker", "reportable", "assessment", "image_quality", "damage_type",
                 "on_drivable_surface", "has_broken_edge_or_rim",
                 "has_depth_or_surface_loss", "temporal_consistency", "size", "description"],
    "properties": {
        "looks_like_speed_breaker": {"type": "boolean"},
        "reportable": {"type": "boolean"},
        "assessment": {"type": "string", "enum": ["clear", "probable", "uncertain", "absent"]},
        "image_quality": {"type": "string", "enum": ["usable", "degraded", "unusable"]},
        "damage_type": {"type": "string", "enum": ["pothole_cavity", "failed_patch",
            "surface_breakup", "rut_or_depression", "other_road_damage", "none"]},
        "on_drivable_surface": {"type": "boolean"},
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


def prompts():
    """Read the live prompt from the pure client so the control cannot drift."""
    src = (ROOT / "static" / "standalone.js").read_text()
    found = re.search(r"const DETECT_PROMPT = `(.*?)`;", src, re.S)
    if not found:
        sys.exit("could not find DETECT_PROMPT in static/standalone.js")
    variants = {"baseline": found.group(1)}
    extra = ROOT / "eval" / "prompts"
    if extra.is_dir():
        for path in sorted(extra.glob("*.txt")):
            variants[path.stem] = path.read_text().rstrip("\n")
    return variants


def normalise_config(model, detail):
    model = model if model in ALLOWED_MODELS else DEFAULT_MODEL
    detail = detail if detail in ALLOWED_DETAILS else "high"
    if detail == "original" and model != "gpt-5.6":
        detail = "high"
    return model, detail


def adaptive_lift(image):
    """Mirror the client's sampled RGB luma test on the already-resized view."""
    from PIL import ImageEnhance
    pixels = image.load()
    step = max(1, math.floor(math.sqrt((image.width * image.height) / 12000)))
    total = count = clipped_dark = clipped_bright = 0
    for y in range(0, image.height, step):
        for x in range(0, image.width, step):
            red, green, blue = pixels[x, y]
            luminance = .2126 * red + .7152 * green + .0722 * blue
            total += luminance
            count += 1
            clipped_dark += luminance < 12
            clipped_bright += luminance > 245
    mean = total / max(1, count)
    dark = clipped_dark / max(1, count)
    bright = clipped_bright / max(1, count)
    if mean >= 72 or bright >= .08:
        return image, {"luminance": mean, "dark": dark, "bright": bright,
                       "enhanced": False}
    lift = min(1.65, max(1.15, 85 / max(35, mean)))
    image = ImageEnhance.Brightness(image).enhance(lift)
    image = ImageEnhance.Contrast(image).enhance(1.10)
    return image, {"luminance": mean, "dark": dark, "bright": bright,
                   "enhanced": True, "brightness": lift}


def encode_view(path, max_dim, quality=85, band=1.0, enhance=False):
    from PIL import Image
    image = Image.open(path).convert("RGB")
    source = {"width": image.width, "height": image.height}
    if band < 1:
        height = max(1, round(image.height * band))
        image = image.crop((0, image.height - height, image.width, image.height))
    scale = min(1.0, max_dim / max(image.size))
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    light = {"enhanced": False}
    if enhance:
        image, light = adaptive_lift(image)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    raw = buf.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode(), {
        "source": source, "output": {"width": image.width, "height": image.height},
        "max_dim": max_dim, "jpeg_quality": quality, "road_band": band,
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
        view, meta = encode_view(paths[primary], 2000, 85, 1.0, True)
        views.append(view); transforms.append(meta)
        note = "\n- Capture layout: one user-framed full image."
    else:
        context, meta = encode_view(paths[primary], 768, 82, 1.0, False)
        views.append(context); transforms.append({"role": "primary_context", **meta})
        for index, path in enumerate(paths):
            view, meta = encode_view(path, 1024, 85, ROAD_BAND, True)
            views.append(view); transforms.append({"role": "chronological_road_crop", "frame_index": index, **meta})
        note = (f"\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. "
                f"Images 2-{len(views)} are lower-road crops in chronological order; "
                f"the sharpest crop is chronological frame {primary + 1}.")
    return views, transforms, note


def build_request(views, prompt, model, detail):
    model, detail = normalise_config(model, detail)
    content = [{"type": "input_image", "image_url": url, "detail": detail} for url in views[:4]]
    image_count = len(content)
    content.append({"type": "input_text", "text":
        f"{prompt}\n\nThe {image_count} supplied image(s) are ordered exactly as labelled by the capture pipeline."})
    return {
        "model": model,
        "reasoning": {"effort": "none" if model == "gpt-5.6" else "minimal"},
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": "road_damage_assessment",
                              "schema": SCHEMA, "strict": True}, "verbosity": "low"},
    }


def decision(result):
    if not result or result.get("looks_like_speed_breaker") is not False:
        return "reject"
    if result.get("reportable") is not True or result.get("damage_type") == "none":
        return "reject"
    if result.get("on_drivable_surface") is not True or result.get("assessment") == "absent":
        return "reject"
    if (result.get("image_quality") == "unusable" or result.get("assessment") == "uncertain"
            or result.get("temporal_consistency") == "inconsistent"):
        return "review"
    if result.get("assessment") not in {"clear", "probable"}:
        return "review"
    if not result.get("has_broken_edge_or_rim") and not result.get("has_depth_or_surface_loss"):
        return "review"
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
    if label in {"pothole", "pothole_cavity", "failed_patch", "surface_breakup",
                 "rut_or_depression", "other_road_damage", "reportable"}:
        return True
    if label in {"not_pothole", "not_reportable", "none"}:
        return False
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--arms", default="baseline", help="comma-separated prompt arms")
    parser.add_argument("--models", default=DEFAULT_MODEL, help="comma-separated model IDs")
    parser.add_argument("--details", default="high", help="comma-separated high/original")
    parser.add_argument("--mode", choices=["manual", "drive"], default="drive")
    parser.add_argument("--images-root", default=str(ROOT / "eval" / "images"))
    parser.add_argument("--labels", default=str(ROOT / "eval" / "labels.json"))
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="first N matching events; smoke tests only")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    parser.add_argument("--dry-run", action="store_true", help="validate and print requests without API calls")
    args = parser.parse_args()

    label_bytes = Path(args.labels).read_bytes()
    all_entries = json.loads(label_bytes)["images"]
    entries = [entry for entry in all_entries if entry_mode(entry) == args.mode]
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
            body = build_request(views, prompt + note, model, detail)
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
                         "decision": decision(result), "cached": cached,
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
                    confusion[(grouped[0]["label"], row.get("damage_type", "none"))] += 1
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
        "mode": args.mode, "trials_per_event": args.trials, "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION, "schema_sha256": sha(json.dumps(SCHEMA, sort_keys=True)),
        "labels_sha256": sha(label_bytes), "configs": [config[0] for config in configs],
        "warning": "The seed set is not a release gate until it contains verified positives and negatives.",
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\nwrote raw.jsonl, manifest.json, summary.json and response cache under {outdir}")


if __name__ == "__main__":
    main()
