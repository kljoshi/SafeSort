"""Exceptions raised by SafeSort.

Everything we raise on purpose inherits from SafeSortError, so the CLI can
catch one type and show a friendly message instead of a traceback.
"""


class SafeSortError(Exception):
    """Base class for all expected SafeSort errors."""


class ScanError(SafeSortError):
    """The folder to scan is missing or is not a folder."""


class RuleError(SafeSortError):
    """The rules file is missing, unreadable, or contains an invalid rule."""


class SelectionError(SafeSortError):
    """A --only / --exclude value is malformed or out of range."""

class HistoryError(SafeSortError):
    """History is missing, corrupt, or unsafe to use."""
