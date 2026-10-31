"""Duplicate-photo CLI and quarantine safety integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from safesort.cli import main
from safesort.duplicates import find_duplicates
from safesort.duplicate_history import DuplicateHistory, quarantine_identity_matches


def make_three(root: Path) -> tuple[Path, Path, Path]:
    a = root / "2024" / "Vacation" / "beach.jpg"
    b = root / "2025" / "Summer" / "IMG_001.JPG"
    c = root / "Backups" / "copy.jpeg"
    for photo in (a, b, c):
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"original photo bytes")
    return a, b, c


def test_recursively_finds_exact_duplicates_with_different_names(tmp_path: Path) -> None:
    a, b, c = make_three(tmp_path)
    (tmp_path / "Downloads" / "different.jpg").parent.mkdir()
    (tmp_path / "Downloads" / "different.jpg").write_bytes(b"not duplicate!!!!!!")
    (tmp_path / "note.txt").write_bytes(b"original photo bytes")
    result = find_duplicates([tmp_path])
    assert result.photo_count == 4
    assert len(result.groups) == 1
    assert {p.path for p in result.groups[0].photos} == {a, b, c}
    assert result.extra_copies == 2
    assert result.recoverable_bytes == 2 * len(b"original photo bytes")


def test_multiple_unrelated_roots_are_compared(tmp_path: Path) -> None:
    a = tmp_path / "first" / "a.jpg"
    b = tmp_path / "second" / "b.png"
    for photo in (a, b):
        photo.parent.mkdir()
        photo.write_bytes(b"same exact bytes")
    result = find_duplicates([a.parent, b.parent])
    assert len(result.groups) == 1
    assert result.extra_copies == 1
    assert {p.path for p in result.groups[0].photos} == {a, b}
    assert main(["duplicates", "scan", str(a.parent), str(b.parent)]) == 0


def test_rejects_overlapping_roots(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    assert main(["duplicates", "scan", str(tmp_path), str(sub)]) == 1


def test_scan_does_not_modify_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b, c = make_three(tmp_path)
    assert main(["duplicates", "scan", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 extra copy/copies" in out
    assert "Preview only" in out
    assert all(p.is_file() for p in (a, b, c))
    assert not (tmp_path / ".safesort").exists()
    assert not (tmp_path / ".safesort-quarantine").exists()


def test_quarantine_and_restore_roundtrip(tmp_path: Path) -> None:
    a, b, c = make_three(tmp_path)
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    originals = [p for p in (a, b, c) if p.is_file()]
    assert len(originals) == 1
    quarantined = list((tmp_path / ".safesort-quarantine").rglob("*.jpg"))
    quarantined += list((tmp_path / ".safesort-quarantine").rglob("*.JPG"))
    quarantined += list((tmp_path / ".safesort-quarantine").rglob("*.jpeg"))
    assert len(quarantined) == 2
    assert find_duplicates([tmp_path]).extra_copies == 0  # exclude quarantine
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert all(p.read_bytes() == b"original photo bytes" for p in (a, b, c))
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0


def test_user_can_choose_keeper_not_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b, c = make_three(tmp_path)
    ordered = [p.path for p in find_duplicates([tmp_path]).groups[0].photos]
    assert len(ordered) == 3
    choices = iter(["2", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(choices))
    assert main(["duplicates", "quarantine", str(tmp_path)]) == 0
    assert ordered[1].exists()
    assert not ordered[0].exists()
    assert not ordered[2].exists()


def test_skipping_a_group_does_not_move_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b, c = make_three(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert main(["duplicates", "quarantine", str(tmp_path)]) == 0
    assert all(p.exists() for p in (a, b, c))
    assert not (tmp_path / ".safesort-quarantine").exists()


def test_rejects_changed_file_since_scanning(tmp_path: Path) -> None:
    a, b, c = make_three(tmp_path)
    # Artificially model a file that gets edited after hashing and before move.
    with patch("safesort.cli.unchanged", return_value=False):
        assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 1
    assert all(p.exists() for p in (a, b, c))


def test_restore_refuses_to_overwrite_original_path(tmp_path: Path) -> None:
    a, b, c = make_three(tmp_path)
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    missing = [p for p in (a, b, c) if not p.exists()]
    assert missing
    missing[0].write_bytes(b"new photo content")
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 1
    assert missing[0].read_bytes() == b"new photo content"
    # After moving the conflicting photo aside, retry succeeds.
    missing[0].unlink()
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert all(p.exists() for p in (a, b, c))


def test_restore_refuses_modified_quarantine_photo(tmp_path: Path) -> None:
    make_three(tmp_path)
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    with DuplicateHistory(tmp_path) as db:
        latest = db.latest_restorable()
        assert latest is not None
        _, items = latest
        assert all(quarantine_identity_matches(item) for item in items)
        item = items[0]
    item.quarantine_path.write_bytes(b"changed content")
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 1
    assert item.quarantine_path.read_bytes() == b"changed content"


def test_quarantine_scan_ignores_hidden_journal_and_quarantine(tmp_path: Path) -> None:
    make_three(tmp_path)
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    assert find_duplicates([tmp_path], include_hidden=True).extra_copies == 0


def test_symlinks_skipped(tmp_path: Path) -> None:
    a = tmp_path / "a.jpg"
    a.write_bytes(b"image bytes")
    link = tmp_path / "b.jpg"
    try:
        link.symlink_to(a)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on platform")
    result = find_duplicates([tmp_path])
    assert result.extra_copies == 0
    assert any(p == link for p, _ in result.skipped)
    assert main(["duplicates", "scan", str(link)]) == 1


def test_quarantine_operations_do_not_change_normal_history(tmp_path: Path) -> None:
    make_three(tmp_path)
    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    assert main(["undo", str(tmp_path), "--yes"]) == 1  # no normal apply history yet


def test_large_windows_ids_in_quarantine_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLite history should never coerce large Windows file IDs into 64-bit ints."""
    from types import SimpleNamespace
    from safesort.models import MoveResult, MoveStatus, PlannedMove

    photo = tmp_path / ".safesort-quarantine" / "batch-1" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"test")
    move = PlannedMove(1, tmp_path / "a.jpg", photo, "duplicate-quarantine")
    real_lstat = Path.lstat
    large = 2**100 + 31

    with DuplicateHistory(tmp_path, create=True) as db:
        _, row_ids = db.begin([(move, "abc", 4)])
        def fake_lstat(path: Path):
            st = real_lstat(path)
            if path == photo:
                return SimpleNamespace(st_mode=st.st_mode, st_dev=large,
                                       st_ino=large + 3, st_mtime_ns=st.st_mtime_ns)
            return st
        monkeypatch.setattr(Path, "lstat", fake_lstat)
        db.record_move(row_ids[0], MoveResult(move, MoveStatus.MOVED))
        batch = db.latest_restorable()
        assert batch is not None
        assert batch[1][0].device == large
        assert batch[1][0].inode == large + 3


