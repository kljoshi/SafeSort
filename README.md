# SafeSort

A lightweight, local Python file organizer that previews folder changes, moves only
approved files, records each operation in SQLite, and can **undo moves without
silently overwriting anything**. No cloud account or network connection required.

## Requirements and setup

- Python 3.10 or later
- macOS, Linux, or Windows (test on copies before using important data)

```bash
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
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

**Safest first run:** make a temporary folder containing copies of some JPG/PDF
files. Run `preview` before `apply`, examine all destination names, and try
`undo` on the temporary folder. Do not start with an irreplaceable folder.

## Rules file

When `--rules` is omitted, SafeSort uses its **built-in rules** (images,
documents, archives, and installers), even when run outside the repository.
Use the included `rules.json` as a template for your own rules, then pass
`--rules path/to/your-rules.json` to use them.

```json
{
  "rules": [
    {"name": "Images", "extensions": [".jpg", ".png"], "destination": "Images"},
    {"name": "PDFs", "extensions": [".pdf"], "destination": "Documents/PDFs"}
  ]
}
```

- The first matching rule wins. Extensions are case-insensitive.
- Destination directories must stay within the selected folder: no absolute
  paths or `..`. The internal `.safesort` folder is reserved.
- Already-organized files are not moved again.
- Collisions get numbered names (`photo (1).jpg`, `photo (2).jpg`, etc.).
- Scanning recursively and including hidden files are **opt-in**.

## History and undo behavior

On the first confirmed `apply`, SafeSort creates
`<selected-folder>/.safesort/history.sqlite3`. The SQLite database contains
operation paths and basic file identity metadata, not the contents of your files.
The journal stores **relative** move paths, so renaming or moving the whole
folder on the same filesystem keeps its undo history usable. Copying the folder
to a different filesystem changes file identities and may prevent undo.
SafeSort excludes any `.safesort` history folder from scans, even with
`--hidden -r`.

- Every `apply` creates a numbered batch **before** moving files. Individual
  successes, skips, and failures are saved as operations finish.
- `undo` reverses the **most recent not-fully-undone batch**, latest files first.
  Run it again after a partial undo to retry unresolved files. When the latest
  batch is fully undone, another `undo` can reverse the previous batch.
- Undo checks file identity (device, inode, size, modification timestamp),
  refuses changed/replaced files, and never overwrites an existing path.
  Move conflicting new files aside manually, then retry if you wish.
- Undo only knows about `apply` runs done with **this updated version**; it
  cannot undo previous runs of the original project that had no history.
- If the program crashes midway, unresolved `pending` journal rows require
  manual inspection; it deliberately does not guess what happened.
- Some operating systems/filesystems limit hard links or metadata preservation.
  Same-volume moves use exclusive hard links + unlink; cross-volume moves use
  exclusive-create copy + unlink. Both avoid replacing a destination.
- Folders created by `apply` are **not deleted by `undo`**, even when empty.
  This is deliberate: removing a directory might affect unrelated content.

## Exit codes

- `0`: completed without move failures / skipped undo operations (or nothing to do)
- `1`: file move failure, partial undo, safety problem, cancelled confirmation,
  invalid rule/file data, or missing history
- `2`: invalid command-line arguments or invalid `history --limit`

## Development and validation

```bash
pytest -q
python -m compileall -q src/safesort
# Optional if installed:
mypy --strict src/safesort
```

## Roadmap

- [x] Phase 0: project skeleton
- [x] Phase 1: models and scanner (`scan`)
- [x] Phase 2: JSON rules and planner (`preview`)
- [x] Phase 3: executor with conflict handling (`apply`)
- [x] Phase 4: SQLite history and guarded `undo`
- [x] Phase 5: CLI history display, exit codes, friendly errors
- [x] Phase 6: initial no-overwrite, symlink, interrupted-history, and identity tests
- [ ] Future work: broader cross-platform testing, interruption recovery wizard,
      filesystem-specific edge-case review, packaging / release automation

**Safety limits:** No file organizer can provide a perfect guarantee against
concurrent modifications by other processes or filesystem failure. This is a
local CLI utility with conservative checks, not a backup solution. Keep backups
of important folders.
