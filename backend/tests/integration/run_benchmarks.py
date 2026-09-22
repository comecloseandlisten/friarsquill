#!/usr/bin/env python3
"""
Benchmark runner for LocalVideoTranscriber integration tests.

Usage:
    python tests/integration/run_benchmarks.py              # run all
    python tests/integration/run_benchmarks.py -k short     # only short files
    python tests/integration/run_benchmarks.py --html       # open HTML report after
    python tests/integration/run_benchmarks.py --fast       # skip long + batch tests
    python tests/integration/run_benchmarks.py --list       # list discovered test data
"""
import argparse
import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = Path(__file__).resolve().parent
REPORTS_DIR = INTEGRATION_DIR / "reports"
TEST_DATA_DIR = BACKEND_DIR.parent / "test_data"

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg",
    ".opus", ".mkv", ".flac", ".aac", ".wma", ".avi", ".mov",
}


def list_test_data():
    """Print discovered test data files."""
    categories = [
        "short", "medium", "long", "multilingual",
        "noisy", "multi_speaker", "edge_cases", "formats",
    ]
    total = 0
    for cat in categories:
        folder = TEST_DATA_DIR / cat
        if not folder.exists():
            print(f"  {cat:20s} — (directory missing)")
            continue
        files = [
            f for f in sorted(folder.iterdir())
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        ]
        total += len(files)
        if files:
            print(f"  {cat:20s} — {len(files)} file(s)")
            for f in files:
                size_mb = f.stat().st_size / (1024 * 1024)
                print(f"    {f.name:40s} {size_mb:8.1f} MB")
        else:
            print(f"  {cat:20s} — (empty)")

    refs = TEST_DATA_DIR / "reference_transcripts"
    ref_count = 0
    if refs.exists():
        ref_count = len(list(refs.glob("*.txt")))
    print(f"\n  Reference transcripts: {ref_count}")
    print(f"  Total test files:     {total}")
    if total == 0:
        print(
            "\n  No test data found! Place audio/video files in test_data/ subdirectories."
            "\n  See test_data/README.md for details."
        )


def run_benchmarks(
    pytest_args: list[str] | None = None,
    html: bool = False,
    fast: bool = False,
    verbose: bool = True,
):
    """Run pytest on the integration suite."""
    cmd = [
        sys.executable, "-m", "pytest",
        str(INTEGRATION_DIR),
        "-v" if verbose else "-q",
        "--tb=short",
        f"--rootdir={BACKEND_DIR}",
    ]

    if fast:
        cmd += ["-k", "not long and not batch"]

    if pytest_args:
        cmd += pytest_args

    # Coverage (optional)
    try:
        import pytest_cov  # noqa: F401
        cmd += [f"--cov={BACKEND_DIR}", "--cov-report=term-missing"]
    except ImportError:
        pass

    print(f"\n{'='*60}")
    print("  LocalVideoTranscriber Integration Benchmarks")
    print(f"{'='*60}")
    print(f"  Test data: {TEST_DATA_DIR}")
    print(f"  Reports:   {REPORTS_DIR}")
    print(f"  Command:   {' '.join(cmd)}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, cwd=str(BACKEND_DIR))

    # Print report location
    latest_json = REPORTS_DIR / "benchmark_latest.json"
    latest_md = REPORTS_DIR / "benchmark_latest.md"
    if latest_json.exists():
        print(f"\n{'='*60}")
        print(f"  JSON report: {latest_json}")
        print(f"  MD report:   {latest_md}")

        data = json.loads(latest_json.read_text(encoding="utf-8"))
        passed = sum(1 for r in data if r.get("error") is None)
        failed = len(data) - passed
        print(f"  Results:     {passed} passed, {failed} failed, {len(data)} total")

        # Quick summary
        wer_entries = [r for r in data if r.get("wer") is not None]
        if wer_entries:
            avg_wer = sum(r["wer"] for r in wer_entries) / len(wer_entries)
            print(f"  Avg WER:     {avg_wer:.2%} (across {len(wer_entries)} files)")

        times = [r["total_time_sec"] for r in data if r.get("total_time_sec")]
        if times:
            print(f"  Total time:  {sum(times):.1f}s")
        print(f"{'='*60}")

    if html and latest_md.exists():
        _open_as_html(latest_md)

    return result.returncode


def _open_as_html(md_path: Path):
    """Convert markdown report to HTML and open in browser."""
    try:
        import markdown  # type: ignore
        html_content = markdown.markdown(
            md_path.read_text(encoding="utf-8"),
            extensions=["tables"],
        )
    except ImportError:
        html_content = f"<pre>{md_path.read_text(encoding='utf-8')}</pre>"

    html_path = md_path.with_suffix(".html")
    html_path.write_text(
        f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Benchmark Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 1200px; margin: 40px auto; padding: 0 20px; color: #333; }}
table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background-color: #f4f4f4; }}
tr:nth-child(even) {{ background-color: #fafafa; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }}
h2 {{ margin-top: 40px; color: #555; }}
</style>
</head><body>
{html_content}
</body></html>""",
        encoding="utf-8",
    )
    print(f"  HTML report: {html_path}")
    webbrowser.open(html_path.as_uri())


def main():
    parser = argparse.ArgumentParser(description="Run integration benchmarks")
    parser.add_argument("--list", action="store_true", help="List discovered test data")
    parser.add_argument("--html", action="store_true", help="Open HTML report after run")
    parser.add_argument("--fast", action="store_true", help="Skip long/batch tests")
    parser.add_argument("-k", dest="filter", help="pytest -k filter expression")
    parser.add_argument("-v", "--verbose", action="store_true", default=True)
    parser.add_argument("extra", nargs="*", help="Extra pytest arguments")
    args = parser.parse_args()

    if args.list:
        list_test_data()
        return

    pytest_args = args.extra or []
    if args.filter:
        pytest_args += ["-k", args.filter]

    exit_code = run_benchmarks(
        pytest_args=pytest_args,
        html=args.html,
        fast=args.fast,
        verbose=args.verbose,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
