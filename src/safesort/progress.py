"""Small dependency-free progress display for interactive duplicate scans."""

from __future__ import annotations

import shutil
import time
from typing import TextIO

from safesort.duplicates import DuplicateProgress


def _size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    unit = 0
    while number >= 1024 and unit < len(units) - 1:
        number /= 1024
        unit += 1
    return f"{number:.1f} {units[unit]}" if unit else f"{value} B"


class TerminalProgress:
    """Throttle terminal updates; show totals only after enumeration completes."""

    def __init__(self, stream: TextIO, interval: float = 0.25) -> None:
        self.stream = stream
        self.interval = interval
        self.started = time.monotonic()
        self.last_update = -1.0
        self.phase = ""
        self.line_width = 0

    def __call__(self, event: DuplicateProgress) -> None:
        now = time.monotonic()
        new_phase = event.phase != self.phase
        completed = event.phase == "hash" and event.current == event.total
        if not new_phase and not completed and now - self.last_update < self.interval:
            return
        self.phase = event.phase
        self.last_update = now
        elapsed = int(now - self.started)
        clock = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        if event.phase == "discover":
            line = f"Finding photos: {event.current:,} found | elapsed {clock}"
        else:
            total = event.total or 0
            if event.bytes_total:
                percent = min(100.0, 100.0 * event.bytes_processed / event.bytes_total)
            else:
                percent = 100.0 * event.current / total if total else 100.0
            # File-count completion supersedes incomplete byte estimates after read failures.
            if completed:
                percent = 100.0
            line = (f"Hashing: {event.current:,}/{total:,} photos | "
                    f"{percent:.0f}% | {_size(event.bytes_processed)} / "
                    f"{_size(event.bytes_total or 0)} | elapsed {clock}")
        # Avoid wrapping into another row on narrow terminals.
        width = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)
        line = line[:width]
        self.stream.write("\r" + line + " " * max(0, self.line_width - len(line)))
        self.stream.flush()
        self.line_width = len(line)

    def finish(self) -> None:
        if self.line_width:
            self.stream.write("\r" + " " * self.line_width + "\r")
            self.stream.flush()
            self.line_width = 0
