from datetime import datetime
from pathlib import Path

import pytest

from safesort.errors import SelectionError
from safesort.models import FileInfo, Rule
from safesort.planner import plan
from safesort.selection import parse_indexes, plan_selected

ROOT = Path("/sort-root")
IMAGES = Rule("Images", (".jpg",), Path("Images"))


def fi(path: Path) -> FileInfo:
    return FileInfo(path=path, size=1, modified=datetime(2026, 1, 1))


def test_single_numbers_and_lists() -> None:
    assert parse_indexes("3", 5) == {3}
    assert parse_indexes("1,3", 5) == {1, 3}


def test_ranges_and_mixed_with_spaces() -> None:
    assert parse_indexes("2-4", 5) == {2, 3, 4}
    assert parse_indexes(" 1 , 3-4 ", 5) == {1, 3, 4}


def test_duplicates_are_fine() -> None:
    assert parse_indexes("1,1,1-2", 5) == {1, 2}


@pytest.mark.parametrize("bad", ["", "1,,2", "a", "1-x", "3-1", "-2"])
def test_malformed_values_raise(bad: str) -> None:
    with pytest.raises(SelectionError):
        parse_indexes(bad, 5)


@pytest.mark.parametrize("bad", ["0", "6", "1-6", "0-2"])
def test_out_of_range_raises_and_says_so(bad: str) -> None:
    with pytest.raises(SelectionError, match="out of range"):
        parse_indexes(bad, 5)


def test_plan_selected_keeps_only_chosen_moves() -> None:
    files = [fi(ROOT / "a.jpg"), fi(ROOT / "b.jpg"), fi(ROOT / "c.jpg")]
    full = plan(files, [IMAGES], ROOT)

    chosen = plan_selected(files, [IMAGES], ROOT, full, {1, 3})

    assert [m.source.name for m in chosen.moves] == ["a.jpg", "c.jpg"]


def test_dropping_a_move_removes_a_rename_that_is_no_longer_needed() -> None:
    files = [fi(ROOT / "a" / "photo.jpg"), fi(ROOT / "b" / "photo.jpg")]
    full = plan(files, [IMAGES], ROOT)
    assert full.moves[1].renamed is True  # second one collides with the first

    chosen = plan_selected(files, [IMAGES], ROOT, full, {2})

    assert len(chosen.moves) == 1
    assert chosen.moves[0].destination == ROOT / "Images" / "photo.jpg"
    assert chosen.moves[0].renamed is False


def test_selecting_nothing_gives_an_empty_plan() -> None:
    files = [fi(ROOT / "a.jpg")]
    full = plan(files, [IMAGES], ROOT)
    assert plan_selected(files, [IMAGES], ROOT, full, set()).moves == []
