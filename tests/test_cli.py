from pathlib import Path

import pytest

from safesort.cli import format_size, main


def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"


def test_scan_lists_files_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "report.pdf").write_text("x")
    code = main(["scan", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "report.pdf" in out
    assert "1 file(s) found" in out


def test_scan_missing_folder_shows_friendly_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scan", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("error: Folder not found")


def test_undo_without_history_shows_friendly_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["undo", str(tmp_path)])
    assert code == 1
    assert "No SafeSort history" in capsys.readouterr().err


# --- preview -----------------------------------------------------------------

RULES = {
    "rules": [
        {"name": "Images", "extensions": [".jpg"], "destination": "Images"},
    ]
}


def make_folder(tmp_path: Path) -> tuple[Path, Path]:
    import json

    folder = tmp_path / "downloads"
    folder.mkdir()
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(RULES))
    return folder, rules


def test_preview_prints_numbered_moves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = make_folder(tmp_path)
    (folder / "vacation.jpg").write_text("x")

    code = main(["preview", str(folder), "--rules", str(rules)])

    out = capsys.readouterr().out
    expected = f"1. vacation.jpg -> {Path('Images') / 'vacation.jpg'}"
    assert code == 0
    assert expected in out
    assert "1 move(s) planned" in out


def test_preview_flags_renamed_moves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = make_folder(tmp_path)
    (folder / "vacation.jpg").write_text("x")
    (folder / "Images").mkdir()
    (folder / "Images" / "vacation.jpg").write_text("existing")

    main(["preview", str(folder), "--rules", str(rules)])

    out = capsys.readouterr().out
    assert "vacation (1).jpg" in out
    assert "[renamed]" in out


def test_preview_lists_unmatched_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = make_folder(tmp_path)
    (folder / "notes.txt").write_text("x")

    code = main(["preview", str(folder), "--rules", str(rules)])

    out = capsys.readouterr().out
    assert code == 0
    assert "0 move(s) planned" in out
    assert "no matching rule" in out
    assert "notes.txt" in out


def test_preview_never_changes_the_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = make_folder(tmp_path)
    (folder / "vacation.jpg").write_text("x")

    main(["preview", str(folder), "--rules", str(rules)])

    assert sorted(p.name for p in folder.iterdir()) == ["vacation.jpg"]
    assert "no files were changed" in capsys.readouterr().out


def test_preview_with_bad_rules_file_shows_friendly_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = make_folder(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{ nope")

    code = main(["preview", str(folder), "--rules", str(bad)])

    assert code == 1
    assert capsys.readouterr().err.startswith("error: Invalid JSON")


def test_preview_with_missing_rules_file_shows_friendly_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = make_folder(tmp_path)
    code = main(["preview", str(folder), "--rules", str(tmp_path / "nope.json")])
    assert code == 1
    assert "Rules file not found" in capsys.readouterr().err


# --- apply -------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from safesort.cli import _confirm  # noqa: E402


def two_photos(tmp_path: Path) -> tuple[Path, Path]:
    folder, rules = make_folder(tmp_path)
    (folder / "a.jpg").write_text("A")
    (folder / "b.jpg").write_text("B")
    return folder, rules


def test_apply_with_yes_moves_files_without_asking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = two_photos(tmp_path)

    with patch("safesort.cli._confirm") as confirm:
        code = main(["apply", str(folder), "--rules", str(rules), "--yes"])

    out = capsys.readouterr().out
    assert code == 0
    confirm.assert_not_called()
    assert (folder / "Images" / "a.jpg").read_text() == "A"
    assert (folder / "Images" / "b.jpg").read_text() == "B"
    assert not (folder / "a.jpg").exists()
    assert "Done: 2 moved, 0 skipped, 0 failed" in out


def test_apply_moves_after_the_user_confirms(tmp_path: Path) -> None:
    folder, rules = two_photos(tmp_path)
    with patch("safesort.cli._confirm", return_value=True) as confirm:
        code = main(["apply", str(folder), "--rules", str(rules)])
    assert code == 0
    confirm.assert_called_once()
    assert (folder / "Images" / "a.jpg").exists()


def test_declining_the_prompt_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = two_photos(tmp_path)

    with patch("safesort.cli._confirm", return_value=False):
        code = main(["apply", str(folder), "--rules", str(rules)])

    assert code == 1
    assert "Cancelled" in capsys.readouterr().out
    assert sorted(p.name for p in folder.iterdir()) == ["a.jpg", "b.jpg"]


def test_apply_only_moves_the_chosen_numbers(tmp_path: Path) -> None:
    folder, rules = two_photos(tmp_path)
    main(["apply", str(folder), "--rules", str(rules), "--only", "1", "--yes"])
    assert (folder / "Images" / "a.jpg").exists()
    assert (folder / "b.jpg").exists()


def test_apply_exclude_skips_the_chosen_numbers(tmp_path: Path) -> None:
    folder, rules = two_photos(tmp_path)
    main(["apply", str(folder), "--rules", str(rules), "--exclude", "1", "--yes"])
    assert (folder / "a.jpg").exists()
    assert (folder / "Images" / "b.jpg").exists()


def test_only_and_exclude_cannot_be_combined(tmp_path: Path) -> None:
    folder, rules = two_photos(tmp_path)
    with pytest.raises(SystemExit):
        main(["apply", str(folder), "--rules", str(rules), "--only", "1", "--exclude", "2"])


def test_bad_selection_shows_friendly_error_and_moves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = two_photos(tmp_path)
    code = main(["apply", str(folder), "--rules", str(rules), "--only", "9", "--yes"])
    assert code == 1
    assert "out of range" in capsys.readouterr().err
    assert sorted(p.name for p in folder.iterdir()) == ["a.jpg", "b.jpg"]


def test_apply_with_nothing_to_move_does_not_prompt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = make_folder(tmp_path)
    (folder / "notes.txt").write_text("x")

    with patch("safesort.cli._confirm") as confirm:
        code = main(["apply", str(folder), "--rules", str(rules)])

    assert code == 0
    confirm.assert_not_called()
    assert "Nothing to move" in capsys.readouterr().out


def test_apply_reports_failures_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, rules = make_folder(tmp_path)
    (folder / "a.jpg").write_text("A")
    (folder / "Images").write_text("a file squatting on the folder name")

    code = main(["apply", str(folder), "--rules", str(rules), "--yes"])

    out = capsys.readouterr().out
    assert code == 1
    assert "FAILED" in out
    assert "1 failed" in out
    assert (folder / "a.jpg").read_text() == "A"


def test_confirm_accepts_only_yes_answers() -> None:
    for answer, expected in [("y", True), ("YES", True), (" y ", True),
                             ("", False), ("n", False), ("maybe", False)]:
        with patch("builtins.input", return_value=answer):
            assert _confirm("?") is expected


def test_confirm_treats_missing_input_as_no() -> None:
    with patch("builtins.input", side_effect=EOFError):
        assert _confirm("?") is False


def test_preview_without_rules_file_uses_bundled_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "photo.jpg").write_text("photo")
    monkeypatch.chdir(tmp_path)
    assert main(["preview", str(folder)]) == 0
    assert "photo.jpg -> Images/photo.jpg" in capsys.readouterr().out
