from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from .schema_tools import instance_path_exists, schema_at_instance_path


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class SchemaStore:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.schemas: dict[str, dict[str, Any]] = {}
        self.schema_files: dict[str, Path] = {}
        self._load_schemas()
        resources = [
            (schema_id, Resource.from_contents(schema))
            for schema_id, schema in self.schemas.items()
        ]
        self.resource_registry = Registry().with_resources(resources)
        self.component_registry = self._load_json(
            self.project_root / "registry" / "components.json"
        )
        self.components = {
            item["name"]: item
            for item in self.component_registry.get("components", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_schemas(self) -> None:
        for path in sorted(self.project_root.glob("schemas/**/*.schema.json")):
            schema = self._load_json(path)
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                raise ValueError(f"Schema 缺少 $id: {path}")
            if schema_id in self.schemas:
                raise ValueError(f"Schema $id 重复: {schema_id}")
            self.schemas[schema_id] = schema
            self.schema_files[schema_id] = path

    def schema_by_id(self, schema_id: str) -> dict[str, Any]:
        try:
            return self.schemas[schema_id]
        except KeyError as exc:
            raise KeyError(f"未加载 Schema: {schema_id}") from exc

    def component(self, name: str) -> dict[str, Any] | None:
        return self.components.get(name)

    def schema_for_component(
        self, name: str, version: str | None = None
    ) -> dict[str, Any]:
        component = self.component(name)
        if component is None:
            raise KeyError(f"未知 Component: {name}")
        wanted = version or component["current_version"]
        for item in component["versions"]:
            if item["version"] == wanted:
                return self.schema_by_id(item["schema_id"])
        raise KeyError(f"Component {name} 不支持 schema_version {wanted}")

    def validator_for(self, schema: dict[str, Any]) -> Draft202012Validator:
        return Draft202012Validator(
            schema,
            registry=self.resource_registry,
            format_checker=FormatChecker(),
        )

    def check_registry(self) -> list[RegistryIssue]:
        issues: list[RegistryIssue] = []

        for schema_id, schema in self.schemas.items():
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                relative_path = self.schema_files[schema_id].relative_to(
                    self.project_root
                )
                issues.append(
                    RegistryIssue(
                        "SCHEMA_DOCUMENT_INVALID",
                        f"/{relative_path.as_posix()}",
                        exc.message,
                    )
                )

        meta = self.schema_by_id(
            "urn:world-simulator:schema:meta:component-registry:0.1.0"
        )
        for error in sorted(
            self.validator_for(meta).iter_errors(self.component_registry),
            key=lambda item: list(item.absolute_path),
        ):
            issues.append(
                RegistryIssue(
                    "REGISTRY_SCHEMA_INVALID",
                    _json_pointer(error.absolute_path),
                    error.message,
                )
            )

        names: set[str] = set()
        for index, component in enumerate(
            self.component_registry.get("components", [])
        ):
            base = f"/components/{index}"
            name = component.get("name")
            if name in names:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_DUPLICATE_COMPONENT",
                        f"{base}/name",
                        f"Component 名称重复: {name}",
                    )
                )
            if isinstance(name, str):
                names.add(name)

            versions = component.get("versions", [])
            values = [
                item.get("version") for item in versions if isinstance(item, dict)
            ]
            if len(values) != len(set(values)):
                issues.append(
                    RegistryIssue(
                        "REGISTRY_DUPLICATE_VERSION",
                        f"{base}/versions",
                        "同一 Component 的版本号重复。",
                    )
                )
            if component.get("current_version") not in values:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_CURRENT_VERSION_MISSING",
                        f"{base}/current_version",
                        "current_version 不在 versions 中。",
                    )
                )

            for vi, version in enumerate(versions):
                schema_id = version.get("schema_id")
                if schema_id not in self.schemas:
                    issues.append(
                        RegistryIssue(
                            "REGISTRY_SCHEMA_MISSING",
                            f"{base}/versions/{vi}/schema_id",
                            f"找不到 Schema: {schema_id}",
                        )
                    )

            if component.get("category") == "index":
                if component.get("authority") != "derived":
                    issues.append(
                        RegistryIssue(
                            "REGISTRY_INDEX_AUTHORITY",
                            f"{base}/authority",
                            "Index Component 必须是 derived。",
                        )
                    )
                if component.get("rebuildable") is not True:
                    issues.append(
                        RegistryIssue(
                            "REGISTRY_INDEX_REBUILDABLE",
                            f"{base}/rebuildable",
                            "Index Component 必须可重建。",
                        )
                    )
                if not component.get("rebuild_from"):
                    issues.append(
                        RegistryIssue(
                            "REGISTRY_INDEX_SOURCE_MISSING",
                            f"{base}/rebuild_from",
                            "Index Component 必须声明 rebuild_from。",
                        )
                    )

            try:
                schema = self.schema_for_component(
                    name, component.get("current_version")
                )
            except (KeyError, TypeError):
                continue
            self._check_paths(issues, component, schema, base)

            for migration in component.get("migrations", []):
                if migration.get("source_version") == migration.get("target_version"):
                    issues.append(
                        RegistryIssue(
                            "REGISTRY_MIGRATION_CYCLE",
                            f"{base}/migrations",
                            "迁移不能指向同一版本。",
                        )
                    )
        return issues

    def _check_paths(
        self,
        issues: list[RegistryIssue],
        component: dict[str, Any],
        schema: dict[str, Any],
        base: str,
    ) -> None:
        preserves_unknown_fields = component.get("unknown_field_policy") == "preserve"
        declarations = [
            (
                component.get("permissions", {}).get("fields", []),
                "REGISTRY_PERMISSION_PATH_MISSING",
                "permissions/fields",
            ),
            (
                component.get("references", []),
                "REGISTRY_REFERENCE_PATH_MISSING",
                "references",
            ),
        ]
        for items, code, label in declarations:
            for index, item in enumerate(items):
                path = item.get("path")
                if (
                    not preserves_unknown_fields
                    and isinstance(path, str)
                    and not instance_path_exists(
                    schema, path, self.schema_by_id
                    )
                ):
                    issues.append(
                        RegistryIssue(
                            code,
                            f"{base}/{label}/{index}/path",
                            f"声明路径不存在于当前 Schema: {path}",
                        )
                    )

        for index, rule in enumerate(component.get("item_identity_rules", [])):
            array_path = rule.get("array_path")
            item_schema = (
                schema_at_instance_path(
                    schema,
                    f"{array_path}/*",
                    self.schema_by_id,
                )
                if isinstance(array_path, str)
                else None
            )
            properties = (
                item_schema.get("properties", {})
                if isinstance(item_schema, dict)
                else {}
            )
            id_field = rule.get("id_field")
            if not preserves_unknown_fields and id_field not in properties:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_ITEM_ID_PATH_MISSING",
                        f"{base}/item_identity_rules/{index}",
                        (
                            "数组条目标识不存在于当前 Schema: "
                            f"{array_path}/*/{id_field}"
                        ),
                    )
                )
        seen_key_aliases: dict[tuple[str, str], str] = {}
        for index, alias in enumerate(
            component.get("correction", {}).get("key_aliases", [])
        ):
            object_path = alias.get("object_path")
            object_schema = (
                schema_at_instance_path(schema, object_path, self.schema_by_id)
                if isinstance(object_path, str)
                else None
            )
            properties = (
                object_schema.get("properties", {})
                if isinstance(object_schema, dict)
                else {}
            )
            if (
                not preserves_unknown_fields
                and alias.get("canonical") not in properties
            ):
                issues.append(
                    RegistryIssue(
                        "REGISTRY_ALIAS_TARGET_MISSING",
                        f"{base}/correction/key_aliases/{index}/canonical",
                        f"别名目标不存在于对象 {object_path!r}。",
                    )
                )

            alias_key = (str(object_path), str(alias.get("alias")))
            prior_target = seen_key_aliases.get(alias_key)
            current_target = str(alias.get("canonical"))
            if prior_target is not None and prior_target != current_target:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_ALIAS_AMBIGUOUS",
                        f"{base}/correction/key_aliases/{index}",
                        (
                            f"同一路径中的字段别名 {alias_key[1]!r} "
                            "不能映射到多个标准字段。"
                        ),
                    )
                )
            seen_key_aliases[alias_key] = current_target

        protected_fields = set(
            self.component_registry.get("correction_policy", {}).get(
                "protected_value_field_names",
                [],
            )
        )
        for index, alias in enumerate(
            component.get("correction", {}).get("value_aliases", [])
        ):
            path = alias.get("path")
            target_schema = (
                schema_at_instance_path(schema, path, self.schema_by_id)
                if isinstance(path, str)
                else None
            )
            issue_path = f"{base}/correction/value_aliases/{index}"
            if target_schema is None and not preserves_unknown_fields:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_VALUE_ALIAS_PATH_MISSING",
                        f"{issue_path}/path",
                        f"值别名路径不存在于当前 Schema: {path}",
                    )
                )
                continue

            field_name = str(path).rsplit("/", 1)[-1]
            if field_name in protected_fields:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_VALUE_ALIAS_PROTECTED",
                        f"{issue_path}/path",
                        f"受保护字段 {field_name!r} 禁止配置值别名。",
                    )
                )
                continue

            allowed_values: list[Any] | None = None
            if isinstance(target_schema, dict):
                if isinstance(target_schema.get("enum"), list):
                    allowed_values = target_schema["enum"]
                elif "const" in target_schema:
                    allowed_values = [target_schema["const"]]
            if preserves_unknown_fields:
                continue
            if allowed_values is None:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_VALUE_ALIAS_NON_ENUM",
                        f"{issue_path}/path",
                        "值别名只允许用于 enum 或 const 字段。",
                    )
                )
            elif alias.get("canonical") not in allowed_values:
                issues.append(
                    RegistryIssue(
                        "REGISTRY_VALUE_ALIAS_TARGET_INVALID",
                        f"{issue_path}/canonical",
                        "值别名的 canonical 必须是 Schema 允许值。",
                    )
                )


def _json_pointer(path_parts: Any) -> str:
    parts = [
        str(item).replace("~", "~0").replace("/", "~1")
        for item in path_parts
    ]
    return "/" + "/".join(parts) if parts else ""
