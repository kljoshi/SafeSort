"""Walk a folder and describe the files in it. Never modifies anything."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from safesort.errors import ScanError
from safesort.history import HISTORY_FOLDER
from safesort.models import FileInfo, ScanResult, SkippedEntry


def _reason(exc: OSError) -> str:
    """Turn an OSError into a short, human-readable reason."""
    if isinstance(exc, PermissionError):
        return "permission denied"
    if isinstance(exc, FileNotFoundError):
        return "disappeared during scan"
    return exc.strerror or str(exc)


def scan(
    root: Path,
    *,
    recursive: bool = False,
    include_hidden: bool = False,
) -> ScanResult:
    """Scan ``root`` and return the files found.

    Problems with individual files or subfolders (permissions, files that
    vanish mid-scan) are recorded in ``result.skipped`` instead of stopping
    the whole scan. Symlinks are never followed; they are reported as skipped.

    Raises:
        ScanError: if ``root`` does not exist or is not a folder.
    """
    root = root.expanduser().resolve()
    if not root.exists():
        raise ScanError(f"Folder not found: {root}")
    if not root.is_dir():
        raise ScanError(f"Not a folder: {root}")

    result = ScanResult(root=root)
    pending: list[Path] = [root]

    while pending:
        folder = pending.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError as exc:
            result.skipped.append(SkippedEntry(folder, _reason(exc)))
            continue

        for entry in entries:
            # Never scan the application journal, even with --hidden.
            if entry.name == HISTORY_FOLDER:
                continue
            if not include_hidden and entry.name.startswith("."):
                continue
            if entry.is_symlink():
                result.skipped.append(SkippedEntry(entry, "symlink"))
                continue
            try:
                if entry.is_dir():
                    if recursive:
                        pending.append(entry)
                elif entry.is_file():
                    info = entry.stat()
                    result.files.append(
                        FileInfo(
                            path=entry,
                            size=info.st_size,
                            modified=datetime.fromtimestamp(info.st_mtime),
                        )
                    )
                else:
                    result.skipped.append(SkippedEntry(entry, "not a regular file"))
            except OSError as exc:
                result.skipped.append(SkippedEntry(entry, _reason(exc)))

    result.files.sort(key=lambda f: f.path)
    return result
