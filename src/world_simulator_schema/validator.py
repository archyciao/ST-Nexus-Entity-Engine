from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema.exceptions import ValidationError

from .correction import CorrectionEngine
from .entity_ids import is_valid_entity_id
from .schema_store import SchemaStore


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class ValidationReport:
    valid: bool
    component: str
    schema_version: str | None
    instance: dict[str, Any]
    corrections: list[dict[str, Any]]
    errors: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "component": self.component,
            "schema_version": self.schema_version,
            "instance": self.instance,
            "corrections": self.corrections,
            "errors": self.errors,
        }


class ComponentValidator:
    def __init__(self, project_root: str | Path):
        self.store = SchemaStore(project_root)
        self.corrector = CorrectionEngine(self.store)

    def check_registry(self) -> list[dict[str, str]]:
        return [item.to_dict() for item in self.store.check_registry()]

    def check_permission(
        self,
        component_name: str,
        *,
        role: str,
        operation: str,
        path: str,
    ) -> dict[str, str] | None:
        component = self.store.component(component_name)
        if component is None:
            return ValidationIssue(
                "UNKNOWN_COMPONENT",
                "",
                f"Component 未注册: {component_name}",
            ).to_dict()

        operation_fields = {
            "read": "read_roles",
            "propose": "propose_roles",
            "write": "write_roles",
        }
        permission_field = operation_fields.get(operation)
        if permission_field is None:
            return ValidationIssue(
                "INVALID_PERMISSION_OPERATION",
                path,
                f"不支持的权限操作: {operation}",
            ).to_dict()

        permissions = component["permissions"]
        rule = permissions["default"]
        matching_rules = [
            item
            for item in permissions.get("fields", [])
            if _path_pattern_matches(item["path"], path)
        ]
        if matching_rules:
            rule = max(
                matching_rules,
                key=lambda item: sum(
                    part != "*" for part in item["path"].split("/")
                ),
            )

        if role in rule[permission_field]:
            return None
        return ValidationIssue(
            "PERMISSION_DENIED",
            path,
            (
                f"角色 {role} 无权对 {component_name}{path} "
                f"执行 {operation}。"
            ),
        ).to_dict()

    def validate(
        self,
        component_name: str,
        instance: dict[str, Any],
        *,
        repair: bool = False,
    ) -> ValidationReport:
        component = self.store.component(component_name)
        if component is None:
            return self._early_error(
                component_name,
                instance,
                "UNKNOWN_COMPONENT",
                "",
                f"Component 未注册: {component_name}",
            )

        source_version = instance.get("schema_version")
        if not isinstance(source_version, str):
            source_version = instance.get("schemaVersion")
        try:
            provisional_schema = self.store.schema_for_component(
                component_name,
                source_version if isinstance(source_version, str) else None,
            )
        except KeyError:
            provisional_schema = self.store.schema_for_component(component_name)

        corrected = instance
        corrections: list[dict[str, Any]] = []
        correction_errors: list[dict[str, Any]] = []
        if repair:
            fixed, correction_items, correction_issues = self.corrector.correct(
                instance,
                provisional_schema,
                component,
            )
            corrected = fixed
            corrections = [item.to_dict() for item in correction_items]
            correction_errors = [item.to_dict() for item in correction_issues]

        version = corrected.get("schema_version")
        if not isinstance(version, str):
            return ValidationReport(
                False,
                component_name,
                None,
                corrected,
                corrections,
                [
                    *correction_errors,
                    ValidationIssue(
                        "MISSING_REQUIRED_FIELD",
                        "/schema_version",
                        "缺少合法的 schema_version。",
                    ).to_dict(),
                ],
            )

        try:
            schema = self.store.schema_for_component(component_name, version)
        except KeyError:
            return ValidationReport(
                False,
                component_name,
                version,
                corrected,
                corrections,
                [
                    *correction_errors,
                    ValidationIssue(
                        "UNSUPPORTED_VERSION",
                        "/schema_version",
                        f"{component_name} 不支持 schema_version {version}",
                    ).to_dict(),
                ],
            )

        schema_errors = [
            _from_jsonschema(error).to_dict()
            for error in sorted(
                self.store.validator_for(schema).iter_errors(corrected),
                key=lambda item: list(item.absolute_path),
            )
        ]
        identity_errors = _validate_item_identity_rules(component, corrected)
        reference_errors = _validate_reference_id_rules(component, corrected)
        errors = [
            *correction_errors,
            *schema_errors,
            *identity_errors,
            *reference_errors,
        ]
        return ValidationReport(
            not errors,
            component_name,
            version,
            corrected,
            corrections,
            errors,
        )

    @staticmethod
    def _early_error(
        component_name: str,
        instance: dict[str, Any],
        code: str,
        path: str,
        message: str,
    ) -> ValidationReport:
        return ValidationReport(
            False,
            component_name,
            None,
            instance,
            [],
            [ValidationIssue(code, path, message).to_dict()],
        )


