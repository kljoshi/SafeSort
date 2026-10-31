"""Load and validate organizing rules from a JSON file."""

from __future__ import annotations

import json
from pathlib import Path

from safesort.errors import RuleError
from safesort.models import Rule


def _normalize_extension(ext: str, rule_name: str) -> str:
    cleaned = ext.strip().lower().removeprefix(".")
    if not cleaned:
        raise RuleError(f"Rule '{rule_name}': empty extension in 'extensions'")
    if "." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise RuleError(
            f"Rule '{rule_name}': extension '{ext}' is not supported. "
            "Use a single extension such as '.gz' (not '.tar.gz')."
        )
    return f".{cleaned}"


def _parse_rule(raw: object, position: int) -> Rule:
    if not isinstance(raw, dict):
        raise RuleError(
            f"Rule #{position} must be an object with "
            "'name', 'extensions', and 'destination'"
        )

    name_raw = raw.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise RuleError(f"Rule #{position} is missing a 'name'")
    name = name_raw.strip()

    ext_raw = raw.get("extensions")
    if (
        not isinstance(ext_raw, list)
        or not ext_raw
        or not all(isinstance(e, str) for e in ext_raw)
    ):
        raise RuleError(f"Rule '{name}': 'extensions' must be a non-empty list of strings")
    extensions = tuple(_normalize_extension(e, name) for e in ext_raw)

    dest_raw = raw.get("destination")
    if not isinstance(dest_raw, str) or not dest_raw.strip():
        raise RuleError(f"Rule '{name}': missing 'destination'")
    destination = Path(dest_raw.strip())
    if destination.anchor or destination.drive:
        raise RuleError(
            f"Rule '{name}': destination '{dest_raw}' must be a relative folder, "
            "not an absolute path"
        )
    if ".." in destination.parts:
        raise RuleError(f"Rule '{name}': destination '{dest_raw}' must not contain '..'")
    if ".safesort" in destination.parts:
        raise RuleError(f"Rule '{name}': the .safesort history folder is reserved")
    if not destination.parts:
        raise RuleError(f"Rule '{name}': destination must name a subfolder")

    return Rule(name=name, extensions=extensions, destination=destination)


def load_rules(path: Path | None = None) -> list[Rule]:
    """Return rules from ``path``, or from the built-in rules when omitted.

    Raises:
        RuleError: if the file can't be read, isn't valid JSON, or any rule
            is invalid. The message says which rule and what is wrong.
    """
    path = path.expanduser() if path is not None else Path(__file__).with_name("default_rules.json")
    try:
        # utf-8-sig tolerates the BOM that some Windows editors add.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise RuleError(f"Rules file not found: {path}") from None
    except OSError as exc:
        raise RuleError(f"Cannot read rules file {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise RuleError(f"Rules file {path} is not valid UTF-8 text") from exc

    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuleError(f"{path} must contain an object with a 'rules' list")

    return [_parse_rule(raw, i) for i, raw in enumerate(data["rules"], start=1)]
