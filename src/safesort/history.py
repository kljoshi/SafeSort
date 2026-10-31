"""Per-folder SQLite journal for reversible file organization.

A journal is written *before* files are moved. Each move is recorded as it
finishes. This preserves completed moves if a later move fails or the process
is interrupted. Pending rows are intentionally not treated as safe to undo:
users must inspect them rather than risk reversing an unknown file.
"""

from __future__ import annotations

import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from safesort.errors import HistoryError
from safesort.models import MoveResult, MoveStatus, PlannedMove

HISTORY_FOLDER = ".safesort"
DB_NAME = "history.sqlite3"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class HistoryMove:
    id: int
    batch_id: int
    source: Path
    destination: Path
    status: str
    undo_status: str
    file_dev: int | None
    file_ino: int | None
    file_size: int | None
    file_mtime_ns: int | None


@dataclass(frozen=True)
class HistoryBatch:
    id: int
    started_at: str
    moved: int
    undone: int
    pending: int
    failed: int


class HistoryDB:
    """A journal scoped to the directory being organized.

    The journal lives in ``<folder>/.safesort/history.sqlite3`` and is
    excluded from all scans, even scans with ``--hidden``.
    """

    def __init__(self, root: Path, *, create: bool = False) -> None:
        root = root.expanduser().resolve()
        self.root = root
        journal_dir = root / HISTORY_FOLDER
        if journal_dir.is_symlink():
            raise HistoryError(f"History folder cannot be a symlink: {journal_dir}")
        if create:
            try:
                journal_dir.mkdir(exist_ok=True)
            except OSError as exc:
                raise HistoryError(f"Cannot create history folder: {exc}") from exc
        if not journal_dir.is_dir():
            raise HistoryError(f"No SafeSort history exists in {root}")

        db_path = journal_dir / DB_NAME
        if db_path.is_symlink():
            raise HistoryError(f"History database cannot be a symlink: {db_path}")
        if not create and not db_path.is_file():
            raise HistoryError(f"No SafeSort history exists in {root}")
        try:
            # In read mode SQLite must not create missing files.
            self.connection = sqlite3.connect(
                str(db_path if create else f"{db_path.as_uri()}?mode=rw"),
                uri=not create,
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            if create:
                self._create_schema()
        except sqlite3.Error as exc:
            raise HistoryError(f"Cannot open SafeSort history: {exc}") from exc

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES batches(id),
                ordinal INTEGER NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                detail TEXT NOT NULL DEFAULT '',
                undo_status TEXT NOT NULL DEFAULT '',
                undo_detail TEXT NOT NULL DEFAULT '',
                file_dev INTEGER,
                file_ino INTEGER,
                file_size INTEGER,
                file_mtime_ns INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_moves_batch ON moves(batch_id);
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> HistoryDB:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start_batch(self, moves: list[PlannedMove]) -> tuple[int, list[int]]:
        """Persist the entire plan before any filesystem change."""
        try:
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT INTO batches (started_at) VALUES (?)", (_timestamp(),)
                )
                batch_id = int(cursor.lastrowid)
                ids: list[int] = []
                for move in moves:
                    cursor = self.connection.execute(
                        """INSERT INTO moves (batch_id, ordinal, source, destination)
                           VALUES (?, ?, ?, ?)""",
                        (
                            batch_id,
                            move.index,
                            str(move.source.relative_to(self.root)),
                            str(move.destination.relative_to(self.root)),
                        ),
                    )
                    ids.append(int(cursor.lastrowid))
                return batch_id, ids
        except sqlite3.Error as exc:
            raise HistoryError(f"Could not record file move plan: {exc}") from exc

    def record_result(self, row_id: int, result: MoveResult) -> None:
        """Record outcome and file identity to prevent unsafe future undos."""
        identity: tuple[int, int, int, int] | tuple[None, None, None, None]
        if result.status is MoveStatus.MOVED:
            try:
                info = result.move.destination.lstat()
                if not stat.S_ISREG(info.st_mode):
                    identity = (None, None, None, None)
                else:
                    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            except OSError:
                identity = (None, None, None, None)
        else:
            identity = (None, None, None, None)
        try:
            with self.connection:
                self.connection.execute(
                    """UPDATE moves SET status=?, detail=?,
                       file_dev=?, file_ino=?, file_size=?, file_mtime_ns=?
                       WHERE id=?""",
                    (result.status.value, result.detail, *identity, row_id),
                )
        except sqlite3.Error as exc:
            raise HistoryError(
                f"Move completed but recording its outcome failed: {exc}. "
                "Inspect the files and history before trying undo."
            ) from exc

    def batches(self, limit: int = 10) -> list[HistoryBatch]:
        try:
            rows = self.connection.execute(
                """SELECT b.id, b.started_at,
                         SUM(CASE WHEN m.status='moved' THEN 1 ELSE 0 END) AS moved,
                         SUM(CASE WHEN m.undo_status='undone' THEN 1 ELSE 0 END) AS undone,
                         SUM(CASE WHEN m.status='pending' THEN 1 ELSE 0 END) AS pending,
                         SUM(CASE WHEN m.status='failed' THEN 1 ELSE 0 END) AS failed
                   FROM batches b JOIN moves m ON m.batch_id=b.id
                   GROUP BY b.id ORDER BY b.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"Could not read history: {exc}") from exc
        return [HistoryBatch(**dict(row)) for row in rows]

    def _restore_path(self, saved: str) -> Path:
        """Resolve a journaled relative path within the current folder root."""
        path = Path(saved)
        if path.is_absolute():
            # Compatibility with early development versions of the journal.
            if not path.is_relative_to(self.root):
                raise HistoryError("Recorded file path is outside this folder")
            return path
        if ".." in path.parts or not path.parts:
            raise HistoryError("Unsafe relative path found in the history database")
        return self.root / path

    def latest_undoable(self) -> tuple[int, list[HistoryMove]] | None:
        """Return the newest batch with still-undoable moves (no skipping ahead)."""
        try:
            row = self.connection.execute(
                """SELECT b.id FROM batches b JOIN moves m ON m.batch_id=b.id
                   GROUP BY b.id
                   HAVING SUM(CASE WHEN m.status='moved'
                                    AND m.undo_status!='undone' THEN 1 ELSE 0 END)>0
                      OR SUM(CASE WHEN m.status='pending' THEN 1 ELSE 0 END)>0
                   ORDER BY b.id DESC LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            batch_id = int(row["id"])
            pending = self.connection.execute(
                "SELECT COUNT(*) FROM moves WHERE batch_id=? AND status='pending'",
                (batch_id,),
            ).fetchone()[0]
            if pending:
                raise HistoryError(
                    f"Batch #{batch_id} contains {pending} interrupted/pending move(s). "
                    "Inspect the folder and journal before undoing."
                )
            rows = self.connection.execute(
                """SELECT id, batch_id, source, destination, status, undo_status,
                          file_dev, file_ino, file_size, file_mtime_ns
                   FROM moves WHERE batch_id=? AND status='moved'
                                  AND undo_status!='undone'
                   ORDER BY ordinal DESC""",
                (batch_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"Could not read history for undo: {exc}") from exc
        return batch_id, [
            HistoryMove(
                id=int(r["id"]),
                batch_id=int(r["batch_id"]),
                source=self._restore_path(str(r["source"])),
                destination=self._restore_path(str(r["destination"])),
                status=str(r["status"]),
                undo_status=str(r["undo_status"]),
                file_dev=r["file_dev"],
                file_ino=r["file_ino"],
                file_size=r["file_size"],
                file_mtime_ns=r["file_mtime_ns"],
            )
            for r in rows
        ]

    def record_undo(self, row_id: int, result: MoveResult) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    "UPDATE moves SET undo_status=?, undo_detail=? WHERE id=?",
                    (
                        "undone" if result.status is MoveStatus.MOVED else "",
                        result.detail,
                        row_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise HistoryError(f"Could not record undo result: {exc}") from exc


def matches_identity(item: HistoryMove) -> bool:
    """Only undo if this is still the exact file originally moved."""
    if None in (item.file_dev, item.file_ino, item.file_size, item.file_mtime_ns):
        return False
    try:
        info = item.destination.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    ) == (item.file_dev, item.file_ino, item.file_size, item.file_mtime_ns)
