from pathlib import Path
from unittest.mock import patch

import pytest

from safesort.executor import _no_clobber_move, execute, execute_move
from safesort.models import MoveStatus, PlannedMove


def make_move(source: Path, destination: Path, index: int = 1) -> PlannedMove:
    return PlannedMove(index=index, source=source, destination=destination, rule_name="Test")


def test_moves_the_file_and_creates_the_destination_folder(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_text("pixels")
    destination = tmp_path / "Images" / "a.jpg"

    result = execute_move(make_move(source, destination))

    assert result.status is MoveStatus.MOVED
    assert not source.exists()
    assert destination.read_text() == "pixels"


def test_missing_source_is_skipped_not_an_error(tmp_path: Path) -> None:
    result = execute_move(make_move(tmp_path / "gone.jpg", tmp_path / "Images" / "gone.jpg"))
    assert result.status is MoveStatus.SKIPPED
    assert "no longer exists" in result.detail


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_text("new")
    (tmp_path / "Images").mkdir()
    destination = tmp_path / "Images" / "a.jpg"
    destination.write_text("precious original")

    result = execute_move(make_move(source, destination))

    assert result.status is MoveStatus.SKIPPED
    assert "already exists" in result.detail
    assert destination.read_text() == "precious original"
    assert source.read_text() == "new"


def test_directory_as_source_is_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "adir"
    folder.mkdir()
    result = execute_move(make_move(folder, tmp_path / "Images" / "adir"))
    assert result.status is MoveStatus.SKIPPED
    assert "not a regular file" in result.detail


def test_symlink_source_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "real.jpg"
    target.write_text("x")
    link = tmp_path / "link.jpg"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported here")

    result = execute_move(make_move(link, tmp_path / "Images" / "link.jpg"))

    assert result.status is MoveStatus.SKIPPED
    assert link.is_symlink()


def test_permission_error_is_reported_as_failed(tmp_path: Path) -> None:
    source = tmp_path / "locked.jpg"
    source.write_text("x")

    with patch("safesort.executor._no_clobber_move", side_effect=PermissionError(13, "denied")):
        result = execute_move(make_move(source, tmp_path / "Images" / "locked.jpg"))

    assert result.status is MoveStatus.FAILED
    assert result.detail == "permission denied"
    assert source.exists()


def test_other_os_errors_are_reported_as_failed(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_text("x")

    with patch("safesort.executor._no_clobber_move", side_effect=OSError(5, "Input/output error")):
        result = execute_move(make_move(source, tmp_path / "Images" / "a.jpg"))

    assert result.status is MoveStatus.FAILED
    assert result.detail == "Input/output error"


def test_destination_folder_blocked_by_a_file_fails_cleanly(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_text("x")
    (tmp_path / "Images").write_text("I am a file, not a folder")

    result = execute_move(make_move(source, tmp_path / "Images" / "a.jpg"))

    assert result.status is MoveStatus.FAILED
    assert source.exists()


def test_one_failure_does_not_stop_the_rest(tmp_path: Path) -> None:
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / name).write_text(name)
    moves = [
        make_move(tmp_path / name, tmp_path / "Images" / name, index=i)
        for i, name in enumerate(("a.jpg", "b.jpg", "c.jpg"), start=1)
    ]

    real_move = _no_clobber_move

    def flaky(src: Path, dst: Path) -> object:
        if Path(src).name == "b.jpg":
            raise PermissionError(13, "denied")
        return real_move(src, dst)

    with patch("safesort.executor._no_clobber_move", side_effect=flaky):
        results = execute(moves)

    assert [r.status for r in results] == [
        MoveStatus.MOVED,
        MoveStatus.FAILED,
        MoveStatus.MOVED,
    ]
    assert (tmp_path / "Images" / "a.jpg").exists()
    assert (tmp_path / "b.jpg").exists()
    assert (tmp_path / "Images" / "c.jpg").exists()


def test_results_keep_plan_order_and_empty_input_is_fine(tmp_path: Path) -> None:
    assert execute([]) == []
    (tmp_path / "x.jpg").write_text("x")
    (tmp_path / "y.jpg").write_text("y")
    moves = [
        make_move(tmp_path / "y.jpg", tmp_path / "I" / "y.jpg", index=1),
        make_move(tmp_path / "x.jpg", tmp_path / "I" / "x.jpg", index=2),
    ]
    assert [r.move.index for r in execute(moves)] == [1, 2]


def test_cross_filesystem_copy_fallback(tmp_path: Path) -> None:
    import errno

    source = tmp_path / "photo.jpg"
    source.write_bytes(b"abc" * 1000)
    destination = tmp_path / "Images" / "photo.jpg"

    with patch("safesort.executor.os.link", side_effect=OSError(errno.EXDEV, "different devices")):
        result = execute_move(make_move(source, destination))

    assert result.status is MoveStatus.MOVED
    assert not source.exists()
    assert destination.read_bytes() == b"abc" * 1000


def test_destination_created_after_check_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_text("source")
    destination = tmp_path / "Images" / "photo.jpg"

    def simulate_race(src: Path, dst: Path, *, follow_symlinks: bool = False) -> None:
        dst.write_text("new file created concurrently")
        raise FileExistsError(17, "File exists")

    with patch("safesort.executor.os.link", side_effect=simulate_race):
        result = execute_move(make_move(source, destination))

    assert result.status is MoveStatus.SKIPPED
    assert destination.read_text() == "new file created concurrently"
    assert source.read_text() == "source"


def test_symlinked_destination_folder_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_text("private")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "Images"
    try:
        link.symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    outcome = execute_move(make_move(source, link / "source.jpg"))
    assert outcome.status is MoveStatus.SKIPPED
    assert source.exists()
    assert not (elsewhere / "source.jpg").exists()
