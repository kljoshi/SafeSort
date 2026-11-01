"""Validate and apply reviewed duplicate-quarantine JSON plans.

Plan files are untrusted input, including when created by SafeSort. All ready
entries must be justified by the original preferred-folder rules, and every
photo is rehashed before a group is moved. No deletion occurs here.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from safesort.bulk_plan import (
    PLAN_FORMAT, PLAN_SCHEMA_VERSION, decide_group, preferred_folders,
)
from safesort.duplicate_history import DuplicateHistory
from safesort.duplicates import (
    PHOTO_EXTENSIONS, QUARANTINE_FOLDER, DuplicateGroup, Photo, unchanged,
)
from safesort.errors import SafeSortError
from safesort.executor import _path_has_symlink_parent, execute_move
from safesort.models import MoveResult, MoveStatus, PlannedMove
from safesort.protected import is_photos_library_path

MAX_PLAN_BYTES = 32 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DECIMAL = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True)
class ApprovedGroup:
    keeper: Photo
    extras: tuple[Photo, ...]


def _invalid(message: str) -> SafeSortError:
    return SafeSortError(f"Invalid bulk plan: {message}")


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise _invalid(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise _invalid(f"{label} must be an array")
    return value


def _int(value: object, label: str, *, nonnegative: bool = True) -> int:
    if type(value) is not int or (nonnegative and value < 0):
        raise _invalid(f"{label} must be a nonnegative integer")
    return value


def _decimal(value: object, label: str) -> int:
    if not isinstance(value, str) or len(value) > 128 or not DECIMAL.fullmatch(value):
        raise _invalid(f"{label} must be a decimal string")
    return int(value)


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise _invalid(f"{label} must be a lowercase SHA-256 digest")
    return value


def _relative_photo(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise _invalid(f"{label} must be a relative POSIX-style file path")
    # Reject normalization tricks, root escapes and internal/protected folders.
    chunks = raw.split("/")
    if (raw.startswith("/") or any(chunk in {"", ".", ".."} for chunk in chunks)
            or chunks[0].endswith(":")):
        raise _invalid(f"unsafe path: {label}")
    relative = Path(raw)
    if relative.is_absolute() or relative.as_posix() != raw:
        raise _invalid(f"noncanonical path: {label}")
    if any(part in {".safesort", QUARANTINE_FOLDER} for part in relative.parts):
        raise _invalid(f"internal SafeSort directory in {label}")
    target = root / relative
    if target.suffix.lower() not in PHOTO_EXTENSIONS or is_photos_library_path(target):
        raise _invalid(f"protected or nonphoto file path: {label}")
    if target.is_symlink() or _path_has_symlink_parent(target):
        raise _invalid(f"symlinked file path: {label}")
    return target


def load_reviewed_plan(root: Path, plan_file: Path) -> tuple[list[ApprovedGroup], int]:
    """Strictly load reviewed plans; never trust JSON as move authorization alone."""
    candidate_root = root.expanduser()
    if candidate_root.is_symlink() or not candidate_root.is_dir():
        raise _invalid("scan root must be a real directory")
    root = candidate_root.resolve()
    if is_photos_library_path(root):
        raise _invalid("cannot operate inside a Photos library")
    source = plan_file.expanduser().absolute()
    if source.is_symlink() or _path_has_symlink_parent(source):
        raise _invalid("plan path cannot contain symlinks")
    try:
        if source.stat().st_size > MAX_PLAN_BYTES:
            raise _invalid("plan file is larger than 32 MB")
        with source.open("r", encoding="utf-8") as stream:
            doc = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _invalid(f"cannot read JSON plan: {exc}") from exc
    doc = _mapping(doc, "plan")
    if doc.get("format") != PLAN_FORMAT or type(doc.get("schema_version")) is not int \
            or doc["schema_version"] != PLAN_SCHEMA_VERSION:
        raise _invalid("unsupported plan format or schema version")
    if doc.get("root") != str(root):
        raise _invalid("plan root does not match the requested folder")

    raw_preferences = _array(doc.get("preferred_folders"), "preferred_folders")
    if any(not isinstance(p, str) or not Path(p).is_absolute() for p in raw_preferences):
        raise _invalid("preferred_folders must contain absolute paths")
    preferences = preferred_folders(root, [Path(p) for p in raw_preferences])
    if tuple(str(folder) for folder in preferences) != tuple(raw_preferences):
        raise _invalid("preferred folder list is not canonical or contains duplicates")

    summary = _mapping(doc.get("summary"), "summary")
    groups = _array(doc.get("groups"), "groups")
    if _int(summary.get("duplicate_groups"), "duplicate_groups") != len(groups):
        raise _invalid("duplicate_groups count disagrees with plan")
    _int(summary.get("photos_scanned"), "photos_scanned")

    ready: list[ApprovedGroup] = []
    seen_paths: set[Path] = set()
    seen_hashes: set[tuple[str, int]] = set()
    ready_count = review_count = proposed = total_bytes = 0
    for index, value in enumerate(groups, 1):
        record = _mapping(value, f"group {index}")
        if _int(record.get("number"), "group number") != index:
            raise _invalid("group numbers must be consecutive")
        digest = _hash(record.get("sha256"), f"group {index} sha256")
        size = _int(record.get("size"), f"group {index} size")
        if (digest, size) in seen_hashes:
            raise _invalid("duplicate group content appears more than once")
        seen_hashes.add((digest, size))
        files = _array(record.get("photos"), f"group {index} photos")
        if len(files) < 2:
            raise _invalid("every duplicate group must have at least two photos")
        photos: list[Photo] = []
        for entry in files:
            entry = _mapping(entry, "photo")
            path = _relative_photo(root, entry.get("path"), "photo path")
            if path in seen_paths:
                raise _invalid("photo path appears in multiple groups")
            seen_paths.add(path)
            if (_int(entry.get("size"), "photo size") != size
                    or _hash(entry.get("sha256"), "photo sha256") != digest):
                raise _invalid("photo content signature disagrees with its group")
            photos.append(Photo(
                path, size, digest,
                _decimal(entry.get("device"), "device"),
                _decimal(entry.get("inode"), "inode"),
                _decimal(entry.get("mtime_ns"), "mtime_ns"),
            ))
        if not isinstance(record.get("reason"), str):
            raise _invalid("group reason is missing")
        decision = decide_group(DuplicateGroup(digest, size, tuple(photos)), preferences)
        keep_raw = record.get("keeper")
        extra_raw = _array(record.get("quarantine"), "quarantine")
        if decision.keeper is None:
            review_count += 1
            if record.get("decision") != "needs_review" or keep_raw is not None or extra_raw:
                raise _invalid("ambiguous group cannot authorize moves")
            continue
        ready_count += 1
        keeper = decision.keeper
        extra = tuple(photo for photo in photos if photo.path != keeper.path)
        expected_extra = [photo.path.relative_to(root).as_posix() for photo in extra]
        if (record.get("decision") != "ready"
                or keep_raw != keeper.path.relative_to(root).as_posix()
                or extra_raw != expected_extra):
            raise _invalid("group keep/quarantine choices disagree with preferred-folder rule")
        ready.append(ApprovedGroup(keeper, extra))
        proposed += len(extra)
        total_bytes += len(extra) * size

    required = {
        "ready_groups": ready_count, "needs_review": review_count,
        "proposed_copies": proposed, "potential_bytes_if_later_purged": total_bytes,
    }
    for label, expected in required.items():
        if _int(summary.get(label), label) != expected:
            raise _invalid(f"{label} disagrees with group decisions")
    if summary["photos_scanned"] < len(seen_paths):
        raise _invalid("photos_scanned count is less than plan entries")
    return ready, review_count


def _group_current(group: ApprovedGroup) -> bool:
    return all(unchanged(photo) for photo in (group.keeper, *group.extras))


def apply_reviewed_plan(root: Path, plan_path: Path, *, read_confirmation) -> int:
    """Validate, explicitly confirm, and journal only safe quarantine moves."""
    groups, review_count = load_reviewed_plan(root, plan_path)
    root = root.expanduser().resolve()
    if not groups:
        print(f"No eligible groups; {review_count} need manual review. No files changed.")
        return 0

    print("Verifying all ready groups against current file contents...")
    progress = _BulkProgress()
    eligible: list[ApprovedGroup] = []
    stale = 0
    for index, group in enumerate(groups, 1):
        if _group_current(group):
            eligible.append(group)
        else:
            stale += 1
        progress.update("Verifying", index, len(groups))
    progress.finish()
    proposed = sum(len(group.extras) for group in eligible)
    bytes_total = sum(photo.size for group in eligible for photo in group.extras)
    print(f"Verified groups: {len(eligible)}; changed/missing groups skipped: {stale}; "
          f"need review: {review_count}")
    print(f"Ready to quarantine: {proposed} photo(s), {bytes_total:,} bytes. "
          "Files are moved, NOT deleted.")
    for group in eligible[:5]:
        for photo in group.extras[:3]:
            print(f"  {photo.path.relative_to(root)} (keeping {group.keeper.path.relative_to(root)})")
    if len(eligible) > 5:
        print(f"  ... {len(eligible)-5} more groups described in your JSON plan")
    if not proposed:
        print("No eligible files to move. No files changed.")
        return 1 if stale else 0
    try:
        response = read_confirmation(f"Type QUARANTINE {proposed} to move these photos: ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No files were changed.")
        return 1
    if response.strip() != f"QUARANTINE {proposed}":
        print("Cancelled. No files were changed.")
        return 1

    batch_name = f"batch-{uuid4().hex[:12]}"
    work: list[tuple[ApprovedGroup, PlannedMove, Photo]] = []
    for group in eligible:
        for photo in group.extras:
            relative = photo.path.relative_to(root)
            destination = root / QUARANTINE_FOLDER / batch_name / relative
            move = PlannedMove(len(work) + 1, photo.path, destination, "duplicate-quarantine")
            work.append((group, move, photo))

    moved = skipped = failed = 0
    with DuplicateHistory(root, create=True) as history:
        batch_id, rows = history.begin([(move, photo.sha256, photo.size)
                                        for _group, move, photo in work])
        print(f"Quarantine history batch #{batch_id} saved.")
        # Check a group again after confirmation, before its first move.
        current_group: ApprovedGroup | None = None
        group_safe = False
        for index, ((group, move, photo), row_id) in enumerate(zip(work, rows), 1):
            if group is not current_group:
                current_group = group
                group_safe = _group_current(group)
            if not group_safe or not unchanged(group.keeper) or not unchanged(photo):
                result = MoveResult(move, MoveStatus.SKIPPED,
                                    "photo or keeper changed since planning")
            else:
                result = execute_move(move)
            history.record_move(row_id, result)
            if result.status is MoveStatus.MOVED:
                moved += 1
            elif result.status is MoveStatus.SKIPPED:
                skipped += 1
            else:
                failed += 1
            if result.status is not MoveStatus.MOVED or not sys.stderr.isatty():
                progress.finish()
                print(f"  {result.status.value} {photo.path.relative_to(root)}"
                      + (f" ({result.detail})" if result.detail else ""))
            progress.update("Quarantining", index, len(work))
        progress.finish()
    print(f"Bulk quarantine: {moved} moved, {skipped} skipped, {failed} failed; "
          f"{stale} stale group(s) excluded before confirmation.")
    return 1 if skipped or failed or stale else 0


class _BulkProgress:
    """Lightweight interactive progress for hash-heavy validation and moves."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.last = 0.0
        self.width = 0

    def update(self, label: str, current: int, total: int) -> None:
        if not sys.stderr.isatty():
            return
        now = time.monotonic()
        if current != total and now - self.last < 0.25:
            return
        self.last = now
        elapsed = int(now - self.started)
        percentage = int(100 * current / total) if total else 100
        line = f"{label}: {current:,}/{total:,} ({percentage}%) | elapsed {elapsed // 60:02d}:{elapsed % 60:02d}"
        sys.stderr.write("\r" + line + " " * max(0, self.width - len(line)))
        sys.stderr.flush()
        self.width = len(line)

    def finish(self) -> None:
        if self.width:
            sys.stderr.write("\r" + " " * self.width + "\r")
            sys.stderr.flush()
            self.width = 0
