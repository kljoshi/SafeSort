"""Carry out planned moves without ever overwriting a destination.

Same-volume moves create a hard link exclusively, then remove the old name.
For cross-volume moves we copy to a file opened in exclusive-create mode and
remove the source only after a successful copy. Both strategies avoid the
check-then-rename overwrite race of ``shutil.move`` on POSIX.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from collections.abc import Sequence
from pathlib import Path

from safesort.models import MoveResult, MoveStatus, PlannedMove


def _path_has_symlink_parent(path: Path) -> bool:
    """Reject a destination routed through a symlinked directory."""
    for parent in path.parents:
        if parent.is_symlink():
            return True
    return False


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _remove_if_ours(destination: Path, original: os.stat_result | None) -> None:
    """Best-effort cleanup, avoiding unrelated files inserted at our target."""
    if original is None:
        return
    try:
        current = destination.lstat()
        if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
            destination.unlink()
    except OSError:
        pass


def _exclusive_copy(source: Path, destination: Path, original: os.stat_result) -> None:
    """Cross-filesystem fallback: copy safely before deleting the source."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    created_identity: os.stat_result | None = None
    try:
        with os.fdopen(source_fd, "rb") as infile:
            source_fd = -1  # the file object now owns the descriptor
            current = os.fstat(infile.fileno())
            if not stat.S_ISREG(current.st_mode) or _identity(current) != _identity(original):
                raise OSError("source changed during move; left unchanged")
            with destination.open("xb") as outfile:
                created_identity = os.fstat(outfile.fileno())
                shutil.copyfileobj(infile, outfile, length=1024 * 1024)
                outfile.flush()
                os.fsync(outfile.fileno())
                if os.name != "nt" and hasattr(os, "fchmod"):
                    os.fchmod(outfile.fileno(), stat.S_IMODE(original.st_mode))

        os.utime(destination, ns=(original.st_atime_ns, original.st_mtime_ns))
        # A source replaced while copying must not be deleted.
        if _identity(source.lstat()) != _identity(original):
            raise OSError("source changed during copy; left unchanged")
        source.unlink()
    except BaseException:
        _remove_if_ours(destination, created_identity)
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def _no_clobber_move(source: Path, destination: Path) -> None:
    """Move a regular file; atomically refuse a name that already exists."""
    original = source.lstat()
    if not stat.S_ISREG(original.st_mode):
        raise OSError("source is no longer a regular file")
    try:
        # A hard link with an existing destination fails with EEXIST.
        # This operation never overwrites someone else's file.
        if os.link in os.supports_follow_symlinks:
            os.link(source, destination, follow_symlinks=False)
        else:
            os.link(source, destination)
    except OSError as exc:
        # External volumes and some mounted/cloud filesystems disallow links.
        copy_fallback = {errno.EXDEV, errno.EPERM, errno.ENOSYS}
        copy_fallback.add(getattr(errno, "EOPNOTSUPP", errno.EXDEV))
        copy_fallback.add(getattr(errno, "ENOTSUP", errno.EXDEV))
        if exc.errno not in copy_fallback:
            raise
        _exclusive_copy(source, destination, original)
        return

    link_identity = destination.lstat()
    try:
        if _identity(source.lstat()) != _identity(original):
            raise OSError("source changed during move; left unchanged")
        source.unlink()
    except BaseException:
        # Only remove the link *we created* if the source is still present.
        _remove_if_ours(destination, link_identity)
        raise


def execute_move(move: PlannedMove) -> MoveResult:
    """Perform one move and report the outcome, without raising file errors."""
    source, destination = move.source, move.destination
    try:
        if source.is_symlink():
            return MoveResult(move, MoveStatus.SKIPPED, "source is now a symlink")
        if not source.exists():
            return MoveResult(move, MoveStatus.SKIPPED, "source no longer exists")
        if not source.is_file():
            return MoveResult(move, MoveStatus.SKIPPED, "source is not a regular file")
        if _path_has_symlink_parent(destination):
            return MoveResult(move, MoveStatus.SKIPPED, "destination uses a symlinked folder")
        if os.path.lexists(destination):
            return MoveResult(move, MoveStatus.SKIPPED, "destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _path_has_symlink_parent(destination):
            return MoveResult(move, MoveStatus.SKIPPED, "destination uses a symlinked folder")
        _no_clobber_move(source, destination)
    except FileExistsError:
        if os.path.lexists(destination):
            return MoveResult(move, MoveStatus.SKIPPED, "destination already exists")
        return MoveResult(move, MoveStatus.FAILED, "destination folder is not a directory")
    except PermissionError:
        return MoveResult(move, MoveStatus.FAILED, "permission denied")
    except FileNotFoundError:
        return MoveResult(move, MoveStatus.SKIPPED, "file disappeared during the move")
    except OSError as exc:
        return MoveResult(move, MoveStatus.FAILED, exc.strerror or str(exc))
    return MoveResult(move, MoveStatus.MOVED)


def execute(moves: Sequence[PlannedMove]) -> list[MoveResult]:
    """Perform moves in order. Returns one result per move."""
    return [execute_move(move) for move in moves]
