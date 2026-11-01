"""Find byte-for-byte duplicate photographs, without changing user files."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from safesort.errors import ScanError
from safesort.models import FileInfo
from safesort.scanner import scan

PHOTO_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".tif", ".tiff", ".heic", ".heif",
})
QUARANTINE_FOLDER = ".safesort-quarantine"
BLOCK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Photo:
    path: Path
    size: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True)
class DuplicateGroup:
    sha256: str
    size: int
    photos: tuple[Photo, ...]


@dataclass
class DuplicateScan:
    roots: tuple[Path, ...]
    photo_count: int = 0
    groups: list[DuplicateGroup] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def extra_copies(self) -> int:
        return sum(len(group.photos) - 1 for group in self.groups)

    @property
    def recoverable_bytes(self) -> int:
        return sum((len(group.photos) - 1) * group.size for group in self.groups)


@dataclass(frozen=True)
class DuplicateProgress:
    """Snapshot emitted while discovering photos or hashing candidates."""
    phase: str  # 'discover' or 'hash'
    current: int
    total: int | None = None
    bytes_processed: int = 0
    bytes_total: int | None = None


def hash_photo(
    path: Path, *, on_bytes: Callable[[int], None] | None = None
) -> tuple[str, os.stat_result]:
    """Hash a regular non-symlink file, rejecting changes while it is read.

    The descriptor's file ID is checked against the directory entry, which
    also limits path-replacement attacks between scan and file opening.
    """
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError("not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
            raise OSError("photo changed before hashing")
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(BLOCK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
        after = os.fstat(stream.fileno())
    if _signature(after) != _signature(before) or _signature(path.lstat()) != _signature(before):
        raise OSError("photo changed during hashing")
    return digest.hexdigest(), before


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def find_duplicates(
    roots: list[Path], *, include_hidden: bool = False,
    progress: Callable[[DuplicateProgress], None] | None = None,
) -> DuplicateScan:
    """Recursively scan one or more roots; only hash possible size matches."""
    if not roots:
        raise ScanError("At least one folder is required")
    resolved: list[Path] = []
    for root in roots:
        candidate = root.expanduser()
        if candidate.is_symlink():
            raise ScanError(f"Scan root cannot be a symlink: {candidate}")
        candidate = candidate.resolve()
        if candidate.name in {QUARANTINE_FOLDER, ".safesort"}:
            raise ScanError("Cannot scan SafeSort's internal data folders directly")
        if candidate not in resolved:
            resolved.append(candidate)
    # Reject overlapping roots: otherwise some photos would be counted twice.
    for i, left in enumerate(resolved):
        for right in resolved[i + 1:]:
            if left in right.parents or right in left.parents:
                raise ScanError(f"Overlapping scan roots: {left} and {right}")

    report = DuplicateScan(tuple(resolved))
    candidates: list[Path] = []
    sizes: dict[int, list[Path]] = {}
    discovered = 0

    def on_file(item: FileInfo) -> None:
        nonlocal discovered
        if item.path.suffix.lower() in PHOTO_EXTENSIONS:
            discovered += 1
        if progress is not None:
            progress(DuplicateProgress("discover", discovered))

    if progress is not None:
        progress(DuplicateProgress("discover", 0))
    for root in resolved:
        result = scan(root, recursive=True, include_hidden=include_hidden,
                      on_file=on_file if progress is not None else None)
        report.skipped.extend((entry.path, entry.reason) for entry in result.skipped)
        for item in result.files:
            # The generic scanner excludes .safesort history but not quarantine.
            if QUARANTINE_FOLDER in item.path.relative_to(root).parts:
                continue
            if item.path.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            candidates.append(item.path)
            sizes.setdefault(item.size, []).append(item.path)
    report.photo_count = len(candidates)

    total_candidates = sum(len(paths) for paths in sizes.values() if len(paths) > 1)
    total_bytes = sum(size * len(paths) for size, paths in sizes.items() if len(paths) > 1)
    processed = 0
    bytes_processed = 0

    def on_bytes(count: int) -> None:
        nonlocal bytes_processed
        bytes_processed += count
        if progress is not None:
            progress(DuplicateProgress("hash", processed, total_candidates,
                                       bytes_processed, total_bytes))

    if progress is not None:
        progress(DuplicateProgress("hash", 0, total_candidates, 0, total_bytes))
    matching: dict[tuple[int, str], list[Photo]] = {}
    for size, photos in sizes.items():
        if len(photos) < 2:
            continue
        for path in photos:
            try:
                digest, info = hash_photo(path, on_bytes=on_bytes if progress is not None else None)
            except OSError as exc:
                report.skipped.append((path, str(exc)))
                continue
            finally:
                processed += 1
                if progress is not None:
                    progress(DuplicateProgress("hash", processed, total_candidates,
                                               bytes_processed, total_bytes))
            matching.setdefault((size, digest), []).append(
                Photo(path, size, digest, info.st_dev, info.st_ino, info.st_mtime_ns)
            )
    for (size, digest), photos in matching.items():
        if len(photos) > 1:
            report.groups.append(DuplicateGroup(digest, size, tuple(sorted(photos, key=lambda p: str(p.path)))))
    report.groups.sort(key=lambda group: str(group.photos[0].path))
    return report


def unchanged(photo: Photo) -> bool:
    """Verify that a planned duplicate is still the same file and bytes."""
    try:
        digest, info = hash_photo(photo.path)
    except OSError:
        return False
    return digest == photo.sha256 and _signature(info) == (
        photo.device, photo.inode, photo.size, photo.mtime_ns,
    )
