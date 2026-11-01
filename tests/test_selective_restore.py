"""Phase 4: selective restore is safe, non-overwriting, and backward compatible."""

from pathlib import Path

import pytest

from safesort.cli import main
from safesort.duplicate_history import DuplicateHistory


def photos(root: Path, count: int = 3, *, label: str = "same") -> list[Path]:
    results = []
    for i in range(count):
        path = root / f"{label}_{i}" / f"photo_{i}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"photo bytes {label}".encode())
        results.append(path)
    return results


def quarantined(root: Path) -> tuple[int, list]:
    with DuplicateHistory(root) as db:
        current = db.latest_restorable()
        assert current is not None
        return current


def setup(root: Path, count: int = 3) -> tuple[list[Path], tuple[int, list]]:
    created = photos(root, count)
    assert main(["duplicates", "quarantine", str(root), "--yes", "--no-progress"]) == 0
    return created, quarantined(root)


def test_list_and_preview_are_read_only(tmp_path, capsys):
    created, (batch_id, items) = setup(tmp_path)
    assert main(["duplicates", "list", str(tmp_path), "--batch", str(batch_id)]) == 0
    result = capsys.readouterr().out
    assert "1." in result and "2." in result
    assert main(["duplicates", "restore", str(tmp_path), "--batch", str(batch_id),
                 "--files", "2", "--preview"]) == 0
    assert "1 of 2 photo(s) selected" in capsys.readouterr().out
    assert sum(p.is_file() for p in created) == 1
    assert all(item.quarantine_path.is_file() for item in items)
    with DuplicateHistory(tmp_path) as db:
        assert len(db.latest_restorable()[1]) == 2


