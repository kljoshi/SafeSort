from datetime import datetime
from pathlib import Path

from safesort.models import FileInfo, Rule
from safesort.planner import plan

ROOT = Path("/sort-root")  # never touched: most tests don't need a real disk

IMAGES = Rule("Images", (".jpg", ".png"), Path("Images"))
DOCS = Rule("Documents", (".pdf",), Path("Documents"))


def fi(path: Path) -> FileInfo:
    return FileInfo(path=path, size=1, modified=datetime(2026, 1, 1))


def test_matching_file_is_planned_into_its_destination() -> None:
    result = plan([fi(ROOT / "photo.jpg")], [IMAGES], ROOT)
    assert len(result.moves) == 1
    move = result.moves[0]
    assert move.source == ROOT / "photo.jpg"
    assert move.destination == ROOT / "Images" / "photo.jpg"
    assert move.rule_name == "Images"
    assert move.renamed is False


def test_matching_ignores_case() -> None:
    result = plan([fi(ROOT / "PHOTO.JPG")], [IMAGES], ROOT)
    assert result.moves[0].destination == ROOT / "Images" / "PHOTO.JPG"


def test_first_matching_rule_wins() -> None:
    first = Rule("First", (".jpg",), Path("A"))
    second = Rule("Second", (".jpg",), Path("B"))
    result = plan([fi(ROOT / "x.jpg")], [first, second], ROOT)
    assert result.moves[0].destination == ROOT / "A" / "x.jpg"


def test_unmatched_files_are_reported_and_not_moved() -> None:
    notes = fi(ROOT / "notes.txt")
    result = plan([notes], [IMAGES], ROOT)
    assert result.moves == []
    assert result.unmatched == [notes]


def test_indexes_count_up_from_one() -> None:
    files = [fi(ROOT / "a.jpg"), fi(ROOT / "skip.txt"), fi(ROOT / "b.pdf")]
    result = plan(files, [IMAGES, DOCS], ROOT)
    assert [m.index for m in result.moves] == [1, 2]


def test_file_already_in_its_destination_is_not_moved() -> None:
    result = plan([fi(ROOT / "Images" / "a.jpg")], [IMAGES], ROOT)
    assert result.moves == []
    assert result.unmatched == []


def test_file_in_a_subfolder_of_its_destination_is_left_alone() -> None:
    result = plan([fi(ROOT / "Images" / "2020" / "a.jpg")], [IMAGES], ROOT)
    assert result.moves == []


def test_existing_destination_gets_a_numbered_name(tmp_path: Path) -> None:
    (tmp_path / "Images").mkdir()
    (tmp_path / "Images" / "photo.jpg").write_text("already here")

    result = plan([fi(tmp_path / "photo.jpg")], [IMAGES], tmp_path)

    move = result.moves[0]
    assert move.destination == tmp_path / "Images" / "photo (1).jpg"
    assert move.renamed is True


def test_numbering_skips_names_that_are_also_taken(tmp_path: Path) -> None:
    (tmp_path / "Images").mkdir()
    (tmp_path / "Images" / "photo.jpg").write_text("1")
    (tmp_path / "Images" / "photo (1).jpg").write_text("2")

    result = plan([fi(tmp_path / "photo.jpg")], [IMAGES], tmp_path)

    assert result.moves[0].destination == tmp_path / "Images" / "photo (2).jpg"


def test_two_files_with_the_same_name_do_not_collide() -> None:
    files = [fi(ROOT / "a" / "photo.jpg"), fi(ROOT / "b" / "photo.jpg")]
    result = plan(files, [IMAGES], ROOT)

    assert result.moves[0].destination == ROOT / "Images" / "photo.jpg"
    assert result.moves[0].renamed is False
    assert result.moves[1].destination == ROOT / "Images" / "photo (1).jpg"
    assert result.moves[1].renamed is True


def test_names_differing_only_by_case_do_not_collide() -> None:
    files = [fi(ROOT / "a" / "Photo.jpg"), fi(ROOT / "b" / "photo.jpg")]
    result = plan(files, [IMAGES], ROOT)
    assert result.moves[0].destination != result.moves[1].destination
    assert result.moves[1].renamed is True


def test_empty_input_gives_an_empty_plan() -> None:
    result = plan([], [IMAGES], ROOT)
    assert result.moves == []
    assert result.unmatched == []


def test_no_rules_means_everything_is_unmatched() -> None:
    result = plan([fi(ROOT / "a.jpg")], [], ROOT)
    assert result.moves == []
    assert len(result.unmatched) == 1


def test_planning_changes_nothing_on_disk(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_text("x")
    (tmp_path / "b.pdf").write_text("x")
    (tmp_path / "Images").mkdir()
    (tmp_path / "Images" / "a.jpg").write_text("y")

    def snapshot() -> list[tuple[str, str]]:
        return sorted(
            (str(p.relative_to(tmp_path)), p.read_text() if p.is_file() else "<dir>")
            for p in tmp_path.rglob("*")
        )

    before = snapshot()
    plan([fi(tmp_path / "a.jpg"), fi(tmp_path / "b.pdf")], [IMAGES, DOCS], tmp_path)
    assert snapshot() == before
