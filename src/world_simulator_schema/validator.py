from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema.exceptions import ValidationError

from .correction import CorrectionEngine
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
        errors = [*correction_errors, *schema_errors]
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


def _json_pointer(path_parts: Any) -> str:
    parts = [
        str(item).replace("~", "~0").replace("/", "~1")
        for item in path_parts
    ]
    return "/" + "/".join(parts) if parts else ""