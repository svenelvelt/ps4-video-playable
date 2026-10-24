# SPDX-FileCopyrightText: 2026 Sven Elvelt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check video compatibility with the PS4 Media Player."""

import argparse
import functools
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__
from .common import (
    DEFAULT_JOBS,
    FORMATS,
    audio_streams,
    evaluate,
    expand_wildcards,
    find_videos,
    frame_rate,
    positive_int,
    probe,
    storage_notes,
    use_utf8_output,
    video_streams,
)

EXIT_CODES = {
    "compatible": 0,
    "needs_verification": 0,
    "not_compatible": 1,
    "error": 2,
}

SUMMARY_LABELS = {
    "compatible": "✅ Likely compatible",
    "needs_verification": "⚠️ Needs verification",
    "not_compatible": "❌ Not compatible",
    "error": "Could not be checked",
}


def expand_paths(paths, recursive=False):
    """Replace folders with the video files in them.

    Other paths are kept as given, so a missing or non-video file is
    still reported.
    """
    files = []
    seen = set()

    for raw_path in expand_wildcards(paths):
        path = Path(raw_path).expanduser()
        candidates = find_videos(path, recursive) if path.is_dir() else [path]

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(candidate)

    return files


def video_summary(stream):
    fps = frame_rate(stream)
    return {
        "index": stream.get("index"),
        "codec": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "level": stream.get("level"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "pix_fmt": stream.get("pix_fmt"),
        "frame_rate": round(fps, 3) if fps else None,
        "color_transfer": stream.get("color_transfer"),
    }


def audio_summary(stream):
    return {
        "index": stream.get("index"),
        "codec": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "channels": stream.get("channels"),
        "language": stream.get("tags", {}).get("language"),
    }


def inspect_file(path, pro=False):
    path = Path(path).expanduser()

    result = {
        "file": str(path),
        "console": "PS4 Pro" if pro else "Standard PS4",
        "status": "error",
        "errors": [],
        "warnings": [],
        "notes": [],
        "video": [],
        "audio": [],
    }

    if not path.is_file():
        result["errors"].append("File does not exist")
        return result

    result["notes"] = storage_notes(path)

    extension = path.suffix.lower()

    if extension not in FORMATS:
        result["status"] = "not_compatible"
        result["errors"].append(f"Unsupported container {extension}")
        return result

    try:
        data = probe(path)
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        result["errors"].append(f"Could not inspect file: {exc}")
        return result

    result["video"] = [video_summary(s) for s in video_streams(data)]
    result["audio"] = [audio_summary(s) for s in audio_streams(data)]

    errors, warnings = evaluate(data, extension, pro)
    result["errors"] = errors
    result["warnings"] = warnings

    if errors:
        result["status"] = "not_compatible"
    elif warnings:
        result["status"] = "needs_verification"
    else:
        result["status"] = "compatible"

    return result


def print_report(result):
    print(f"\n{'=' * 60}")
    print(f"File: {result['file']}")
    print(f"Console: {result['console']}")
    print(f"{'=' * 60}")

    if result["status"] == "error":
        print(f"ERROR: {result['errors'][0]}")
        return

    for stream in result["video"]:
        print(f"\nVIDEO STREAM #{stream['index']}")
        print(f"  Codec:      {stream['codec']}")
        print(f"  Profile:    {stream['profile']}")
        print(f"  Level:      {stream['level']}")
        print(f"  Resolution: {stream['width']}x{stream['height']}")
        print(f"  Pixel fmt:  {stream['pix_fmt']}")
        print(f"  Frame rate: {stream['frame_rate']}")

    for stream in result["audio"]:
        print(f"\nAUDIO STREAM #{stream['index']}")
        print(f"  Codec:    {stream['codec']}")
        print(f"  Profile:  {stream['profile']}")
        print(f"  Channels: {stream['channels']}")
        print(f"  Language: {stream['language']}")

    print(f"\n{'-' * 60}")
    print("RESULT")

    if result["status"] == "not_compatible":
        print("❌ NOT COMPATIBLE")
    elif result["status"] == "needs_verification":
        print("⚠️ NEEDS VERIFICATION")
    else:
        print("✅ LIKELY COMPATIBLE")

    for error in result["errors"]:
        print(f"  ❌ {error}")

    for warning in result["warnings"]:
        print(f"  ⚠️ {warning}")

    if result["status"] == "compatible":
        print("  Container, video and audio meet the checked requirements.")

    for note in result["notes"]:
        print(f"  ℹ️ {note}")


def print_summary(results, after_reports=True):
    if after_reports:
        print(f"\n{'=' * 60}")
    count = len(results)
    print(f"SUMMARY ({count} file{'' if count == 1 else 's'})")

    for status, label in SUMMARY_LABELS.items():
        matching = [r for r in results if r["status"] == status]
        print(f"{len(matching):>5}  {label}")

    problems = [
        r for r in results if r["status"] in {"not_compatible", "error"}
    ]
    if problems:
        print("\nWon't play or couldn't be checked:")
        for result in problems:
            print(f"  {result['file']}: {result['errors'][0]}")

    unsure = [r for r in results if r["status"] == "needs_verification"]
    if unsure:
        print("\nNeed verification:")
        for result in unsure:
            print(f"  {result['file']}: {result['warnings'][0]}")

    noted = [r for r in results if r["notes"]]
    if noted:
        print("\nNotes:")
        for result in noted:
            print(f"  {result['file']}: {result['notes'][0]}")


def main():
    use_utf8_output()
    parser = argparse.ArgumentParser(
        prog="ps4-check",
        description="Check videos for PS4 Media Player compatibility",
    )
    parser.add_argument(
        "paths", nargs="+", help="Video files or folders to inspect"
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Also check videos in subfolders",
    )
    parser.add_argument(
        "--pro", action="store_true", help="Check against PS4 Pro capabilities"
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--summary",
        action="store_true",
        help="Print only the summary, not a report per file",
    )
    output.add_argument(
        "--json", action="store_true", help="Print results as JSON"
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=positive_int,
        default=DEFAULT_JOBS,
        metavar="N",
        help=f"Check N files at a time (default: {DEFAULT_JOBS})",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    if not shutil.which("ffprobe"):
        parser.error("ffprobe is not installed or not in PATH")

    files = expand_paths(args.paths, args.recursive)

    if not files:
        print("No video files found.", file=sys.stderr)
        sys.exit(2)

    full_reports = not (args.summary or args.json)
    # Show progress while nothing else is printed, but keep it out of
    # redirected output.
    show_progress = not full_reports and sys.stderr.isatty()

    results = []
    pool = ThreadPoolExecutor(max_workers=args.jobs)
    inspect = functools.partial(inspect_file, pro=args.pro)

    try:
        # map() returns results in input order while later files are
        # already being checked.
        for number, result in enumerate(pool.map(inspect, files), 1):
            results.append(result)

            if full_reports:
                print_report(result)
            elif show_progress:
                print(
                    f"\rChecked {number}/{len(files)}...",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        print("\nStopped by user.", file=sys.stderr)
        sys.exit(130)

    pool.shutdown()

    if show_progress:
        # Clear the progress line by overwriting it with spaces; the
        # escape code for this isn't understood by older Windows consoles.
        width = len(f"Checked {len(files)}/{len(files)}...")
        print("\r" + " " * width + "\r", end="", file=sys.stderr, flush=True)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif args.summary or len(results) > 1:
        print_summary(results, after_reports=full_reports)

    sys.exit(max(EXIT_CODES[r["status"]] for r in results))


if __name__ == "__main__":
    main()
