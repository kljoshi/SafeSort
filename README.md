# SafeSort

A lightweight, local Python file organizer that previews folder changes, moves only approved files, records operations in SQLite, and can **undo moves without silently overwriting anything**. No cloud account or network connection required.

## Requirements and setup

- Python 3.10 or later
- macOS, Linux, or Windows (test on copies before using important data)

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
```

On Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Commands

```bash
safesort scan ~/Downloads             # list immediate files
safesort scan ~/Downloads -r          # list recursively
safesort scan ~/Downloads --hidden    # include dotfiles

safesort preview ~/Downloads                         # show plan; change nothing
safesort preview ~/Downloads --rules my-rules.json -r # custom rules, recursively

safesort apply ~/Downloads                      # show moves, then ask confirmation
safesort apply ~/Downloads --only 1,3,5-7      # selected preview numbers
safesort apply ~/Downloads --exclude 2         # except preview number 2
safesort apply ~/Downloads --yes                # skip confirmation (use carefully)

safesort history ~/Downloads                    # show recent batches / undo status
safesort history ~/Downloads --limit 5
safesort undo ~/Downloads                       # confirm, then reverse latest batch
safesort undo ~/Downloads --yes                 # reverse without prompting
```

You can also run `python -m safesort ...` after installing.

**Safest first run:** Make a temporary folder containing copies of JPG/PDF files. Run `preview` before `apply`, examine all destination names, and try `undo` on the temporary folder. Do not start with an irreplaceable folder.

## Duplicate Photo Finder (v0.3.0)

SafeSort can detect **exact duplicate photos** across folders and subfolders using SHA-256 hashes, even when the files have different names.

### Scan for duplicate photos

Scan one root folder and all its subfolders:

```bash
safesort duplicates scan ~/Pictures
```

Scan multiple unrelated folders, including external drives:

```bash
safesort duplicates scan ~/Pictures ~/Downloads /Volumes/ExternalDrive/Photos
```

Scanning supports multiple root folders. **Quarantine currently supports only one root folder and its subfolders per operation.**

### Quarantine duplicate photos

Quarantined photos are stored inside the hidden .safesort-quarantine folder under the selected root directory. SafeSort preserves their original relative folder paths and records their locations for restoration.

```bash
safesort duplicates quarantine ~/Pictures
```

SafeSort displays each duplicate group and lets you choose one photo to keep. All other copies in that group are proposed for quarantine. You can skip any group without moving its files. SafeSort displays the proposed moves and requires confirmation before proceeding.

### Restore quarantined photos

```bash
safesort duplicates restore ~/Pictures
```

SafeSort attempts to restore files from the latest quarantine batch to their original locations. A restore may be refused if a file has changed, been deleted, or its original path is occupied.

### Safety features and limitations

- Scans include nested subfolders and never move or delete files.
- Duplicate detection compares file contents; it does not rely on filenames.
- Apple Photos libraries (`.photoslibrary`) are excluded and protected from file moves.
- Quarantine requires selecting copies and confirming the proposed moves.
- Quarantine history supports restoration when safe.
- No automatic permanent deletion is included.
- Visually similar, resized, or recompressed photos are **not** considered exact duplicates unless their file bytes are identical.

**Disk space:** Quarantined files still occupy disk space. Review them before manually deleting any unwanted copies. Once deleted, SafeSort cannot restore those copies. Moving files to the system Trash may not free space until the Trash is emptied.

## Rules file

When `--rules` is omitted, SafeSort uses its **built-in rules** (images, documents, archives, and installers), even when run outside the repository. Use the included `rules.json` as a template for your own rules, then pass `--rules path/to/your-rules.json` to use them.

```json
{
  "rules": [
    {"name": "Images", "extensions": [".jpg", ".png"], "destination": "Images"},
    {"name": "PDFs", "extensions": [".pdf"], "destination": "Documents/PDFs"}
  ]
}
```

- The first matching rule wins. Extensions are case-insensitive.
- Destination directories must stay within the selected folder: no absolute paths or `..`. The internal `.safesort` folder is reserved.
- Already-organized files are not moved again.
- Collisions get numbered names (`photo (1).jpg`, `photo (2).jpg`, etc.).
- For the general file organizer, recursive scanning and including hidden files are **opt-in**.

## History and undo behavior

On the first confirmed `apply`, SafeSort creates `<selected-folder>/.safesort/history.sqlite3`. The database contains operation paths and basic file identity metadata, not your file contents. The journal stores **relative** move paths, so renaming or moving the whole folder on the same filesystem keeps its undo history usable. Copying the folder to a different filesystem changes file identities and may prevent undo.

SafeSort excludes any `.safesort` history folder from general scans, even with `--hidden -r`.

- Every `apply` creates a numbered batch **before** moving files. Individual successes, skips, and failures are saved as operations finish.
- `undo` reverses the **most recent not-fully-undone batch**, latest files first. Run it again after a partial undo to retry unresolved files.
- Undo checks file identity (device, inode, size, modification timestamp), refuses changed or replaced files, and never overwrites an existing path.
- Undo only knows about `apply` runs recorded by versions with history support; it cannot undo older, unrecorded file moves.
- If the program crashes midway, unresolved `pending` journal rows require manual inspection.
- Same-volume moves use exclusive hard links and unlink; cross-volume moves use exclusive-create copy and unlink. Filesystem restrictions may affect these operations.
- Folders created by `apply` are **not deleted by `undo`**, even when empty.

## Exit codes

- `0`: completed without move failures or skipped undo operations (or nothing to do)
- `1`: file move failure, partial undo, safety problem, cancelled confirmation, invalid rule/file data, or missing history
- `2`: invalid command-line arguments or invalid `history --limit`

## Development and validation

```bash
python -m pytest -q
python -m compileall -q src/safesort
# Optional if installed:
mypy --strict src/safesort
```

**Safety limits:** No file organizer can guarantee protection against concurrent changes by other processes or filesystem failures. SafeSort is a local CLI utility with conservative checks, **not a backup solution**. Keep backups of important folders.
