# SPDX-FileCopyrightText: 2026 Sven Elvelt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Convert video files to PS4 Media Player compatible MP4 files."""

import argparse
import collections
import functools
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .common import (
    FPS_TOLERANCE,
    audio_streams,
    check_video,
    evaluate,
    expand_wildcards,
    find_videos,
    frame_rate,
    is_hdr,
    is_interlaced,
    positive_int,
    probe,
    storage_notes,
    use_utf8_output,
    video_streams,
)

OUTPUT_SUFFIX = "-ps4.mp4"

PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)

# HDR (PQ/HLG) to SDR BT.709, so colors don't look washed out.
TONEMAP = (
    "zscale=t=linear:npl=100,format=gbrpf32le,"
    "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv"
)


def get_duration(metadata):
    try:
        duration = float(metadata["format"]["duration"])
        return max(duration, 0)
    except (KeyError, TypeError, ValueError):
        return 0


def format_time(seconds):
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@functools.cache
def has_filter(name):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return any(
        line.split()[1:2] == [name] for line in result.stdout.splitlines()
    )


def collect_files(paths, recursive=False):
    files = []
    seen = set()

    for raw_path in expand_wildcards(paths):
        path = Path(raw_path).expanduser()

        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            # Skip files this script produced on an earlier run.
            candidates = [
                p
                for p in find_videos(path, recursive)
                if not p.name.lower().endswith(OUTPUT_SUFFIX)
            ]
        else:
            print(f"Warning: Path not found: {path}")
            continue

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(candidate)

    return files


def describe_audio(stream):
    parts = [
        stream.get("codec_name", "unknown"),
        f"{stream.get('channels', '?')} ch",
    ]
    language = stream.get("tags", {}).get("language")
    if language:
        parts.append(language)
    if stream.get("disposition", {}).get("default"):
        parts.append("default")
    return ", ".join(parts)


def select_audio(audios, args):
    """Pick the audio stream to keep. Returns (stream, warning)."""
    if not audios:
        return None, None

    if args.audio_track is not None:
        if args.audio_track > len(audios):
            raise LookupError(
                f"Audio track {args.audio_track} does not exist "
                f"(file has {len(audios)})"
            )
        return audios[args.audio_track - 1], None

    warning = None
    if args.audio_lang:
        wanted = args.audio_lang.lower()
        for stream in audios:
            language = stream.get("tags", {}).get("language", "")
            if language.lower() == wanted:
                return stream, None
        warning = f"No '{args.audio_lang}' audio track, using the default"

    for stream in audios:
        if stream.get("disposition", {}).get("default"):
            return stream, warning
    return audios[0], warning


def plan_video(stream, args, tonemap=False):
    """Return (ffmpeg options, description) for the video stream."""
    errors, warnings = check_video(stream, ".mp4", pro=False)
    reasons = errors + warnings

    fps = frame_rate(stream)
    if fps is None:
        reasons.append("frame rate could not be determined")
    elif fps > args.max_fps + FPS_TOLERANCE:
        reasons.append(f"frame rate {fps:.2f} fps is above {args.max_fps}")

    interlaced = is_interlaced(stream)
    if interlaced:
        reasons.append("video is interlaced")

    if args.reencode:
        reasons.append("--reencode")

    if not reasons:
        return ["-c:v", "copy"], "copy (already compatible)"

    filters = []
    if interlaced:
        filters.append("bwdif=mode=send_frame")

    # Respect aspect ratio, avoid upscaling, use even dimensions.
    # Cap the frame rate without raising lower rates.
    filters.append(
        "scale=w='min(1920,iw)':h='min(1080,ih)':"
        "force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,"
        f"fps=fps='min(source_fps,{args.max_fps})':round=down"
    )

    if tonemap:
        filters.append(TONEMAP)

    # fmt: off
    options = [
        # PS4-compatible video.
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.2",
        "-pix_fmt", "yuv420p",
        "-preset", args.preset,
        "-crf", str(args.crf),

        # Limit bitrate and decoder requirements.
        "-maxrate", "20000k",
        "-bufsize", "40000k",
        "-x264-params", "ref=4:bframes=3",

        "-vf", ",".join(filters),
    ]

    if tonemap:
        options += [
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
        ]
    # fmt: on

    return options, f"re-encode to H.264 ({reasons[0]})"


