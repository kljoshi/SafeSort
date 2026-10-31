"""Choose a subset of a plan, using the numbers shown by ``preview``."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from safesort.errors import SelectionError
from safesort.models import FileInfo, Plan, Rule
from safesort.planner import plan


def _to_int(text: str, spec: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise SelectionError(f"'{text.strip()}' is not a number in '{spec}'") from None


def parse_indexes(spec: str, max_index: int) -> set[int]:
    """Parse '1,3,5-7' into {1, 3, 5, 6, 7}, checking each is 1..max_index."""
    chosen: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise SelectionError(f"Empty entry in '{spec}'. Use numbers like 1,3,5-7")

        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start, end = _to_int(start_text, spec), _to_int(end_text, spec)
            if start > end:
                raise SelectionError(f"Range '{part}' goes backwards")
        else:
            start = end = _to_int(part, spec)

        for number in (start, end):
            if not 1 <= number <= max_index:
                raise SelectionError(
                    f"Move number {number} is out of range "
                    f"(the plan has {max_index} move(s))"
                )
        chosen.update(range(start, end + 1))
    return chosen


def plan_selected(
    files: Sequence[FileInfo],
    rules: Sequence[Rule],
    root: Path,
    full_plan: Plan,
    selected: set[int],
) -> Plan:
    """Build a new plan containing only the selected moves.

    We re-plan just the chosen files instead of filtering the old plan. That
    way renames are recalculated: if move 1 is dropped, a file that was being
    renamed "photo (1).jpg" to avoid it can now keep its real name.
    """
    sources = {m.source for m in full_plan.moves if m.index in selected}
    return plan([f for f in files if f.path in sources], rules, root)
