"""Integrated history / undo tests against isolated temporary folders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from safesort.cli import main
from safesort.history import HistoryDB, matches_identity
from safesort.scanner import scan


def setup_folder(tmp_path: Path) -> tuple[Path, Path]:
    folder = tmp_path / "downloads"
    folder.mkdir()
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": [
        {"name": "Images", "extensions": [".jpg"], "destination": "Images"},
    ]}))
    return folder, rules


def apply(folder: Path, rules: Path) -> int:
    return main(["apply", str(folder), "--rules", str(rules), "--yes"])


def test_apply_history_undo_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("first")
    (folder / "b.jpg").write_text("second")
    assert apply(folder, rules) == 0
    assert main(["history", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "2 moved, 0 undone, 2 remaining" in out
    assert main(["undo", str(folder), "--yes"]) == 0
    assert (folder / "a.jpg").read_text() == "first"
    assert (folder / "b.jpg").read_text() == "second"
    assert not (folder / "Images" / "a.jpg").exists()
    assert main(["undo", str(folder), "--yes"]) == 0
    assert "Nothing to undo" in capsys.readouterr().out
    assert main(["history", str(folder)]) == 0
    assert "2 moved, 2 undone, 0 remaining" in capsys.readouterr().out


def test_journal_is_excluded_even_with_hidden_recursive(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "hello.jpg").write_text("hello")
    assert apply(folder, rules) == 0
    result = scan(folder, recursive=True, include_hidden=True)
    assert all(".safesort" not in f.path.parts for f in result.files)
    assert all(".safesort" not in str(f.path) for f in result.files)


def test_undo_does_not_overwrite_reused_original_path(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("original")
    assert apply(folder, rules) == 0
    (folder / "a.jpg").write_text("new file, must remain")
    assert main(["undo", str(folder), "--yes"]) == 1
    assert (folder / "a.jpg").read_text() == "new file, must remain"
    assert (folder / "Images" / "a.jpg").read_text() == "original"
    (folder / "a.jpg").unlink()
    assert main(["undo", str(folder), "--yes"]) == 0
    assert (folder / "a.jpg").read_text() == "original"


def test_undo_refuses_changed_destination(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("untouched")
    assert apply(folder, rules) == 0
    (folder / "Images" / "a.jpg").write_text("altered contents that must remain")
    assert main(["undo", str(folder), "--yes"]) == 1
    assert not (folder / "a.jpg").exists()
    assert (folder / "Images" / "a.jpg").read_text() == "altered contents that must remain"


def test_undo_refuses_replacement_with_same_name(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("old")
    assert apply(folder, rules) == 0
    destination = folder / "Images" / "a.jpg"
    destination.unlink()
    destination.write_text("new")
    assert main(["undo", str(folder), "--yes"]) == 1
    assert destination.read_text() == "new"
    assert not (folder / "a.jpg").exists()


def test_latest_batch_undone_first(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("a")
    assert apply(folder, rules) == 0
    (folder / "b.jpg").write_text("b")
    assert apply(folder, rules) == 0
    assert main(["undo", str(folder), "--yes"]) == 0
    assert (folder / "b.jpg").exists()
    assert not (folder / "a.jpg").exists()
    assert main(["undo", str(folder), "--yes"]) == 0
    assert (folder / "a.jpg").exists()


def test_undo_conflict_renamed_file_correctly(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("to move")
    (folder / "Images").mkdir()
    (folder / "Images" / "a.jpg").write_text("existing")
    assert apply(folder, rules) == 0
    assert (folder / "Images" / "a (1).jpg").read_text() == "to move"
    assert main(["undo", str(folder), "--yes"]) == 0
    assert (folder / "a.jpg").read_text() == "to move"
    assert (folder / "Images" / "a.jpg").read_text() == "existing"


def test_undo_decline_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("x")
    assert apply(folder, rules) == 0
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert main(["undo", str(folder)]) == 1
    assert (folder / "Images" / "a.jpg").exists()


def test_interrupted_batch_requires_manual_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("x")
    with HistoryDB(folder, create=True) as db:
        from safesort.models import PlannedMove
        db.start_batch([PlannedMove(1, folder / "a.jpg", folder / "Images" / "a.jpg", "Images")])
    assert main(["undo", str(folder), "--yes"]) == 1
    assert "interrupted/pending" in capsys.readouterr().err
    assert (folder / "a.jpg").exists()


def test_apply_records_failure_and_no_undo(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "Images").write_text("blocked")
    (folder / "a.jpg").write_text("original")
    assert apply(folder, rules) == 1
    with HistoryDB(folder) as db:
        batches = db.batches()
        assert len(batches) == 1
        assert batches[0].failed == 1
        assert batches[0].moved == 0
        assert batches[0].pending == 0
    assert main(["undo", str(folder), "--yes"]) == 0
    assert (folder / "a.jpg").read_text() == "original"


def test_missing_history_does_not_create_any_files(tmp_path: Path) -> None:
    folder, _ = setup_folder(tmp_path)
    assert main(["history", str(folder)]) == 1
    assert not (folder / ".safesort").exists()


def test_history_folder_must_not_be_symlink(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (folder / ".safesort").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    assert apply(folder, rules) == 1
    assert (folder / "a.jpg").exists()
    assert list(outside.iterdir()) == []


def test_history_survives_folder_with_hash_in_name(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    renamed = folder.with_name("my #downloads")
    folder.rename(renamed)
    (renamed / "a.jpg").write_text("important")
    assert apply(renamed, rules) == 0
    assert main(["undo", str(renamed), "--yes"]) == 0
    assert (renamed / "a.jpg").read_text() == "important"


def test_history_survives_renaming_folder(tmp_path: Path) -> None:
    folder, rules = setup_folder(tmp_path)
    (folder / "a.jpg").write_text("contents")
    assert apply(folder, rules) == 0
    new_folder = tmp_path / "renamed-downloads"
    folder.rename(new_folder)
    assert main(["undo", str(new_folder), "--yes"]) == 0
    assert (new_folder / "a.jpg").read_text() == "contents"
