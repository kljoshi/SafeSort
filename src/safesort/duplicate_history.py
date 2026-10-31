"""Separate SQLite journal for reversible duplicate-photo quarantine moves."""

from __future__ import annotations

import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from safesort.duplicates import QUARANTINE_FOLDER, hash_photo
from safesort.errors import SafeSortError
from safesort.history import HISTORY_FOLDER
from safesort.models import MoveResult, MoveStatus, PlannedMove
from safesort.protected import is_photos_library_path

DB_NAME = "duplicates.sqlite3"


class DuplicateHistoryError(SafeSortError):
    """The duplicate photo journal could not be trusted or accessed."""


@dataclass(frozen=True)
class QuarantinedPhoto:
    row_id: int
    source: Path
    quarantine_path: Path
    sha256: str
    size: int
    device: int | None
    inode: int | None
    mtime_ns: int | None


class DuplicateHistory:
    def __init__(self, root: Path, *, create: bool = False) -> None:
        self.root = root.expanduser().resolve()
        if is_photos_library_path(self.root):
            raise DuplicateHistoryError(f"Protected Apple Photos library: {self.root}")
        folder = self.root / HISTORY_FOLDER
        database = folder / DB_NAME
        if folder.is_symlink() or database.is_symlink():
            raise DuplicateHistoryError("SafeSort journal path cannot be a symlink")
        if create:
            folder.mkdir(exist_ok=True)
        if not folder.is_dir() or (not create and not database.is_file()):
            raise DuplicateHistoryError(f"No duplicate quarantine history in {self.root}")
        try:
            self.connection = sqlite3.connect(
                str(database if create else f"{database.as_uri()}?mode=rw"),
                uri=not create,
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            if create:
                self.connection.executescript("""
                    CREATE TABLE IF NOT EXISTS duplicate_batches (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS duplicate_moves (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_id INTEGER NOT NULL REFERENCES duplicate_batches(id),
                        ordinal INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        destination TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        detail TEXT NOT NULL DEFAULT '',
                        restore_status TEXT NOT NULL DEFAULT '',
                        file_dev TEXT,
                        file_ino TEXT,
                        file_mtime_ns INTEGER
                    );
                """)
        except sqlite3.Error as exc:
            raise DuplicateHistoryError(f"Cannot open duplicate history: {exc}") from exc

    def __enter__(self) -> DuplicateHistory:
        return self

    def __exit__(self, *_args: object) -> None:
        self.connection.close()

    def _relative(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise DuplicateHistoryError(f"Path is outside the scan root: {path}") from exc
        if not relative.parts or ".." in relative.parts:
            raise DuplicateHistoryError(f"Unsafe history path: {path}")
        return str(relative)

    def _restore_path(self, raw: str) -> Path:
        relative = Path(raw)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise DuplicateHistoryError("Unsafe path in duplicate journal")
        return self.root / relative

    def begin(self, moves: list[tuple[PlannedMove, str, int]]) -> tuple[int, list[int]]:
        """Write the full plan before modifying any photos."""
        try:
            with self.connection:
                batch = self.connection.execute(
                    "INSERT INTO duplicate_batches (created_at) VALUES (?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
                )
                batch_id = int(batch.lastrowid)
                rows: list[int] = []
                for move, sha256, size in moves:
                    row = self.connection.execute(
                        """INSERT INTO duplicate_moves
                           (batch_id, ordinal, source, destination, sha256, size)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (batch_id, move.index, self._relative(move.source),
                         self._relative(move.destination), sha256, size),
                    )
                    rows.append(int(row.lastrowid))
                return batch_id, rows
        except sqlite3.Error as exc:
            raise DuplicateHistoryError(f"Cannot save duplicate plan: {exc}") from exc

    def record_move(self, row_id: int, result: MoveResult) -> None:
        dev: str | None = None
        ino: str | None = None
        mtime: int | None = None
        if result.status is MoveStatus.MOVED:
            try:
                info = result.move.destination.lstat()
                if stat.S_ISREG(info.st_mode):
                    # The 'id:' prefix protects large Windows IDs from SQLite
                    # INTEGER affinity, including pre-existing database schemas.
                    dev, ino, mtime = f"id:{info.st_dev}", f"id:{info.st_ino}", info.st_mtime_ns
            except OSError:
                pass
        try:
            with self.connection:
                self.connection.execute(
                    """UPDATE duplicate_moves SET status=?, detail=?,
                        file_dev=?, file_ino=?, file_mtime_ns=? WHERE id=?""",
                    (result.status.value, result.detail, dev, ino, mtime, row_id),
                )
        except sqlite3.Error as exc:
            raise DuplicateHistoryError(
                "Photo move may have completed but journaling failed. "
                "Inspect quarantine and history manually before retrying."
            ) from exc

    def latest_restorable(self) -> tuple[int, list[QuarantinedPhoto]] | None:
        try:
            batch = self.connection.execute("""
                SELECT batch_id FROM duplicate_moves
                WHERE status='pending' OR (status='moved' AND restore_status!='restored')
                ORDER BY batch_id DESC LIMIT 1
            """).fetchone()
            if batch is None:
                return None
            batch_id = int(batch["batch_id"])
            pending = self.connection.execute(
                "SELECT COUNT(*) FROM duplicate_moves WHERE batch_id=? AND status='pending'",
                (batch_id,),
            ).fetchone()[0]
            if pending:
                raise DuplicateHistoryError(
                    f"Quarantine batch #{batch_id} has {pending} interrupted move(s). "
                    "Inspect the files and journal manually before restoring."
                )
            rows = self.connection.execute("""
                SELECT * FROM duplicate_moves WHERE batch_id=?
                AND status='moved' AND restore_status!='restored'
                ORDER BY ordinal DESC
            """, (batch_id,)).fetchall()
            result: list[QuarantinedPhoto] = []
            for r in rows:
                dest = self._restore_path(str(r["destination"]))
                if QUARANTINE_FOLDER not in dest.relative_to(self.root).parts:
                    raise DuplicateHistoryError("Journal references a destination outside quarantine")
                result.append(QuarantinedPhoto(
                    int(r["id"]), self._restore_path(str(r["source"])), dest,
                    str(r["sha256"]), int(r["size"]),
                    _decode_id(r["file_dev"]), _decode_id(r["file_ino"]),
                    r["file_mtime_ns"],
                ))
            return batch_id, result
        except sqlite3.Error as exc:
            raise DuplicateHistoryError(f"Cannot read duplicate history: {exc}") from exc

    def record_restore(self, row_id: int, result: MoveResult) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    "UPDATE duplicate_moves SET restore_status=? WHERE id=?",
                    ("restored" if result.status is MoveStatus.MOVED else "", row_id),
                )
        except sqlite3.Error as exc:
            raise DuplicateHistoryError(
                "Restoration may have completed but journaling failed; inspect files manually."
            ) from exc


def _decode_id(raw: str | int | None) -> int | None:
    if raw is None:
        return None
    return int(raw.removeprefix("id:") if isinstance(raw, str) else raw)


def quarantine_identity_matches(item: QuarantinedPhoto) -> bool:
    """Do not restore a quarantined file that was edited/replaced."""
    if None in (item.device, item.inode, item.mtime_ns):
        return False
    try:
        hashed, info = hash_photo(item.quarantine_path)
    except OSError:
        return False
    return (hashed == item.sha256 and info.st_dev == item.device
            and info.st_ino == item.inode and info.st_size == item.size
            and info.st_mtime_ns == item.mtime_ns)
