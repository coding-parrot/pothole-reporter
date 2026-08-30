#!/usr/bin/env python3
"""Focused safety tests for the generated catalog-pack pruner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "prune-catalog-packs.py"
SPEC = importlib.util.spec_from_file_location("catalog_pack_pruner", TOOL)
assert SPEC is not None and SPEC.loader is not None
PRUNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PRUNER
SPEC.loader.exec_module(PRUNER)


def snapshot(root: pathlib.Path) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            result[relative] = f"symlink:{os.readlink(path)}".encode()
        elif path.is_file():
            result[relative] = path.read_bytes()
        elif path.is_dir():
            result[relative + "/"] = None
    return result


def pack_bytes(family, state: str, marker: str) -> bytes:
    return (
        json.dumps(
            {
                "format": family.pack_format,
                "state_code": state.upper(),
                "marker": marker,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def add_pack(root: pathlib.Path, family, state: str, marker: str):
    content = pack_bytes(family, state, marker)
    digest = hashlib.sha256(content).hexdigest()
    filename = f"{family.filename_prefix}-{digest}.json"
    path = root / family.pack_root_relative_path / state / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    manifest_path = f"{family.manifest_path_prefix}/{state}/{filename}"
    return path, manifest_path, digest


def write_manifests(root: pathlib.Path, references: dict[str, list[tuple[str, str, str]]]) -> None:
    for family in PRUNER.FAMILIES:
        resources = {}
        for index, (manifest_path, digest, state) in enumerate(references.get(family.name, [])):
            resources[f"resource-{index}"] = {
                "path": manifest_path,
                "sha256": digest,
                "state_code": state.upper(),
            }
        manifest = root / family.manifest_relative_path
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"resources": resources}) + "\n", encoding="utf-8")


def create_roots(root: pathlib.Path) -> None:
    for family in PRUNER.FAMILIES:
        (root / family.pack_root_relative_path).mkdir(parents=True, exist_ok=True)


def create_release_tag(root: pathlib.Path, tag: str = "v1.0.0") -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Pruner Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "pruner@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "--", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "--no-gpg-sign", "-m", "release"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "tag", "--no-sign", tag], check=True
    )


class CatalogPackPrunerTest(unittest.TestCase):
    def test_check_preserves_referenced_and_reports_stale_without_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[0]
            referenced_path, referenced_manifest_path, referenced_digest = add_pack(
                root, family, "mh", "live"
            )
            stale_path, _, _ = add_pack(root, family, "mh", "old")
            write_manifests(
                root,
                {
                    family.name: [
                        (referenced_manifest_path, referenced_digest, "mh"),
                    ]
                },
            )
            create_release_tag(root)
            before = snapshot(root)

            plan = PRUNER.plan_prune(root)

            self.assertEqual(
                [pack.disk_relative_path for pack in plan.referenced],
                [referenced_path.relative_to(root)],
            )
            self.assertEqual(
                [pack.disk_relative_path for pack in plan.stale],
                [stale_path.relative_to(root)],
            )
            self.assertEqual(snapshot(root), before)
            self.assertEqual(PRUNER.main(["--project-root", str(root)]), 1)
            self.assertEqual(snapshot(root), before)

    def test_apply_deletes_only_stale_packs_and_empty_leaf_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[1]
            live_path, live_manifest_path, live_digest = add_pack(root, family, "dl", "live")
            stale_beside_live, _, _ = add_pack(root, family, "dl", "old")
            stale_only, _, _ = add_pack(root, family, "ga", "old-only")
            empty_leaf = root / PRUNER.FAMILIES[2].pack_root_relative_path / "py"
            empty_leaf.mkdir(parents=True)
            write_manifests(
                root,
                {family.name: [(live_manifest_path, live_digest, "dl")]},
            )
            create_release_tag(root)

            plan = PRUNER.apply_prune(root)

            self.assertEqual(len(plan.stale), 2)
            self.assertTrue(live_path.is_file())
            self.assertFalse(stale_beside_live.exists())
            self.assertTrue(stale_beside_live.parent.is_dir())
            self.assertFalse(stale_only.exists())
            self.assertFalse(stale_only.parent.exists())
            self.assertFalse(empty_leaf.exists())
            self.assertEqual(PRUNER.main(["--check", "--project-root", str(root)]), 0)

    def test_apply_preserves_pack_referenced_only_by_a_release_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[1]
            released_path, released_manifest_path, released_digest = add_pack(
                root, family, "wb", "released"
            )
            never_released_path, _, _ = add_pack(root, family, "wb", "orphan")
            write_manifests(
                root,
                {
                    family.name: [
                        (released_manifest_path, released_digest, "wb"),
                    ]
                },
            )
            create_release_tag(root, "v1.36.0")

            live_path, live_manifest_path, live_digest = add_pack(
                root, family, "wb", "live"
            )
            write_manifests(
                root,
                {family.name: [(live_manifest_path, live_digest, "wb")]},
            )

            plan = PRUNER.apply_prune(root)

            self.assertTrue(released_path.is_file())
            self.assertTrue(live_path.is_file())
            self.assertFalse(never_released_path.exists())
            self.assertEqual(
                {pack.manifest_path for pack in plan.referenced},
                {released_manifest_path, live_manifest_path},
            )
            self.assertEqual(
                [pack.disk_relative_path for pack in plan.stale],
                [never_released_path.relative_to(root)],
            )

    def test_apply_refuses_when_v1_release_tags_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[0]
            stale_path, _, _ = add_pack(root, family, "mh", "orphan")
            write_manifests(root, {})
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            before = snapshot(root)

            with self.assertRaisesRegex(PRUNER.PruneError, r"no released v1\.\* Git tags"):
                PRUNER.apply_prune(root)

            self.assertTrue(stale_path.exists())
            self.assertEqual(snapshot(root), before)

    def test_apply_refuses_a_shallow_release_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source = base / "source"
            source.mkdir()
            create_roots(source)
            family = PRUNER.FAMILIES[0]
            add_pack(source, family, "mh", "orphan")
            write_manifests(source, {})
            create_release_tag(source)

            checkout = base / "checkout"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--depth",
                    "1",
                    source.as_uri(),
                    str(checkout),
                ],
                check=True,
            )
            before = snapshot(checkout)

            with self.assertRaisesRegex(PRUNER.PruneError, "release history is shallow"):
                PRUNER.apply_prune(checkout)

            self.assertEqual(snapshot(checkout), before)

    def test_rejects_manifest_path_traversal_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[0]
            stale_path, _, _ = add_pack(root, family, "mh", "old")
            write_manifests(root, {})
            create_release_tag(root)
            digest = "a" * 64
            write_manifests(
                root,
                {
                    family.name: [
                        (f"packs/v1/contracts/mh/../highways-{digest}.json", digest, "mh")
                    ]
                },
            )
            before = snapshot(root)

            with self.assertRaisesRegex(PRUNER.PruneError, "traversal|outside"):
                PRUNER.apply_prune(root)

            self.assertTrue(stale_path.exists())
            self.assertEqual(snapshot(root), before)

    def test_rejects_symlink_and_unexpected_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[0]
            stale_path, _, _ = add_pack(root, family, "mh", "old")
            target = root / "outside.json"
            target.write_text("do not touch\n", encoding="utf-8")
            symlink = root / family.pack_root_relative_path / "dl"
            write_manifests(root, {})
            create_release_tag(root)
            symlink.symlink_to(target)
            before = snapshot(root)

            with self.assertRaisesRegex(PRUNER.PruneError, "symlink"):
                PRUNER.apply_prune(root)

            self.assertEqual(target.read_text(encoding="utf-8"), "do not touch\n")
            self.assertTrue(stale_path.exists())
            self.assertEqual(snapshot(root), before)

            symlink.unlink()
            unexpected = root / family.pack_root_relative_path / "README.txt"
            unexpected.write_text("unexpected\n", encoding="utf-8")
            before_unexpected = snapshot(root)
            with self.assertRaisesRegex(PRUNER.PruneError, "unexpected entry"):
                PRUNER.apply_prune(root)
            self.assertEqual(snapshot(root), before_unexpected)

    def test_rejects_symlink_manifest_without_reading_or_mutating_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[0]
            stale_path, _, _ = add_pack(root, family, "mh", "old")
            write_manifests(root, {})
            create_release_tag(root)
            manifest = root / family.manifest_relative_path
            manifest.unlink()
            outside = root / "outside-manifest.json"
            outside.write_text('{"resources": {}}\n', encoding="utf-8")
            manifest.symlink_to(outside)
            before = snapshot(root)

            with self.assertRaisesRegex(PRUNER.PruneError, "symlink.*manifest"):
                PRUNER.apply_prune(root)

            self.assertEqual(outside.read_text(encoding="utf-8"), '{"resources": {}}\n')
            self.assertTrue(stale_path.exists())
            self.assertEqual(snapshot(root), before)

    def test_rejects_pack_whose_content_does_not_match_filename_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_roots(root)
            family = PRUNER.FAMILIES[2]
            write_manifests(root, {})
            create_release_tag(root)
            state_dir = root / family.pack_root_relative_path / "up"
            state_dir.mkdir(parents=True)
            corrupted = state_dir / f"{family.filename_prefix}-{'a' * 64}.json"
            corrupted.write_text('{"not":"the named digest"}\n', encoding="utf-8")
            before = snapshot(root)

            with self.assertRaisesRegex(PRUNER.PruneError, "filename digest"):
                PRUNER.apply_prune(root)

            self.assertEqual(snapshot(root), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
