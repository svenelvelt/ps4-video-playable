"""Run both scripts on short clips generated with ffmpeg."""

import json
import shutil
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
        reason="ffmpeg/ffprobe not installed",
    ),
]

TEST_PATTERN = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24"]
TONE = ["-f", "lavfi", "-i", "sine"]


def ffmpeg(*argv):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *argv], check=True)


def run(module, *argv):
    """Run ps4_video_playable.<module> the way the installed command does."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            f"ps4_video_playable.{module}",
            *map(str, argv),
        ],
        capture_output=True,
        text=True,
    )


def streams(path):
    # fmt: off
    command = [
        "ffprobe", "-v", "error",
        "-show_streams", "-of", "json", str(path),
    ]
    # fmt: on
    result = subprocess.run(
        command, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)["streams"]


def test_remux_copies_video_and_converts_audio(tmp_path):
    source = tmp_path / "movie.mkv"
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "flac", "-ac", "6",
        source,
    )
    # fmt: on

    result = run("convert", source)
    assert result.returncode == 0, result.stdout
    assert "Video plan: copy" in result.stdout

    output = tmp_path / "movie-ps4.mp4"
    codecs = [(s["codec_name"], s.get("channels")) for s in streams(output)]
    assert codecs == [("h264", None), ("ac3", 6)]
    assert not list(tmp_path.glob("*.part"))

    check = run("check", "--json", output)
    assert check.returncode == 0
    assert json.loads(check.stdout)[0]["status"] == "compatible"

    rerun = run("convert", tmp_path)
    assert "Skipped:    1" in rerun.stdout


def test_compatible_file_is_skipped(tmp_path):
    source = tmp_path / "ok.mp4"
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        source,
    )
    # fmt: on

    result = run("convert", source)
    assert "already PS4 compatible" in result.stdout
    assert not (tmp_path / "ok-ps4.mp4").exists()


def test_reencode_caps_frame_rate(tmp_path):
    source = tmp_path / "fast.mkv"
    # fmt: off
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60",
        *TONE, "-t", "1",
        "-c:v", "mpeg4", "-c:a", "libmp3lame",
        source,
    )
    # fmt: on

    result = run("convert", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Video plan: re-encode" in result.stdout

    video, audio = streams(tmp_path / "fast-ps4.mp4")
    assert (video["codec_name"], video["profile"]) == ("h264", "High")
    assert video["pix_fmt"] == "yuv420p"
    assert video["avg_frame_rate"] == "30/1"
    assert (audio["codec_name"], audio["channels"]) == ("aac", 2)


def test_checker_does_not_write_files(tmp_path):
    source = tmp_path / "ten.mkv"
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p10le",
        source,
    )
    # fmt: on

    result = run("check", source)
    assert result.returncode == 1
    assert "NOT COMPATIBLE" in result.stdout
    assert [p.name for p in tmp_path.iterdir()] == ["ten.mkv"]
