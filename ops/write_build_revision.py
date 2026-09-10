#!/usr/bin/env python3
"""Scrie revizia checkout-ului și receipt-ul canonic în release-ul Forge."""

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


def write_release_receipt(repository: Path, revision: str) -> None:
    # Use the same checkout identity as .build-revision, even when the caller
    # inherited a different Forge/Git revision. The canonical writer is verbatim.
    environment = os.environ.copy()
    environment["FORGE_DEPLOY_SHA"] = revision
    try:
        subprocess.run(
            ["node", str(Path(__file__).with_name("write-release-receipt.mjs"))],
            cwd=repository,
            env=environment,
            # Forge captures stdout as RELEASE_SHA; keep the receipt in its log.
            stdout=sys.stderr,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("release_receipt_write_failed") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=".")
    parser.add_argument("--output", default=".build-revision")
    args = parser.parse_args()
    try:
        repository = Path(args.repository).resolve()
        revision = write_revision(repository, Path(args.output).resolve())
        write_release_receipt(repository, revision)
        print(revision)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
