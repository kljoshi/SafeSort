# SafeSort duplicate-photo safety

## Apple Photos libraries

SafeSort skips any folder whose name ends with `.photoslibrary`, even inside a
recursively scanned folder or when `--hidden` is supplied. It prints a skipped
item with the reason `protected Apple Photos library`.

You cannot scan or quarantine using a `.photoslibrary` package (or any folder
inside it) as the scan root. SafeSort also refuses individual moves into or
out of these packages, including attempted restores. Apple Photos manages the
originals, derivatives, and databases inside its library; use the Photos app
to review duplicates held there.

This protection does not apply to ordinary exported photos outside the Photos
library. For example, separately exported JPEGs in `~/Pictures/Exports/` can
be scanned and quarantined as usual.

## Quarantine is not deletion

`duplicates scan` changes no files. `duplicates quarantine` moves extra copies
into `<root>/.safesort-quarantine/` for review. Those files continue to use
storage. `duplicates restore` reverses supported quarantine moves.

SafeSort does **not** automatically delete quarantined files or currently
provide a `duplicates purge` command. After independently reviewing copies
and keeping a backup, you can manually delete unnecessary quarantined files.
Doing so prevents SafeSort from restoring those files; stale journal entries
may remain. Moving files to macOS Trash does not normally reclaim their space
until the Trash is emptied. Actual savings can differ from estimates due to
filesystem snapshots, hard links, and storage optimization.

**Never manipulate files within a `.photoslibrary` package directly.**
