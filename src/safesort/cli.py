"""Command-line interface for SafeSort."""

from __future__ import annotations

import argparse
import sys
from uuid import uuid4
from collections.abc import Sequence
from pathlib import Path

from safesort.errors import SafeSortError
from safesort.duplicates import (
    QUARANTINE_FOLDER, DuplicateGroup, DuplicateScan, Photo, find_duplicates, unchanged,
)
from safesort.duplicate_history import DuplicateHistory, quarantine_identity_matches
from safesort.executor import execute_move
from safesort.history import HistoryDB, matches_identity
from safesort.models import MoveStatus, PlannedMove, ScanResult
from safesort.planner import plan as build_plan
from safesort.rules import load_rules
from safesort.scanner import scan
from safesort.selection import parse_indexes, plan_selected

MAX_UNMATCHED_SHOWN = 10


def format_size(num_bytes: int) -> str:
    """Format a byte count like '1.2 KB'."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[index]}"


def _confirm(prompt: str) -> bool:
    """Ask a yes/no question. Anything except y/yes (or no input) means no."""
    try:
        answer = input(prompt)
    except EOFError:
        print("\nNo input available. Use --yes to skip the prompt.", file=sys.stderr)
        return False
    except KeyboardInterrupt:
        print()
        return False
    return answer.strip().lower() in {"y", "yes"}


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    parser.add_argument("--hidden", action="store_true", help="include hidden (dot) files")


def _add_rules_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="rules file (default: built-in rules)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safesort",
        description="Organize files safely and find exact duplicate photos.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="list the files in a folder")
    scan_p.add_argument("folder", type=Path)
    _add_scan_options(scan_p)

    preview_p = sub.add_parser("preview", help="show what would be moved (changes nothing)")
    preview_p.add_argument("folder", type=Path)
    _add_rules_option(preview_p)
    _add_scan_options(preview_p)

    apply_p = sub.add_parser("apply", help="move the files (asks for confirmation first)")
    apply_p.add_argument("folder", type=Path)
    _add_rules_option(apply_p)
    _add_scan_options(apply_p)
    choice = apply_p.add_mutually_exclusive_group()
    choice.add_argument(
        "--only", metavar="NUMBERS", help="move only these preview numbers, e.g. 1,3,5-7"
    )
    choice.add_argument(
        "--exclude", metavar="NUMBERS", help="move everything except these preview numbers"
    )
    apply_p.add_argument(
        "-y", "--yes", action="store_true", help="don't ask for confirmation"
    )

    undo_p = sub.add_parser("undo", help="reverse the latest unfinished apply")
    undo_p.add_argument("folder", type=Path)
    undo_p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    history_p = sub.add_parser("history", help="show recent apply and undo history")
    history_p.add_argument("folder", type=Path)
    history_p.add_argument("--limit", type=int, default=10, help="how many batches to list")
    duplicates_p = sub.add_parser("duplicates", help="find or quarantine exact duplicate photos")
    dup_sub = duplicates_p.add_subparsers(dest="duplicates_command", required=True)
    dup_scan = dup_sub.add_parser("scan", help="recursively scan one or more photo roots")
    dup_scan.add_argument("folders", nargs="+", type=Path)
    dup_scan.add_argument("--hidden", action="store_true", help="include hidden photos")
    dup_quarantine = dup_sub.add_parser("quarantine", help="review and move extra copies to quarantine")
    dup_quarantine.add_argument("folder", type=Path)
    dup_quarantine.add_argument("--hidden", action="store_true", help="include hidden photos")
    dup_quarantine.add_argument("--yes", action="store_true", help="keep first path in each group without prompting")
    dup_restore = dup_sub.add_parser("restore", help="restore the latest quarantined batch")
    dup_restore.add_argument("folder", type=Path)
    dup_restore.add_argument("--yes", action="store_true", help="restore without confirmation")
    return parser


def _report_skipped(result: ScanResult) -> None:
    if result.skipped:
        print(f"{len(result.skipped)} item(s) skipped:", file=sys.stderr)
        for item in result.skipped:
            print(f"  {item.path}: {item.reason}", file=sys.stderr)


def _describe(move: PlannedMove, root: Path) -> str:
    source = move.source.relative_to(root)
    destination = move.destination.relative_to(root)
    return f"{source} -> {destination}"


def cmd_scan(args: argparse.Namespace) -> int:
    result = scan(args.folder, recursive=args.recursive, include_hidden=args.hidden)

    print(f"Scanned {result.root}")
    if result.files:
        names = [str(f.path.relative_to(result.root)) for f in result.files]
        width = min(max(len(n) for n in names), 50)
        for name, info in zip(names, result.files):
            print(f"  {name:<{width}}  {format_size(info.size):>9}  {info.extension}")
    print(f"{len(result.files)} file(s) found")

    _report_skipped(result)
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    result = scan(args.folder, recursive=args.recursive, include_hidden=args.hidden)
    the_plan = build_plan(result.files, rules, result.root)

    print(f"Preview for {result.root}")
    if the_plan.moves:
        width = len(str(len(the_plan.moves)))
        for move in the_plan.moves:
            flag = "  [renamed]" if move.renamed else ""
            print(f"  {move.index:>{width}}. {_describe(move, result.root)}{flag}")
    print(f"{len(the_plan.moves)} move(s) planned")

    unmatched = the_plan.unmatched
    if unmatched:
        print(f"{len(unmatched)} file(s) left alone (no matching rule):")
        for file in unmatched[:MAX_UNMATCHED_SHOWN]:
            print(f"  {file.path.relative_to(result.root)}")
        if len(unmatched) > MAX_UNMATCHED_SHOWN:
            print(f"  ... and {len(unmatched) - MAX_UNMATCHED_SHOWN} more")

    print("Preview only: no files were changed.")
    _report_skipped(result)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    result = scan(args.folder, recursive=args.recursive, include_hidden=args.hidden)
    the_plan = build_plan(result.files, rules, result.root)

    if args.only is not None or args.exclude is not None:
        count = len(the_plan.moves)
        if args.only is not None:
            selected = parse_indexes(args.only, count)
        else:
            selected = set(range(1, count + 1)) - parse_indexes(args.exclude, count)
        the_plan = plan_selected(result.files, rules, result.root, the_plan, selected)

    moves = the_plan.moves
    _report_skipped(result)
    if not moves:
        print("Nothing to move.")
        return 0

    print(f"About to move {len(moves)} file(s) in {result.root}:")
    for move in moves:
        flag = "  [renamed]" if move.renamed else ""
        print(f"  {_describe(move, result.root)}{flag}")
    print("Successful moves will be saved to the local undo history.")

    if not args.yes and not _confirm(f"Move {len(moves)} file(s)? [y/N] "):
        print("Cancelled. No files were changed.")
        return 1

    counts = {status: 0 for status in MoveStatus}
    with HistoryDB(result.root, create=True) as history:
        batch_id, row_ids = history.start_batch(moves)
        print(f"Saved plan as history batch #{batch_id}.")
        for row_id, move in zip(row_ids, moves):
            outcome = execute_move(move)
            history.record_result(row_id, outcome)
            counts[outcome.status] += 1
            label = outcome.status.value
            if outcome.status is MoveStatus.FAILED:
                label = label.upper()
            line = f"  {label:<7} {_describe(outcome.move, result.root)}"
            if outcome.detail:
                line += f"  ({outcome.detail})"
            print(line)

    print(
        f"Done: {counts[MoveStatus.MOVED]} moved, "
        f"{counts[MoveStatus.SKIPPED]} skipped, "
        f"{counts[MoveStatus.FAILED]} failed"
    )
    return 1 if counts[MoveStatus.FAILED] else 0


def cmd_history(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= 100:
        print("error: --limit must be between 1 and 100", file=sys.stderr)
        return 2
    with HistoryDB(args.folder) as history:
        batches = history.batches(args.limit)
    if not batches:
        print("No recorded file moves yet.")
        return 0

    print(f"Recent SafeSort history for {args.folder.expanduser().resolve()}:")
    for batch in batches:
        outstanding = batch.moved - batch.undone
        print(
            f"  #{batch.id}  {batch.started_at}  "
            f"{batch.moved} moved, {batch.undone} undone, "
            f"{outstanding} remaining, {batch.failed} failed, "
            f"{batch.pending} interrupted"
        )
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    with HistoryDB(args.folder) as history:
        latest = history.latest_undoable()
        if latest is None:
            print("Nothing to undo.")
            return 0
        batch_id, items = latest
        root = args.folder.expanduser().resolve()
        print(f"Undo history batch #{batch_id}: {len(items)} file(s) in {root}")
        for item in items:
            print(f"  {_describe(PlannedMove(0, item.destination, item.source, 'undo'), root)}")
        if not args.yes and not _confirm(f"Undo {len(items)} file(s)? [y/N] "):
            print("Cancelled. No files were changed.")
            return 1

        counts = {status: 0 for status in MoveStatus}
        for item in items:
            reverse = PlannedMove(
                index=0,
                source=item.destination,
                destination=item.source,
                rule_name="undo",
            )
            if not matches_identity(item):
                from safesort.models import MoveResult
                outcome = MoveResult(
                    reverse, MoveStatus.SKIPPED,
                    "file is missing, modified, replaced, or its identity is unknown",
                )
            else:
                outcome = execute_move(reverse)
            history.record_undo(item.id, outcome)
            counts[outcome.status] += 1
            line = f"  {outcome.status.value:<7} {_describe(reverse, root)}"
            if outcome.detail:
                line += f"  ({outcome.detail})"
            print(line)

    print(
        f"Undo: {counts[MoveStatus.MOVED]} restored, "
        f"{counts[MoveStatus.SKIPPED]} skipped, "
        f"{counts[MoveStatus.FAILED]} failed"
    )
    return 1 if counts[MoveStatus.SKIPPED] or counts[MoveStatus.FAILED] else 0


def _print_duplicate_report(report: DuplicateScan) -> None:
    print(f"Scanned {report.photo_count} photo(s) across {len(report.roots)} root(s)")
    for group_number, group in enumerate(report.groups, start=1):
        print(f"Group {group_number}: {len(group.photos)} exact copies, {format_size(group.size)} each")
        for number, photo in enumerate(group.photos, start=1):
            print(f"  {number}. {photo.path}")
    print(f"{len(report.groups)} group(s), {report.extra_copies} extra copy/copies")
    print(f"Potential space after deletion: {format_size(report.recoverable_bytes)}")
    print("Same file bytes only; resized or recompressed photos are not considered matches.")
    for path, reason in report.skipped:
        print(f"Skipped {path}: {reason}", file=sys.stderr)


def cmd_duplicates(args: argparse.Namespace) -> int:
    if args.duplicates_command == "scan":
        report = find_duplicates(args.folders, include_hidden=args.hidden)
        _print_duplicate_report(report)
        print("Preview only: no files were moved or deleted.")
        return 0
    if args.duplicates_command == "quarantine":
        return _quarantine_duplicates(args)
    return _restore_duplicates(args)


def _quarantine_duplicates(args: argparse.Namespace) -> int:
    report = find_duplicates([args.folder], include_hidden=args.hidden)
    _print_duplicate_report(report)
    if not report.groups:
        print("Nothing to quarantine.")
        return 0
    root = report.roots[0]
    # Pick keeper per group interactively; blank means skip the group.
    choices: list[tuple[DuplicateGroup, int]] = []
    for group_number, group in enumerate(report.groups, start=1):
        if args.yes:
            keeper = 1
        else:
            try:
                raw = input(f"Group {group_number}: keep which photo [1-{len(group.photos)}], Enter to skip? ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled. No files were changed.")
                return 1
            if not raw:
                continue
            if not raw.isdecimal() or not 1 <= int(raw) <= len(group.photos):
                print("Invalid choice. No files were changed.", file=sys.stderr)
                return 2
            keeper = int(raw)
        choices.append((group, keeper - 1))
    if not choices:
        print("No groups selected. No files were changed.")
        return 0
    batch_name = f"batch-{uuid4().hex[:12]}"
    work: list[tuple[PlannedMove, Photo, Photo]] = []
    for group, keeper_index in choices:
        keeper = group.photos[keeper_index]
        for photo in group.photos:
            if photo is keeper:
                continue
            relative = photo.path.relative_to(root)
            dest = root / QUARANTINE_FOLDER / batch_name / relative
            move = PlannedMove(len(work) + 1, photo.path, dest, "duplicate-quarantine")
            work.append((move, photo, keeper))
    print(f"Proposed quarantine: {len(work)} photo(s), keeping one in each selected group.")
    for move, _photo, _keeper in work:
        print(f"  {move.source.relative_to(root)} -> {move.destination.relative_to(root)}")
    print("Photos will NOT be deleted. Use 'duplicates restore' to reverse the latest batch.")
    if not args.yes and not _confirm("Quarantine these copies? [y/N] "):
        print("Cancelled. No files were changed.")
        return 1

    counts = {status: 0 for status in MoveStatus}
    with DuplicateHistory(root, create=True) as history:
        batch_id, row_ids = history.begin([(move, photo.sha256, photo.size) for move, photo, _ in work])
        print(f"Quarantine history batch #{batch_id} saved.")
        from safesort.models import MoveResult
        for (move, photo, keeper), row_id in zip(work, row_ids):
            # Both the selected retained copy and the extra copy must still
            # match the originally scanned identities and SHA-256 contents.
            if not unchanged(photo) or not unchanged(keeper):
                outcome = MoveResult(move, MoveStatus.SKIPPED, "photo or keeper changed since scan")
            else:
                outcome = execute_move(move)
            history.record_move(row_id, outcome)
            counts[outcome.status] += 1
            print(f"  {outcome.status.value} {move.source.relative_to(root)}"
                  + (f" ({outcome.detail})" if outcome.detail else ""))
    print(f"Quarantine: {counts[MoveStatus.MOVED]} moved, {counts[MoveStatus.SKIPPED]} skipped, "
          f"{counts[MoveStatus.FAILED]} failed.")
    return 1 if counts[MoveStatus.FAILED] or counts[MoveStatus.SKIPPED] else 0


def _restore_duplicates(args: argparse.Namespace) -> int:
    from safesort.models import MoveResult
    root = args.folder.expanduser().resolve()
    with DuplicateHistory(root) as history:
        latest = history.latest_restorable()
        if latest is None:
            print("Nothing to restore.")
            return 0
        batch_id, items = latest
        print(f"Restore quarantine batch #{batch_id}: {len(items)} photo(s)")
        for item in items:
            print(f"  {item.quarantine_path.relative_to(root)} -> {item.source.relative_to(root)}")
        if not args.yes and not _confirm(f"Restore these {len(items)} photo(s)? [y/N] "):
            print("Cancelled. No files were changed.")
            return 1
        counts = {status: 0 for status in MoveStatus}
        for item in items:
            move = PlannedMove(0, item.quarantine_path, item.source, "duplicate-restore")
            if not quarantine_identity_matches(item):
                outcome = MoveResult(move, MoveStatus.SKIPPED, "quarantined file changed or is missing")
            else:
                outcome = execute_move(move)
            history.record_restore(item.row_id, outcome)
            counts[outcome.status] += 1
            print(f"  {outcome.status.value} {item.source.relative_to(root)}"
                  + (f" ({outcome.detail})" if outcome.detail else ""))
    print(f"Restore: {counts[MoveStatus.MOVED]} restored, {counts[MoveStatus.SKIPPED]} skipped, "
          f"{counts[MoveStatus.FAILED]} failed.")
    return 1 if counts[MoveStatus.FAILED] or counts[MoveStatus.SKIPPED] else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "scan": cmd_scan, "preview": cmd_preview, "apply": cmd_apply,
        "history": cmd_history, "undo": cmd_undo, "duplicates": cmd_duplicates,
    }
    handler = handlers[args.command]
    try:
        return handler(args)
    except SafeSortError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
