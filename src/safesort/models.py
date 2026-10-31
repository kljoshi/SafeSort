"""Core data types shared across SafeSort."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


@dataclass(frozen=True)
class FileInfo:
    """One file found by the scanner."""

    path: Path
    size: int
    modified: datetime

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        """Lowercase extension including the dot, or '' if there is none."""
        return self.path.suffix.lower()


@dataclass(frozen=True)
class SkippedEntry:
    """Something the scanner could not or chose not to include, and why."""

    path: Path
    reason: str


@dataclass
class ScanResult:
    """Everything a scan produced: files found plus entries skipped."""

    root: Path
    files: list[FileInfo] = field(default_factory=list)
    skipped: list[SkippedEntry] = field(default_factory=list)


@dataclass(frozen=True)
class Rule:
    """One organizing rule: files with these extensions go to ``destination``.

    ``extensions`` are lowercase with a leading dot (e.g. ".jpg").
    ``destination`` is relative to the folder being sorted.
    """

    name: str
    extensions: tuple[str, ...]
    destination: Path


@dataclass(frozen=True)
class PlannedMove:
    """A proposed move. Nothing has happened yet."""

    index: int  # 1-based position in the plan, used to refer to it in the CLI
    source: Path
    destination: Path
    rule_name: str
    renamed: bool = False  # True if the name was changed to avoid a conflict


@dataclass
class Plan:
    """The planner's output: moves to make and files no rule matched."""

    moves: list[PlannedMove] = field(default_factory=list)
    unmatched: list[FileInfo] = field(default_factory=list)


class MoveStatus(Enum):
    MOVED = "moved"
    SKIPPED = "skipped"  # we chose not to move it (something changed since planning)
    FAILED = "failed"  # we tried and the operating system refused


@dataclass(frozen=True)
class MoveResult:
    """What happened to one planned move."""

    move: PlannedMove
    status: MoveStatus
    detail: str = ""  # the reason, for skipped and failed moves
