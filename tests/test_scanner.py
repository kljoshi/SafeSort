from pathlib import Path
from unittest.mock import patch

import pytest

from safesort.errors import ScanError
from safesort.scanner import scan


def touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def names(files: list) -> list[str]:  # type: ignore[type-arg]
    return [f.name for f in files]


def test_finds_files_in_the_top_folder(tmp_path: Path) -> None:
    touch(tmp_path / "b.txt")
    touch(tmp_path / "a.jpg")
    result = scan(tmp_path)
    assert names(result.files) == ["a.jpg", "b.txt"]  # sorted


def test_records_size(tmp_path: Path) -> None:
    touch(tmp_path / "a.txt", "hello")
    assert scan(tmp_path).files[0].size == 5


def test_ignores_subfolders_by_default(tmp_path: Path) -> None:
    touch(tmp_path / "top.txt")
    touch(tmp_path / "sub" / "nested.txt")
    assert names(scan(tmp_path).files) == ["top.txt"]


def test_recursive_includes_nested_files(tmp_path: Path) -> None:
    touch(tmp_path / "top.txt")
    touch(tmp_path / "sub" / "deeper" / "nested.txt")
    result = scan(tmp_path, recursive=True)
    assert sorted(names(result.files)) == ["nested.txt", "top.txt"]


def test_hidden_files_skipped_by_default(tmp_path: Path) -> None:
    touch(tmp_path / ".secret")
    touch(tmp_path / "visible.txt")
    assert names(scan(tmp_path).files) == ["visible.txt"]


def test_hidden_files_included_on_request(tmp_path: Path) -> None:
    touch(tmp_path / ".secret")
    touch(tmp_path / "visible.txt")
    result = scan(tmp_path, include_hidden=True)
    assert names(result.files) == [".secret", "visible.txt"]


def test_empty_folder_gives_empty_result(tmp_path: Path) -> None:
    result = scan(tmp_path)
    assert result.files == []
    assert result.skipped == []


def test_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="not found"):
        scan(tmp_path / "nope")


def test_a_file_instead_of_a_folder_raises(tmp_path: Path) -> None:
    file = touch(tmp_path / "a.txt")
    with pytest.raises(ScanError, match="Not a folder"):
        scan(file)


def test_symlinks_are_skipped_not_followed(tmp_path: Path) -> None:
    target = touch(tmp_path / "real.txt")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported here")
    result = scan(tmp_path)
    assert names(result.files) == ["real.txt"]
    assert [(s.path.name, s.reason) for s in result.skipped] == [("link.txt", "symlink")]


def test_unreadable_folder_is_reported_not_fatal(tmp_path: Path) -> None:
    touch(tmp_path / "ok.txt")
    locked = tmp_path / "locked"
    touch(locked / "secret.txt")

    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path):  # type: ignore[no-untyped-def]
        if self == locked:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    with patch.object(Path, "iterdir", fake_iterdir):
        result = scan(tmp_path, recursive=True)

    assert names(result.files) == ["ok.txt"]
    assert [(s.path, s.reason) for s in result.skipped] == [(locked, "permission denied")]
