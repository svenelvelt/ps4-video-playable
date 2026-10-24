"""Shared fixtures: fake ffprobe streams and parsed converter options."""

import pytest

from ps4_video_playable.convert import build_parser


@pytest.fixture
def make_video():
    """Build an ffprobe video stream: compatible 1080p H.264 by default."""

    def make(**overrides):
        stream = {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "profile": "High",
            "level": 40,
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "24000/1001",
            "field_order": "progressive",
        }
        stream.update(overrides)
        return stream

    return make


@pytest.fixture
def make_audio():
    """Build an ffprobe audio stream: stereo AAC LC by default."""

    def make(
        codec="aac",
        profile="LC",
        channels=2,
        language=None,
        default=False,
        index=1,
    ):
        stream = {
            "index": index,
            "codec_type": "audio",
            "codec_name": codec,
            "profile": profile,
            "channels": channels,
            "disposition": {"default": int(default)},
        }
        if language:
            stream["tags"] = {"language": language}
        return stream

    return make


@pytest.fixture
def parse_args():
    """Parse ps4_convert command-line options, e.g. parse_args("--stereo")."""

    def parse(*argv):
        return build_parser().parse_args(["input.mkv", *argv])

    return parse
