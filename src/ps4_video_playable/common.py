# SPDX-FileCopyrightText: 2026 Sven Elvelt
# SPDX-License-Identifier: GPL-3.0-or-later

"""PS4 Media Player compatibility rules shared by ps4-check and ps4-convert."""

import json
import subprocess

# Sony's supported video/audio combinations.
# "container" is the name ffprobe reports in format_name for that extension.
FORMATS = {
    ".mkv": {
        "container": "matroska",
        "video": {"h264"},
        "audio": {"mp3", "aac", "ac3"},
    },
    ".avi": {
        "container": "avi",
        "video": {"h264", "mpeg4"},
        "audio": {"mp3", "aac", "ac3"},
    },
    ".mp4": {
        "container": "mp4",
        "video": {"h264"},
        "audio": {"aac", "ac3", "pcm"},
    },
    ".m4v": {
        "container": "mp4",
        "video": {"h264"},
        "audio": {"aac", "ac3", "pcm"},
    },
    ".mpg": {
        "container": "mpeg",
        "video": {"mpeg2video"},
        "audio": {"mp2", "mp3", "aac", "ac3", "pcm"},
    },
    ".mpeg": {
        "container": "mpeg",
        "video": {"mpeg2video"},
        "audio": {"mp2", "mp3", "aac", "ac3", "pcm"},
    },
    ".vob": {
        "container": "mpeg",
        "video": {"mpeg2video"},
        "audio": {"mp2", "mp3", "aac", "ac3", "pcm"},
    },
    ".ts": {
        "container": "mpegts",
        "video": {"h264", "mpeg2video"},
        "audio": {"mp2", "aac", "ac3"},
    },
    ".m2ts": {
        "container": "mpegts",
        "video": {"h264", "mpeg2video"},
        "audio": {"mp2", "aac", "ac3"},
    },
    ".mts": {
        "container": "mpegts",
        "video": {"h264", "mpeg2video"},
        "audio": {"mp2", "aac", "ac3"},
    },
}

H264_PROFILES = {"High", "Main", "Baseline", "Constrained Baseline"}

# PQ (HDR10) and HLG transfer characteristics as reported by ffprobe.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

INTERLACED_FIELD_ORDERS = {"tt", "bb", "tb", "bt"}

MAX_FPS = 60

# Average frame rates drift slightly (Matroska stores timestamps in
# whole milliseconds), so 30 fps can be reported as 30.02.
FPS_TOLERANCE = 0.5


def probe(path):
    # fmt: off
    command = [
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]
    # fmt: on
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def video_streams(data):
    # Cover art is stored as a video stream but is not played.
    return [
        s
        for s in data.get("streams", [])
        if s.get("codec_type") == "video"
        and not s.get("disposition", {}).get("attached_pic", 0)
    ]


def audio_streams(data):
    return [
        s for s in data.get("streams", []) if s.get("codec_type") == "audio"
    ]


def frame_rate(stream):
    """Return the stream's frame rate in fps, or None if unknown."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            numerator, denominator = stream.get(key, "").split("/")
            numerator, denominator = int(numerator), int(denominator)
        except ValueError:
            continue
        if numerator > 0 and denominator > 0:
            return numerator / denominator
    return None


def is_hdr(stream):
    return stream.get("color_transfer") in HDR_TRANSFERS


def is_interlaced(stream):
    return stream.get("field_order") in INTERLACED_FIELD_ORDERS


def audio_type(codec):
    if codec.startswith("pcm_"):
        return "pcm"
    return codec


def check_video(stream, extension, pro):
    errors = []
    warnings = []

    codec = stream.get("codec_name", "unknown")
    profile = stream.get("profile", "unknown")
    level = stream.get("level")
    width = stream.get("width", 0)
    height = stream.get("height", 0)

    allowed = FORMATS[extension]["video"]

    if codec not in allowed:
        errors.append(f"Unsupported video codec: {codec}")
        return errors, warnings

    if codec == "h264":
        # "High 10", "High 4:2:2", "High 4:4:4 ..." need a decoder
        # beyond 8-bit 4:2:0, which the PS4 does not have.
        if profile.startswith("High "):
            errors.append(f"Unsupported H.264 profile: {profile}")
        elif profile not in H264_PROFILES:
            warnings.append(
                f"H.264 profile {profile} is not confirmed supported"
            )

        # H.264 level is represented as an integer:
        # 42 = 4.2, 52 = 5.2
        max_level = 52 if pro and extension in {".mp4", ".m4v"} else 42

        if isinstance(level, int) and level > 0:
            if level > max_level:
                errors.append(
                    f"H.264 level {level / 10:.1f} exceeds "
                    f"supported level {max_level / 10:.1f}"
                )
        else:
            warnings.append("H.264 level could not be determined")

    elif codec == "mpeg4":
        if profile not in {"Advanced Simple Profile", "Simple Profile"}:
            warnings.append(f"MPEG-4 profile {profile} needs verification")

    if width and height:
        if pro and (width > 3840 or height > 2160):
            errors.append(
                f"Resolution {width}x{height} exceeds 4K (3840x2160)"
            )
        elif not pro and (width > 1920 or height > 1080):
            warnings.append(
                f"Resolution {width}x{height} exceeds normal "
                "1080p expectations for standard PS4"
            )

    fps = frame_rate(stream)
    if fps and fps > MAX_FPS + FPS_TOLERANCE:
        warnings.append(f"Frame rate {fps:.2f} fps exceeds {MAX_FPS} fps")

    if is_hdr(stream):
        warnings.append(
            f"HDR video ({stream['color_transfer']}) may look washed out"
        )

    pix_fmt = stream.get("pix_fmt", "unknown")
    if codec == "h264":
        if pix_fmt == "unknown":
            warnings.append("Pixel format could not be determined")
        elif pix_fmt not in {"yuv420p", "yuvj420p"}:
            errors.append(
                f"Unsupported pixel format: {pix_fmt} (8-bit yuv420p required)"
            )

    return errors, warnings


def check_audio(stream, extension):
    errors = []
    warnings = []

    codec = stream.get("codec_name", "unknown")
    profile = stream.get("profile", "unknown")
    normalized = audio_type(codec)

    allowed = FORMATS[extension]["audio"]

    if normalized not in allowed:
        errors.append(f"Unsupported audio codec: {codec}")
    elif codec == "aac" and profile not in {"LC", "unknown"}:
        errors.append(f"Unsupported AAC profile: {profile} (AAC LC required)")
    elif codec == "aac" and profile == "unknown":
        warnings.append("AAC profile could not be determined")

    return errors, warnings


def evaluate(data, extension, pro=False):
    """Check probed file data. Returns (errors, warnings)."""
    if extension not in FORMATS:
        return [f"Unsupported container {extension}"], []

    errors = []
    warnings = []

    format_name = data.get("format", {}).get("format_name", "unknown")
    if FORMATS[extension]["container"] not in format_name.split(","):
        errors.append(
            f"Container is {format_name}, which does not match "
            f"the {extension} extension"
        )

    videos = video_streams(data)
    audios = audio_streams(data)

    if not videos:
        errors.append("No video stream found")

    if not audios:
        warnings.append("No audio stream found")

    for stream in videos:
        e, w = check_video(stream, extension, pro)
        errors.extend(e)
        warnings.extend(w)

    for stream in audios:
        e, w = check_audio(stream, extension)
        errors.extend(e)
        warnings.extend(w)

    return errors, warnings
