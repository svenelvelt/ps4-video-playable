# SPDX-FileCopyrightText: 2026 Sven Elvelt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check video compatibility with the PS4 Media Player."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .common import (
    FORMATS,
    audio_streams,
    evaluate,
    frame_rate,
    probe,
    video_streams,
)

EXIT_CODES = {
    "compatible": 0,
    "needs_verification": 0,
    "not_compatible": 1,
    "error": 2,
}


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
        "video": [],
        "audio": [],
    }

    if not path.is_file():
        result["errors"].append("File does not exist")
        return result

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
    print(f"File: {Path(result['file']).name}")
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


def main():
    parser = argparse.ArgumentParser(
        description="Check videos for PS4 Media Player compatibility"
    )
    parser.add_argument("files", nargs="+", help="Video files to inspect")
    parser.add_argument(
        "--pro", action="store_true", help="Check against PS4 Pro capabilities"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print results as JSON"
    )
    args = parser.parse_args()

    if not shutil.which("ffprobe"):
        parser.error("ffprobe is not installed or not in PATH")

    results = [inspect_file(f, args.pro) for f in args.files]

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for result in results:
            print_report(result)

    sys.exit(max(EXIT_CODES[r["status"]] for r in results))


if __name__ == "__main__":
    main()
