"""Turn scanned files plus rules into a plan of moves.

This module is pure planning: it may look at the disk (to detect name
conflicts) but never creates, moves, or deletes anything.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from safesort.models import FileInfo, Plan, PlannedMove, Rule


def _first_match(file: FileInfo, rules: Sequence[Rule]) -> Rule | None:
    for rule in rules:
        if file.extension in rule.extensions:
            return rule
    return None


def _key(path: Path) -> str:
    # Case-fold so "Photo.jpg" and "photo.jpg" count as the same name.
    # Case-insensitive filesystems (macOS, Windows) treat them as one file;
    # being cautious on case-sensitive ones only costs an extra rename.
    return str(path).casefold()


def _free_name(target: Path, claimed: set[str]) -> Path:
    """Return ``target``, or ``name (1).ext``, ``name (2).ext``... if taken."""
    candidate = target
    counter = 0
    while _key(candidate) in claimed or os.path.lexists(candidate):
        counter += 1
        candidate = target.with_name(f"{target.stem} ({counter}){target.suffix}")
    return candidate


def plan(files: Sequence[FileInfo], rules: Sequence[Rule], root: Path) -> Plan:
    """Decide where each file should go.

    - The first rule whose extensions contain the file's extension wins.
    - Files no rule matches are listed in ``unmatched`` and left alone.
    - Files already inside their destination folder are not moved.
    - A destination that is taken (on disk, or by an earlier planned move)
      gets a numbered name, and the move is marked ``renamed``.
    """
    result = Plan()
    claimed: set[str] = set()

    for file in files:
        rule = _first_match(file, rules)
        if rule is None:
            result.unmatched.append(file)
            continue

        target_dir = root / rule.destination
        if file.path.is_relative_to(target_dir):
            claimed.add(_key(file.path))
            continue

        target = target_dir / file.name
        final = _free_name(target, claimed)
        claimed.add(_key(final))
        result.moves.append(
            PlannedMove(
                index=len(result.moves) + 1,
                source=file.path,
                destination=final,
                rule_name=rule.name,
                renamed=final != target,
            )
        )

    return result