def _from_jsonschema(error: ValidationError) -> ValidationIssue:
    path = _json_pointer(error.absolute_path)
    validator = error.validator
    if validator == "required":
        code = "MISSING_REQUIRED_FIELD"
    elif validator in {"additionalProperties", "unevaluatedProperties"}:
        code = "UNKNOWN_FIELD"
    elif validator == "type":
        code = "INVALID_TYPE"
    elif validator in {
        "const",
        "enum",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "uniqueItems",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
    }:
        code = "INVALID_VALUE"
    else:
        code = "SCHEMA_VALIDATION_ERROR"

    if any(str(part).endswith("_ref") for part in error.absolute_path):
        if validator in {"const", "pattern", "required", "additionalProperties"}:
            code = "INVALID_REFERENCE"
    return ValidationIssue(code, path, error.message)


def _path_pattern_matches(pattern: str, path: str) -> bool:
    pattern_parts = pattern.strip("/").split("/") if pattern else []
    path_parts = path.strip("/").split("/") if path else []
    if len(pattern_parts) != len(path_parts):
        return False
    return all(
        expected == "*" or expected == actual
        for expected, actual in zip(pattern_parts, path_parts)
    )


def _validate_item_identity_rules(
    component: dict[str, Any],
    instance: dict[str, Any],
) -> list[dict[str, str]]:
    """检查数组条目的局部稳定标识，避免后续更新指向多个对象。

    Registry 只声明数组路径和标识字段；这里读取实际 Component 数据，
    在同一数组内发现重复标识时返回明确错误。字段缺失或类型错误仍交由
    JSON Schema 报告，避免同一问题产生多份含义不同的错误。
    """

    issues: list[dict[str, str]] = []
    for rule in component.get("item_identity_rules", []):
        array_path = rule["array_path"]
        id_field = rule["id_field"]
        items = _value_at_pointer(instance, array_path)
        if not isinstance(items, list):
            continue

        seen: dict[str, int] = {}
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            identity = item.get(id_field)
            if not isinstance(identity, str):
                continue
            if identity in seen:
                issues.append(
                    ValidationIssue(
                        "DUPLICATE_ITEM_ID",
                        f"{array_path}/{index}/{id_field}",
                        (
                            f"{id_field} 与第 {seen[identity]} 项重复: "
                            f"{identity}"
                        ),
                    ).to_dict()
                )
            else:
                seen[identity] = index
    return issues


def _value_at_pointer(instance: Any, pointer: str) -> Any:
    """按简单 JSON Pointer 读取值；找不到路径时返回 None。"""

    current = instance
    for raw_part in [part for part in pointer.split("/") if part]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _validate_reference_id_rules(
    component: dict[str, Any],
    instance: dict[str, Any],
) -> list[dict[str, str]]:
    """检查每条已登记引用的 ID 前缀是否与其 Type 完全一致。

    JSON Schema 负责确认 ID 和 Type 各自的格式；本步骤补充两者之间的
    交叉约束，避免把 ``character_...`` 错当成 Item 或 Location 引用。
    """

    issues: list[dict[str, str]] = []
    for declaration in component.get("references", []):
        pattern = declaration["path"]
        for path, reference in _values_at_pattern(instance, pattern):
            if not isinstance(reference, dict):
                continue
            entity_id = reference.get("id")
            entity_type = reference.get("type")
            if not isinstance(entity_id, str) or not isinstance(entity_type, str):
                continue
            if not is_valid_entity_id(entity_id, entity_type):
                issues.append(
                    ValidationIssue(
                        "INVALID_REFERENCE_ID",
                        f"{path}/id",
                        "引用 ID 必须符合 type_series，且前缀必须等于 type。",
                    ).to_dict()
                )
    return issues


def _values_at_pattern(instance: Any, pointer: str) -> list[tuple[str, Any]]:
    """按含 ``*`` 的 JSON Pointer 取得所有实际路径和值。"""

    states: list[tuple[str, Any]] = [("", instance)]
    for raw_part in [part for part in pointer.split("/") if part]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        next_states: list[tuple[str, Any]] = []
        for current_path, current in states:
            if part == "*" and isinstance(current, list):
                next_states.extend(
                    (f"{current_path}/{index}", value)
                    for index, value in enumerate(current)
                )
            elif isinstance(current, dict) and part in current:
                next_states.append((f"{current_path}/{part}", current[part]))
        states = next_states
    return states


def _json_pointer(path_parts: Any) -> str:
    parts = [
        str(item).replace("~", "~0").replace("/", "~1")
        for item in path_parts
    ]
    return "/" + "/".join(parts) if parts else ""
