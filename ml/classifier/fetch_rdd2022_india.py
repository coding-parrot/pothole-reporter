#!/usr/bin/env python3
"""Fetch only the India subset of RDD2022 from the figshare record.

The per-country links on the Sekimoto Lab README (bigdatacup S3 bucket) answered
403 on 2026-10-07, so the source is the figshare deposit, a single 13.26 GB zip of
all six countries, each stored inside it as its own uncompressed zip. The outer
central directory is read over an HTTP Range request, then only the byte range of
RDD2022/India.zip (527 MB) is downloaded and unpacked. No other country is fetched.

Record: https://doi.org/10.6084/m9.figshare.21431547.v1
Licence: CC BY 4.0 on the figshare record; the Sekimoto Lab README states
CC BY-SA 4.0 for the images. See MODEL_CARD.md for the attribution.
"""
import hashlib
import io
import json
import struct
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

URL = "https://ndownloader.figshare.com/files/38030910"
EXPECTED_SIZE = 13264172619
EXPECTED_MD5 = "b62bd51d2ffcfaa76c60f234f0cc2bb3"  # whole zip, reported by figshare
WORK = Path(__file__).resolve().parent / "work"
OUT = WORK / "rdd2022_india"
CHUNK = 64 * 1024 * 1024


def fetch(start, end):
    """Bytes [start, end) of the remote zip. The presigned redirect lasts 10 s, so
    every request resolves it again."""
    for attempt in range(5):
        try:
            request = urllib.request.Request(URL, headers={
                "Range": f"bytes={start}-{end - 1}", "User-Agent": "curl/8"})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            if len(data) == end - start:
                return data
        except Exception as error:  # noqa: BLE001 - retried, then reported
            last = error
    raise RuntimeError(f"range {start}-{end} failed: {last}")


class Tail(io.RawIOBase):
    """A read-only view of the remote file that only ever serves a cached tail."""

    def __init__(self, tail_start, tail):
        self.tail_start, self.tail, self.position = tail_start, tail, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = (offset if whence == 0 else
                         self.position + offset if whence == 1 else EXPECTED_SIZE + offset)
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size < 0:
            size = EXPECTED_SIZE - self.position
        if self.position < self.tail_start:
            raise RuntimeError("central directory starts before the cached tail")
        begin = self.position - self.tail_start
        data = self.tail[begin:begin + size]
        self.position += len(data)
        return data


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    done = OUT / "fetch-receipt.json"
    if done.exists():
        print(done.read_text())
        return
    tail_size = 1024 * 1024
    tail_start = EXPECTED_SIZE - tail_size
    outer = zipfile.ZipFile(Tail(tail_start, fetch(tail_start, EXPECTED_SIZE)))
    # The deposit is a zip of one stored (uncompressed) zip per country.
    member = outer.getinfo("RDD2022/India.zip")
    if member.compress_type != zipfile.ZIP_STORED:
        sys.exit("India.zip is not stored; a byte range would not be a valid zip")
    name_length, extra_length = struct.unpack(
        "<HH", fetch(member.header_offset + 26, member.header_offset + 30))
    low = member.header_offset + 30 + name_length + extra_length
    high = low + member.compress_size
    print(f"India.zip is bytes {low}..{high} ({(high - low) / 1e6:.0f} MB)", flush=True)
    inner_path = WORK / "rdd2022_india.zip"
    have = inner_path.stat().st_size if inner_path.exists() else 0
    with open(inner_path, "ab") as inner:
        position = low + have
        while position < high:
            step = min(CHUNK, high - position)
            inner.write(fetch(position, position + step))
            position += step
            print(f"  {(position - low) / 1e6:.0f} MB", flush=True)
    crc = 0
    digest = hashlib.sha256()
    with open(inner_path, "rb") as inner:
        while block := inner.read(8 * 1024 * 1024):
            crc = zlib.crc32(block, crc)
            digest.update(block)
    if crc != member.CRC:
        sys.exit("India.zip does not match the CRC-32 in the outer zip")
    counts = {"images": 0, "annotations": 0}
    with zipfile.ZipFile(inner_path) as inner:
        for info in inner.infolist():
            if info.is_dir() or "India/" not in info.filename:
                continue
            relative = info.filename.split("India/", 1)[1]
            target = OUT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(inner.read(info))
            if relative.endswith(".jpg"):
                counts["images"] += 1
            elif relative.endswith(".xml"):
                counts["annotations"] += 1
    inner_path.unlink()
    receipt = {
        "source_url": URL,
        "record": "https://doi.org/10.6084/m9.figshare.21431547.v1",
        "fetched_on": "2026-10-07",
        "whole_zip_bytes": EXPECTED_SIZE,
        "whole_zip_md5_reported": EXPECTED_MD5,
        "member": "RDD2022/India.zip",
        "member_bytes": member.compress_size,
        "member_sha256": digest.hexdigest(),
        "integrity": "India.zip matched the CRC-32 recorded in the outer zip",
        **counts,
    }
    done.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
