"""Phase 3: a reviewed plan must never authorize unsafe or implicit moves."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from safesort.cli import main
from safesort.duplicate_history import DuplicateHistory
from safesort.duplicates import QUARANTINE_FOLDER


def _photo(root: Path, filename: str, data: bytes = b"identical photo bytes") -> Path:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _make_plan(root: Path, plan_file: Path) -> dict:
    assert main(["duplicates", "quarantine", str(root), "--bulk", "--preview",
                 "--prefer-path", "Favorite", "--plan-file", str(plan_file)]) == 0
    return json.loads(plan_file.read_text(encoding="utf-8"))


def _execute(root: Path, plan_file: Path) -> int:
    return main(["duplicates", "quarantine", str(root), "--bulk", "--apply-plan", str(plan_file)])


def test_bulk_apply_moves_then_existing_restore_restores(tmp_path: Path, monkeypatch, capsys):
    favorite = _photo(tmp_path, "Favorite/a.jpg")
    extras = [_photo(tmp_path, "Backup/b.jpg"), _photo(tmp_path, "Backup/c.jpg")]
    plan = tmp_path.parent / f"{tmp_path.name}-valid.json"
    _make_plan(tmp_path, plan)
    questions = []

    def confirm(prompt):
        questions.append(prompt)
        return "QUARANTINE 2"

    monkeypatch.setattr("builtins.input", confirm)
    assert _execute(tmp_path, plan) == 0
    assert questions == ["Type QUARANTINE 2 to move these photos: "]
    assert favorite.read_bytes() == b"identical photo bytes"
    assert all(not p.exists() for p in extras)
    quarantined = list((tmp_path / QUARANTINE_FOLDER).rglob("*.jpg"))
    assert len(quarantined) == 2
    with DuplicateHistory(tmp_path) as history:
        batch, items = history.latest_restorable()
        assert len(items) == 2
        assert all(x.sha256 for x in items)
    assert "2 moved" in capsys.readouterr().out
    assert main(["duplicates", "restore", str(tmp_path), "--yes"]) == 0
    assert all(p.read_bytes() == b"identical photo bytes" for p in extras)


def test_cancellation_never_creates_history(tmp_path: Path, monkeypatch):
    _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-cancel.json"
    _make_plan(tmp_path, plan)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert _execute(tmp_path, plan) == 1
    assert extra.exists()
    assert not (tmp_path / QUARANTINE_FOLDER).exists()
    assert not (tmp_path / ".safesort").exists()


@pytest.mark.parametrize("which", ["Favorite/a.jpg", "Backup/b.jpg"])
def test_stale_keeper_or_extra_skips_entire_group(tmp_path: Path, monkeypatch, which):
    _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-stale-{which.split('/')[0]}.json"
    _make_plan(tmp_path, plan)
    (tmp_path / which).write_bytes(b"modified now")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("must not confirm stale-only plan"))
    assert _execute(tmp_path, plan) == 1
    assert extra.exists()
    assert not (tmp_path / QUARANTINE_FOLDER).exists()
    assert not (tmp_path / ".safesort").exists()


def test_stale_group_is_skipped_but_other_group_is_safe(tmp_path: Path, monkeypatch):
    _photo(tmp_path, "Favorite/a.jpg", b"first")
    changed = _photo(tmp_path, "Backup/b.jpg", b"first")
    _photo(tmp_path, "Favorite/c.jpg", b"second")
    extra = _photo(tmp_path, "Backup/d.jpg", b"second")
    plan = tmp_path.parent / f"{tmp_path.name}-partial.json"
    _make_plan(tmp_path, plan)
    changed.write_bytes(b"changed")
    monkeypatch.setattr("builtins.input", lambda _: "QUARANTINE 1")
    assert _execute(tmp_path, plan) == 1  # stale report, though one safe group moved
    assert changed.exists() and not extra.exists()
    assert (tmp_path / "Favorite/c.jpg").exists()


def test_file_changed_after_confirmation_is_skipped(tmp_path: Path, monkeypatch):
    keeper = _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-race.json"
    _make_plan(tmp_path, plan)

    def change_then_confirm(_prompt):
        keeper.write_bytes(b"replaced after preflight")
        return "QUARANTINE 1"

    monkeypatch.setattr("builtins.input", change_then_confirm)
    assert _execute(tmp_path, plan) == 1
    assert extra.exists()
    assert not (tmp_path / QUARANTINE_FOLDER).exists() or not list((tmp_path / QUARANTINE_FOLDER).rglob("*.jpg"))


def test_plan_root_mismatch_rejected(tmp_path: Path, monkeypatch):
    _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-root.json"
    doc = _make_plan(tmp_path, plan)
    doc["root"] = str(tmp_path.parent)
    plan.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("should fail before prompt"))
    assert _execute(tmp_path, plan) != 0
    assert extra.exists()


@pytest.mark.parametrize("change", [
    lambda d: d["groups"][0].update(keeper="Backup/b.jpg"),
    lambda d: d["groups"][0].update(quarantine=["Favorite/a.jpg"]),
    lambda d: d["groups"][0]["photos"][1].update(path="../bad.jpg"),
    lambda d: d["groups"][0]["photos"][1].update(path=".safesort-quarantine/evil.jpg"),
    lambda d: d["groups"][0]["photos"][1].update(size=100000),
    lambda d: d["groups"][0].update(decision="needs_review"),
    lambda d: d["summary"].update(proposed_copies=999),
    lambda d: d["groups"].append(d["groups"][0]),
])
def test_tampered_plan_rejected(tmp_path: Path, monkeypatch, change):
    _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-tamper.json"
    doc = _make_plan(tmp_path, plan)
    change(doc)
    plan.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("bad plan must never request confirmation"))
    assert _execute(tmp_path, plan) != 0
    assert extra.exists()
    assert not (tmp_path / ".safesort").exists()


def test_plan_with_only_ambiguous_groups_does_not_move(tmp_path: Path, monkeypatch):
    for name in ("Favorite/a.jpg", "Favorite/b.jpg", "Backup/c.jpg"):
        _photo(tmp_path, name)
    plan = tmp_path.parent / f"{tmp_path.name}-ambiguous.json"
    _make_plan(tmp_path, plan)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("ambiguous plan must not prompt"))
    assert _execute(tmp_path, plan) == 0
    assert all((tmp_path / n).exists() for n in ("Favorite/a.jpg", "Favorite/b.jpg", "Backup/c.jpg"))
    assert not (tmp_path / QUARANTINE_FOLDER).exists()


def test_symlink_photo_replacement_is_rejected(tmp_path: Path, monkeypatch):
    keeper = _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-symlink.json"
    _make_plan(tmp_path, plan)
    extra.unlink()
    try:
        extra.symlink_to(keeper)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("symlink plan must fail"))
    assert _execute(tmp_path, plan) != 0
    assert keeper.exists()


@pytest.mark.parametrize("flags", [
    ["--yes"], ["--preview"], ["--plan-file", "a.json"],
    ["--prefer-path", "Favorite"], ["--hidden"],
])
def test_apply_does_not_allow_conflicting_flags(tmp_path: Path, flags):
    _photo(tmp_path, "Favorite/a.jpg")
    other = _photo(tmp_path, "Backup/b.jpg")
    assert main(["duplicates", "quarantine", str(tmp_path), "--bulk",
                 "--apply-plan", "missing.json", *flags]) == 2
    assert other.exists()
    assert not (tmp_path / ".safesort").exists()


def test_apply_requires_bulk(tmp_path: Path):
    assert main(["duplicates", "quarantine", str(tmp_path), "--apply-plan", "missing.json"]) == 2


def test_file_conflict_destination_is_not_overwritten(tmp_path: Path, monkeypatch):
    _photo(tmp_path, "Favorite/a.jpg")
    extra = _photo(tmp_path, "Backup/b.jpg")
    plan = tmp_path.parent / f"{tmp_path.name}-collision.json"
    _make_plan(tmp_path, plan)
    # Emulate a destination conflict created after the batch name is generated.
    from safesort import bulk_apply
    original_uuid = bulk_apply.uuid4
    class FakeUUID:
        hex = "abc123def456000000"
    monkeypatch.setattr(bulk_apply, "uuid4", lambda: FakeUUID())
    target = tmp_path / QUARANTINE_FOLDER / "batch-abc123def456" / "Backup/b.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing must remain")
    monkeypatch.setattr("builtins.input", lambda _: "QUARANTINE 1")
    assert _execute(tmp_path, plan) == 1
    assert target.read_bytes() == b"existing must remain"
    assert extra.exists()
    monkeypatch.setattr(bulk_apply, "uuid4", original_uuid)
