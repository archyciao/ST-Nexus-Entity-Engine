"""Entity 跨组件校验器。

文件功能：在单个 Component 已通过 JSON Schema 校验后，继续检查同一 Entity
内部多个 Component 之间是否互相一致。它解决 JSON Schema 不适合表达的规则，
例如 Character Relation 的端点、关系面向参与者和方向状态必须指向同一对人物。

架构位置与运作方式：调用方把完整 Entity 交给 :class:`EntityValidator`；校验器
先逐个调用 ComponentValidator，再根据 Entity Type 执行专项规则，最后只返回
结构化报告，不修改数据。数据库中的“同一人物对只能有一个 Relation”等全局
唯一性仍由存储层负责，不由本文件假装完成。

输入与输出：输入是已经解析成 ``dict`` 的 Entity；输出是
``EntityValidationReport``。报告包含是否通过以及可供人、AI 和程序定位的错误。
本模块不做模糊纠正、不生成 ID、不读取目标 Entity，也不写入任何权威数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .entity_ids import is_valid_entity_id
from .stage_context import validate_stage_framework
from .validator import ComponentValidator, ValidationIssue


@dataclass
class EntityValidationReport:
    """一次完整 Entity 校验的结果。

    ``valid`` 表示是否可以进入后续流程；``entity_id`` 方便日志关联；
    ``errors`` 中每项均包含稳定错误码、JSON 路径和中文解释。
    """

    valid: bool
    entity_id: str | None
    errors: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        """把报告转换为便于命令行、日志或 API 输出的普通字典。"""

        return {
            "valid": self.valid,
            "entity_id": self.entity_id,
            "errors": self.errors,
        }


class EntityValidator:
    """组合 Component 校验与指定 Entity Type 的跨组件规则。"""

    def __init__(self, project_root: str | Path):
        """加载项目 Registry 和 Schema；不会扫描或修改世界数据。"""

        self.component_validator = ComponentValidator(project_root)

    def validate(self, entity: dict[str, Any]) -> EntityValidationReport:
        """校验一个完整 Entity，并汇总结构级与跨组件错误。

        处理顺序为：检查 Entity 外壳，逐个验证 Component 与宿主类型，再执行
        Character Relation 专项一致性规则。即使前一步发现问题，也尽量继续收集
        其他可安全判断的错误，让 AI 或维护者一次看到完整问题清单。
        """

        errors: list[dict[str, str]] = []
        entity_id = entity.get("id")
        entity_type = entity.get("type")
        description = entity.get("description")
        components = entity.get("components")

        if not isinstance(entity_type, str):
            errors.append(_issue("INVALID_ENTITY_TYPE", "/type", "Entity 缺少合法 type。"))
        if not isinstance(entity_id, str) or not isinstance(entity_type, str) or not is_valid_entity_id(entity_id, entity_type):
            errors.append(
                _issue(
                    "INVALID_ENTITY_ID",
                    "/id",
                    "Entity ID 必须使用 type_ULID，并与 Entity Type 一致。",
                )
            )
        if not isinstance(description, str) or not description.strip():
            errors.append(
                _issue(
                    "INVALID_ENTITY_DESCRIPTION",
                    "/description",
                    "Entity 必须提供可供引用预览使用的非空 description。",
                )
            )
        if not isinstance(components, dict):
            errors.append(
                _issue(
                    "INVALID_COMPONENTS",
                    "/components",
                    "Entity components 必须是以 Component 名称为键的对象。",
                )
            )
            return EntityValidationReport(False, entity_id if isinstance(entity_id, str) else None, errors)

        # 先保证每个 Component 自身合法，并且允许挂载在当前 Entity Type 上。
        for component_name, instance in components.items():
            registry_item = self.component_validator.store.component(component_name)
            if registry_item is not None and isinstance(entity_type, str):
                if entity_type not in registry_item["allowed_entity_types"]:
                    errors.append(
                        _issue(
                            "COMPONENT_NOT_ALLOWED_ON_ENTITY",
                            f"/components/{component_name}",
                            f"{component_name} 不允许挂载在 {entity_type} Entity。",
                        )
                    )
            if not isinstance(instance, dict):
                errors.append(
                    _issue(
                        "INVALID_COMPONENT_INSTANCE",
                        f"/components/{component_name}",
                        "Component 实例必须是对象。",
                    )
                )
                continue
            report = self.component_validator.validate(component_name, instance)
            for component_error in report.errors:
                suffix = component_error["path"]
                errors.append(
                    {
                        **component_error,
                        "path": f"/components/{component_name}{suffix}",
                    }
                )

        if entity_type == "character_relation":
            errors.extend(_validate_character_relation(components))
        elif entity_type == "item":
            errors.extend(_validate_item(components))
        elif entity_type == "concept":
            errors.extend(_validate_stage_component(components, "stage_framework"))
        elif entity_type == "skill":
            errors.extend(_validate_stage_component(components, "skill_progression"))

        if entity_type == "character":
            errors.extend(_validate_character_skill_stage_state(components))

        return EntityValidationReport(
            valid=not errors,
            entity_id=entity_id if isinstance(entity_id, str) else None,
            errors=errors,
        )


def _validate_character_relation(components: dict[str, Any]) -> list[dict[str, str]]:
    """检查 Character Relation 端点、面向、状态与主观 Memory 锚点的一致性。"""

    errors: list[dict[str, str]] = []
    required = {
        "entity_management",
        "relation_endpoint_reference",
        "character_relation_aspects",
        "character_relation_state",
    }
    for component_name in sorted(required - components.keys()):
        errors.append(
            _issue(
                "MISSING_RELATION_COMPONENT",
                f"/components/{component_name}",
                f"完整 Character Relation 缺少 {component_name}。",
            )
        )

    endpoint_items = _data_array(components, "relation_endpoint_reference", "participant_refs")
    endpoint_ids = [_ref_id(item) for item in endpoint_items]
    valid_endpoint_ids = [item for item in endpoint_ids if item is not None]
    endpoint_set = set(valid_endpoint_ids)
    if len(valid_endpoint_ids) == 2 and valid_endpoint_ids != sorted(valid_endpoint_ids):
        errors.append(
            _issue(
                "NON_CANONICAL_RELATION_ENDPOINT_ORDER",
                "/components/relation_endpoint_reference/data/participant_refs",
                "两个 Character 端点必须按 ID 升序保存。",
            )
        )

    aspects = _data_array(components, "character_relation_aspects", "aspects")
    aspect_ids = {
        item.get("aspect_id")
        for item in aspects
        if isinstance(item, dict) and isinstance(item.get("aspect_id"), str)
    }
    for aspect_index, aspect in enumerate(aspects):
        if not isinstance(aspect, dict):
            continue
        role_entries = aspect.get("participant_roles", [])
        role_ids = {
            _ref_id(item.get("character_ref"))
            for item in role_entries
            if isinstance(item, dict)
        }
        role_ids.discard(None)
        if endpoint_set and role_ids != endpoint_set:
            errors.append(
                _issue(
                    "RELATION_ASPECT_PARTICIPANT_MISMATCH",
                    f"/components/character_relation_aspects/data/aspects/{aspect_index}/participant_roles",
                    "每个关系面向的参与者必须正好是 Relation 的两个端点。",
                )
            )
        perspective = aspect.get("perspective")
        if isinstance(perspective, dict) and perspective.get("mode") == "character_view":
            owner_id = _ref_id(perspective.get("owner_ref"))
            if endpoint_set and owner_id not in endpoint_set:
                errors.append(
                    _issue(
                        "RELATION_PERSPECTIVE_OWNER_MISMATCH",
                        f"/components/character_relation_aspects/data/aspects/{aspect_index}/perspective/owner_ref",
                        "单方认知的 Owner 必须是当前 Relation 的一个端点。",
                    )
                )
        disclosure = aspect.get("disclosure")
        concealed = disclosure.get("concealed_from_refs", []) if isinstance(disclosure, dict) else []
        concealed_ids = {_ref_id(item) for item in concealed}
        concealed_ids.discard(None)
        if endpoint_set and not concealed_ids.issubset(endpoint_set):
            errors.append(
                _issue(
                    "RELATION_DISCLOSURE_TARGET_MISMATCH",
                    f"/components/character_relation_aspects/data/aspects/{aspect_index}/disclosure/concealed_from_refs",
                    "当前版本只能对本 Relation 的参与者声明主动隐瞒。",
                )
            )

    states = _data_array(components, "character_relation_state", "directional_states")
    seen_directions: set[tuple[str, str]] = set()
    for state_index, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        source_id = _ref_id(state.get("from_character_ref"))
        target_id = _ref_id(state.get("toward_character_ref"))
        direction = (source_id, target_id)
        if endpoint_set and ({source_id, target_id} != endpoint_set or source_id == target_id):
            errors.append(
                _issue(
                    "RELATION_STATE_ENDPOINT_MISMATCH",
                    f"/components/character_relation_state/data/directional_states/{state_index}",
                    "方向状态必须从一个 Relation 端点指向另一个端点。",
                )
            )
        if source_id is not None and target_id is not None:
            if direction in seen_directions:
                errors.append(
                    _issue(
                        "DUPLICATE_RELATION_DIRECTION",
                        f"/components/character_relation_state/data/directional_states/{state_index}",
                        "同一方向最多保存一条综合状态。",
                    )
                )
            seen_directions.add(direction)

    memory_entries = _data_array(components, "relation_memory_index", "memory_refs")
    subjective_aspects = [
        aspect
        for aspect in aspects
        if isinstance(aspect, dict)
        and isinstance(aspect.get("perspective"), dict)
        and aspect["perspective"].get("mode") == "character_view"
    ]
    if subjective_aspects and "relation_memory_index" not in components:
        errors.append(
            _issue(
                "RELATION_MEMORY_INDEX_REQUIRED",
                "/components/relation_memory_index",
                "存在单方认知型关系面向时，完整快照必须带有可重建的 RelationMemoryIndex。",
            )
        )
    formation_anchors: set[str] = set()
    seen_memory_ids: set[str] = set()
    for memory_index, entry in enumerate(memory_entries):
        if not isinstance(entry, dict):
            continue
        memory_id = _ref_id(entry.get("memory_ref"))
        if memory_id is not None:
            if memory_id in seen_memory_ids:
                errors.append(
                    _issue(
                        "DUPLICATE_RELATION_MEMORY",
                        f"/components/relation_memory_index/data/memory_refs/{memory_index}/memory_ref",
                        "同一个 Memory 在 RelationMemoryIndex 中只能出现一次；相关面向和角色应放在同一项。",
                    )
                )
            seen_memory_ids.add(memory_id)
        related_ids = set(entry.get("related_aspect_ids", []))
        unknown_ids = related_ids - aspect_ids
        if unknown_ids:
            errors.append(
                _issue(
                    "UNKNOWN_RELATION_ASPECT_REFERENCE",
                    f"/components/relation_memory_index/data/memory_refs/{memory_index}/related_aspect_ids",
                    "Memory 索引引用了当前 Relation 中不存在的 aspect_id。",
                )
            )
        if "formation_basis" in entry.get("index_roles", []):
            formation_anchors.update(related_ids)

    # 本校验器只持有当前 Relation 快照，可以确认形成依据存在，却无法读取
    # 目标 Memory 的 Owner。Memory 是否确实属于 perspective.owner_ref，
    # 应由具备跨 Entity 读取能力的持久化/引用校验阶段确认。
    for aspect_index, aspect in enumerate(aspects):
        if (
            isinstance(aspect, dict)
            and isinstance(aspect.get("perspective"), dict)
            and aspect["perspective"].get("mode") == "character_view"
            and aspect.get("aspect_id") not in formation_anchors
        ):
            errors.append(
                _issue(
                    "RELATION_ASPECT_WITHOUT_MEMORY_BASIS",
                    f"/components/character_relation_aspects/data/aspects/{aspect_index}/aspect_id",
                    "每个单方认知型关系面向必须由至少一个 formation_basis Memory 锚定。",
                )
            )

    return errors


def _validate_item(components: dict[str, Any]) -> list[dict[str, str]]:
    """检查 Item 堆叠数量和放置角色的跨组件含义。"""

    errors: list[dict[str, str]] = []
    profile = _component_data(components, "item_profile")
    state = _component_data(components, "item_state")
    instance_mode = profile.get("instance_mode")
    has_quantity = "quantity" in state
    if instance_mode == "stack" and not has_quantity:
        errors.append(
            _issue(
                "STACK_ITEM_QUANTITY_REQUIRED",
                "/components/item_state/data/quantity",
                "stack Item 必须保存当前数量和单位。",
            )
        )
    if instance_mode in {"unique", "individual"} and has_quantity:
        errors.append(
            _issue(
                "NON_STACK_ITEM_HAS_QUANTITY",
                "/components/item_state/data/quantity",
                "只有 stack Item 可以保存 quantity。",
            )
        )

    placement = _component_data(components, "current_placement_reference")
    target = placement.get("placement_ref")
    role = placement.get("placement_role")
    target_type = target.get("type") if isinstance(target, dict) else None
    roles_by_target = {
        "character": {"carried", "equipped", "worn"},
        "item": {"contained", "stored"},
        "location": {"placed", "stored"},
    }
    if target_type is not None and target_type not in roles_by_target:
        errors.append(
            _issue(
                "ITEM_PLACEMENT_TARGET_TYPE",
                "/components/current_placement_reference/data/placement_ref/type",
                "Item 当前放置目标只能是 Character、Item 或 Location。",
            )
        )
    elif target_type in roles_by_target and role not in roles_by_target[target_type]:
        errors.append(
            _issue(
                "ITEM_PLACEMENT_ROLE_MISMATCH",
                "/components/current_placement_reference/data/placement_role",
                f"placement_role {role!r} 不适用于 {target_type} 目标。",
            )
        )
    return errors


def _validate_stage_component(
    components: dict[str, Any], component_name: str
) -> list[dict[str, str]]:
    """把阶段体系跨数组错误映射到完整 Entity 路径。"""

    component = components.get(component_name)
    if not isinstance(component, dict) or not isinstance(component.get("data"), dict):
        return []
    return [
        _issue(
            item.code,
            f"/components/{component_name}/data{item.path}",
            item.message,
        )
        for item in validate_stage_framework(component["data"])
    ]


def _validate_character_skill_stage_state(
    components: dict[str, Any]
) -> list[dict[str, str]]:
    """检查 SkillReference 中语义阶段和 MUV 当前值的权威边界。"""

    errors: list[dict[str, str]] = []
    for index, entry in enumerate(_data_array(components, "skill_reference", "skill_refs")):
        if not isinstance(entry, dict) or not isinstance(entry.get("stage_state"), dict):
            continue
        stage_state = entry["stage_state"]
        mode = stage_state.get("evaluation_mode")
        current_stage_id = stage_state.get("current_stage_id")
        numeric_values = stage_state.get("numeric_values", [])
        path = f"/components/skill_reference/data/skill_refs/{index}/stage_state"
        if mode in {"semantic", "hybrid"} and not isinstance(current_stage_id, str):
            errors.append(
                _issue(
                    "EXPLICIT_SKILL_STAGE_REQUIRED",
                    f"{path}/current_stage_id",
                    f"{mode} 模式必须由 Character SkillReference 保存显式当前阶段。",
                )
            )
        if mode == "semantic" and numeric_values:
            errors.append(
                _issue(
                    "SEMANTIC_SKILL_STAGE_HAS_NUMERIC_VALUE",
                    f"{path}/numeric_values",
                    "semantic 模式不应保存 MUV 当前值。",
                )
            )
        if mode == "numeric_derived" and not numeric_values:
            errors.append(
                _issue(
                    "NUMERIC_SKILL_STAGE_VALUE_REQUIRED",
                    f"{path}/numeric_values",
                    "numeric_derived 模式必须提供受控写入的 MUV 当前值。",
                )
            )
        seen: set[str] = set()
        for value_index, value in enumerate(numeric_values):
            if not isinstance(value, dict):
                continue
            binding_id = value.get("numeric_binding_id")
            if isinstance(binding_id, str) and binding_id in seen:
                errors.append(
                    _issue(
                        "DUPLICATE_SKILL_NUMERIC_VALUE",
                        f"{path}/numeric_values/{value_index}/numeric_binding_id",
                        "同一 SkillReference 中一个 numeric_binding_id 只能有一个当前值。",
                    )
                )
            if isinstance(binding_id, str):
                seen.add(binding_id)
    return errors


def _component_data(
    components: dict[str, Any], component_name: str
) -> dict[str, Any]:
    """安全读取 Component data；缺失或不合法时返回空对象。"""

    component = components.get(component_name)
    if not isinstance(component, dict) or not isinstance(component.get("data"), dict):
        return {}
    return component["data"]


def _data_array(
    components: dict[str, Any], component_name: str, field_name: str
) -> list[Any]:
    """安全读取 ``components[name].data[field]`` 中的数组；缺失时返回空数组。"""

    component = components.get(component_name)
    if not isinstance(component, dict):
        return []
    data = component.get("data")
    if not isinstance(data, dict):
        return []
    value = data.get(field_name)
    return value if isinstance(value, list) else []


def _ref_id(value: Any) -> str | None:
    """从最小 Entity Reference 中读取 ID；结构不合法时交给 Component 校验报错。"""

    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    """统一创建与 ComponentValidator 相同格式的错误。"""

    return ValidationIssue(code, path, message).to_dict()
