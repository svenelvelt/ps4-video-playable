# ps4-video-playable

[![CI](https://github.com/svenelvelt/ps4-video-playable/actions/workflows/ci.yml/badge.svg)](https://github.com/svenelvelt/ps4-video-playable/actions/workflows/ci.yml)

Two command-line tools for playing video files on the PS4 Media Player:

- `ps4-check` reports whether a file should play, and why not if it won't.
- `ps4-convert` turns files into PS4-compatible MP4s, re-encoding only what
  needs it.

The rules follow Sony's
[list of supported file formats for PS4](https://www.playstation.com/en-us/support/hardware/play-video-music-discs-usb-drives/).

## Installation

Works on Linux, macOS and Windows. You need Python 3.10+ and FFmpeg 5.0+
(`ffmpeg` and `ffprobe` on your PATH):

| System | Install FFmpeg with |
|---|---|
| Windows | `winget install --id Gyan.FFmpeg` |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` |
| Arch | `sudo pacman -S ffmpeg` |

Then install the tool with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/svenelvelt/ps4-video-playable
```

or with [pipx](https://pipx.pypa.io/):

```sh
pipx install git+https://github.com/svenelvelt/ps4-video-playable
```

Both put the `ps4-check` and `ps4-convert` commands on your PATH.

**Mac users:** when you copy videos to a FAT32 or exFAT USB drive, macOS
adds hidden `._` files next to them, which the PS4 may list as broken
videos. Both commands ignore these files. To remove them from the drive,
run `dot_clean -m /Volumes/<drive name>` before ejecting.

## Checking files

```sh
ps4-check movie.mkv
ps4-check ~/Videos                 # every video in a folder
ps4-check -r ~/Videos              # ...and in its subfolders
ps4-check -r --summary ~/Videos    # just the summary, no per-file reports
ps4-check --pro movie.mp4          # PS4 Pro limits (4K, H.264 level 5.2 in MP4)
ps4-check --json *.mkv > report.json
```

When you check more than one file, a summary at the end counts each result
and lists the files that won't play or need verification, with the main
reason for each:

```
SUMMARY (3 files)
    1  ✅ Likely compatible
    0  ⚠️ Needs verification
    2  ❌ Not compatible
    0  Could not be checked

Won't play or couldn't be checked:
  Videos/anime.mkv: Unsupported H.264 profile: High 10
  Videos/concert.mkv: Unsupported audio codec: flac
```

For large folders, `--summary` skips the per-file reports and shows a
progress counter while it works. Several files are checked at once
(`-j N` to change how many; the default depends on your CPU).
Converted `-ps4.mp4` files are included when checking a folder, so you can
check your converted videos too.

Each file gets one of three results:

| Result | Meaning | Exit code |
|---|---|---|
| ✅ LIKELY COMPATIBLE | Container, video and audio meet the known requirements | 0 |
| ⚠️ NEEDS VERIFICATION | Nothing is known to be wrong, but something couldn't be confirmed | 0 |
| ❌ NOT COMPATIBLE | At least one requirement is not met | 1 |

Exit code 2 means a file was missing or couldn't be read, or a folder had no
videos in it. With several files, the command exits with the highest code.

Notes (ℹ️) point out things that don't affect playback and don't change the
result. For example, a file of 4 GB or more is flagged because it won't fit
on a USB drive formatted as FAT32; format the drive as exFAT instead, which
the PS4 also reads.

## Converting files

```sh
ps4-convert movie.mkv
ps4-convert -r ~/Videos -o ~/ps4          # whole folder tree, into ~/ps4
ps4-convert movie.mkv --audio-lang eng    # pick the English audio track
ps4-convert -n ~/Videos                   # dry run: show the plan, convert nothing
```

The output is `<name>-ps4.mp4`, written next to the input unless you give
`-o`. For each file the converter:

- **skips** it if it already passes the check (use `--force` to convert anyway);
- **copies** the video untouched if it is already compatible H.264. A file
  that only needs new audio converts in seconds;
- otherwise **re-encodes** the video to H.264 High@4.2, at most 1080p and
  `--max-fps` (default 30). It deinterlaces interlaced video and tone-maps
  HDR to SDR (tone-mapping needs ffmpeg built with the `zscale` filter);
- keeps **one audio track**: the `--audio-lang` or `--audio-track` choice,
  else the track marked default, else the first. AAC LC and AC-3 are copied.
  Other surround audio becomes AC-3 5.1, and other stereo audio becomes AAC;
- drops subtitles and data streams;
- **checks the result** with the same rules as `ps4-check` and reports it.
  A converted file that still fails the check counts as failed (it is kept,
  so you can look at it).

Useful options:

| Option | Effect |
|---|---|
| `--audio-lang LANG` | Prefer the track tagged with this language, e.g. `eng`, `jpn` |
| `--audio-track N` | Use track N, numbered as in the "Audio tracks" list the command prints |
| `--stereo` | Downmix to stereo AAC instead of keeping surround |
| `--max-fps N` | Frame-rate cap for re-encoded video (1–60, default 30) |
| `--crf N`, `--preset P` | x264 quality and speed (defaults 20, `medium`) |
| `--reencode` | Never copy streams, always re-encode both |
| `-y`, `--overwrite` | Replace existing `-ps4.mp4` files |
| `-n`, `--dry-run` | Show what would be done to each file without converting anything |

Both commands also take `--version`.

Files that end in `-ps4.mp4` are ignored when scanning folders, so
re-running on the same folder won't convert them again. Interrupted or failed
conversions leave no partial file behind.

## Development

```sh
git clone https://github.com/svenelvelt/ps4-video-playable
cd ps4-video-playable
uv sync                       # creates .venv with the project, ruff and pytest
uv run ps4-check movie.mkv    # run the commands from your checkout
```

```sh
uv run pytest                 # all tests
uv run pytest -m "not e2e"    # skip the tests that run ffmpeg
uv run ruff check             # lint (--fix applies safe fixes)
uv run ruff format            # format
```

CI runs the same lint, format and test steps on Linux, macOS and Windows
(Python 3.14), plus Linux with Python 3.10, for every push and pull request.

Lists of ffmpeg/ffprobe arguments are wrapped in `# fmt: off` / `# fmt: on`
so each flag stays on one line with its value, e.g. `"-crf", str(args.crf),`.

Project layout:

- `src/ps4_video_playable/common.py`: Sony's format table and the compatibility
  rules, used by both commands
- `src/ps4_video_playable/check.py`: `ps4-check`
- `src/ps4_video_playable/convert.py`: `ps4-convert`
- `tests/`: unit tests, plus end-to-end tests that convert short generated
  clips (skipped if ffmpeg isn't installed)

## License

Copyright © 2026 Sven Elvelt.

Licensed under the [GNU General Public License v3.0 or later](LICENSE).
You may use, change and share this program, including commercially. If
you distribute it or a modified version, you must do so under the same
license and make the source code available.

## Disclaimer

This project is not affiliated with or endorsed by Sony Interactive
Entertainment. "PlayStation" and "PS4" are trademarks of Sony Interactive
Entertainment Inc. The checks are based on Sony's published format list and
can't guarantee that a particular file plays.
