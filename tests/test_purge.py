"""Irreversible duplicate purge must require review, retained copy and journal."""

from __future__ import annotations

from pathlib import Path

import pytest

from safesort.cli import main
from safesort.duplicate_history import DuplicateHistory


def setup_quarantine(root: Path) -> tuple[Path, list[Path]]:
    photo_paths = [root / "2024" / "keeper.jpg", root / "2025" / "copy.JPG", root / "Backup" / "copy.jpeg"]
    for path in photo_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"same photo contents")
    assert main(["duplicates", "quarantine", str(root), "--yes"]) == 0
    keeper = [p for p in photo_paths if p.exists()]
    assert len(keeper) == 1
    with DuplicateHistory(root) as history:
        latest = history.latest_restorable()
        assert latest is not None
        _, rows = latest
        quarantined = [item.quarantine_path for item in rows]
    assert len(quarantined) == 2
    return keeper[0], quarantined


def answer(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    values = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(values))


def test_preview_and_list_are_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, copies = setup_quarantine(tmp_path)
    assert main(["duplicates", "list", str(tmp_path)]) == 0
    assert main(["duplicates", "purge", str(tmp_path), "--preview"]) == 0
    assert "Preview only: no files were deleted" in capsys.readouterr().out
    assert all(p.is_file() for p in copies)


def test_purge_requires_exact_typed_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, copies = setup_quarantine(tmp_path)
    answer(monkeypatch, "all", "DELETE")
    assert main(["duplicates", "purge", str(tmp_path)]) == 0
    assert all(p.is_file() for p in copies)


def test_purge_does_not_execute_without_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, copies = setup_quarantine(tmp_path)
    answer(monkeypatch, "")
    assert main(["duplicates", "purge", str(tmp_path)]) == 0
    assert all(p.is_file() for p in copies)


def test_purge_selected_only_and_restore_remaining(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, copies = setup_quarantine(tmp_path)
    answer(monkeypatch, "1", "DELETE 1")
    assert main(["duplicates", "purge", str(tmp_path)]) == 0
    assert not copies[0].exists() and copies[1].exists()
    assert keeper.exists()
    with DuplicateHistory(tmp_path) as db:
        latest = db.latest_restorable()
        assert latest is not None
        assert len(latest[1]) == 1
        purged = db.connection.execute(
            "SELECT COUNT(*) FROM duplicate_moves WHERE restore_status='purged'"
        ).fetchone()[0]
        assert purged == 1
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert not copies[0].exists()
    assert keeper.exists()
    assert main(["duplicates", "purge", str(tmp_path), "--preview"]) == 0


def test_purge_all_removes_extra_copies_not_keeper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, copies = setup_quarantine(tmp_path)
    answer(monkeypatch, "all", "DELETE 1")
    assert main(["duplicates", "purge", str(tmp_path)]) == 0
    assert keeper.read_bytes() == b"same photo contents"
    assert not any(p.exists() for p in copies)
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert keeper.exists()


def test_purge_refuses_when_retained_copy_disappeared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, copies = setup_quarantine(tmp_path)
    keeper.unlink()
    answer(monkeypatch, "all", "DELETE 1")
    assert main(["duplicates", "purge", str(tmp_path)]) == 1
    assert all(p.is_file() for p in copies)


def test_purge_refuses_when_retained_copy_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, copies = setup_quarantine(tmp_path)
    keeper.write_bytes(b"different photo content")
    answer(monkeypatch, "all", "DELETE 1")
    assert main(["duplicates", "purge", str(tmp_path)]) == 1
    assert all(p.is_file() for p in copies)


def test_purge_refuses_changed_quarantined_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, copies = setup_quarantine(tmp_path)
    copies[0].write_bytes(b"changed")
    answer(monkeypatch, "all", "DELETE 1")
    assert main(["duplicates", "purge", str(tmp_path)]) == 1
    assert copies[0].is_file()  # changed file is preserved
    assert not copies[1].exists()  # independently verified copy can be purged


def test_purge_refuses_invalid_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, copies = setup_quarantine(tmp_path)
    answer(monkeypatch, "3")
    assert main(["duplicates", "purge", str(tmp_path)]) == 2
    assert all(p.exists() for p in copies)


def test_purge_refuses_symlinked_quarantine_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, copies = setup_quarantine(tmp_path)
    # Replace a batch directory with a symlink to another folder holding a photo.
    # Deliberately target the top-level .safesort-quarantine directory.
    quarantine_root = tmp_path / ".safesort-quarantine"
    moved_root = tmp_path / "moved-quarantine"
    quarantine_root.rename(moved_root)
    try:
        quarantine_root.symlink_to(moved_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        moved_root.rename(quarantine_root)
        pytest.skip("symlink creation unavailable")
    answer(monkeypatch, "all", "DELETE 1")
    assert main(["duplicates", "purge", str(tmp_path)]) == 1
    assert len([p for p in moved_root.rglob("*") if p.is_file()]) == 2
    assert keeper_count(tmp_path) == 1


def keeper_count(root: Path) -> int:
    return sum(p.is_file() for p in root.glob("2024/*.jpg")) + sum(p.is_file() for p in root.glob("2025/*.JPG")) + sum(p.is_file() for p in root.glob("Backup/*.jpeg"))


def test_interrupted_purge_blocks_retries_and_restore(tmp_path: Path) -> None:
    _, copies = setup_quarantine(tmp_path)
    with DuplicateHistory(tmp_path) as db:
        latest = db.latest_restorable()
        assert latest is not None
        db.begin_purge(latest[1][0].row_id)
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 1
    assert main(["duplicates", "purge", str(tmp_path), "--preview"]) == 1
    assert all(p.is_file() for p in copies)
