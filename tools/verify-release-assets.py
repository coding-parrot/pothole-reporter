#!/usr/bin/env python3
"""Fail closed when release web assets are stale, divergent, or unexpectedly bundled."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile


CORDOVA_GENERATED_ASSETS = frozenset({"cordova.js", "cordova_plugins.js"})
HOSTED_DOC_FILES = frozenset({
    "BMC_PILOT.md",
    "CATALOG_REFRESH.md",
    "DEMO.md",
    "SOURCES.md",
    "architecture.excalidraw",
    "architecture.png",
    "coverage-overview.svg",
    "example-pothole-thumb.jpg",
    "example-pothole.jpg",
    "privacy.html",
    "sources.html",
})
HOSTED_DOC_PREFIXES = ("packs/",)
AAB_PUBLIC_PREFIX = "base/assets/public/"
APK_PUBLIC_PREFIX = "assets/public/"


class AssetVerificationError(RuntimeError):
    pass


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        raise AssetVerificationError(f"asset directory is missing: {root}")
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def _format_names(names: set[str]) -> str:
    ordered = sorted(names)
    shown = ordered[:8]
    suffix = f" (+{len(ordered) - len(shown)} more)" if len(ordered) > len(shown) else ""
    return ", ".join(shown) + suffix


def _require_same_set(expected: set[str], actual: set[str], label: str) -> None:
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing [{_format_names(missing)}]")
        if extra:
            details.append(f"unexpected [{_format_names(extra)}]")
        raise AssetVerificationError(f"{label} file set differs: {'; '.join(details)}")


def _require_same_bytes(expected: dict[str, Path], actual: dict[str, Path], label: str) -> None:
    for relative_path in sorted(expected):
        if expected[relative_path].read_bytes() != actual[relative_path].read_bytes():
            raise AssetVerificationError(f"{label} content differs: {relative_path}")


def verify_source_trees(static: Path, www: Path, docs: Path, packaged: Path) -> None:
    static_files = _files(static)
    www_files = _files(www)
    docs_files = _files(docs)
    packaged_files = _files(packaged)

    # static/ is the canonical app asset set. Android www must be an exact mirror,
    # while docs/ may additionally contain only the explicitly hosted pages/data packs.
    _require_same_set(set(static_files), set(www_files), "static-to-www mirror")
    _require_same_bytes(static_files, www_files, "static-to-www mirror")

    missing_docs = set(static_files) - set(docs_files)
    if missing_docs:
        raise AssetVerificationError(
            f"static-to-docs mirror file set differs: missing [{_format_names(missing_docs)}]"
        )
    unexpected_docs = {
        name for name in set(docs_files) - set(static_files)
        if name not in HOSTED_DOC_FILES
        and not any(name.startswith(prefix) for prefix in HOSTED_DOC_PREFIXES)
    }
    if unexpected_docs:
        raise AssetVerificationError(
            "static-to-docs mirror has unclassified hosted files: "
            f"[{_format_names(unexpected_docs)}]"
        )
    _require_same_bytes(static_files, docs_files, "static-to-docs mirror")

    expected_packaged = set(www_files) | set(CORDOVA_GENERATED_ASSETS)
    _require_same_set(expected_packaged, set(packaged_files), "www-to-Android mirror")
    _require_same_bytes(www_files, packaged_files, "www-to-Android mirror")


def _verify_archive_assets(
    packaged: Path,
    artifact: Path,
    public_prefix: str,
    artifact_label: str,
) -> None:
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise AssetVerificationError(f"release {artifact_label} is missing or empty: {artifact}")
    packaged_files = _files(packaged)
    with zipfile.ZipFile(artifact) as archive:
        public_entries: dict[str, str] = {}
        duplicates: set[str] = set()
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(public_prefix):
                continue
            relative_path = info.filename[len(public_prefix):]
            if not relative_path:
                continue
            if relative_path in public_entries:
                duplicates.add(relative_path)
            public_entries[relative_path] = info.filename
        if duplicates:
            raise AssetVerificationError(
                f"{artifact_label} has duplicate public assets: [{_format_names(duplicates)}]"
            )
        _require_same_set(
            set(packaged_files),
            set(public_entries),
            f"{artifact_label} public assets",
        )
        for relative_path in sorted(packaged_files):
            if archive.read(public_entries[relative_path]) != packaged_files[relative_path].read_bytes():
                raise AssetVerificationError(
                    f"{artifact_label} asset differs from source: {relative_path}"
                )


def verify_aab(packaged: Path, aab: Path) -> None:
    _verify_archive_assets(packaged, aab, AAB_PUBLIC_PREFIX, "AAB")


def verify_apk(packaged: Path, apk: Path) -> None:
    _verify_archive_assets(packaged, apk, APK_PUBLIC_PREFIX, "APK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--www", type=Path, required=True)
    parser.add_argument("--docs", type=Path, required=True)
    parser.add_argument("--packaged", type=Path, required=True)
    parser.add_argument("--aab", type=Path)
    parser.add_argument("--apk", type=Path)
    args = parser.parse_args()
    try:
        verify_source_trees(args.static, args.www, args.docs, args.packaged)
        if args.aab is not None:
            verify_aab(args.packaged, args.aab)
        if args.apk is not None:
            verify_apk(args.packaged, args.apk)
    except (AssetVerificationError, OSError, zipfile.BadZipFile) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("Release asset mirrors verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
