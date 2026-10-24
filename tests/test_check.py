"""Tests for how ps4-check expands its arguments and summarises results."""

from ps4_video_playable.check import expand_paths, print_summary


class TestExpandPaths:
    def test_folder_is_replaced_by_its_videos(self, tmp_path):
        for name in ("b.mkv", "a.avi", "cover.jpg"):
            (tmp_path / name).touch()
        names = [p.name for p in expand_paths([tmp_path])]
        assert names == ["a.avi", "b.mkv"]

    def test_converted_files_are_included(self, tmp_path):
        (tmp_path / "movie-ps4.mp4").touch()
        assert [p.name for p in expand_paths([tmp_path])] == ["movie-ps4.mp4"]

    def test_recursive(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.mp4").touch()
        assert expand_paths([tmp_path]) == []
        assert expand_paths([tmp_path], recursive=True) == [
            tmp_path / "sub" / "c.mp4"
        ]

    def test_files_are_kept_even_if_missing_or_not_video(self, tmp_path):
        missing = tmp_path / "missing.mkv"
        text = tmp_path / "notes.txt"
        text.touch()
        assert expand_paths([missing, text]) == [missing, text]

    def test_duplicates_are_removed(self, tmp_path):
        video = tmp_path / "a.mkv"
        video.touch()
        assert expand_paths([video, tmp_path, video]) == [video]


def result(file, status, errors=(), warnings=()):
    return {
        "file": file,
        "status": status,
        "errors": list(errors),
        "warnings": list(warnings),
    }


class TestPrintSummary:
    def test_counts_and_lists_problem_files(self, capsys):
        print_summary(
            [
                result("ok.mp4", "compatible"),
                result(
                    "maybe.mkv",
                    "needs_verification",
                    warnings=["No audio stream found", "second warning"],
                ),
                result(
                    "bad.mkv",
                    "not_compatible",
                    errors=["Unsupported audio codec: flac", "second error"],
                ),
                result("gone.mkv", "error", errors=["File does not exist"]),
            ]
        )
        out = capsys.readouterr().out

        assert "SUMMARY (4 files)" in out
        assert "    1  ✅ Likely compatible" in out
        assert "    1  ⚠️ Needs verification" in out
        assert "    1  ❌ Not compatible" in out
        assert "    1  Could not be checked" in out
        assert "bad.mkv: Unsupported audio codec: flac" in out
        assert "gone.mkv: File does not exist" in out
        assert "maybe.mkv: No audio stream found" in out
        assert "ok.mp4" not in out
        assert "second" not in out

    def test_no_file_lists_when_all_compatible(self, capsys):
        print_summary([result("ok.mp4", "compatible")])
        out = capsys.readouterr().out
        assert "SUMMARY (1 file)" in out
        assert "Won't play" not in out
        assert "Need verification" not in out

    def test_divider_only_after_reports(self, capsys):
        print_summary([result("ok.mp4", "compatible")])
        assert capsys.readouterr().out.startswith("\n====")

        print_summary([result("ok.mp4", "compatible")], after_reports=False)
        assert capsys.readouterr().out.startswith("SUMMARY")
