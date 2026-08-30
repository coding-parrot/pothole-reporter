#!/usr/bin/env python3
"""Safely report or remove superseded content-addressed catalog packs.

The catalog builders intentionally leave old content-addressed files behind. This
tool protects packs referenced by the current manifests or by a manifest in any
released ``v1.*`` Git tag. A file is only removable after the release history,
manifests, and whole managed pack tree have passed strict validation.

Running without a mode flag is the same as ``--check`` and never mutates the
checkout.  Use ``--apply`` explicitly to remove validated orphan packs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIRECTORY_RE = re.compile(r"^[a-z]{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PruneError(RuntimeError):
    """Raised when catalog artifacts cannot be pruned safely."""


@dataclass(frozen=True)
class Family:
    name: str
    manifest_relative_path: Path
    pack_root_relative_path: Path
    manifest_path_prefix: str
    filename_prefix: str
    pack_format: str
    legacy_pack_formats: tuple[str, ...] = ()

    @property
    def allowed_pack_formats(self) -> tuple[str, ...]:
        return (self.pack_format, *self.legacy_pack_formats)

    @property
    def path_pattern(self) -> re.Pattern[str]:
        return re.compile(
            rf"^{re.escape(self.manifest_path_prefix)}/"
            rf"(?P<state>[a-z]{{2}})/"
            rf"{re.escape(self.filename_prefix)}-(?P<digest>[0-9a-f]{{64}})\.json$"
        )

    @property
    def released_manifest_pattern(self) -> re.Pattern[str]:
        name = self.manifest_relative_path.name
        prefix, separator, _ = name.rpartition("-v")
        if not separator:
            raise AssertionError(f"versioned manifest path required: {name}")
        parent = self.manifest_relative_path.parent.as_posix()
        return re.compile(
            rf"^{re.escape(parent)}/{re.escape(prefix)}-v"
            rf"[0-9]+(?:\.[0-9]+)*\.json$"
        )


FAMILIES = (
    Family(
        name="contracts",
        manifest_relative_path=Path("docs/contract-manifest-v1.36.json"),
        pack_root_relative_path=Path("docs/packs/v1/contracts"),
        manifest_path_prefix="packs/v1/contracts",
        filename_prefix="highways",
        pack_format="pothole-highway-contract-pack",
    ),
    Family(
        name="road-notices",
        manifest_relative_path=Path("docs/road-notice-manifest-v1.36.json"),
        pack_root_relative_path=Path("docs/packs/v1/road-notices"),
        manifest_path_prefix="packs/v1/road-notices",
        filename_prefix="notices",
        pack_format="pothole-official-road-notice-pack",
        legacy_pack_formats=("pothole-gepnic-road-notice-pack",),
    ),
    Family(
        name="road-agreements",
        manifest_relative_path=Path("docs/road-agreement-manifest-v1.36.json"),
        pack_root_relative_path=Path("docs/packs/v1/road-agreements"),
        manifest_path_prefix="packs/v1/road-agreements",
        filename_prefix="agreements",
        pack_format="pothole-pmgsy-road-agreement-pack",
    ),
)


@dataclass(frozen=True)
class PackFile:
    family: Family
    manifest_path: str
    state: str
    filename: str

    @property
    def disk_relative_path(self) -> Path:
        return Path("docs") / PurePosixPath(self.manifest_path)


@dataclass(frozen=True)
class EmptyLeaf:
    family: Family
    state: str

    @property
    def disk_relative_path(self) -> Path:
        return self.family.pack_root_relative_path / self.state


@dataclass(frozen=True)
class PrunePlan:
    referenced: tuple[PackFile, ...]
    stale: tuple[PackFile, ...]
    empty_after_prune: tuple[EmptyLeaf, ...]

    @property
    def has_work(self) -> bool:
        return bool(self.stale or self.empty_after_prune)


def _error(message: str) -> PruneError:
    return PruneError(message)


def _lstat_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise _error(f"missing {label}: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise _error(f"refusing symlink for {label}: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise _error(f"{label} is not a regular file: {path}")
    return metadata


def _validate_directory_chain(project_root: Path, relative_path: Path) -> Path:
    """Validate each managed directory component without resolving symlinks."""

    try:
        root_metadata = project_root.lstat()
    except FileNotFoundError as exc:
        raise _error(f"project root does not exist: {project_root}") from exc
    if stat.S_ISLNK(root_metadata.st_mode):
        raise _error(f"refusing symlink project root: {project_root}")
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise _error(f"project root is not a directory: {project_root}")

    current = project_root
    for part in relative_path.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise _error(f"missing managed pack directory: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise _error(f"refusing symlink managed pack directory: {current}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise _error(f"managed pack path is not a directory: {current}")
    return current


def _read_json_without_following_symlinks(path: Path, label: str) -> Any:
    _lstat_regular_file(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _error(f"{label} is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            raw = stream.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error(f"invalid UTF-8 JSON in {label} {path}: {exc}") from exc
    except OSError as exc:
        raise _error(f"could not safely open {label} {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_manifest_path(family: Family, value: Any, label: str) -> tuple[str, str, str]:
    if not isinstance(value, str) or value != value.strip() or "\\" in value:
        raise _error(f"{label}.path must be a canonical POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise _error(f"{label}.path contains an absolute or traversal component: {value!r}")
    match = family.path_pattern.fullmatch(value)
    if match is None:
        raise _error(
            f"{label}.path is outside the exact {family.name} content-addressed root: "
            f"{value!r}"
        )
    return match.group("state"), PurePosixPath(value).name, match.group("digest")


def _references_from_manifest(
    manifest: Any, family: Family, source_label: str
) -> dict[str, PackFile]:
    if not isinstance(manifest, dict):
        raise _error(f"{source_label} must be a JSON object")
    resources = manifest.get("resources")
    if not isinstance(resources, dict):
        raise _error(f"{source_label} resources must be an object")

    references: dict[str, PackFile] = {}
    for resource_id, resource in sorted(resources.items()):
        label = f"{source_label} resource {resource_id!r}"
        if not isinstance(resource_id, str) or not resource_id:
            raise _error(f"{source_label} resource IDs must be non-empty strings")
        if not isinstance(resource, dict):
            raise _error(f"{label} must be an object")
        state, filename, digest = _parse_manifest_path(family, resource.get("path"), label)
        resource_sha = resource.get("sha256")
        if not isinstance(resource_sha, str) or SHA256_RE.fullmatch(resource_sha) is None:
            raise _error(f"{label}.sha256 must be a lowercase SHA-256 digest")
        if resource_sha != digest:
            raise _error(f"{label}.path digest does not match its sha256 field")
        state_code = resource.get("state_code")
        if not isinstance(state_code, str) or state_code != state.upper():
            raise _error(f"{label}.state_code does not match its path")
        manifest_relative = resource["path"]
        if manifest_relative in references:
            raise _error(f"duplicate pack path in {source_label}: {manifest_relative}")
        references[manifest_relative] = PackFile(
            family=family,
            manifest_path=manifest_relative,
            state=state,
            filename=filename,
        )
    return references


def _manifest_references(project_root: Path, family: Family) -> dict[str, PackFile]:
    manifest_path = project_root / family.manifest_relative_path
    label = f"{family.name} manifest"
    manifest = _read_json_without_following_symlinks(manifest_path, label)
    return _references_from_manifest(manifest, family, label)


def _run_git(project_root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(project_root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise _error(f"could not inspect Git release history: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise _error(f"could not inspect Git release history{suffix}")
    return result.stdout


def _released_manifest_references(
    project_root: Path,
) -> dict[str, dict[str, PackFile]]:
    """Load every managed-pack reference preserved by a released v1 tag."""

    top_level = _run_git(project_root, "rev-parse", "--show-toplevel")
    try:
        git_root = Path(top_level.decode("utf-8").strip()).absolute()
    except UnicodeDecodeError as exc:
        raise _error("Git project root is not valid UTF-8") from exc
    try:
        same_root = os.path.samefile(git_root, project_root)
    except OSError as exc:
        raise _error(f"could not compare Git project root: {exc}") from exc
    if not same_root:
        raise _error(
            f"project root is not the Git checkout root: {project_root} (Git: {git_root})"
        )

    shallow = _run_git(project_root, "rev-parse", "--is-shallow-repository")
    if shallow.strip() != b"false":
        raise _error("release history is shallow; fetch complete history and v1 tags first")

    raw_refs = _run_git(
        project_root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/tags/v1.*",
    )
    try:
        release_tags = tuple(
            line for line in raw_refs.decode("utf-8").splitlines() if line
        )
    except UnicodeDecodeError as exc:
        raise _error("Git release tag names are not valid UTF-8") from exc
    if not release_tags:
        raise _error("no released v1.* Git tags found; refusing to prune")

    references = {family.name: {} for family in FAMILIES}
    for release_tag in release_tags:
        raw_paths = _run_git(
            project_root,
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            release_tag,
            "--",
            "docs",
        )
        try:
            paths = raw_paths.decode("utf-8").split("\0")
        except UnicodeDecodeError as exc:
            raise _error(f"Git paths in {release_tag} are not valid UTF-8") from exc
        for family in FAMILIES:
            for manifest_path in paths:
                if family.released_manifest_pattern.fullmatch(manifest_path) is None:
                    continue
                raw_manifest = _run_git(
                    project_root,
                    "cat-file",
                    "blob",
                    f"{release_tag}:{manifest_path}",
                )
                label = f"{family.name} release manifest {release_tag}:{manifest_path}"
                try:
                    manifest = json.loads(raw_manifest.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _error(f"invalid UTF-8 JSON in {label}: {exc}") from exc
                references[family.name].update(
                    _references_from_manifest(manifest, family, label)
                )
    return references


def _read_pack_bytes_without_following_symlinks(path: Path) -> bytes:
    _lstat_regular_file(path, "catalog pack")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _error(f"catalog pack is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            return stream.read()
    except OSError as exc:
        raise _error(f"could not safely open catalog pack {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_pack_content(path: Path, family: Family, expected_digest: str, state: str) -> None:
    raw = _read_pack_bytes_without_following_symlinks(path)
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_digest:
        raise _error(f"catalog pack content does not match its filename digest: {path}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(f"invalid UTF-8 JSON catalog pack {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("format") not in family.allowed_pack_formats
    ):
        raise _error(f"catalog pack has the wrong family format: {path}")
    if payload.get("state_code") != state.upper():
        raise _error(f"catalog pack state_code does not match its path: {path}")


def _scan_family(
    project_root: Path, family: Family
) -> tuple[dict[str, PackFile], dict[str, set[str]]]:
    root = _validate_directory_chain(project_root, family.pack_root_relative_path)
    packs: dict[str, PackFile] = {}
    files_by_state: dict[str, set[str]] = {}
    try:
        with os.scandir(root) as entries:
            root_entries = sorted(entries, key=lambda entry: entry.name)
    except OSError as exc:
        raise _error(f"could not scan managed pack directory {root}: {exc}") from exc

    for state_entry in root_entries:
        try:
            metadata = state_entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise _error(f"could not inspect managed pack entry {state_entry.path}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise _error(f"refusing symlink in managed pack tree: {state_entry.path}")
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or STATE_DIRECTORY_RE.fullmatch(state_entry.name) is None
        ):
            raise _error(f"unexpected entry in managed pack root: {state_entry.path}")

        state = state_entry.name
        files_by_state[state] = set()
        try:
            with os.scandir(state_entry.path) as entries:
                state_entries = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            raise _error(f"could not scan state pack directory {state_entry.path}: {exc}") from exc
        for pack_entry in state_entries:
            try:
                pack_metadata = pack_entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _error(
                    f"could not inspect managed pack entry {pack_entry.path}: {exc}"
                ) from exc
            if stat.S_ISLNK(pack_metadata.st_mode):
                raise _error(f"refusing symlink in managed pack tree: {pack_entry.path}")
            if not stat.S_ISREG(pack_metadata.st_mode):
                raise _error(f"unexpected non-file in state pack directory: {pack_entry.path}")

            manifest_path = f"{family.manifest_path_prefix}/{state}/{pack_entry.name}"
            match = family.path_pattern.fullmatch(manifest_path)
            if match is None or match.group("state") != state:
                raise _error(f"unexpected file in managed pack tree: {pack_entry.path}")
            digest = match.group("digest")
            _validate_pack_content(Path(pack_entry.path), family, digest, state)
            pack = PackFile(
                family=family,
                manifest_path=manifest_path,
                state=state,
                filename=pack_entry.name,
            )
            packs[manifest_path] = pack
            files_by_state[state].add(manifest_path)
    return packs, files_by_state


def plan_prune(project_root: Path = PROJECT_ROOT) -> PrunePlan:
    """Validate all managed inputs and return an immutable prune plan."""

    project_root = Path(project_root).absolute()
    released_references = _released_manifest_references(project_root)
    referenced: list[PackFile] = []
    stale: list[PackFile] = []
    empty_after_prune: list[EmptyLeaf] = []

    for family in FAMILIES:
        # Validate ``docs`` and every managed-root component before opening a
        # manifest beneath it; a symlink in a parent must never be traversed.
        _validate_directory_chain(project_root, family.pack_root_relative_path)
        manifest_references = _manifest_references(project_root, family)
        manifest_references.update(released_references[family.name])
        disk_packs, files_by_state = _scan_family(project_root, family)
        missing = sorted(set(manifest_references) - set(disk_packs))
        if missing:
            raise _error(
                f"{family.name} manifest references missing pack(s): " + ", ".join(missing)
            )

        family_stale_paths = set(disk_packs) - set(manifest_references)
        referenced.extend(manifest_references.values())
        stale.extend(disk_packs[path] for path in sorted(family_stale_paths))
        for state, state_files in sorted(files_by_state.items()):
            if not (state_files - family_stale_paths):
                empty_after_prune.append(EmptyLeaf(family=family, state=state))

    return PrunePlan(
        referenced=tuple(sorted(referenced, key=lambda pack: pack.manifest_path)),
        stale=tuple(sorted(stale, key=lambda pack: pack.manifest_path)),
        empty_after_prune=tuple(
            sorted(empty_after_prune, key=lambda leaf: leaf.disk_relative_path.as_posix())
        ),
    )


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _delete_stale_pack(project_root: Path, pack: PackFile) -> None:
    """Delete one prevalidated pack through no-follow directory descriptors."""

    match = pack.family.path_pattern.fullmatch(pack.manifest_path)
    if (
        match is None
        or match.group("state") != pack.state
        or pack.filename != PurePosixPath(pack.manifest_path).name
    ):
        raise _error(f"internal unsafe stale-pack path: {pack.manifest_path}")

    family_root = project_root / pack.family.pack_root_relative_path
    root_descriptor: int | None = None
    state_descriptor: int | None = None
    try:
        root_descriptor = os.open(family_root, _directory_open_flags())
        state_descriptor = os.open(pack.state, _directory_open_flags(), dir_fd=root_descriptor)
        metadata = os.stat(pack.filename, dir_fd=state_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise _error(f"stale pack changed type before deletion: {pack.manifest_path}")
        os.unlink(pack.filename, dir_fd=state_descriptor)
    except FileNotFoundError as exc:
        raise _error(f"stale pack disappeared before deletion: {pack.manifest_path}") from exc
    except OSError as exc:
        raise _error(f"could not safely delete stale pack {pack.manifest_path}: {exc}") from exc
    finally:
        if state_descriptor is not None:
            os.close(state_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def _remove_empty_leaf(project_root: Path, leaf: EmptyLeaf) -> bool:
    family_root = project_root / leaf.family.pack_root_relative_path
    root_descriptor: int | None = None
    state_descriptor: int | None = None
    try:
        root_descriptor = os.open(family_root, _directory_open_flags())
        state_descriptor = os.open(leaf.state, _directory_open_flags(), dir_fd=root_descriptor)
        if os.listdir(state_descriptor):
            return False
        os.close(state_descriptor)
        state_descriptor = None
        os.rmdir(leaf.state, dir_fd=root_descriptor)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _error(
            f"could not safely remove empty pack directory "
            f"{leaf.disk_relative_path}: {exc}"
        ) from exc
    finally:
        if state_descriptor is not None:
            os.close(state_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def apply_prune(project_root: Path = PROJECT_ROOT) -> PrunePlan:
    """Validate, delete only orphan packs, and remove leaf directories left empty."""

    project_root = Path(project_root).absolute()
    plan = plan_prune(project_root)
    for pack in plan.stale:
        _delete_stale_pack(project_root, pack)
    for leaf in plan.empty_after_prune:
        _remove_empty_leaf(project_root, leaf)
    return plan


def _report_lines(plan: PrunePlan, *, applied: bool) -> Iterable[str]:
    action = "removed" if applied else "stale"
    yield (
        f"catalog packs: {len(plan.referenced)} referenced, "
        f"{len(plan.stale)} {action}, "
        f"{len(plan.empty_after_prune)} empty leaf director"
        f"{'y' if len(plan.empty_after_prune) == 1 else 'ies'}"
    )
    for pack in plan.stale:
        yield f"  {action}: {pack.disk_relative_path.as_posix()}"
    for leaf in plan.empty_after_prune:
        verb = "removed" if applied else "would remove"
        yield f"  {verb} empty: {leaf.disk_relative_path.as_posix()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or safely prune unreferenced generated catalog packs."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="report stale packs; never mutate (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="delete validated stale packs and empty leaf directories",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="checkout root (defaults to the checkout containing this tool)",
    )
    arguments = parser.parse_args(argv)

    try:
        plan = (
            apply_prune(arguments.project_root)
            if arguments.apply
            else plan_prune(arguments.project_root)
        )
    except PruneError as exc:
        print(f"catalog pack prune refused: {exc}", file=sys.stderr)
        return 2

    for line in _report_lines(plan, applied=arguments.apply):
        print(line)
    if not arguments.apply and plan.has_work:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
