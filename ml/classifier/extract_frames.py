#!/usr/bin/env python3
"""Turn the private drive videos and RDD2022 India into prepared drive frames.

Videos are sampled at 2 frames a second with ffmpeg (autorotated, whole frame, PNG so
the only JPEG generation is the app's own), then each frame gets the app's Drive Mode
preparation. RDD2022 images get the same preparation. Output is one JPEG per frame in
work/frames/<source>/ and work/frames/index.jsonl, one line per frame.
"""
import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from common import FRAMES, WORK, prepare_drive_jpeg, sha256_hex

VIDEO_SETS = {
    # source prefix: directory. The two Downloads clips are a different recording from
    # the Desktop clips of the same number, so the prefix keeps them apart.
    "desktop": Path.home() / "Desktop" / "pothole video segments",
    "downloads": Path.home() / "Downloads",
}
FPS = 2


def video_frames(prefix, video):
    from PIL import Image

    source = f"{prefix}-{video.stem.replace('_', '-')}"
    target = FRAMES / source
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(dir=WORK) as scratch:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
            "-map", "0:v:0", "-vf", f"fps={FPS}", "-start_number", "0",
            f"{scratch}/%05d.png",
        ], check=True)
        for png in sorted(Path(scratch).glob("*.png")):
            index = int(png.stem)
            jpeg = prepare_drive_jpeg(Image.open(png))
            name = f"{source}__t{index * 1000 // FPS:07d}.jpg"
            (target / name).write_bytes(jpeg)
            rows.append({
                "path": f"{source}/{name}", "sha256": sha256_hex(jpeg),
                "domain": "drive_video", "source": source,
                "seconds": index / FPS,
            })
    return rows


def rdd_frame(path):
    from PIL import Image

    split = path.parent.parent.name  # train or test
    target = FRAMES / f"rdd2022-india-{split}"
    target.mkdir(parents=True, exist_ok=True)
    jpeg = prepare_drive_jpeg(Image.open(path))
    (target / path.name).write_bytes(jpeg)
    return {
        "path": f"rdd2022-india-{split}/{path.name}", "sha256": sha256_hex(jpeg),
        "domain": "rdd2022_india", "source": f"rdd2022-india-{split}",
        "rdd_index": int(path.stem.split("_")[-1]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["videos", "rdd"], default=None)
    args = parser.parse_args()
    FRAMES.mkdir(parents=True, exist_ok=True)
    index_path = FRAMES / "index.jsonl"
    rows = {}
    if index_path.exists():
        for line in index_path.read_text().splitlines():
            row = json.loads(line)
            rows[row["path"]] = row
    if args.only in (None, "videos"):
        jobs = []
        for prefix, directory in VIDEO_SETS.items():
            jobs += [(prefix, video) for video in sorted(directory.glob("segment_*.mp4"))]
        with ProcessPoolExecutor(max_workers=3) as pool:
            for result in pool.map(video_frames, *zip(*jobs)):
                for row in result:
                    rows[row["path"]] = row
                print(result[0]["source"], len(result), flush=True)
    if args.only in (None, "rdd"):
        images = sorted((WORK / "rdd2022_india").glob("*/images/*.jpg"))
        with ProcessPoolExecutor(max_workers=3) as pool:
            for count, row in enumerate(pool.map(rdd_frame, images, chunksize=64), 1):
                rows[row["path"]] = row
                if count % 1000 == 0:
                    print("rdd", count, flush=True)
    with open(index_path, "w") as output:
        for path in sorted(rows):
            output.write(json.dumps(rows[path], sort_keys=True) + "\n")
    print("frames", len(rows))


if __name__ == "__main__":
    main()
