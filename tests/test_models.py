from datetime import datetime
from pathlib import Path

from safesort.models import FileInfo


def make(name: str) -> FileInfo:
    return FileInfo(path=Path("/tmp") / name, size=10, modified=datetime(2026, 1, 1))


def test_name_is_the_filename() -> None:
    assert make("report.pdf").name == "report.pdf"


def test_extension_is_lowercase_with_dot() -> None:
    assert make("PHOTO.JPG").extension == ".jpg"


def test_no_extension_gives_empty_string() -> None:
    assert make("Makefile").extension == ""
