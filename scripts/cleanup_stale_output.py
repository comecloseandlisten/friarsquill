#!/usr/bin/env python3
"""Cleanup stale temporary summary outputs.

Scans ``backend/output/`` and ``output/`` (relative to the repo root) for files
matching ``tmp*_summary.md`` whose modification time is older than ``--days``
days and deletes them. In ``--dry-run`` mode the files are only listed.

Usage:
    python scripts/cleanup_stale_output.py [--dry-run] [--days N] [--root PATH]

Safe to rerun (idempotent) — missing directories and missing files are tolerated.
Uses only the Python standard library: ``pathlib``, ``argparse``, ``time``, ``os``.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


SCAN_DIRS = ("backend/output", "output")
PATTERN = "tmp*_summary.md"


def build_parser() -> argparse.ArgumentParser:
    script_default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Delete stale tmp*_summary.md files from backend/output and output.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be deleted without removing them.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Minimum age in days for a file to be considered stale (default: 7).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=script_default_root,
        help="Repository root to scan (default: the script's parent's parent).",
    )
    return parser


def iter_stale_files(root: Path, cutoff_ts: float):
    """Yield (path, size, mtime) tuples for stale matches under ``root``."""
    for rel_dir in SCAN_DIRS:
        scan_dir = root / rel_dir
        if not scan_dir.is_dir():
            continue
        for entry in scan_dir.glob(PATTERN):
            if not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError as exc:
                print(f"ERROR: stat {entry}: {exc}", file=sys.stderr)
                continue
            if stat.st_mtime <= cutoff_ts:
                yield entry, stat.st_size, stat.st_mtime


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root.resolve()

    if args.days < 0:
        print("ERROR: --days must be >= 0", file=sys.stderr)
        return 1

    cutoff_ts = time.time() - (args.days * 86400)
    total_count = 0
    total_bytes = 0
    had_errors = False

    for path, size, _mtime in iter_stale_files(root, cutoff_ts):
        if args.dry_run:
            print(f"WOULD DELETE: {path}")
        else:
            try:
                os.remove(path)
            except OSError as exc:
                print(f"ERROR: remove {path}: {exc}", file=sys.stderr)
                had_errors = True
                continue
            print(f"DELETE: {path}")
        total_count += 1
        total_bytes += size

    print(f"Total: {total_count} files, {total_bytes} bytes")
    return 1 if had_errors else 0


if __name__ == "__main__":
    sys.exit(main())