def plan_audio(stream, args):
    """Return (ffmpeg options, description) for the audio stream."""
    codec = stream.get("codec_name")
    profile = stream.get("profile")
    channels = stream.get("channels") or 2
    max_channels = 2 if args.stereo else 6

    # AAC LC and AC-3 play as they are in MP4.
    if (
        not args.reencode
        and channels <= max_channels
        and (codec == "ac3" or (codec == "aac" and profile == "LC"))
    ):
        return ["-c:a", "copy"], "copy (already compatible)"

    # fmt: off
    # Keep surround sound: AC-3 carries up to 5.1 channels.
    if channels > 2 and not args.stereo:
        channels = min(channels, 6)
        return [
            "-c:a", "ac3",
            "-b:a", "640k",
            "-ar", "48000",
            "-ac", str(channels),
        ], f"AC-3, {channels} channels"

    return [
        "-c:a", "aac",
        "-profile:a", "aac_low",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
    ], "AAC stereo"
    # fmt: on


def run_ffmpeg(command, duration):
    """Run ffmpeg with a progress bar. Returns (return code, stderr)."""
    start_time = time.monotonic()
    last_percent = -1

    # stderr goes to a file: a pipe that is only read at the end can
    # fill up and stall ffmpeg.
    with tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="replace"
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        try:
            for line in process.stdout:
                line = line.strip()

                if line == "progress=end":
                    break

                if not line.startswith("out_time=") or duration <= 0:
                    continue

                timestamp = line.split("=", 1)[1]
                try:
                    hours, minutes, seconds = timestamp.split(":")
                    progress_seconds = (
                        int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                    )
                except (ValueError, TypeError):
                    continue

                percent = min(100, int(progress_seconds / duration * 100))
                if percent == last_percent:
                    continue

                elapsed = time.monotonic() - start_time
                if progress_seconds > 0:
                    speed = progress_seconds / max(elapsed, 0.01)
                    remaining = max(0, (duration - progress_seconds) / speed)
                    eta = format_time(remaining)
                else:
                    eta = "unknown"

                bar_size = 30
                filled = int(bar_size * percent / 100)
                bar = "#" * filled + "-" * (bar_size - filled)

                print(
                    f"\r[{bar}] {percent:3d}% | ETA: {eta}", end="", flush=True
                )
                last_percent = percent

            return_code = process.wait()

        except KeyboardInterrupt:
            print("\nConversion interrupted.")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise

        print()
        stderr_file.seek(0)
        return return_code, stderr_file.read()


def verify_output(output):
    """Run the compatibility check on a converted file and report it.

    Returns False if the file is not compatible.
    """
    try:
        errors, warnings = evaluate(probe(output), ".mp4")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Check: could not inspect the converted file: {exc}")
        return False

    if errors:
        print("Check: ❌ NOT COMPATIBLE")
    elif warnings:
        print("Check: ⚠️ NEEDS VERIFICATION")
    else:
        print("Check: ✅ LIKELY COMPATIBLE")

    for error in errors:
        print(f"  ❌ {error}")
    for warning in warnings:
        print(f"  ⚠️ {warning}")
    for note in storage_notes(output):
        print(f"  ℹ️ {note}")

    return not errors


