"""Guard interactive quarantine choices without changing move semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from safesort.cli import main


def _two_groups(root: Path) -> tuple[Path, Path, Path, Path]:
    originals = tuple(root / name for name in ("a.jpg", "b.jpeg", "c.jpg", "d.jpeg"))
    for path, contents in zip(originals, (b"first group", b"first group", b"second group", b"second group")):
        path.write_bytes(contents)
    return originals


def test_invalid_answer_retries_same_group_and_keeps_prior_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b, c, d = _two_groups(tmp_path)
    prompts: list[str] = []
    choices = iter(["1", "111", "2", "y"])

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(choices)

    monkeypatch.setattr("builtins.input", ask)
    assert main(["duplicates", "quarantine", str(tmp_path), "--no-progress"]) == 0
    assert a.exists() and d.exists()
    assert not b.exists() and not c.exists()
    assert prompts[1] == prompts[2]  # Same group asked again.
    assert "Invalid choice: enter 1-2" in capsys.readouterr().err


def test_malformed_answers_retry_without_discarding_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    choices = iter(["0", "-1", "xyz", "3", "2", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(choices))
    assert main(["duplicates", "quarantine", str(tmp_path)]) == 0
    assert not a.exists() and b.exists()
    assert capsys.readouterr().err.count("Invalid choice") == 4


@pytest.mark.parametrize("cancel", [EOFError, KeyboardInterrupt])
def test_cancel_during_review_is_read_only_even_after_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: type[Exception]
) -> None:
    photos = _two_groups(tmp_path)
    prompts = iter(["1", "111"])

    def ask(_: str) -> str:
        try:
            return next(prompts)
        except StopIteration:
            raise cancel()

    monkeypatch.setattr("builtins.input", ask)
    assert main(["duplicates", "quarantine", str(tmp_path)]) == 1
    assert all(photo.is_file() for photo in photos)
    assert not (tmp_path / ".safesort-quarantine").exists()
    assert not (tmp_path / ".safesort").exists()


def test_skip_after_invalid_answer_leaves_group_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = _two_groups(tmp_path)
    choices = iter(["111", "", "",])
    monkeypatch.setattr("builtins.input", lambda _: next(choices))
    assert main(["duplicates", "quarantine", str(tmp_path)]) == 0
    assert all(photo.is_file() for photo in photos)
    assert not (tmp_path / ".safesort-quarantine").exists()
