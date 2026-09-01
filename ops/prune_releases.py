#!/usr/bin/env python3
"""Păstrează release-ul activ și cel mai nou rollback pe hostul Forge comun."""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_ROOTS = {
    Path("/home/dunarea/dunarea.info/releases"): Path(
        "/home/dunarea/dunarea.info/current"
    ),
    Path("/home/forge/0x730.com/releases"): Path("/home/forge/0x730.com/current"),
}


class PruneError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleasePlan:
    root: Path
    current_link: Path
    current: Path
    keep: tuple[Path, ...]
    remove: tuple[Path, ...]


def _canonical(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise PruneError("release_root_refused")
    return Path(*path.parts)


def plan_releases(
    root: Path,
    *,
    current_link: Path | None = None,
    allowed_roots: dict[Path, Path] = ALLOWED_ROOTS,
) -> ReleasePlan:
    root = _canonical(root)
    expected_link = allowed_roots.get(root)
    if expected_link is None:
        raise PruneError("release_root_refused")
    if current_link is not None and _canonical(current_link) != expected_link:
        raise PruneError("release_current_link_refused")
    current_link = expected_link

    try:
        root_info = root.lstat()
        current = current_link.resolve(strict=True)
    except OSError as exc:
        raise PruneError("release_layout_missing") from exc
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink():
        raise PruneError("release_root_invalid")
    if current.parent != root or not current.is_dir() or current.is_symlink():
        raise PruneError("release_current_invalid")

    releases = []
    for entry in root.iterdir():
        if not entry.name.isdigit() or entry.is_symlink() or not entry.is_dir():
            raise PruneError("release_entry_invalid")
        releases.append(entry)
    if current not in releases:
        raise PruneError("release_current_missing")

    rollback = sorted(
        (entry for entry in releases if entry != current),
        key=lambda entry: (entry.stat().st_mtime_ns, entry.name),
        reverse=True,
    )[:1]
    keep = (current, *rollback)
    return ReleasePlan(
        root=root,
        current_link=current_link,
        current=current,
        keep=keep,
        remove=tuple(sorted(set(releases) - set(keep), key=lambda entry: entry.name)),
    )


def _tree_bytes(path: Path) -> int:
    total = path.lstat().st_size
    for entry in path.rglob("*"):
        total += entry.lstat().st_size
    return total


def apply_plan(plan: ReleasePlan) -> int:
    reclaimed = 0
    for release in plan.remove:
        try:
            active = plan.current_link.resolve(strict=True)
        except OSError as exc:
            raise PruneError("release_current_changed") from exc
        if active != plan.current:
            raise PruneError("release_current_changed")
        if (
            release.parent != plan.root
            or release == plan.current
            or release.is_symlink()
        ):
            raise PruneError("release_delete_refused")
        reclaimed += _tree_bytes(release)
        shutil.rmtree(release)
    return reclaimed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = plan_releases(args.root)
        reclaimed = apply_plan(plan) if args.apply else 0
        print(
            json.dumps(
                {
                    "event": "release_prune",
                    "root": str(plan.root),
                    "mode": "apply" if args.apply else "dry-run",
                    "before": len(plan.keep) + len(plan.remove),
                    "after": len(plan.keep),
                    "current": plan.current.name,
                    "kept": [entry.name for entry in plan.keep],
                    "removed": [entry.name for entry in plan.remove],
                    "reclaimedBytes": reclaimed,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, PruneError) as exc:
        print(
            json.dumps(
                {"event": "release_prune_failed", "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