def test_scan_excludes_apple_photos_packages_even_with_hidden(tmp_path: Path) -> None:
    outside = tmp_path / "RegularPhotos" / "keep.jpg"
    outside.parent.mkdir()
    outside.write_bytes(b"matching bytes")
    package = tmp_path / "Year2025" / "Photos Library.photoslibrary"
    inside_a = package / "originals" / "0" / "image.jpg"
    inside_b = package / "originals" / "1" / "image_copy.jpg"
    for image in (inside_a, inside_b):
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"matching bytes")

    for hidden in (False, True):
        result = find_duplicates([tmp_path], include_hidden=hidden)
        assert result.photo_count == 1
        assert not result.groups
        assert any(path == package and "protected Apple Photos library" in reason
                   for path, reason in result.skipped)
        assert outside.read_bytes() == inside_a.read_bytes()


def test_rejects_apple_photos_package_as_root_and_nested_root(tmp_path: Path) -> None:
    package = tmp_path / "Family.PhOtOsLiBrArY"
    nested = package / "originals" / "0"
    nested.mkdir(parents=True)
    image = nested / "family.heic"
    image.write_bytes(b"keep me")
    for root in (package, nested):
        assert main(["duplicates", "scan", str(root)]) == 1
        assert main(["duplicates", "quarantine", str(root), "--yes"]) == 1
        assert image.read_bytes() == b"keep me"
    assert not (package / ".safesort").exists()
    assert not (package / ".safesort-quarantine").exists()


def test_quarantine_only_moves_regular_photos_not_photos_library(tmp_path: Path) -> None:
    outside_a = tmp_path / "regularA" / "photo.jpg"
    outside_b = tmp_path / "regularB" / "photo.jpg"
    inside = tmp_path / "Photos Library.photoslibrary" / "originals" / "same.jpg"
    for path in (outside_a, outside_b, inside):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"same photo")

    assert main(["duplicates", "quarantine", str(tmp_path), "--yes"]) == 0
    assert inside.read_bytes() == b"same photo"
    assert sum(p.exists() for p in (outside_a, outside_b)) == 1
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert outside_a.exists() and outside_b.exists() and inside.exists()
