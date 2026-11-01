"""Phase 2: bulk planning is conservative and never moves photo files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from safesort.bulk_plan import (
    PLAN_FORMAT, create_bulk_plan, decide_group, plan_to_dict, preferred_folders,
)
from safesort.cli import main
from safesort.duplicates import find_duplicates
from safesort.errors import ScanError


def _image(root: Path, filename: str, contents: bytes = b"same bytes") -> Path:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def test_unique_preferred_path_proposes_extra_copy_without_moving(tmp_path: Path) -> None:
    favorite = _image(tmp_path, "Family Photos/keep.jpg")
    backup = _image(tmp_path, "Backup/copy.jpg")
    report = find_duplicates([tmp_path])
    plan = create_bulk_plan(report, preferred_folders(tmp_path, [Path("Family Photos")]))
    assert plan.ready_groups == 1 and plan.needs_review == 0
    assert plan.proposed_copies == 1 and plan.potential_bytes == len(b"same bytes")
    assert plan.groups[0].keeper is not None
    assert plan.groups[0].keeper.path == favorite
    assert [p.path for p in plan.groups[0].proposed_moves] == [backup]
    assert favorite.is_file() and backup.is_file()
    assert not (tmp_path / ".safesort-quarantine").exists()


def test_tied_preference_skips_even_if_lower_priority_has_a_unique_copy(tmp_path: Path) -> None:
    for name in ("Favorite/a.jpg", "Favorite/b.jpg", "Backup/c.jpg"):
        _image(tmp_path, name)
    report = find_duplicates([tmp_path])
    folders = preferred_folders(tmp_path, [Path("Favorite"), Path("Backup")])
    plan = create_bulk_plan(report, folders)
    assert plan.ready_groups == 0 and plan.needs_review == 1
    assert plan.proposed_copies == 0
    assert "multiple copies" in plan.groups[0].reason


def test_no_preference_skips_everything(tmp_path: Path) -> None:
    _image(tmp_path, "a.jpg")
    _image(tmp_path, "b.jpg")
    plan = create_bulk_plan(find_duplicates([tmp_path]), ())
    assert plan.ready_groups == 0 and plan.proposed_copies == 0
    assert "no copy" in plan.groups[0].reason


def test_priority_order_determines_keeper(tmp_path: Path) -> None:
    a = _image(tmp_path, "Important/a.jpg")
    b = _image(tmp_path, "Other/b.jpg")
    report = find_duplicates([tmp_path])
    forward = preferred_folders(tmp_path, [Path("Important"), Path("Other")])
    backward = preferred_folders(tmp_path, [Path("Other"), Path("Important")])
    assert decide_group(report.groups[0], forward).keeper.path == a
    assert decide_group(report.groups[0], backward).keeper.path == b


def test_folder_prefix_not_treated_as_subfolder(tmp_path: Path) -> None:
    _image(tmp_path, "Photos-old/a.jpg")
    _image(tmp_path, "Other/b.jpg")
    (tmp_path / "Photos").mkdir()
    plan = create_bulk_plan(find_duplicates([tmp_path]), (tmp_path / "Photos",))
    assert plan.needs_review == 1


def test_nested_preference_priority(tmp_path: Path) -> None:
    a = _image(tmp_path, "All/important/a.jpg")
    _image(tmp_path, "All/other/b.jpg")
    report = find_duplicates([tmp_path])
    folders = preferred_folders(tmp_path, [Path("All/important"), Path("All")])
    assert decide_group(report.groups[0], folders).keeper.path == a
    reverse = preferred_folders(tmp_path, [Path("All"), Path("All/important")])
    assert decide_group(report.groups[0], reverse).keeper is None


@pytest.mark.parametrize("folder", ["../outside", "no-such-folder", "Photos Library.photoslibrary"])
def test_bad_preferences_rejected(tmp_path: Path, folder: str) -> None:
    (tmp_path / "Photos Library.photoslibrary").mkdir()
    with pytest.raises(ScanError):
        preferred_folders(tmp_path, [Path(folder)])


def test_symlink_preference_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "Family"
    folder.mkdir()
    link = tmp_path / "Link"
    try:
        link.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ScanError):
        preferred_folders(tmp_path, [link])


def test_bulk_preview_writes_reviewable_plan_without_moving(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    favorite = _image(tmp_path, "Favorite/a.jpg")
    extra = _image(tmp_path, "Backup/b.jpg")
    plan_path = tmp_path.parent / f"{tmp_path.name}-plan.json"
    assert main(["duplicates", "quarantine", str(tmp_path), "--bulk", "--preview",
                 "--prefer-path", "Favorite", "--plan-file", str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["format"] == PLAN_FORMAT
    assert plan["summary"]["ready_groups"] == 1
    assert plan["summary"]["proposed_copies"] == 1
    assert plan["groups"][0]["keeper"] == "Favorite/a.jpg"
    assert plan["groups"][0]["quarantine"] == ["Backup/b.jpg"]
    assert plan["groups"][0]["photos"][0]["sha256"]
    assert favorite.exists() and extra.exists()
    assert not (tmp_path / ".safesort-quarantine").exists()
    assert not (tmp_path / ".safesort").exists()
    assert "Preview only" in capsys.readouterr().out


def test_json_includes_large_windows_ids_as_strings(tmp_path: Path) -> None:
    _image(tmp_path, "Favorite/a.jpg")
    _image(tmp_path, "Backup/b.jpg")
    doc = plan_to_dict(create_bulk_plan(find_duplicates([tmp_path]),
                                        preferred_folders(tmp_path, [Path("Favorite")])))
    assert all(isinstance(photo["inode"], str) for photo in doc["groups"][0]["photos"])


def test_plan_write_refuses_overwrite(tmp_path: Path) -> None:
    _image(tmp_path, "Fav/a.jpg")
    _image(tmp_path, "Backup/b.jpg")
    target = tmp_path.parent / f"{tmp_path.name}-plan.json"
    target.write_text("important old file", encoding="utf-8")
    assert main(["duplicates", "quarantine", str(tmp_path), "--bulk", "--preview",
                 "--prefer-path", "Fav", "--plan-file", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "important old file"
    assert (tmp_path / "Fav/a.jpg").exists() and (tmp_path / "Backup/b.jpg").exists()


@pytest.mark.parametrize("flags", [
    ["--bulk"],
    ["--bulk", "--preview"],
    ["--bulk", "--preview", "--yes", "--plan-file", "a.json"],
    ["--prefer-path", "Favorite"],
    ["--preview"],
])
def test_bad_bulk_flags_never_move_photos(tmp_path: Path, flags: list[str]) -> None:
    a = _image(tmp_path, "Favorite/a.jpg")
    b = _image(tmp_path, "Backup/b.jpg")
    assert main(["duplicates", "quarantine", str(tmp_path), *flags]) != 0
    assert a.exists() and b.exists()
    assert not (tmp_path / ".safesort-quarantine").exists()
    assert not (tmp_path / ".safesort").exists()


def test_no_duplicate_groups_creates_read_only_plan(tmp_path: Path) -> None:
    only = _image(tmp_path, "Favorite/only.jpg")
    plan_path = tmp_path.parent / f"{tmp_path.name}-empty.json"
    assert main(["duplicates", "quarantine", str(tmp_path), "--bulk", "--preview",
                 "--prefer-path", "Favorite", "--plan-file", str(plan_path)]) == 0
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    assert doc["groups"] == []
    assert only.exists()
