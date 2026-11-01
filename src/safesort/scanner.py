"""Walk a folder and describe the files in it. Never modifies anything."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from safesort.errors import ScanError
from safesort.history import HISTORY_FOLDER
from safesort.models import FileInfo, ScanResult, SkippedEntry
from safesort.protected import is_photos_library_path


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
    on_file: Callable[[FileInfo], None] | None = None,
) -> ScanResult:
    """Scan ``root`` and return the files found.

    Problems with individual files or subfolders (permissions, files that
    vanish mid-scan) are recorded in ``result.skipped`` instead of stopping
    the whole scan. Symlinks are never followed; they are reported as skipped.

    Raises:
        ScanError: if ``root`` does not exist or is not a folder.
    """
    root = root.expanduser().resolve()
    if is_photos_library_path(root):
        raise ScanError(f"Protected Apple Photos library cannot be scanned: {root}")
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
            # Apple Photos library bundles are managed by Photos; do not enter
            # them, even when --hidden or --recursive is used.
            if is_photos_library_path(entry):
                result.skipped.append(SkippedEntry(entry, "protected Apple Photos library"))
                continue
            # Never scan the application journal, even with --hidden.
            if entry.name in {HISTORY_FOLDER, ".safesort-quarantine"}:
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
                    file_info = FileInfo(
                        path=entry,
                        size=info.st_size,
                        modified=datetime.fromtimestamp(info.st_mtime),
                    )
                    result.files.append(file_info)
                    if on_file is not None:
                        on_file(file_info)
                else:
                    result.skipped.append(SkippedEntry(entry, "not a regular file"))
            except OSError as exc:
                result.skipped.append(SkippedEntry(entry, _reason(exc)))

    result.files.sort(key=lambda f: f.path)
    return result
