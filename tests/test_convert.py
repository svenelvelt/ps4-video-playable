"""Tests for how ps4_convert picks streams and builds ffmpeg options."""

import pytest

from ps4_video_playable.convert import (
    collect_files,
    plan_audio,
    plan_video,
    select_audio,
)


def option(options, flag):
    """Return the value that follows an ffmpeg flag."""
    return options[options.index(flag) + 1]


class TestSelectAudio:
    @pytest.fixture
    def tracks(self, make_audio):
        return [
            make_audio(language="eng", index=1),
            make_audio(language="jpn", default=True, index=2),
        ]

    def test_prefers_default_track(self, tracks, parse_args):
        stream, warning = select_audio(tracks, parse_args())
        assert stream["index"] == 2
        assert warning is None

    def test_first_track_without_default(self, make_audio, parse_args):
        tracks = [make_audio(index=1), make_audio(index=2)]
        stream, _ = select_audio(tracks, parse_args())
        assert stream["index"] == 1

    def test_language_ignores_case(self, tracks, parse_args):
        stream, _ = select_audio(tracks, parse_args("--audio-lang", "ENG"))
        assert stream["index"] == 1

    def test_missing_language_falls_back_with_warning(
        self, tracks, parse_args
    ):
        stream, warning = select_audio(
            tracks, parse_args("--audio-lang", "ger")
        )
        assert stream["index"] == 2
        assert "ger" in warning

    def test_track_number(self, tracks, parse_args):
        stream, _ = select_audio(tracks, parse_args("--audio-track", "1"))
        assert stream["index"] == 1

    def test_track_out_of_range(self, tracks, parse_args):
        with pytest.raises(LookupError, match="does not exist"):
            select_audio(tracks, parse_args("--audio-track", "3"))

    def test_no_audio(self, parse_args):
        assert select_audio([], parse_args()) == (None, None)


class TestPlanVideo:
    def test_compatible_is_copied(self, make_video, parse_args):
        options, _ = plan_video(make_video(), parse_args())
        assert options == ["-c:v", "copy"]

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"codec_name": "mpeg2video"}, "Unsupported video codec"),
            ({"pix_fmt": "yuv420p10le"}, "pixel format"),
            ({"width": 3840, "height": 2160}, "Resolution"),
            ({"avg_frame_rate": "60/1"}, "frame rate"),
            ({"avg_frame_rate": "0/0"}, "frame rate could not"),
            ({"field_order": "tt"}, "interlaced"),
        ],
    )
    def test_reencoded_with_reason(
        self, make_video, parse_args, overrides, reason
    ):
        options, plan = plan_video(make_video(**overrides), parse_args())
        assert option(options, "-c:v") == "libx264"
        assert reason in plan

    def test_reencode_flag(self, make_video, parse_args):
        options, plan = plan_video(make_video(), parse_args("--reencode"))
        assert option(options, "-c:v") == "libx264"
        assert "--reencode" in plan

    def test_encoder_settings(self, make_video, parse_args):
        options, _ = plan_video(
            make_video(field_order="tt"),
            parse_args("--crf", "18", "--preset", "slow"),
        )
        assert option(options, "-crf") == "18"
        assert option(options, "-preset") == "slow"
        assert option(options, "-level:v") == "4.2"
        assert option(options, "-pix_fmt") == "yuv420p"

    def test_higher_max_fps_allows_copy(self, make_video, parse_args):
        options, _ = plan_video(
            make_video(avg_frame_rate="60/1"), parse_args("--max-fps", "60")
        )
        assert options == ["-c:v", "copy"]

    def test_max_fps_is_in_filter(self, make_video, parse_args):
        options, _ = plan_video(
            make_video(avg_frame_rate="60/1"), parse_args("--max-fps", "25")
        )
        assert "min(source_fps,25)" in option(options, "-vf")

    def test_interlaced_is_deinterlaced_first(self, make_video, parse_args):
        options, _ = plan_video(make_video(field_order="tt"), parse_args())
        assert option(options, "-vf").startswith("bwdif")

    @pytest.mark.parametrize("tonemap", [True, False])
    def test_hdr_tonemap(self, make_video, parse_args, tonemap):
        stream = make_video(color_transfer="smpte2084")
        options, _ = plan_video(stream, parse_args(), tonemap=tonemap)
        assert ("tonemap" in option(options, "-vf")) is tonemap
        assert ("-color_trc" in options) is tonemap


class TestPlanAudio:
    @pytest.mark.parametrize(
        ("codec", "profile", "channels"),
        [("aac", "LC", 2), ("ac3", None, 2), ("ac3", None, 6)],
    )
    def test_compatible_is_copied(
        self, make_audio, parse_args, codec, profile, channels
    ):
        stream = make_audio(codec=codec, profile=profile, channels=channels)
        assert plan_audio(stream, parse_args())[0] == ["-c:a", "copy"]

    @pytest.mark.parametrize(
        ("channels", "expected"), [(3, "3"), (6, "6"), (8, "6")]
    )
    def test_surround_becomes_ac3(
        self, make_audio, parse_args, channels, expected
    ):
        stream = make_audio(codec="dts", profile="DTS", channels=channels)
        options, _ = plan_audio(stream, parse_args())
        assert option(options, "-c:a") == "ac3"
        assert option(options, "-ac") == expected

    @pytest.mark.parametrize(
        ("codec", "profile", "channels", "argv"),
        [
            ("ac3", None, 6, ["--stereo"]),
            ("aac", "HE-AAC", 2, []),
            ("flac", None, 2, []),
            ("aac", "LC", 2, ["--reencode"]),
        ],
    )
    def test_stereo_aac(
        self, make_audio, parse_args, codec, profile, channels, argv
    ):
        stream = make_audio(codec=codec, profile=profile, channels=channels)
        options, plan = plan_audio(stream, parse_args(*argv))
        assert option(options, "-c:a") == "aac"
        assert option(options, "-ac") == "2"
        assert plan == "AAC stereo"


class TestCollectFiles:
    def test_finds_videos_and_skips_previous_output(self, tmp_path):
        for name in ("a.mkv", "b.MP4", "a-ps4.mp4", "notes.txt"):
            (tmp_path / name).touch()
        names = [p.name for p in collect_files([tmp_path])]
        assert names == ["a.mkv", "b.MP4"]

    def test_recursive(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.avi").touch()
        assert collect_files([tmp_path]) == []
        assert [p.name for p in collect_files([tmp_path], True)] == ["c.avi"]

    def test_explicit_file_is_kept_and_deduplicated(self, tmp_path):
        video = tmp_path / "x-ps4.mp4"
        video.touch()
        assert collect_files([video, video, tmp_path]) == [video]

    def test_missing_path_warns(self, tmp_path, capsys):
        assert collect_files([tmp_path / "nope.mkv"]) == []
        assert "Path not found" in capsys.readouterr().out
