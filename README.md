# SafeSort

SafeSort is a lightweight, local Python file organizer and exact duplicate-photo finder. It previews changes, moves only approved files, records operations in SQLite, and can reverse recorded moves **without silently overwriting existing files**. It runs locally: no cloud account or network connection is required.

> **Important:** SafeSort is not a backup tool. Test on copies, keep independent backups of important photos, and review all proposed actions before moving or permanently deleting anything.

## Requirements and setup

- Python 3.10 or later.
- macOS, Linux, or Windows. Cross-platform behavior can vary by filesystem; test with disposable files first.

Clone or download the source code, then open a terminal in the SafeSort project directory.

**macOS or Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
```

**Windows PowerShell:**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
```

After installation, use the `safesort` command, or run `python -m safesort ...`.

## File organizer commands

```bash
safesort scan ~/Downloads                       # list immediate files
safesort scan ~/Downloads -r                    # list recursively
safesort scan ~/Downloads --hidden              # include dotfiles

safesort preview ~/Downloads                    # show a plan; move nothing
safesort preview ~/Downloads --rules my-rules.json -r

safesort apply ~/Downloads                      # preview moves and ask to confirm
safesort apply ~/Downloads --only 1,3,5-7      # choose preview numbers
safesort apply ~/Downloads --exclude 2         # exclude preview number 2
safesort apply ~/Downloads --yes                # skip confirmation; use carefully

safesort history ~/Downloads                    # view recent operation batches
safesort history ~/Downloads --limit 5
safesort undo ~/Downloads                       # confirm and undo latest batch
safesort undo ~/Downloads --yes                 # undo without confirmation
```

**Safest first run:** Create a temporary folder containing copies of a few files. Run `preview` before `apply`, verify the destinations, and test `undo`. Do not begin with irreplaceable files.

## Duplicate photo finder (v0.4.1)

SafeSort finds **byte-for-byte identical photos** using file size and SHA-256 hashes, even if filenames differ. It searches nested subfolders. Supported types include JPG, JPEG, PNG, GIF, WebP, BMP, TIFF, HEIC, and HEIF.

It does **not** detect visually similar photos that were resized, edited, or recompressed. The scanner does not follow symlinks. Hidden folders are excluded unless `--hidden` is supplied; internal `.safesort` and `.safesort-quarantine` folders and protected Apple Photos `.photoslibrary` packages are excluded.

### 1. Scan for duplicates (read-only)

```bash
safesort duplicates scan ~/Pictures
safesort duplicates scan ~/Pictures ~/Downloads /Volumes/ExternalDrive/Photos
safesort duplicates scan ~/Pictures --hidden
```

Multiple roots are supported for **scanning only**. Quarantine and bulk planning operate on **one selected root** at a time.

**Live progress indicator:** Interactive scans display how many photos have been found, how many candidates have been hashed, bytes processed, and elapsed time. The hashing percentage refers to **hashing only**, not to the entire folder discovery and scan. The display is automatically suppressed when output is redirected. To disable it:

```bash
safesort duplicates scan ~/Pictures --no-progress
safesort duplicates quarantine ~/Pictures --no-progress
```

Large photo libraries, external disks, and slow filesystem operations can still take time. The progress indicator does not accelerate scanning or provide a reliable time remaining estimate.

### 2. Quarantine with manual choices

```bash
safesort duplicates quarantine ~/Pictures
```

SafeSort displays each duplicate group and asks which numbered photo to **keep**. Press Enter to skip a group. Invalid input asks again rather than canceling earlier selections. After all choices are collected, SafeSort previews the moves and requests confirmation. The chosen keeper remains in its original folder; extra copies move to quarantine rather than being deleted.

An older noninteractive option is available:

```bash
safesort duplicates quarantine ~/Pictures --yes
```

**Warning:** This chooses the first photo in each group based on scan ordering and bypasses normal review and confirmation. It may disrupt your preferred folder organization. Use it only on disposable test data; prefer the reviewed bulk workflow below for important collections.

### 3. Plan smart bulk quarantine (read-only)

