"""Tests for how ps4_convert picks streams and builds ffmpeg options."""

from pathlib import Path

import pytest

from ps4_video_playable.convert import (
    BurnIn,
    collect_files,
    escape_filter_value,
    plan_audio,
    plan_video,
    select_audio,
    select_subtitle,
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
        assert options == ["-map", "0:0", "-c:v", "copy"]

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
        assert options == ["-map", "0:0", "-c:v", "copy"]

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


def subtitle(
    codec="subrip",
    language=None,
    default=False,
    forced=False,
    index=2,
    **extra,
):
    stream = {
        "index": index,
        "codec_type": "subtitle",
        "codec_name": codec,
        "disposition": {"default": int(default), "forced": int(forced)},
        **extra,
    }
    if language:
        stream["tags"] = {"language": language}
    return stream


class TestSelectSubtitle:
    def test_nothing_requested(self, parse_args):
        assert select_subtitle([subtitle()], parse_args()) == (None, None)

    def test_language_ignores_case(self, parse_args):
        tracks = [
            subtitle(language="jpn", index=2),
            subtitle("ass", "eng", index=3),
        ]
        stream, warning = select_subtitle(
            tracks, parse_args("--burn-subs", "ENG")
        )
        assert stream["index"] == 3
        assert warning is None

    def test_prefers_full_track_over_forced(self, parse_args):
        tracks = [
            subtitle(language="eng", forced=True, default=True, index=2),
            subtitle(language="eng", index=3),
        ]
        stream, _ = select_subtitle(tracks, parse_args("--burn-subs", "eng"))
        assert stream["index"] == 3

    def test_prefers_default_among_full_tracks(self, parse_args):
        tracks = [
            subtitle(language="eng", index=2),
            subtitle(language="eng", default=True, index=3),
        ]
        stream, _ = select_subtitle(tracks, parse_args("--burn-subs", "eng"))
        assert stream["index"] == 3

    def test_missing_language_burns_none_with_warning(self, parse_args):
        stream, warning = select_subtitle(
            [subtitle(language="jpn")], parse_args("--burn-subs", "eng")
        )
        assert stream is None
        assert "eng" in warning

    def test_track_number(self, parse_args):
        tracks = [subtitle(index=2), subtitle(index=3)]
        stream, _ = select_subtitle(
            tracks, parse_args("--burn-sub-track", "2")
        )
        assert stream["index"] == 3

    def test_track_out_of_range(self, parse_args):
        with pytest.raises(LookupError, match="does not exist"):
            select_subtitle([], parse_args("--burn-sub-track", "1"))

    def test_options_are_mutually_exclusive(self, parse_args):
        with pytest.raises(SystemExit):
            parse_args("--burn-subs", "eng", "--burn-sub-track", "1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("movie.mkv", "movie.mkv"),
        # Windows path: drive colon and backslashes.
        (r"C:\a'b", r"C\\:\\\\a\\\'b"),
        ("x [1], y; z", r"x \[1\]\, y\; z"),
    ],
)
def test_escape_filter_value(value, expected):
    assert escape_filter_value(value) == expected


class TestBurnIn:
    def test_text_subtitles_are_drawn_last(self, make_video, parse_args):
        burn_in = BurnIn(Path("in.mkv"), subtitle("ass"), position=1)
        options, plan = plan_video(make_video(), parse_args(), burn_in=burn_in)
        assert option(options, "-c:v") == "libx264"
        assert "burning in subtitles" in plan
        assert option(options, "-map") == "0:0"
        assert option(options, "-vf").endswith(
            "subtitles=filename=in.mkv:si=1"
        )

    def test_text_subtitles_come_after_tonemapping(
        self, make_video, parse_args
    ):
        burn_in = BurnIn(Path("in.mkv"), subtitle(), position=0)
        stream = make_video(color_transfer="smpte2084")
        options, _ = plan_video(stream, parse_args(), True, burn_in)
        vf = option(options, "-vf")
        assert vf.index("tonemap") < vf.index("subtitles=")

    def test_picture_subtitles_are_overlaid(self, make_video, parse_args):
        subs = subtitle("hdmv_pgs_subtitle", width=1920, height=1080, index=5)
        burn_in = BurnIn(Path("in.mkv"), subs, position=0)
        options, _ = plan_video(make_video(), parse_args(), burn_in=burn_in)
        assert "-vf" not in options
        assert option(options, "-map") == "[video]"
        graph = option(options, "-filter_complex")
        assert graph.startswith("[0:0][0:5]overlay=eof_action=pass,scale=")
        assert graph.endswith("[video]")

    def test_picture_subtitles_are_fitted_to_the_video(
        self, make_video, parse_args
    ):
        subs = subtitle("hdmv_pgs_subtitle", width=1920, height=1080, index=5)
        stream = make_video(width=1920, height=800, field_order="tt")
        burn_in = BurnIn(Path("in.mkv"), subs, position=0)
        options, _ = plan_video(stream, parse_args(), burn_in=burn_in)
        graph = option(options, "-filter_complex").split(";")
        assert graph[0] == "[0:5]scale=1920:800[subs]"
        assert graph[1] == "[0:0]bwdif=mode=send_frame[base]"
        assert graph[2].startswith("[base][subs]overlay")

    def test_picture_subtitles_are_tonemapped_with_the_video(
        self, make_video, parse_args
    ):
        subs = subtitle("dvd_subtitle", index=3)
        stream = make_video(color_transfer="smpte2084")
        burn_in = BurnIn(Path("in.mkv"), subs, position=0)
        options, _ = plan_video(stream, parse_args(), True, burn_in)
        graph = option(options, "-filter_complex")
        assert graph.index("tonemap") < graph.index("overlay")


def test_language_codes_match_across_standards(make_audio, parse_args):
    tracks = [make_audio(language="jpn", index=1)]
    tracks.append(make_audio(language="de", index=2))
    stream, _ = select_audio(tracks, parse_args("--audio-lang", "ger"))
    assert stream["index"] == 2

    subs = [subtitle(language="en", index=3)]
    stream, _ = select_subtitle(subs, parse_args("--burn-subs", "eng"))
    assert stream["index"] == 3
