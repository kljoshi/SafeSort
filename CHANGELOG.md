# Changelog

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
