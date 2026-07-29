from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from .schema_store import SchemaStore
from .schema_tools import materialize_schema


@dataclass(frozen=True)
class Correction:
    path: str
    original: Any
    corrected: Any
    method: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "original": self.original,
            "corrected": self.corrected,
            "method": self.method,
            "confidence": round(self.confidence, 4),
        }


@dataclass(frozen=True)
class CorrectionIssue:
    code: str
    path: str
    message: str
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.candidates:
            result["candidates"] = list(self.candidates)
        return result


class CorrectionEngine:
    def __init__(self, store: SchemaStore):
        self.store = store
        self.policy = store.component_registry["correction_policy"]

    def correct(
        self,
        instance: dict[str, Any],
        schema: dict[str, Any],
        component: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Correction], list[CorrectionIssue]]:
        corrections: list[Correction] = []
        issues: list[CorrectionIssue] = []
        aliases = component.get("correction", {}).get("key_aliases", [])
        value_aliases = component.get("correction", {}).get("value_aliases", [])
        corrected = self._visit(
            deepcopy(instance),
            schema,
            "",
            aliases,
            value_aliases,
            corrections,
            issues,
        )
        return corrected, corrections, issues

    def _visit(
        self,
        value: Any,
        schema: dict[str, Any] | bool,
        path: str,
        aliases: list[dict[str, Any]],
        value_aliases: list[dict[str, Any]],
        corrections: list[Correction],
        issues: list[CorrectionIssue],
    ) -> Any:
        resolved = materialize_schema(schema, self.store.schema_by_id)
        if isinstance(resolved, bool):
            return value

        if isinstance(value, dict):
            properties = resolved.get("properties", {})
            for original_key in list(value.keys()):
                key = original_key
                if key not in properties:
                    match = self._match_key(key, tuple(properties), path, aliases)
                    if match is not None:
                        canonical, method, confidence, candidates = match
                        key_path = _join_pointer(path, key)
                        if canonical and canonical in value and canonical != key:
                            issues.append(
                                CorrectionIssue(
                                    "CORRECTION_COLLISION",
                                    key_path,
                                    f"字段 {key!r} 修正后与已有字段 {canonical!r} 冲突。",
                                    (canonical,),
                                )
                            )
                        elif canonical:
                            value[canonical] = value.pop(key)
                            corrections.append(
                                Correction(
                                    key_path,
                                    key,
                                    canonical,
                                    method,
                                    confidence,
                                )
                            )
                            key = canonical
                        elif candidates:
                            issues.append(
                                CorrectionIssue(
                                    "AMBIGUOUS_FIELD",
                                    key_path,
                                    f"字段 {key!r} 存在多个接近候选，未自动修改。",
                                    candidates,
                                )
                            )

                child_schema = properties.get(key)
                if child_schema is not None and key in value:
                    value[key] = self._visit(
                        value[key],
                        child_schema,
                        _join_pointer(path, key),
                        aliases,
                        value_aliases,
                        corrections,
                        issues,
                    )
            return value

        if isinstance(value, list):
            item_schema = resolved.get("items")
            if item_schema is not None:
                for index, item in enumerate(value):
                    value[index] = self._visit(
                        item,
                        item_schema,
                        _join_pointer(path, str(index)),
                        aliases,
                        value_aliases,
                        corrections,
                        issues,
                    )
            return value

        return self._correct_value(value, path, value_aliases, corrections)

    def _match_key(
        self,
        key: str,
        candidates: tuple[str, ...],
        object_path: str,
        aliases: list[dict[str, Any]],
    ) -> tuple[str | None, str, float, tuple[str, ...]] | None:
        normalized = normalize_key(key)
        normalized_matches = [
            item for item in candidates if normalize_key(item) == normalized
        ]
        if len(normalized_matches) == 1:
            return normalized_matches[0], "normalized", 1.0, ()

        for alias in aliases:
            if not _path_pattern_matches(
                str(alias.get("object_path", "")), object_path
            ):
                continue
            if normalize_key(str(alias.get("alias", ""))) == normalized:
                return str(alias["canonical"]), "alias", 1.0, ()

        if not self.policy.get("fuzzy_key_matching", False) or not candidates:
            return None

        scored = sorted(
            (
                SequenceMatcher(
                    None,
                    normalized,
                    normalize_key(candidate),
                ).ratio(),
                candidate,
            )
            for candidate in candidates
        )
        best_score, best = scored[-1]
        second_score = scored[-2][0] if len(scored) > 1 else 0.0
        threshold = float(self.policy["fuzzy_threshold"])
        margin = float(self.policy["minimum_margin"])

        if best_score >= threshold and best_score - second_score >= margin:
            return best, "fuzzy", best_score, ()

        close = tuple(
            candidate
            for score, candidate in reversed(scored)
            if score >= threshold
        )
        if len(close) > 1:
            return None, "ambiguous", best_score, close
        return None

    def _correct_value(
        self,
        value: Any,
        path: str,
        aliases: list[dict[str, Any]],
        corrections: list[Correction],
    ) -> Any:
        field_name = path.rsplit("/", 1)[-1] if path else ""
        protected = set(self.policy.get("protected_value_field_names", []))
        if field_name in protected:
            return value

        for alias in aliases:
            if alias.get("path") == path and alias.get("alias") == value:
                corrected = alias.get("canonical")
                corrections.append(
                    Correction(path, value, corrected, "value_alias", 1.0)
                )
                return corrected
        return value


def normalize_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[-\s]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.lower()


def _join_pointer(parent: str, segment: str) -> str:
    escaped = segment.replace("~", "~0").replace("/", "~1")
    return f"{parent}/{escaped}" if parent else f"/{escaped}"


def _path_pattern_matches(pattern: str, actual: str) -> bool:
    pattern_parts = [item for item in pattern.split("/") if item]
    actual_parts = [item for item in actual.split("/") if item]
    if len(pattern_parts) != len(actual_parts):
        return False
    return all(
        expected == "*" or expected == observed
        for expected, observed in zip(pattern_parts, actual_parts)
    )