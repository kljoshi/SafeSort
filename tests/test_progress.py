"""Progress display should be accurate and should never change duplicate behavior."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from safesort.cli import main
from safesort.duplicates import DuplicateProgress, find_duplicates
from safesort.progress import TerminalProgress


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def make_copies(root: Path) -> None:
    for name in ("a.jpg", "nested/b.jpeg"):
        photo = root / name
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"same photo" * 1024)
    (root / "unique.png").write_bytes(b"different")
    (root / "notes.txt").write_text("not a photo")


def test_progress_reports_discovery_and_bytes_hashed(tmp_path: Path) -> None:
    make_copies(tmp_path)
    events: list[DuplicateProgress] = []
    report = find_duplicates([tmp_path], progress=events.append)
    assert report.photo_count == 3
    assert report.extra_copies == 1
    discover = [event for event in events if event.phase == "discover"]
    hash_events = [event for event in events if event.phase == "hash"]
    assert discover[0].current == 0
    assert discover[-1].current == 3
    assert all(event.total is None for event in discover)
    assert hash_events[0].current == 0
    assert hash_events[0].total == 2
    assert hash_events[0].bytes_total == 2 * len(b"same photo" * 1024)
    assert hash_events[-1].current == 2
    assert hash_events[-1].bytes_processed == hash_events[-1].bytes_total
    assert any(event.bytes_processed > 0 for event in hash_events)


def test_progress_no_size_matches(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"123")
    (tmp_path / "b.jpg").write_bytes(b"1234")
    events: list[DuplicateProgress] = []
    result = find_duplicates([tmp_path], progress=events.append)
    assert result.extra_copies == 0
    assert events[-1] == DuplicateProgress("hash", 0, 0, 0, 0)


def test_renderer_tracks_phase_and_clears_tty_line() -> None:
    stream = FakeTTY()
    bar = TerminalProgress(stream, interval=0)
    bar(DuplicateProgress("discover", 30))
    bar(DuplicateProgress("hash", 0, 2, 0, 100))
    bar(DuplicateProgress("hash", 1, 2, 50, 100))
    bar.finish()
    output = stream.getvalue()
    assert "Finding photos: 30 found" in output
    assert "Hashing: 1/2 photos | 50%" in output
    assert output.endswith("\r")


def test_cli_progress_on_interactive_terminal(tmp_path: Path, monkeypatch) -> None:
    make_copies(tmp_path)
    tty = FakeTTY()
    monkeypatch.setattr(sys, "stderr", tty)
    assert main(["duplicates", "scan", str(tmp_path)]) == 0
    assert "Finding photos:" in tty.getvalue()
    assert "Hashing:" in tty.getvalue()
    assert "\r" in tty.getvalue()


def test_cli_no_progress_switch(tmp_path: Path, monkeypatch) -> None:
    make_copies(tmp_path)
    tty = FakeTTY()
    monkeypatch.setattr(sys, "stderr", tty)
    assert main(["duplicates", "scan", str(tmp_path), "--no-progress"]) == 0
    assert tty.getvalue() == ""


def test_cli_no_progress_when_redirected(tmp_path: Path, capsys) -> None:
    make_copies(tmp_path)
    assert main(["duplicates", "scan", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert "Scanned 3 photo(s)" in output.out
    assert "Finding photos:" not in output.err
    assert "Hashing:" not in output.err
