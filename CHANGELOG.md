# Changelog

## 0.4.1 — Work in progress: scan progress and input safety

- Added interactive live progress during photo discovery and SHA-256 hashing.
- Show candidate photo count, bytes hashed, and elapsed time for long scans.
- Added `--no-progress` to suppress interactive status output.
- Invalid quarantine keeper choices now re-prompt instead of aborting all selections.
- No changes to duplicate classification, quarantine move semantics, or deletion behavior.

## 0.4.0 — Reviewed duplicate purge

- Added `duplicates list` to inspect files in the latest restorable quarantine batch.
- Added `duplicates purge <folder> --preview` to examine deletion candidates without changes.
- Added interactive `duplicates purge <folder>` with explicit selection and `DELETE <batch>` confirmation.
- Verify retained, byte-identical photo exists outside quarantine before deleting any copy.
- Verify each quarantined photo's recorded hash and file identity before deletion.
- Journal purge intent before unlinking and record irreversible deletions afterward.
- Preserve an interrupted purge as a manual-review condition rather than retrying automatically.

## 0.3.0 — Exact duplicate photo finder

- Added `duplicates scan` to find SHA-256 identical photos recursively in one
  or more separately selected folders.
- Added interactive `duplicates quarantine` to choose the photo to keep for
  each group, preview the moves, and move extras to a review folder.
- Added `duplicates restore`, using a separate SQLite journal, to reverse the
  latest quarantine batch without overwriting existing or modified files.
- Excluded the quarantine and internal journal folders from recursive scanning.
- Added cross-platform regression tests, including large Windows file IDs.
- Also includes the v0.2.0 Windows path test and SQLite file-ID fixes.

## 0.2.0 — Undo and safety update

- Added SQLite apply history stored per folder under `.safesort`.
- Added `history` and guarded `undo` CLI commands.
- Journal plans before applying, then write each individual outcome.
- Verify moved-file identity before reversing; refuse destination conflicts.
- Ensure the history folder is excluded from scanning and cannot be targeted
  by file-organization rules.
- Replace overwrite-prone `shutil.move` with non-overwriting hard-link moves,
  with exclusive-create copying for cross-filesystem cases.
- Refuse moves through symlinked destination folders.
- Add integration and safety regression tests and a GitHub Actions test matrix.

## 0.1.0 — Original project

- Scanner, JSON rules, planner, subset selection, and file moves.
- Preview-only and confirmed apply workflows.
