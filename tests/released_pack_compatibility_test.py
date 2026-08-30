#!/usr/bin/env python3
"""Keep every pack used by a released Android build available on Pages.

The checked-in index makes this test useful in shallow checkouts. In a
full clone the index is also compared with every v1.* tag, so a new release cannot
silently add an unprotected pack. No network access is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INDEX = ROOT / "tests" / "fixtures" / "released-android-packs-v1.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompatibilityError(RuntimeError):
    pass


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise CompatibilityError(result.stderr.strip() or "git command failed")
    return result.stdout


def pack_records(value, source: str):
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str) and path.startswith("packs/"):
            yield path, value.get("bytes"), value.get("sha256"), source
        for child in value.values():
            yield from pack_records(child, source)
    elif isinstance(value, list):
        for child in value:
            yield from pack_records(child, source)


def merge(records):
    merged: dict[str, dict] = {}
    for path, byte_count, digest, source in records:
        parts = pathlib.PurePosixPath(path).parts
        if not parts or parts[0] != "packs" or any(part in ("", ".", "..") for part in parts):
            raise CompatibilityError(f"unsafe pack path in {source}: {path!r}")
        if byte_count is not None and (isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0):
            raise CompatibilityError(f"invalid byte count for {path} in {source}")
        if digest is not None and (not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise CompatibilityError(f"invalid SHA-256 for {path} in {source}")
        prior = merged.setdefault(path, {"bytes": byte_count, "sha256": digest, "sources": []})
        for field, value in (("bytes", byte_count), ("sha256", digest)):
            if value is not None and prior[field] not in (None, value):
                raise CompatibilityError(f"conflicting {field} for {path}: {source}")
            if value is not None:
                prior[field] = value
        prior["sources"].append(source)
    return merged


def current_records():
    records = []
    for path in sorted(DOCS.glob("*manifest*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CompatibilityError(f"cannot read {path.relative_to(ROOT)}: {error}") from error
        records.extend(pack_records(payload, path.relative_to(ROOT).as_posix()))
    if not records:
        raise CompatibilityError("no current manifest pack references found")
    return records


def released_tags() -> list[str]:
    return git("tag", "-l", "v1.*", "--sort=version:refname").splitlines()


def released_records(tags: list[str]):
    records = []
    first_seen: dict[str, str] = {}
    for tag in tags:
        files = git("ls-tree", "-r", "--name-only", tag, "--", "android-app/www").splitlines()
        manifests = [
            path for path in files
            if path.endswith(".json") and "manifest" in pathlib.PurePosixPath(path).name
        ]
        for manifest in manifests:
            source = f"{tag}:{manifest}"
            try:
                payload = json.loads(git("show", source))
            except json.JSONDecodeError as error:
                raise CompatibilityError(f"invalid released manifest {source}: {error}") from error
            found = list(pack_records(payload, source))
            records.extend(found)
            for path, _bytes, _sha, _source in found:
                first_seen.setdefault(path, tag)
    return records, first_seen


def load_index():
    try:
        payload = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompatibilityError(f"cannot read compatibility index: {error}") from error
    if payload.get("format") != "pothole-released-android-pack-index" or payload.get("schema_version") != 1:
        raise CompatibilityError("unsupported released-pack compatibility index")
    tags = payload.get("released_tags")
    resources = payload.get("resources")
    if not isinstance(tags, list) or not isinstance(resources, list):
        raise CompatibilityError("malformed released-pack compatibility index")
    records = [
        (item.get("path"), item.get("bytes"), item.get("sha256"), "compatibility index")
        for item in resources if isinstance(item, dict)
    ]
    if len(records) != len(resources) or any(not isinstance(item[0], str) for item in records):
        raise CompatibilityError("malformed resource in released-pack compatibility index")
    return tags, records


def index_payload(tags: list[str], records, first_seen: dict[str, str]):
    resources = []
    for path, metadata in sorted(merge(records).items()):
        if metadata["bytes"] is None or metadata["sha256"] is None:
            raise CompatibilityError(f"released pack lacks bytes/SHA-256 metadata: {path}")
        resources.append({
            "bytes": metadata["bytes"], "first_tag": first_seen[path],
            "path": path, "sha256": metadata["sha256"],
        })
    return {
        "format": "pothole-released-android-pack-index",
        "schema_version": 1,
        "released_tags": tags,
        "resources": resources,
    }


def validate_files(references) -> None:
    for relative, metadata in sorted(references.items()):
        path = DOCS.joinpath(*pathlib.PurePosixPath(relative).parts)
        if path.is_symlink() or not path.is_file():
            raise CompatibilityError(f"missing published pack: docs/{relative}")
        content = path.read_bytes()
        if metadata["bytes"] is not None and len(content) != metadata["bytes"]:
            raise CompatibilityError(f"byte-count mismatch: docs/{relative}")
        if metadata["sha256"] is not None and hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise CompatibilityError(f"SHA-256 mismatch: docs/{relative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-index", action="store_true")
    args = parser.parse_args()
    try:
        tags = released_tags()
        shallow = git("rev-parse", "--is-shallow-repository").strip() == "true"
        if args.write_index:
            if shallow or not tags:
                raise CompatibilityError("a full clone with v1.* tags is required to write the index")
            records, first_seen = released_records(tags)
            INDEX.write_text(json.dumps(index_payload(tags, records, first_seen), indent=2) + "\n", encoding="utf-8")
            print(f"wrote {INDEX.relative_to(ROOT)}")
            return 0

        indexed_tags, indexed_records = load_index()
        indexed = merge(indexed_records)
        if not shallow and tags:
            actual_records, first_seen = released_records(tags)
            actual_payload = index_payload(tags, actual_records, first_seen)
            expected_payload = json.loads(INDEX.read_text(encoding="utf-8"))
            if actual_payload != expected_payload:
                raise CompatibilityError(
                    "released-pack index is stale; run this test with --write-index in a full clone"
                )
        validate_files(merge([*current_records(), *indexed_records]))
        print(
            f"RELEASED PACK COMPATIBILITY PASS "
            f"({len(indexed_tags)} tags, {len(indexed)} immutable packs)"
        )
        return 0
    except CompatibilityError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
