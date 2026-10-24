"""Run both scripts on short clips generated with ffmpeg."""

import json
import os
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


def run(module, *argv, env=None):
    """Run ps4_video_playable.<module> the way the installed command does.

    Arguments are passed without a shell, so wildcards reach the command
    unexpanded, as they do on Windows.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            f"ps4_video_playable.{module}",
            *map(str, argv),
        ],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, **(env or {})},
    )


def streams(path):
    # fmt: off
    command = [
        "ffprobe", "-v", "error",
        "-show_streams", "-of", "json", str(path),
    ]
    # fmt: on
    result = subprocess.run(
        command, capture_output=True, encoding="utf-8", check=True
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
    assert "Check: ✅ LIKELY COMPATIBLE" in result.stdout

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


def test_check_folder(tmp_path):
    (tmp_path / "sub").mkdir()
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        tmp_path / "ok.mp4",
    )
    ffmpeg(
        *TEST_PATTERN, *TONE, "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "flac",
        tmp_path / "sub" / "bad.mkv",
    )
    # fmt: on
    (tmp_path / "poster.jpg").touch()

    flat = run("check", "--json", tmp_path)
    assert flat.returncode == 0
    assert [r["status"] for r in json.loads(flat.stdout)] == ["compatible"]

    deep = run("check", "-r", tmp_path)
    assert deep.returncode == 1
    assert "SUMMARY (2 files)" in deep.stdout
    assert "bad.mkv: Unsupported audio codec: flac" in deep.stdout

    summary = run("check", "-r", "--summary", tmp_path)
    assert summary.returncode == 1
    assert summary.stdout.startswith("SUMMARY (2 files)")
    assert "VIDEO STREAM" not in summary.stdout
    # Progress is only shown on a terminal, never in captured output.
    assert summary.stderr == ""

    both = run("check", "--summary", "--json", tmp_path)
    assert both.returncode == 2
    assert "not allowed with" in both.stderr

    empty = tmp_path / "empty"
    empty.mkdir()
    nothing = run("check", empty)
    assert nothing.returncode == 2
    assert "No video files found" in nothing.stderr


def test_dry_run_converts_nothing(tmp_path):
    source = tmp_path / "movie.mkv"
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "flac",
        source,
    )
    # fmt: on
    output_dir = tmp_path / "out"

    result = run("convert", "--dry-run", "-o", output_dir, source)
    assert result.returncode == 0, result.stdout
    assert "Video plan: copy" in result.stdout
    assert "Audio plan: AAC stereo" in result.stdout
    assert "To convert: 1" in result.stdout
    assert not output_dir.exists()


def test_parallel_check_keeps_order(tmp_path):
    for name in ("c.mp4", "a.mp4", "b.mp4"):
        # fmt: off
        ffmpeg(
            *TEST_PATTERN, *TONE, "-t", "1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            tmp_path / name,
        )
        # fmt: on

    result = run("check", "--json", "-j", "3", tmp_path)
    files = [r["file"] for r in json.loads(result.stdout)]
    assert files == [str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4")]


@pytest.mark.parametrize("module", ["check", "convert"])
def test_version(module):
    result = run(module, "--version")
    assert result.returncode == 0
    name, version = result.stdout.split()
    assert name == f"ps4-{module}"
    assert version[0].isdigit()


def make_compatible_clip(path, *extra):
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        *extra, path,
    )
    # fmt: on


def test_non_ascii_names_and_tags(tmp_path):
    source = tmp_path / "café 日本.mkv"
    make_compatible_clip(source, "-metadata", "title=日本語のタイトル")

    result = run("check", "--json", source)
    assert result.returncode == 0, result.stderr
    [report] = json.loads(result.stdout)
    assert report["file"] == str(source)
    assert report["status"] == "compatible"


def test_redirected_output_on_legacy_encoding(tmp_path):
    # Windows uses a legacy code page such as cp1252 for redirected
    # output, which can't encode the result symbols.
    source = tmp_path / "ok.mp4"
    make_compatible_clip(source)

    result = run("check", source, env={"PYTHONIOENCODING": "cp1252"})
    assert result.returncode == 0, result.stderr
    assert "✅ LIKELY COMPATIBLE" in result.stdout


def test_unexpanded_wildcards(tmp_path):
    for name in ("a.mp4", "b.mp4"):
        make_compatible_clip(tmp_path / name)

    result = run("check", "--json", tmp_path / "*.mp4")
    files = [r["file"] for r in json.loads(result.stdout)]
    assert files == [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]

    missing = run("check", tmp_path / "*.avi")
    assert missing.returncode == 2
    assert "File does not exist" in missing.stdout

    plan = run("convert", "--dry-run", "--force", tmp_path / "*.mp4")
    assert "Found 2 video file(s)." in plan.stdout


def first_frame(path):
    """Return the raw grey pixels of the frame at 0.5 s."""
    # fmt: off
    command = [
        "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(path),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    # fmt: on
    return subprocess.run(command, capture_output=True, check=True).stdout


def test_burn_in_text_subtitles(tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello PS4\n")
    # Characters that need escaping inside an ffmpeg filter, and that
    # are allowed in Windows file names.
    source = tmp_path / "it's [a], b; c.mkv"
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-i", srt, "-t", "1",
        "-map", "0", "-map", "1", "-map", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-c:s", "srt", "-metadata:s:s:0", "language=eng",
        source,
    )
    # fmt: on

    with_subs = run(
        "convert", "--burn-subs", "eng", "-o", tmp_path / "subs", source
    )
    assert with_subs.returncode == 0, with_subs.stdout + with_subs.stderr
    assert "Subtitle plan: burn in track 1" in with_subs.stdout

    without = run("convert", "--force", "-o", tmp_path / "plain", source)
    assert "Subtitle plan: none (use --burn-subs" in without.stdout

    burned = tmp_path / "subs" / "it's [a], b; c-ps4.mp4"
    plain = tmp_path / "plain" / "it's [a], b; c-ps4.mp4"
    assert [s["codec_type"] for s in streams(burned)] == ["video", "audio"]
    assert first_frame(burned) != first_frame(plain)


def test_compatible_file_is_converted_to_burn_in(tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    source = tmp_path / "ok.mp4"
    # fmt: off
    ffmpeg(
        *TEST_PATTERN, *TONE, "-i", srt, "-t", "1",
        "-map", "0", "-map", "1", "-map", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-c:s", "mov_text",
        source,
    )
    # fmt: on

    plain = run("convert", "--dry-run", source)
    assert "already PS4 compatible" in plain.stdout

    burn = run("convert", "--dry-run", "--burn-sub-track", "1", source)
    assert "Video plan: re-encode to H.264 (burning in" in burn.stdout

    missing = run("convert", "--dry-run", "--burn-sub-track", "2", source)
    assert missing.returncode == 1
    assert "Subtitle track 2 does not exist" in missing.stdout
