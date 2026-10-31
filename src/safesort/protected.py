"""Identify managed photo-library packages that SafeSort must never modify.

Apple Photos stores its internal database, originals, and derived media inside
``*.photoslibrary`` packages. These files must be managed via Photos, not a
file organizer. Name matching is intentionally case-insensitive, even when the
filesystem itself is case-sensitive.
"""

from __future__ import annotations

from pathlib import Path


def is_photos_library_path(path: Path) -> bool:
    """Whether a path is an Apple Photos library package or anything inside it.

    This checks the whole path rather than just the leaf name, so callers
    cannot accidentally move a file from ``Library.photoslibrary/originals``.
    """
    return any(part.casefold().endswith(".photoslibrary") for part in path.parts)
