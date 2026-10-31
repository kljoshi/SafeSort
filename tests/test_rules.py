import json
from pathlib import Path
from typing import Any

import pytest

from safesort.errors import RuleError
from safesort.models import Rule
from safesort.rules import load_rules


def write_rules(path: Path, rules: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"rules": rules}))
    return path


def good_rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "name": "Images",
        "extensions": [".jpg"],
        "destination": "Images",
    }
    rule.update(overrides)
    return rule


def test_valid_rules_file_loads_in_order(tmp_path: Path) -> None:
    rules_file = write_rules(
        tmp_path / "rules.json",
        [
            good_rule(extensions=[".jpg", ".png"]),
            {"name": "Documents", "extensions": [".pdf"], "destination": "Documents"},
        ],
    )

    rules = load_rules(rules_file)

    assert [r.name for r in rules] == ["Images", "Documents"]
    assert rules[0] == Rule(
        name="Images",
        extensions=(".jpg", ".png"),
        destination=Path("Images"),
    )
    assert rules[1].extensions == (".pdf",)


def test_extensions_are_lowercased_and_given_a_dot(tmp_path: Path) -> None:
    rules_file = write_rules(tmp_path / "r.json", [good_rule(extensions=["JPG", ".Png"])])
    assert load_rules(rules_file)[0].extensions == (".jpg", ".png")


def test_nested_relative_destination_is_allowed(tmp_path: Path) -> None:
    rules_file = write_rules(tmp_path / "r.json", [good_rule(destination="Media/Images")])
    assert load_rules(rules_file)[0].destination == Path("Media/Images")


def test_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "r.json"
    bad.write_text("{ this is not json")
    with pytest.raises(RuleError, match="Invalid JSON"):
        load_rules(bad)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RuleError, match="not found"):
        load_rules(tmp_path / "nope.json")


def test_file_without_rules_list_raises(tmp_path: Path) -> None:
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"something": "else"}))
    with pytest.raises(RuleError, match="'rules' list"):
        load_rules(bad)


def test_missing_destination_names_the_rule(tmp_path: Path) -> None:
    rule = good_rule(name="Pictures")
    del rule["destination"]
    with pytest.raises(RuleError, match="Pictures.*destination"):
        load_rules(write_rules(tmp_path / "r.json", [rule]))


def test_missing_name_says_which_position(tmp_path: Path) -> None:
    rule = good_rule()
    del rule["name"]
    with pytest.raises(RuleError, match="Rule #2"):
        load_rules(write_rules(tmp_path / "r.json", [good_rule(), rule]))


@pytest.mark.parametrize("bad", ["jpg", [], [1, 2], [".jpg", 3]])
def test_extensions_must_be_a_list_of_strings(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(RuleError, match="extensions"):
        load_rules(write_rules(tmp_path / "r.json", [good_rule(extensions=bad)]))


def test_multi_part_extension_is_rejected_with_advice(tmp_path: Path) -> None:
    with pytest.raises(RuleError, match="tar.gz"):
        load_rules(write_rules(tmp_path / "r.json", [good_rule(extensions=[".tar.gz"])]))


def test_absolute_destination_raises(tmp_path: Path) -> None:
    absolute = str(tmp_path.resolve())
    with pytest.raises(RuleError, match="relative"):
        load_rules(write_rules(tmp_path / "r.json", [good_rule(destination=absolute)]))


@pytest.mark.parametrize("bad", ["../outside", "Images/../../x"])
def test_destination_with_dotdot_raises(tmp_path: Path, bad: str) -> None:
    with pytest.raises(RuleError, match=r"\.\."):
        load_rules(write_rules(tmp_path / "r.json", [good_rule(destination=bad)]))


def test_the_example_rules_file_in_the_repo_is_valid() -> None:
    example = Path(__file__).parent.parent / "rules.json"
    assert len(load_rules(example)) > 0


def test_history_folder_is_reserved(tmp_path: Path) -> None:
    import json

    from safesort.errors import RuleError
    from safesort.rules import load_rules

    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"rules": [
        {"name": "Bad", "extensions": [".jpg"], "destination": ".safesort/Images"},
    ]}))
    with pytest.raises(RuleError, match="reserved"):
        load_rules(path)
