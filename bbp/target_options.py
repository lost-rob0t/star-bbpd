from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def option_map(options: list[Any]) -> dict[str, Any]:
    """Normalize target options without evaluating arbitrary input.

    Accepted forms are `{key: value}` objects or `{key: ..., value: ...}`
    entries. Later entries replace earlier values deterministically.
    """
    result: dict[str, Any] = {}
    for item in options:
        if not isinstance(item, Mapping):
            continue
        if "key" in item and "value" in item and isinstance(item["key"], str):
            result[item["key"]] = deepcopy(item["value"])
            continue
        for key, value in item.items():
            if isinstance(key, str):
                result[key] = deepcopy(value)
    return result


def string_option(options: list[Any], name: str, default: str = "") -> str:
    value = option_map(options).get(name, default)
    return value if isinstance(value, str) else default


def bool_option(options: list[Any], name: str, default: bool = False) -> bool:
    value = option_map(options).get(name, default)
    return value if isinstance(value, bool) else default


def string_list_option(options: list[Any], name: str) -> list[str]:
    value = option_map(options).get(name, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
