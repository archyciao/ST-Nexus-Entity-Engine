from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

Schema = dict[str, Any] | bool
Lookup = Callable[[str], dict[str, Any]]


def _merge(left: Any, right: Any) -> Any:
    if isinstance(left, dict) and isinstance(right, dict):
        result = deepcopy(left)
        for key, value in right.items():
            if key == "required" and isinstance(value, list):
                existing = result.get(key, [])
                result[key] = list(dict.fromkeys([*existing, *value]))
            elif key in result:
                result[key] = _merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result
    return deepcopy(right)


def materialize_schema(
    schema: Schema,
    lookup: Lookup,
    seen_refs: frozenset[str] | None = None,
) -> Schema:
    """Resolve the small $ref/allOf subset needed for schema-guided correction."""

    if isinstance(schema, bool):
        return schema

    seen = seen_refs or frozenset()
    result: dict[str, Any] = {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and not ref.startswith("#") and ref not in seen:
        result = _merge(
            result,
            materialize_schema(lookup(ref), lookup, seen | {ref}),
        )

    for part in schema.get("allOf", []):
        result = _merge(result, materialize_schema(part, lookup, seen))

    local = {
        key: value
        for key, value in schema.items()
        if key not in {"$ref", "allOf"}
    }
    return _merge(result, local)


def schema_at_instance_path(
    schema: Schema,
    pointer: str,
    lookup: Lookup,
) -> Schema | None:
    current = materialize_schema(schema, lookup)
    if pointer in {"", "/"}:
        return current

    for segment in [item for item in pointer.split("/") if item]:
        current = materialize_schema(current, lookup)
        if isinstance(current, bool):
            return None
        current = (
            current.get("items")
            if segment == "*"
            else current.get("properties", {}).get(segment)
        )
        if current is None:
            return None
    return materialize_schema(current, lookup)


def instance_path_exists(schema: Schema, pointer: str, lookup: Lookup) -> bool:
    return schema_at_instance_path(schema, pointer, lookup) is not None