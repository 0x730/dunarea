#!/usr/bin/env python3
"""Scrie atomic revizia checkout-ului curent în release-ul Forge."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def git_revision(repository: Path) -> str:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("build_revision_git_failed") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("build_revision_invalid")
    return value


def write_revision(repository: Path, output: Path) -> str:
    revision = git_revision(repository)
    partial = output.with_name(output.name + ".partial")
    try:
        partial.write_text(revision + "\n", encoding="ascii")
        os.chmod(partial, 0o644)
        os.replace(partial, output)
    finally:
        if partial.exists():
            partial.unlink()
    if output.read_text(encoding="ascii").strip() != revision:
        raise RuntimeError("build_revision_write_mismatch")
    return revision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=".")
    parser.add_argument("--output", default=".build-revision")
    args = parser.parse_args()
    try:
        print(write_revision(Path(args.repository).resolve(), Path(args.output).resolve()))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