def test_selective_restore_one_then_rest(tmp_path, monkeypatch, capsys):
    created, (batch_id, items) = setup(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert main(["duplicates", "restore", str(tmp_path), "--batch", str(batch_id),
                 "--files", "1"]) == 0
    assert items[0].source.read_bytes() == b"photo bytes same"
    assert not items[0].quarantine_path.exists()
    assert items[1].quarantine_path.is_file()
    with DuplicateHistory(tmp_path) as db:
        remains = db.restorable_batch(batch_id)
    assert remains[1] == [items[1]]
    assert main(["duplicates", "list", str(tmp_path)]) == 0
    listing = capsys.readouterr().out
    assert "1 photo(s) awaiting review" in listing
    # Current numbers shift after partial restore; select from fresh listing.
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1", "--yes"]) == 0
    assert all(p.exists() for p in created)
    assert main(["duplicates", "restore", str(tmp_path)]) == 0


def test_multi_select_ranges_only_restore_selected(tmp_path):
    _, (_, items) = setup(tmp_path, count=5)
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1,3-4", "--yes"]) == 0
    for i, item in enumerate(items, start=1):
        assert item.source.exists() == (i in {1, 3, 4})
        assert item.quarantine_path.exists() == (i == 2)
    assert len(quarantined(tmp_path)[1]) == 1


def test_explicit_older_batch_instead_of_latest(tmp_path):
    old = photos(tmp_path, count=2, label="old")
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    old_batch, old_items = quarantined(tmp_path)
    newer = photos(tmp_path, count=2, label="new")
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    latest_batch, latest_items = quarantined(tmp_path)
    assert latest_batch != old_batch
    assert main(["duplicates", "restore", str(tmp_path), "--batch", str(old_batch),
                 "--files", "1", "--yes"]) == 0
    assert old_items[0].source.exists()
    assert latest_items[0].quarantine_path.exists()
    assert sum(p.exists() for p in newer) == 1
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert sum(p.exists() for p in old) == 2
    assert sum(p.exists() for p in newer) == 2


def test_invalid_file_selection_does_not_restore(tmp_path, capsys):
    _, (_, items) = setup(tmp_path)
    for selection in ("0", "3", "1,", "2-1", "all", "1,999", ""):
        assert main(["duplicates", "restore", str(tmp_path), "--files", selection]) == 2
    assert all(i.quarantine_path.exists() for i in items)
    assert "Invalid restore selection" in capsys.readouterr().err


def test_unknown_batch_does_not_pick_another(tmp_path, capsys):
    _, (_, items) = setup(tmp_path)
    assert main(["duplicates", "restore", str(tmp_path), "--batch", "999", "--yes"]) == 1
    assert main(["duplicates", "list", str(tmp_path), "--batch", "999"]) == 1
    assert "Unknown quarantine batch" in capsys.readouterr().err
    for command in ("list", "restore"):
        assert main(["duplicates", command, str(tmp_path), "--batch", "0"]) == 2
    assert all(i.quarantine_path.exists() for i in items)


def test_cancellation_no_change(tmp_path, monkeypatch):
    _, (_, items) = setup(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1"]) == 1
    assert all(i.quarantine_path.exists() for i in items)


def test_restore_rejects_occupied_destination(tmp_path, capsys):
    _, (_, items) = setup(tmp_path)
    chosen = items[0]
    chosen.source.write_bytes(b"do not overwrite this document")
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1", "--preview"]) == 0
    assert "original destination already exists" in capsys.readouterr().out
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1", "--yes"]) == 1
    assert chosen.source.read_bytes() == b"do not overwrite this document"
    assert chosen.quarantine_path.exists()
    assert items[1].quarantine_path.exists()


def test_changed_quarantine_file_is_preserved(tmp_path):
    _, (_, items) = setup(tmp_path)
    item = items[0]
    item.quarantine_path.write_bytes(b"modified in quarantine")
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1", "--yes"]) == 1
    assert item.quarantine_path.read_bytes() == b"modified in quarantine"
    assert not item.source.exists()
    assert items[1].quarantine_path.exists()


def test_file_changed_during_confirmation_is_skipped(tmp_path, monkeypatch):
    _, (_, items) = setup(tmp_path)
    item = items[0]
    def mutate(_):
        item.quarantine_path.write_bytes(b"modified after prompt")
        return "y"
    monkeypatch.setattr("builtins.input", mutate)
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1"]) == 1
    assert item.quarantine_path.exists()
    assert not item.source.exists()


def test_symlinked_destination_parent_is_blocked(tmp_path):
    _, (_, items) = setup(tmp_path)
    item = items[0]
    parent = item.source.parent
    moved_parent = tmp_path.parent / f"{tmp_path.name}-real-parent"
    parent.rename(moved_parent)
    try:
        parent.symlink_to(moved_parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        moved_parent.rename(parent)
        pytest.skip("cannot create directory symlink")
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1", "--yes"]) == 1
    assert item.quarantine_path.exists()
    assert not item.source.exists()


def test_explicit_restored_batch_has_no_pending_files(tmp_path):
    _, (batch_id, _) = setup(tmp_path)
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert main(["duplicates", "list", str(tmp_path), "--batch", str(batch_id)]) == 0
    assert main(["duplicates", "restore", str(tmp_path), "--batch", str(batch_id),
                 "--preview"]) == 0


def test_older_purged_entry_not_restored(tmp_path, monkeypatch):
    _, (_, items) = setup(tmp_path)
    selections = iter(["1", "DELETE 1"])
    monkeypatch.setattr("builtins.input", lambda _: next(selections))
    assert main(["duplicates", "purge", str(tmp_path)]) == 0
    assert main(["duplicates", "restore", str(tmp_path), "--files", "1", "--yes"]) == 0
    # One photo was purged and is not recovered; the other was restored.
    assert not items[0].quarantine_path.exists()
    assert not items[1].quarantine_path.exists()
    assert items[1].source.exists()
    assert not items[0].source.exists()


def test_interrupted_purge_blocks_selection(tmp_path):
    _, (batch_id, items) = setup(tmp_path)
    with DuplicateHistory(tmp_path) as db:
        db.begin_purge(items[0].row_id)
    assert main(["duplicates", "restore", str(tmp_path), "--batch", str(batch_id),
                 "--files", "2", "--preview"]) == 1
    assert items[1].quarantine_path.exists()