Instead of answering thousands of prompts, tell SafeSort which folders you prefer to **keep** photos in. You can repeat `--prefer-path` to set priority, highest first.

```bash
safesort duplicates quarantine ~/Pictures \
  --bulk \
  --preview \
  --prefer-path "Family" \
  --prefer-path "Sorted/Archive" \
  --plan-file ~/Desktop/safesort-review.json
```

In this example, `Family` and `Sorted/Archive` must already exist inside `~/Pictures`. Relative preference paths are interpreted relative to the selected scan root. Use your real folder names.

For each duplicate group:

- If exactly **one** copy is found in the highest-priority matching preferred folder, SafeSort proposes keeping it and quarantining the other copies.
- If multiple copies match that folder, or no preferred folder matches, it marks the group **needs review** and proposes **no moves** for that group.
- The result includes ready groups, groups needing review, proposed moves, and possible space recovery **only if copies are later permanently purged**.

The command writes a JSON plan. **No photos are moved or deleted.** SafeSort refuses to overwrite an existing plan file; use a new filename for a new plan.

**Important limitation:** The JSON plan is intended for inspection, not arbitrary manual editing of keep/quarantine decisions. When applying it, SafeSort validates the plan against its preferred-folder rules and rejects modified decisions that do not match those rules. To change preferences, regenerate a plan with different `--prefer-path` arguments. Manual selection remains available for ambiguous groups.

### 4. Apply a reviewed bulk quarantine plan

After reviewing the generated JSON and confirming the intended keeper folders:

```bash
safesort duplicates quarantine ~/Pictures \
  --bulk \
  --apply-plan ~/Desktop/safesort-review.json
```

SafeSort validates the plan, checks file identities and SHA-256 hashes again, excludes changed or missing groups, and displays a summary. It then requires an exact typed confirmation like `QUARANTINE 2` (where **2 is the number of proposed files**, not a batch ID).

Only eligible extra copies are **moved**, not deleted. SafeSort records operations in a separate SQLite journal for later restoration. The command can report skipped or failed moves; inspect those results rather than assuming every proposed move succeeded. If you modify files after generating the plan, regenerate it before applying.

### 5. List quarantined photos

```bash
safesort duplicates list ~/Pictures
safesort duplicates list ~/Pictures --batch 3
```

The first command lists the latest quarantine batch with pending photos; `--batch` selects a particular batch ID. Each available photo has a selection number. **Those numbers can change after a restore or purge**, so list again before selecting files.

Quarantined copies live under:

```text
<root>/.safesort-quarantine/batch-<unique-id>/<original-relative-path>
```

The folder is hidden by default. You may inspect its contents in Finder or File Explorer, but avoid renaming, moving, or editing quarantined photos outside SafeSort; doing so can prevent safe restoration and leave history records inconsistent.

### 6. Restore quarantined photos

Restore all eligible photos in the latest pending batch (existing behavior):

```bash
safesort duplicates restore ~/Pictures
```

Preview restoring specific selection numbers:

```bash
safesort duplicates restore ~/Pictures --files 1,3,5-7 --preview
```

Restore only those selected files, with confirmation:

```bash
safesort duplicates restore ~/Pictures --files 1,3,5-7
```

Or work with an older batch:

```bash
safesort duplicates list ~/Pictures --batch 3
safesort duplicates restore ~/Pictures --batch 3 --files 1,2 --preview
safesort duplicates restore ~/Pictures --batch 3 --files 1,2
```

SafeSort checks quarantine-file identity and SHA-256 content, refuses to overwrite occupied original paths, and skips unsafe, changed, or missing files. `--preview` moves nothing. **Restoration is not possible for files that have already been permanently deleted.**

### 7. Permanently purge reviewed copies (irreversible)

SafeSort also provides an **optional, interactive permanent-deletion command**:

```bash
safesort duplicates purge ~/Pictures --preview
safesort duplicates purge ~/Pictures
```

