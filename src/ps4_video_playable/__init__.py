# SPDX-FileCopyrightText: 2026 Sven Elvelt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check and convert video files for the PS4 Media Player."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ps4-video-playable")
except PackageNotFoundError:
    __version__ = "unknown"
