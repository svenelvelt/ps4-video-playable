"""Tests for the compatibility rules in ps4_video_playable.common."""

import argparse

import pytest

from ps4_video_playable import common
from ps4_video_playable.common import (
    check_audio,
    check_video,
    evaluate,
    find_videos,
    frame_rate,
    positive_int,
    storage_notes,
)

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"


@pytest.mark.parametrize(
    ("rates", "expected"),
    [
        ({"avg_frame_rate": "24000/1001"}, pytest.approx(23.976, abs=1e-3)),
        ({"avg_frame_rate": "0/0", "r_frame_rate": "25/1"}, 25),
        ({"avg_frame_rate": "0/0"}, None),
        ({}, None),
    ],
)
def test_frame_rate(rates, expected):
    assert frame_rate(rates) == expected


class TestCheckVideo:
    def test_compatible(self, make_video):
        assert check_video(make_video(), ".mp4", pro=False) == ([], [])

    def test_unsupported_codec(self, make_video):
        errors, _ = check_video(make_video(codec_name="hevc"), ".mp4", False)
        assert errors == ["Unsupported video codec: hevc"]

    def test_10bit_is_an_error(self, make_video):
        stream = make_video(profile="High 10", pix_fmt="yuv420p10le")
        errors, _ = check_video(stream, ".mkv", pro=False)
        assert errors == [
            "Unsupported H.264 profile: High 10",
            "Unsupported pixel format: yuv420p10le (8-bit yuv420p required)",
        ]

    @pytest.mark.parametrize(
        ("extension", "pro", "allowed"),
        [
            (".mp4", False, False),
            (".mp4", True, True),
            (".mkv", True, False),
        ],
    )
    def test_level_limit(self, make_video, extension, pro, allowed):
        stream = make_video(level=51, width=3840, height=2160)
        errors, _ = check_video(stream, extension, pro)
        assert not errors if allowed else errors

    def test_unknown_level_warns(self, make_video):
        _, warnings = check_video(make_video(level=-99), ".mp4", False)
        assert "H.264 level could not be determined" in warnings

    def test_pro_rejects_above_4k(self, make_video):
        stream = make_video(level=52, width=4096, height=2304)
        errors, _ = check_video(stream, ".mp4", pro=True)
        assert any("exceeds 4K" in e for e in errors)

    @pytest.mark.parametrize(
        ("rate", "warns"),
        [("120/1", True), ("60/1", False), ("60060/1000", False)],
    )
    def test_frame_rate_limit(self, make_video, rate, warns):
        _, warnings = check_video(
            make_video(avg_frame_rate=rate), ".mp4", False
        )
        assert any("Frame rate" in w for w in warnings) is warns

    @pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
    def test_hdr_warns(self, make_video, transfer):
        stream = make_video(color_transfer=transfer)
        _, warnings = check_video(stream, ".mp4", False)
        assert any("HDR" in w for w in warnings)


class TestCheckAudio:
    @pytest.mark.parametrize(
        ("codec", "profile", "extension"),
        [
            ("aac", "LC", ".mp4"),
            ("ac3", None, ".mkv"),
            ("pcm_s16le", None, ".mp4"),
            ("mp2", None, ".ts"),
        ],
    )
    def test_supported(self, make_audio, codec, profile, extension):
        stream = make_audio(codec=codec, profile=profile)
        assert check_audio(stream, extension) == ([], [])

    @pytest.mark.parametrize(
        ("codec", "profile", "extension"),
        [
            ("aac", "HE-AAC", ".mp4"),
            ("dts", "DTS", ".mkv"),
            ("eac3", None, ".mp4"),
            ("pcm_s16le", None, ".mkv"),
        ],
    )
    def test_unsupported(self, make_audio, codec, profile, extension):
        errors, _ = check_audio(
            make_audio(codec=codec, profile=profile), extension
        )
        assert errors

    def test_unknown_aac_profile_warns(self, make_audio):
        stream = make_audio(profile="unknown")
        assert check_audio(stream, ".mp4") == (
            [],
            ["AAC profile could not be determined"],
        )


class TestEvaluate:
    def test_compatible(self, make_video, make_audio):
        data = {
            "format": {"format_name": MP4},
            "streams": [make_video(), make_audio()],
        }
        assert evaluate(data, ".mp4") == ([], [])

    def test_cover_art_is_ignored(self, make_video, make_audio):
        cover = {
            "index": 2,
            "codec_type": "video",
            "codec_name": "png",
            "disposition": {"attached_pic": 1},
        }
        data = {
            "format": {"format_name": MP4},
            "streams": [make_video(), make_audio(), cover],
        }
        assert evaluate(data, ".mp4") == ([], [])

    def test_container_must_match_extension(self, make_video, make_audio):
        data = {
            "format": {"format_name": "matroska,webm"},
            "streams": [make_video(), make_audio()],
        }
        errors, _ = evaluate(data, ".mp4")
        assert any("Container" in e for e in errors)

    def test_missing_streams(self):
        data = {"format": {"format_name": MP4}, "streams": []}
        assert evaluate(data, ".mp4") == (
            ["No video stream found"],
            ["No audio stream found"],
        )

    def test_unknown_extension(self):
        assert evaluate({}, ".mov") == (["Unsupported container .mov"], [])


class TestFindVideos:
    def test_only_video_files_sorted(self, tmp_path):
        for name in ("b.mkv", "a.MP4", "cover.png", "notes.txt"):
            (tmp_path / name).touch()
        (tmp_path / "folder.avi").mkdir()
        names = [p.name for p in find_videos(tmp_path)]
        assert names == ["a.MP4", "b.mkv"]

    def test_recursive(self, tmp_path):
        (tmp_path / "top.mkv").touch()
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.ts").touch()
        assert [p.name for p in find_videos(tmp_path)] == ["top.mkv"]
        assert [p.name for p in find_videos(tmp_path, recursive=True)] == [
            "deep.ts",
            "top.mkv",
        ]


class TestStorageNotes:
    def test_small_file_has_no_notes(self, tmp_path):
        video = tmp_path / "a.mkv"
        video.write_bytes(b"x" * 10)
        assert storage_notes(video) == []

    def test_file_over_fat32_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(common, "FAT32_MAX_FILE_SIZE", 9)
        video = tmp_path / "a.mkv"
        video.write_bytes(b"x" * 10)
        [note] = storage_notes(video)
        assert "FAT32" in note
        assert "exFAT" in note

    def test_file_at_limit_fits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(common, "FAT32_MAX_FILE_SIZE", 10)
        video = tmp_path / "a.mkv"
        video.write_bytes(b"x" * 10)
        assert storage_notes(video) == []

    def test_missing_file(self, tmp_path):
        assert storage_notes(tmp_path / "missing.mkv") == []


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_positive_int_rejects(value):
    with pytest.raises((argparse.ArgumentTypeError, ValueError)):
        positive_int(value)


def test_positive_int_accepts():
    assert positive_int("3") == 3
