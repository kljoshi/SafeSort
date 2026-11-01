"""Conservative, read-only proposed decisions for duplicate-photo quarantine.

Plan application is separately implemented in safesort.bulk_apply.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from safesort.duplicates import DuplicateGroup, DuplicateScan, Photo
from safesort.errors import ScanError
from safesort.protected import is_photos_library_path

PLAN_FORMAT = "safesort-duplicate-quarantine-plan"
PLAN_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GroupDecision:
    group: DuplicateGroup
    keeper: Photo | None
    reason: str

    @property
    def proposed_moves(self) -> tuple[Photo, ...]:
        if self.keeper is None:
            return ()
        return tuple(photo for photo in self.group.photos if photo != self.keeper)


@dataclass(frozen=True)
class BulkPlan:
    root: Path
    photo_count: int
    preferred_folders: tuple[Path, ...]
    groups: tuple[GroupDecision, ...]

    @property
    def ready_groups(self) -> int:
        return sum(decision.keeper is not None for decision in self.groups)

    @property
    def needs_review(self) -> int:
        return len(self.groups) - self.ready_groups

    @property
    def proposed_copies(self) -> int:
        return sum(len(decision.proposed_moves) for decision in self.groups)

    @property
    def potential_bytes(self) -> int:
        return sum(len(decision.proposed_moves) * decision.group.size for decision in self.groups)


def preferred_folders(root: Path, requested: list[Path]) -> tuple[Path, ...]:
    """Validate ordered keep preferences; reject path escapes and symlinks."""
    root = root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ScanError(f"Not a real folder: {root}")
    resolved: list[Path] = []
    for raw in requested:
        path = raw.expanduser()
        if not path.is_absolute():
            path = root / path
        # Test the path components before .resolve() hides symlinks.
        probe = path
        while True:
            if probe.is_symlink():
                raise ScanError(f"Preference cannot include a symlink: {raw}")
            if probe == probe.parent:
                break
            probe = probe.parent
        path = path.resolve()
        if not path.is_relative_to(root):
            raise ScanError(f"Preferred folder is outside scan root: {raw}")
        if is_photos_library_path(path):
            raise ScanError(f"Protected Apple Photos library cannot be preferred: {raw}")
        if not path.is_dir():
            raise ScanError(f"Preferred folder does not exist: {raw}")
        if path not in resolved:
            resolved.append(path)
    return tuple(resolved)


def decide_group(group: DuplicateGroup, preferences: tuple[Path, ...]) -> GroupDecision:
    """Keep exactly one highest-priority matching photo; skip all ambiguous groups."""
    for folder in preferences:
        matches = [photo for photo in group.photos if photo.path.is_relative_to(folder)]
        if len(matches) == 1:
            return GroupDecision(group, matches[0], f"unique copy in preferred folder: {folder}")
        if len(matches) > 1:
            return GroupDecision(group, None, f"multiple copies in preferred folder: {folder}")
    return GroupDecision(group, None, "no copy in a preferred folder")


def create_bulk_plan(report: DuplicateScan, preferences: tuple[Path, ...]) -> BulkPlan:
    if len(report.roots) != 1:
        raise ScanError("Bulk quarantine planning requires exactly one scan root")
    root = report.roots[0]
    if any(not folder.is_relative_to(root) for folder in preferences):
        raise ScanError("Preferred folder is outside scan root")
    return BulkPlan(root, report.photo_count, preferences,
                    tuple(decide_group(group, preferences) for group in report.groups))


def plan_to_dict(plan: BulkPlan) -> dict[str, object]:
    """Serialize planned choices and scan identities for future revalidation.

    The executor MUST distrust this JSON, validate every path and hash,
    and recheck retained copies immediately before moving anything.
    """
    def rel(path: Path) -> str:
        return path.relative_to(plan.root).as_posix()

    return {
        "format": PLAN_FORMAT,
        "schema_version": PLAN_SCHEMA_VERSION,
        "root": str(plan.root),
        "preferred_folders": [str(folder) for folder in plan.preferred_folders],
        "summary": {
            "photos_scanned": plan.photo_count,
            "duplicate_groups": len(plan.groups),
            "ready_groups": plan.ready_groups,
            "needs_review": plan.needs_review,
            "proposed_copies": plan.proposed_copies,
            "potential_bytes_if_later_purged": plan.potential_bytes,
        },
        "groups": [
            {
                "number": number,
                "sha256": decision.group.sha256,
                "size": decision.group.size,
                "decision": "ready" if decision.keeper is not None else "needs_review",
                "reason": decision.reason,
                "keeper": rel(decision.keeper.path) if decision.keeper is not None else None,
                "quarantine": [rel(photo.path) for photo in decision.proposed_moves],
                "photos": [
                    {
                        "path": rel(photo.path),
                        "size": photo.size,
                        "sha256": photo.sha256,
                        # Use strings for large filesystem IDs (Windows/exFAT).
                        "device": str(photo.device),
                        "inode": str(photo.inode),
                        "mtime_ns": str(photo.mtime_ns),
                    }
                    for photo in decision.group.photos
                ],
            }
            for number, decision in enumerate(plan.groups, 1)
        ],
    }


def save_bulk_plan(plan: BulkPlan, destination: Path) -> Path:
    """Write only an explicitly requested JSON plan; never overwrite a file."""
    destination = destination.expanduser().absolute()
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ScanError(f"Plan parent folder does not exist or is a symlink: {destination.parent}")
    try:
        # x mode atomically rejects existing files, including symlinks.
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(plan_to_dict(plan), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise ScanError(f"Plan file already exists (will not overwrite): {destination}") from exc
    except OSError as exc:
        raise ScanError(f"Cannot write plan file: {exc}") from exc
    return destination