def convert(path, args):
    """Convert one file.

    Returns "converted", "skipped", "failed", or "planned" for --dry-run.
    """
    print(f"\n{'=' * 65}")
    print(f"Input: {path.name}")

    try:
        metadata = probe(path)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Error probing file: {exc}")
        return "failed"

    videos = video_streams(metadata)
    audios = audio_streams(metadata)

    if not videos:
        print("Error: No video stream found.")
        return "failed"

    video = videos[0]
    duration = get_duration(metadata)

    print(f"Video: {video.get('codec_name', 'unknown')}")
    print(f"Resolution: {video.get('width', '?')}x{video.get('height', '?')}")
    print(f"Duration: {format_time(duration)}")

    if not (args.force or args.reencode):
        errors, warnings = evaluate(metadata, path.suffix.lower())
        if not errors and not warnings:
            print(
                "Skipped: already PS4 compatible "
                "(use --force to convert anyway)."
            )
            return "skipped"

    try:
        audio, audio_warning = select_audio(audios, args)
    except LookupError as exc:
        print(f"Error: {exc}")
        return "failed"

    if audios:
        print("Audio tracks:")
        for number, stream in enumerate(audios, 1):
            marker = "*" if stream is audio else " "
            print(f"  {marker}{number}: {describe_audio(stream)}")
    else:
        print("Audio tracks: none")

    if audio_warning:
        print(f"Warning: {audio_warning}")

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        output_dir = path.parent

    output = output_dir / f"{path.stem}{OUTPUT_SUFFIX}"
    # Encode to a temporary name so a failed run never leaves a
    # file that looks finished.
    partial = output.with_name(output.name + ".part")

    # Never overwrite the source file, including through symlinks.
    if output.resolve() == path.resolve():
        print("Error: Output would overwrite the original file.")
        return "failed"

    if output.exists() and not args.overwrite:
        print(f"Skipped: {output} already exists.")
        return "skipped"

    tonemap = is_hdr(video) and has_filter("zscale")
    if is_hdr(video) and not tonemap:
        print(
            "Warning: HDR video, but this ffmpeg has no zscale filter "
            "to convert it. Colors will look washed out."
        )

    video_options, video_plan = plan_video(video, args, tonemap)

    # fmt: off
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-progress", "pipe:1",
        "-y",
        "-i", str(path),
        "-map", f"0:{video['index']}",
    ]

    if audio:
        audio_options, audio_plan = plan_audio(audio, args)
        command += ["-map", f"0:{audio['index']}"] + audio_options
    else:
        audio_plan = "none"

    command += [
        # No subtitles or data streams.
        "-sn",
        "-dn",

        *video_options,

        # MP4 settings.
        "-movflags", "+faststart",
        "-f", "mp4",

        str(partial),
    ]
    # fmt: on

    print(f"Output: {output}")
    print(f"Video plan: {video_plan}")
    print(f"Audio plan: {audio_plan}")
    if video_options[1] != "copy":
        print(f"Quality (CRF): {args.crf}")
        print(f"Preset: {args.preset}")

    if args.dry_run:
        print("Dry run: nothing converted.")
        return "planned"

    output_dir.mkdir(parents=True, exist_ok=True)
    print("\nConverting...")

    start_time = time.monotonic()

    try:
        try:
            return_code, stderr = run_ffmpeg(command, duration)
        except OSError as exc:
            print(f"Error starting ffmpeg: {exc}")
            return "failed"

        if return_code == 0:
            partial.replace(output)
            elapsed = time.monotonic() - start_time
            size_mb = output.stat().st_size / (1024 * 1024)

            print("Conversion completed successfully!")
            print(f"Time taken: {format_time(elapsed)}")
            print(f"Output size: {size_mb:.1f} MB")
            print(f"Saved to: {output}")

            if not verify_output(output):
                print("The converted file was kept, but it may not play.")
                return "failed"
            return "converted"

        print("Conversion failed.")
        if stderr:
            print(stderr[-3000:])
        return "failed"

    finally:
        # Remove incomplete output.
        partial.unlink(missing_ok=True)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ps4-convert",
        description="Convert videos to PS4-compatible MP4 files.",
    )

    parser.add_argument(
        "inputs", nargs="+", help="Video files or directories to convert"
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        help="Directory for converted videos (default: alongside input)",
    )

    parser.add_argument(
        "--crf",
        type=int,
        default=20,
        choices=range(0, 52),
        metavar="0-51",
        help="Video quality: lower is better (default: 20)",
    )

    parser.add_argument(
        "--preset",
        choices=PRESETS,
        default="medium",
        help="Encoding preset (default: medium)",
    )

    parser.add_argument(
        "--max-fps",
        type=int,
        default=30,
        choices=range(1, 61),
        metavar="1-60",
        help="Lower frame rates above this (default: 30)",
    )

    parser.add_argument(
        "--audio-lang",
        metavar="LANG",
        help="Prefer the audio track with this language tag, e.g. eng",
    )

    parser.add_argument(
        "--audio-track",
        type=positive_int,
        metavar="N",
        help="Use audio track N (1 = first, as listed in the output)",
    )

    parser.add_argument(
        "--stereo",
        action="store_true",
        help="Downmix audio to stereo AAC instead of keeping surround",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Convert files that are already PS4 compatible",
    )

    parser.add_argument(
        "--reencode",
        action="store_true",
        help="Always re-encode video and audio, never copy (implies --force)",
    )

    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Search directories recursively",
    )

    parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="Overwrite previously converted files",
    )

    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be done to each file without converting",
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    return parser


def main():
    use_utf8_output()
    parser = build_parser()
    args = parser.parse_args()

    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            parser.error(f"{executable} is not installed or not in PATH")

    files = collect_files(args.inputs, args.recursive)

    if not files:
        print("No video files found.")
        sys.exit(1)

    print(f"Found {len(files)} video file(s).")

    counts = collections.Counter()

    try:
        for path in files:
            counts[convert(path, args)] += 1

    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(130)

    print(f"\n{'=' * 65}")
    print("SUMMARY (dry run)" if args.dry_run else "SUMMARY")
    if args.dry_run:
        print(f"To convert: {counts['planned']}")
    else:
        print(f"Successful: {counts['converted']}")
    print(f"Skipped:    {counts['skipped']}")
    print(f"Failed:     {counts['failed']}")

    sys.exit(1 if counts["failed"] else 0)


if __name__ == "__main__":
    main()