The interactive command lists eligible quarantined photos, asks which numbered files to delete, and requires a typed confirmation such as `DELETE 3`, where **3 is the quarantine batch ID, not the number of files**. Before deleting, it checks that the quarantined photo is unchanged and that an identical retained photo still exists outside quarantine. These are safeguards, **not a guarantee** against every filesystem or concurrent-change failure.

**Purge permanently deletes selected files without using the system Trash. SafeSort cannot undo a successful purge.** It operates on the latest eligible quarantine batch; it does not currently provide a `--batch` flag for purge. Maintain independent backups before using it on important photos.

### Safety features and limitations

- Read-only scans and bulk previews do not move or delete photos.
- Duplicate matching is based on **identical file bytes**, not visual similarity or filename.
- Apple Photos `.photoslibrary` packages are protected from scanning and moves.
- Bulk plans are revalidated before execution; unsafe, changed, or missing groups are skipped.
- Quarantine moves are journaled in `<root>/.safesort/duplicates.sqlite3`, separate from the general file-organizer history.
- Quarantine keeps the original relative folder structure and does **not** free disk space on its own.
- Restores refuse to overwrite existing destinations or restore modified quarantined files.
- Permanent purge requires file selection and explicit confirmation, and cannot be undone.
- Photos synced by cloud services, hard links, and filesystem behavior may affect space estimates or move safety. Avoid modifying managed photo libraries.
- Incomplete operations or interrupted history may require manual inspection. No tool can fully prevent concurrent-change races or hardware failures.

## Rules file

When `--rules` is omitted, SafeSort uses its **built-in rules** (images, documents, archives, and installers), even when run outside the repository. Use the included `rules.json` as a template, then pass `--rules path/to/your-rules.json` to use custom rules.

```json
{
  "rules": [
    {"name": "Images", "extensions": [".jpg", ".png"], "destination": "Images"},
    {"name": "PDFs", "extensions": [".pdf"], "destination": "Documents/PDFs"}
  ]
}
```

- The first matching rule wins; extensions are case-insensitive.
- Destinations must remain inside the selected folder: no absolute paths or `..`. The internal `.safesort` folder is reserved.
- Already-organized files are not moved again.
- Name collisions get numbered files (`photo (1).jpg`, `photo (2).jpg`, etc.).
- For the general file organizer, recursive scanning and hidden files are **opt-in**.

## History and undo behavior

On the first confirmed general `apply`, SafeSort creates `<selected-folder>/.safesort/history.sqlite3`. The database stores operation paths and basic file identity metadata, not your file contents. The journal stores **relative** move paths, so moving or renaming the entire folder on the same filesystem may preserve undo history. Copying it to another filesystem changes file identities and can prevent undo.

SafeSort excludes the `.safesort` history folder from general scans, even with `--hidden -r`.

- Every `apply` creates a batch **before** moving files. Individual successes, skips, and failures are recorded as operations finish.
- `undo` reverses the most recent batch not fully undone, latest moves first. Run it again to retry unresolved files after a partial undo.
- Undo checks file identity (device, inode, size, modification timestamp), refuses changed or replaced files, and never overwrites an existing destination.
- Undo cannot reverse moves performed before history support or moves made outside SafeSort.
- If the program crashes midway, unresolved `pending` journal rows require manual inspection.
- Same-volume moves use exclusive hard links and unlink where supported; cross-volume moves use exclusive-create copy and unlink. Filesystem restrictions may affect behavior.
- Directories created by `apply` are **not** deleted by `undo`, even if empty.

The general `apply`/`undo` journal and duplicate-quarantine journal are separate.

## Exit codes

- `0`: completed without move failures or skipped undo operations (or nothing to do).
- `1`: file move failure, partial undo, safety issue, canceled confirmation, invalid rule/file data, or missing history.
- `2`: invalid command-line arguments or invalid `history --limit`.

## Development and validation

```bash
python -m pytest -q
python -m compileall -q src/safesort
# Optional, if installed:
mypy --strict src/safesort
```

**Safety limits:** A file organizer cannot guarantee protection from concurrent changes, unexpected interruption, or storage failures. SafeSort is a local CLI utility, **not a backup solution**. Keep backups of important files.
