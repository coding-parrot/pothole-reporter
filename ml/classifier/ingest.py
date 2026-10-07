"""Add whole public-dataset images to work/frames as prepared drive frames.

Every image gets the app's Drive Mode preparation (common.prepare_drive_jpeg: the whole
frame, downscaled only, never cropped) and one line in work/frames/index.jsonl. The raw
download is not kept. A 64-bit difference hash of each frame is stored so the same
picture re-uploaded under another dataset's name can be found (build_manifest.py keeps
such twins out of training when one of them is held out).
"""
import io
import json
import os
import threading

from common import FRAMES, prepare_drive_jpeg, sha256_hex

INDEX = FRAMES / "index.jsonl"
_lock = threading.Lock()


def dhash(image):
    """Difference hash: 9 x 8 greyscale, one bit per horizontal neighbour pair."""
    from PIL import Image

    small = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    pixels = list(small.getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return f"{bits:016x}"


def read_index():
    rows = {}
    if INDEX.exists():
        for line in INDEX.read_text().splitlines():
            row = json.loads(line)
            rows[row["path"]] = row
    return rows


def write_index(rows):
    temporary = INDEX.with_suffix(".jsonl.tmp")
    with open(temporary, "w") as output:
        for path in sorted(rows):
            output.write(json.dumps(rows[path], sort_keys=True) + "\n")
    os.replace(temporary, INDEX)


def prepare(raw, source, name, domain, **extra):
    """Raw image bytes in; the prepared frame is written and its index row returned."""
    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(raw))
    width, height = image.size
    # Phone photos carry their rotation in EXIF; the app's camera frames are upright.
    image = ImageOps.exif_transpose(image)
    jpeg = prepare_drive_jpeg(image)
    target = FRAMES / source
    target.mkdir(parents=True, exist_ok=True)
    stem = name.rsplit(".", 1)[0].replace("/", "__").replace(" ", "_")
    (target / f"{stem}.jpg").write_bytes(jpeg)
    return {"path": f"{source}/{stem}.jpg", "sha256": sha256_hex(jpeg), "domain": domain,
            "source": source, "raw_sha256": sha256_hex(raw), "raw_size": [width, height],
            "dhash": dhash(Image.open(io.BytesIO(jpeg))), **extra}


class Appender:
    """Collects rows from worker threads and saves the index every few hundred frames,
    so an interrupted fetch resumes from what it already has."""

    def __init__(self, every=300):
        self.rows = read_index()
        self.every, self.pending = every, 0

    def has(self, path):
        return path in self.rows and (FRAMES / path).exists()

    def add(self, row):
        with _lock:
            self.rows[row["path"]] = row
            self.pending += 1
            if self.pending >= self.every:
                write_index(self.rows)
                self.pending = 0

    def close(self):
        with _lock:
            write_index(self.rows)
            self.pending = 0
