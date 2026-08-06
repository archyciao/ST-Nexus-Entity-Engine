"""Entity 之间的引用校验与可重建索引投影。

文件功能：把一组完整 Entity 当作一个封闭测试网络，检查每条引用是否真的指向
存在且类型一致的目标，并用权威 Reference 自动重建 Character、Location、Item、
Organization、Event 与 Relation 侧的反向 Index。它解决单个 EntityValidator
无法读取目标 Entity、阶段框架或容器链的问题。

架构位置：本模块位于单 Entity 校验之后、持久化写入之前。Reference 是事实方向，
Index 只是固定代码产生的查询入口；本模块不会让索引反向覆盖 Memory、Event 或
Relation 的权威内容，也不负责 Event 边界和 Memory 正文生成。
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entity_validator import EntityValidator
from .stage_context import StageContextError, build_stage_context
from .validator import ValidationIssue


@dataclass
class EntityNetworkValidationReport:
    """一组 Entity 的结构、引用与反向索引校验结果。"""

    valid: bool
    entity_count: int
    errors: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        """转换为适合命令行、日志和测试报告的普通字典。"""

        return {
            "valid": self.valid,
            "entity_count": self.entity_count,
            "errors": self.errors,
        }


class EntityNetworkValidator:
    """校验一个封闭 Entity 集合中的跨 Entity 引用与索引一致性。"""

    _projected_components = {
        "memory_index",
        "relation_index",
        "history_index",
        "event_memory_index",
        "relation_memory_index",
        "inventory_index",
        "containment_index",
        "contents_index",
        "child_location_index",
        "child_organization_index",
    }

    def __init__(self, project_root: str | Path):
        """加载 Registry、Component Schema 与单 Entity 校验器。"""

        self.entity_validator = EntityValidator(project_root)
        self.store = self.entity_validator.component_validator.store

    def validate(
        self,
        entities: Iterable[dict[str, Any]],
        *,
        require_derived_indexes: bool = False,
    ) -> EntityNetworkValidationReport:
        """校验封闭网络。

        ``require_derived_indexes`` 为 false 时，Index 可以省略；一旦存在就必须与
        权威 Reference 一致。为 true 时还要求应有的反向 Index 已经物化，适合在
        固定脚本重建之后或正式提交之前使用。
        """

        entity_list = list(entities)
        errors: list[dict[str, str]] = []
        by_id: dict[str, dict[str, Any]] = {}

        for entity_index, entity in enumerate(entity_list):
            if not isinstance(entity, dict):
                errors.append(
                    _issue(
                        "INVALID_NETWORK_ENTITY",
                        f"/entities/{entity_index}",
                        "Entity 网络中的每一项都必须是对象。",
                    )
                )
                continue

            report = self.entity_validator.validate(entity)
            for item in report.errors:
                errors.append(
                    {
                        **item,
                        "path": f"/entities/{entity_index}{item['path']}",
                    }
                )

            entity_id = entity.get("id")
            if not isinstance(entity_id, str):
                continue
            if entity_id in by_id:
                errors.append(
                    _issue(
                        "DUPLICATE_ENTITY_ID",
                        f"/entities/{entity_index}/id",
                        f"封闭网络中出现重复 Entity ID：{entity_id}。",
                    )
                )
            else:
                by_id[entity_id] = entity

        errors.extend(self._validate_registered_references(entity_list, by_id))
        errors.extend(_validate_character_skill_stage_links(entity_list, by_id))
        errors.extend(_validate_memory_network_requirements(entity_list, by_id))
        errors.extend(_validate_memory_reference_ownership(entity_list, by_id))
        errors.extend(_validate_relation_memory_ownership(entity_list, by_id))
        errors.extend(_validate_event_related_entity_uniqueness(entity_list))
        errors.extend(_validate_event_location_sequence_uniqueness(entity_list))
        errors.extend(_validate_item_placement_network(entity_list, by_id))
        errors.extend(
            _validate_parent_cycles(
                entity_list,
                entity_type="location",
                component_name="parent_location_reference",
                reference_field="parent_location_ref",
                code="LOCATION_PARENT_CYCLE",
            )
        )
        errors.extend(
            _validate_parent_cycles(
                entity_list,
                entity_type="organization",
                component_name="parent_organization_reference",
                reference_field="parent_organization_ref",
                code="ORGANIZATION_PARENT_CYCLE",
            )
        )

        projected = rebuild_derived_indexes(entity_list)
        projected_by_id = {
            item.get("id"): item
            for item in projected
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        errors.extend(
            _validate_projected_indexes(
                entity_list,
                projected_by_id,
                self._projected_components,
                require_derived_indexes=require_derived_indexes,
            )
        )

        return EntityNetworkValidationReport(
            valid=not errors,
            entity_count=len(entity_list),
            errors=errors,
        )

    def _validate_registered_references(
        self,
        entities: list[dict[str, Any]],
        by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, str]]:
        """按照 Registry 的引用路径核对目标存在性与实际 Type。"""

        errors: list[dict[str, str]] = []
        for entity_index, entity in enumerate(entities):
            if not isinstance(entity, dict):
                continue
            components = entity.get("components")
            if not isinstance(components, dict):
                continue
            for component_name, component in components.items():
                if not isinstance(component, dict):
                    continue
                registry_item = self.store.component(component_name)
                if registry_item is None:
                    continue
                for reference_rule in registry_item.get("references", []):
                    pointer = reference_rule.get("path")
                    if not isinstance(pointer, str):
                        continue
                    for reference in _walk_pointer(component, pointer):
                        if not isinstance(reference, dict):
                            continue
                        target_id = reference.get("id")
                        target_type = reference.get("type")
                        if not isinstance(target_id, str) or not isinstance(target_type, str):
                            continue
                        target = by_id.get(target_id)
                        path = f"/entities/{entity_index}/components/{component_name}{pointer}"
                        if target is None:
                            errors.append(
                                _issue(
                                    "MISSING_REFERENCE_TARGET",
                                    path,
                                    f"引用目标不存在于当前封闭网络：{target_id}。",
                                )
                            )
                        elif target.get("type") != target_type:
                            errors.append(
                                _issue(
                                    "REFERENCE_TARGET_TYPE_MISMATCH",
                                    path,
                                    f"引用声明为 {target_type}，目标 Entity 实际为 {target.get('type')}。",
                                )
                            )
        return errors


def rebuild_derived_indexes(
    entities: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """根据权威 Reference 返回重建反向 Index 后的深拷贝。

    当前稳定闭环还包括 Event 相关对象的 History、Location/Organization 的直接
    父引用反向目录，以及 Item CurrentPlacement 反向生成 Character Inventory、
    Location Containment 和 Item Contents。Index 不能反向覆盖这些权威 Reference。
    """

    result = copy.deepcopy(list(entities))
    by_id = {
        item.get("id"): item
        for item in result
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    memories_by_owner: dict[str, set[str]] = defaultdict(set)
    memories_by_event: dict[str, set[str]] = defaultdict(set)
    events_by_entity: dict[str, set[str]] = defaultdict(set)
    relations_by_character: dict[str, set[str]] = defaultdict(set)
    relation_memory_links: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(dict)
    child_locations: dict[str, set[str]] = defaultdict(set)
    child_organizations: dict[str, set[str]] = defaultdict(set)
    inventory_by_character: dict[str, list[dict[str, Any]]] = defaultdict(list)
    contents_by_item: dict[str, set[str]] = defaultdict(set)
    contents_by_location: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for entity in result:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("id")
        entity_type = entity.get("type")
        if not isinstance(entity_id, str):
            continue

        if entity_type == "memory":
            owner_id = _reference_id(
                _component_data(entity, "memory_owner_reference").get("owner_ref")
            )
            if owner_id is not None:
                memories_by_owner[owner_id].add(entity_id)
            for event_ref in _component_data(entity, "source_event_reference").get(
                "event_refs", []
            ):
                event_id = _reference_id(event_ref)
                if event_id is not None:
                    memories_by_event[event_id].add(entity_id)
            for link in _component_data(entity, "relation_context_reference").get(
                "relation_links", []
            ):
                if not isinstance(link, dict):
                    continue
                relation_id = _reference_id(link.get("relation_ref"))
                if relation_id is None:
                    continue
                aggregate = relation_memory_links[relation_id].setdefault(
                    entity_id,
                    {"aspects": set(), "roles": set()},
                )
                aggregate["aspects"].update(
                    item for item in link.get("related_aspect_ids", []) if isinstance(item, str)
                )
                aggregate["roles"].update(
                    item for item in link.get("context_roles", []) if isinstance(item, str)
                )

        elif entity_type == "event":
            for entry in _component_data(entity, "event_location_reference").get(
                "location_refs", []
            ):
                location_id = _reference_id(
                    entry.get("location_ref") if isinstance(entry, dict) else None
                )
                if location_id is not None:
                    events_by_entity[location_id].add(entity_id)
            for reference in _component_data(
                entity, "event_related_entity_reference"
            ).get("related_entity_refs", []):
                related_entity_id = _reference_id(reference)
                if related_entity_id is not None:
                    events_by_entity[related_entity_id].add(entity_id)

        elif entity_type == "character_relation":
            for participant_ref in _component_data(
                entity, "relation_endpoint_reference"
            ).get("participant_refs", []):
                participant_id = _reference_id(participant_ref)
                if participant_id is not None:
                    relations_by_character[participant_id].add(entity_id)

        elif entity_type == "character":
            location_id = _reference_id(
                _component_data(entity, "current_location_reference").get("location_ref")
            )
            if location_id is not None:
                contents_by_location[location_id].append(
                    {
                        "entity_ref": {"id": entity_id, "type": "character"},
                        "containment_role": "present",
                    }
                )

        elif entity_type == "location":
            parent_id = _reference_id(
                _component_data(entity, "parent_location_reference").get(
                    "parent_location_ref"
                )
            )
            if parent_id is not None:
                child_locations[parent_id].add(entity_id)

        elif entity_type == "organization":
            parent_id = _reference_id(
                _component_data(entity, "parent_organization_reference").get(
                    "parent_organization_ref"
                )
            )
            if parent_id is not None:
                child_organizations[parent_id].add(entity_id)

        elif entity_type == "item":
            placement = _component_data(entity, "current_placement_reference")
            target = placement.get("placement_ref")
            target_id = _reference_id(target)
            target_type = target.get("type") if isinstance(target, dict) else None
            role = placement.get("placement_role")
            if target_type == "character" and target_id is not None:
                if role in {"carried", "equipped", "worn"}:
                    inventory_by_character[target_id].append(
                        {
                            "item_ref": {"id": entity_id, "type": "item"},
                            "inventory_roles": [role],
                        }
                    )
            elif target_type == "item" and target_id is not None:
                contents_by_item[target_id].add(entity_id)
            elif target_type == "location" and target_id is not None:
                contents_by_location[target_id].append(
                    {
                        "entity_ref": {"id": entity_id, "type": "item"},
                        "containment_role": "stored" if role == "stored" else "placed",
                    }
                )

    for entity_id, entity in by_id.items():
        entity_type = entity.get("type")
        if entity_type == "character":
            _replace_simple_index(
                entity,
                "memory_index",
                "memory_refs",
                memories_by_owner.get(entity_id, set()),
                "memory",
            )
            _replace_simple_index(
                entity,
                "relation_index",
                "relation_refs",
                relations_by_character.get(entity_id, set()),
                "character_relation",
            )
            _upsert_history_index(entity, events_by_entity.get(entity_id, set()))
            _replace_index_component(
                entity,
                "inventory_index",
                "item_refs",
                sorted(
                    inventory_by_character.get(entity_id, []),
                    key=lambda item: _reference_id(item.get("item_ref")) or "",
                ),
            )

        elif entity_type == "location":
            _upsert_history_index(entity, events_by_entity.get(entity_id, set()))
            _replace_simple_index(
                entity,
                "child_location_index",
                "location_refs",
                child_locations.get(entity_id, set()),
                "location",
            )
            _replace_index_component(
                entity,
                "containment_index",
                "entity_refs",
                sorted(
                    contents_by_location.get(entity_id, []),
                    key=lambda item: _reference_id(item.get("entity_ref")) or "",
                ),
            )

        elif entity_type == "item":
            _upsert_history_index(entity, events_by_entity.get(entity_id, set()))
            _replace_simple_index(
                entity,
                "contents_index",
                "item_refs",
                contents_by_item.get(entity_id, set()),
                "item",
            )

        elif entity_type == "organization":
            _upsert_history_index(entity, events_by_entity.get(entity_id, set()))
            _replace_simple_index(
                entity,
                "child_organization_index",
                "organization_refs",
                child_organizations.get(entity_id, set()),
                "organization",
            )

        elif entity_type in {"skill", "concept"}:
            _upsert_history_index(entity, events_by_entity.get(entity_id, set()))

        elif entity_type == "event":
            _replace_simple_index(
                entity,
                "event_memory_index",
                "memory_refs",
                memories_by_event.get(entity_id, set()),
                "memory",
            )

        elif entity_type == "character_relation":
            _upsert_history_index(entity, events_by_entity.get(entity_id, set()))
            entries = []
            for memory_id, aggregate in sorted(
                relation_memory_links.get(entity_id, {}).items()
            ):
                entries.append(
                    {
                        "memory_ref": {"id": memory_id, "type": "memory"},
                        "related_aspect_ids": sorted(aggregate["aspects"]),
                        "index_roles": sorted(aggregate["roles"]),
                    }
                )
            _replace_index_component(entity, "relation_memory_index", "memory_refs", entries)

    return result


def _validate_memory_network_requirements(
    entities: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Memory 在实体网络中必须同时拥有 Owner 和至少一个 Event 锚点。"""

    del by_id
    errors: list[dict[str, str]] = []
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or entity.get("type") != "memory":
            continue
        components = entity.get("components", {})
        for component_name in ("memory_owner_reference", "source_event_reference"):
            if component_name not in components:
                errors.append(
                    _issue(
                        "MISSING_MEMORY_NETWORK_COMPONENT",
                        f"/entities/{index}/components/{component_name}",
                        f"Memory 缺少形成实体网络所需的 {component_name}。",
                    )
                )
    return errors


def _validate_character_skill_stage_links(
    entities: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """核对 Character 当前阶段、MUV 值与目标 Skill/Concept 框架。"""

    errors: list[dict[str, str]] = []
    for entity_index, character in enumerate(entities):
        if not isinstance(character, dict) or character.get("type") != "character":
            continue
        entries = _component_data(character, "skill_reference").get("skill_refs", [])
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("stage_state"), dict):
                continue
            stage_state = entry["stage_state"]
            framework_ref = stage_state.get("framework_ref")
            framework_id = _reference_id(framework_ref)
            framework = by_id.get(framework_id) if framework_id is not None else None
            if not isinstance(framework, dict):
                continue
            framework_type = framework.get("type")
            skill_id = _reference_id(entry.get("skill_ref"))
            path = (
                f"/entities/{entity_index}/components/skill_reference/data/"
                f"skill_refs/{entry_index}/stage_state"
            )
            if framework_type == "skill" and framework_id != skill_id:
                errors.append(
                    _issue(
                        "SKILL_STAGE_FRAMEWORK_MISMATCH",
                        f"{path}/framework_ref",
                        "Skill 自身阶段框架必须来自当前 skill_ref；共享框架应引用 Concept。",
                    )
                )
                continue
            component_name = (
                "skill_progression" if framework_type == "skill" else "stage_framework"
            )
            framework_data = _component_data(framework, component_name)
            if not framework_data:
                errors.append(
                    _issue(
                        "STAGE_FRAMEWORK_COMPONENT_MISSING",
                        f"{path}/framework_ref",
                        f"目标 {framework_type} 没有可供解析的 {component_name}。",
                    )
                )
                continue

            evaluation = framework_data.get("stage_evaluation")
            framework_mode = evaluation.get("mode") if isinstance(evaluation, dict) else None
            host_mode = stage_state.get("evaluation_mode")
            if framework_mode != host_mode:
                errors.append(
                    _issue(
                        "STAGE_EVALUATION_MODE_MISMATCH",
                        f"{path}/evaluation_mode",
                        "Host 的 evaluation_mode 必须与所引用阶段框架一致。",
                    )
                )
                continue

            id_field = "skill_stage_id" if framework_type == "skill" else "stage_id"
            stage_ids = {
                stage.get(id_field)
                for stage in framework_data.get("stages", [])
                if isinstance(stage, dict) and isinstance(stage.get(id_field), str)
            }
            current_stage_id = stage_state.get("current_stage_id")
            if isinstance(current_stage_id, str) and current_stage_id not in stage_ids:
                errors.append(
                    _issue(
                        "UNKNOWN_HOST_STAGE",
                        f"{path}/current_stage_id",
                        "Host 当前阶段不存在于所引用的 Skill/Concept 阶段框架。",
                    )
                )

            binding_ids = {
                item.get("numeric_binding_id")
                for item in framework_data.get("stage_evaluation", {}).get(
                    "numeric_bindings", []
                )
                if isinstance(item, dict)
                and isinstance(item.get("numeric_binding_id"), str)
            }
            numeric_values: dict[str, float] = {}
            numeric_bindings_valid = True
            for value_index, value in enumerate(stage_state.get("numeric_values", [])):
                if not isinstance(value, dict):
                    continue
                binding_id = value.get("numeric_binding_id")
                number = value.get("value")
                if binding_id not in binding_ids:
                    numeric_bindings_valid = False
                    errors.append(
                        _issue(
                            "UNKNOWN_HOST_NUMERIC_BINDING",
                            f"{path}/numeric_values/{value_index}/numeric_binding_id",
                            "Host MUV 当前值引用了阶段框架中不存在的 Binding。",
                        )
                    )
                elif isinstance(number, (int, float)) and not isinstance(number, bool):
                    numeric_values[str(binding_id)] = float(number)

            if host_mode == "numeric_derived" and numeric_bindings_valid:
                try:
                    package = build_stage_context(
                        framework_data,
                        numeric_values=numeric_values,
                    )
                except StageContextError as exc:
                    errors.append(
                        _issue(exc.code, f"{path}/numeric_values", str(exc))
                    )
                else:
                    if (
                        isinstance(current_stage_id, str)
                        and current_stage_id != package["current_stage_id"]
                    ):
                        errors.append(
                            _issue(
                                "STALE_NUMERIC_STAGE_CACHE",
                                f"{path}/current_stage_id",
                                "缓存阶段与当前 MUV 确定性投影不一致，应由脚本重建。",
                            )
                        )
    return errors


def _validate_memory_reference_ownership(
    entities: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Character 只能固定或激活属于自己的 Memory。"""

    errors: list[dict[str, str]] = []
    for index, character in enumerate(entities):
        if not isinstance(character, dict) or character.get("type") != "character":
            continue
        character_id = character.get("id")
        entries = _component_data(character, "memory_reference").get("memory_refs", [])
        for memory_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            memory_id = _reference_id(entry.get("memory_ref"))
            memory = by_id.get(memory_id) if memory_id is not None else None
            if memory is None or memory.get("type") != "memory":
                continue
            owner_id = _reference_id(
                _component_data(memory, "memory_owner_reference").get("owner_ref")
            )
            if owner_id is not None and owner_id != character_id:
                errors.append(
                    _issue(
                        "MEMORY_REFERENCE_OWNER_MISMATCH",
                        f"/entities/{index}/components/memory_reference/data/memory_refs/{memory_index}/memory_ref",
                        "Character 的固定或活跃 Memory 必须由该 Character 自己拥有。",
                    )
                )
    return errors


def _validate_relation_memory_ownership(
    entities: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """核对单方认知 Relation Aspect 与形成依据 Memory 的 Owner。"""

    errors: list[dict[str, str]] = []
    for entity_index, relation in enumerate(entities):
        if not isinstance(relation, dict) or relation.get("type") != "character_relation":
            continue
        aspect_owners: dict[str, str] = {}
        for aspect in _component_data(relation, "character_relation_aspects").get(
            "aspects", []
        ):
            if not isinstance(aspect, dict):
                continue
            perspective = aspect.get("perspective")
            if not isinstance(perspective, dict) or perspective.get("mode") != "character_view":
                continue
            aspect_id = aspect.get("aspect_id")
            owner_id = _reference_id(perspective.get("owner_ref"))
            if isinstance(aspect_id, str) and owner_id is not None:
                aspect_owners[aspect_id] = owner_id

        entries = _component_data(relation, "relation_memory_index").get("memory_refs", [])
        for memory_index, entry in enumerate(entries):
            if not isinstance(entry, dict) or "formation_basis" not in entry.get(
                "index_roles", []
            ):
                continue
            memory_id = _reference_id(entry.get("memory_ref"))
            memory = by_id.get(memory_id) if memory_id is not None else None
            if memory is None or memory.get("type") != "memory":
                continue
            memory_owner = _reference_id(
                _component_data(memory, "memory_owner_reference").get("owner_ref")
            )
            for aspect_id in entry.get("related_aspect_ids", []):
                expected_owner = aspect_owners.get(aspect_id)
                if expected_owner is not None and memory_owner != expected_owner:
                    errors.append(
                        _issue(
                            "RELATION_MEMORY_OWNER_MISMATCH",
                            f"/entities/{entity_index}/components/relation_memory_index/data/memory_refs/{memory_index}/memory_ref",
                            "单方认知 Aspect 的 formation_basis Memory 必须属于该认知 Owner。",
                        )
                    )
    return errors


def _validate_event_location_sequence_uniqueness(
    entities: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """同一 Event 的两次地点经过不得使用同一个顺序号。

    同一 Location 可以在离开后再次经过，因此这里只校验顺序，不把重复地点误判
    为错误。
    """

    errors: list[dict[str, str]] = []
    for entity_index, event in enumerate(entities):
        if not isinstance(event, dict) or event.get("type") != "event":
            continue
        seen: set[int] = set()
        entries = _component_data(event, "event_location_reference").get(
            "location_refs", []
        )
        for location_index, entry in enumerate(entries):
            sequence = entry.get("sequence") if isinstance(entry, dict) else None
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                continue
            if sequence in seen:
                errors.append(
                    _issue(
                        "DUPLICATE_EVENT_LOCATION_SEQUENCE",
                        f"/entities/{entity_index}/components/event_location_reference/data/location_refs/{location_index}/sequence",
                        "同一 Event 的地点经过顺序不能重复。",
                    )
                )
            seen.add(sequence)
    return errors


def _validate_event_related_entity_uniqueness(
    entities: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """同一 Event 中同一相关 Entity 只保留一个条目。"""

    errors: list[dict[str, str]] = []
    for entity_index, event in enumerate(entities):
        if not isinstance(event, dict) or event.get("type") != "event":
            continue
        seen: set[str] = set()
        entries = _component_data(event, "event_related_entity_reference").get(
            "related_entity_refs", []
        )
        for related_index, reference in enumerate(entries):
            related_entity_id = _reference_id(reference)
            if related_entity_id is None:
                continue
            if related_entity_id in seen:
                errors.append(
                    _issue(
                        "DUPLICATE_EVENT_RELATED_ENTITY",
                        f"/entities/{entity_index}/components/event_related_entity_reference/data/related_entity_refs/{related_index}",
                        "同一 Entity 在 Event 相关对象中只能出现一次。",
                    )
                )
            seen.add(related_entity_id)
    return errors


def _validate_item_placement_network(
    entities: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """检查 Item 容器目标能力与袋中袋放置循环。"""

    errors: list[dict[str, str]] = []
    parent_items: dict[str, str] = {}
    entity_positions: dict[str, int] = {}
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or entity.get("type") != "item":
            continue
        item_id = entity.get("id")
        if not isinstance(item_id, str):
            continue
        entity_positions[item_id] = index
        placement = _component_data(entity, "current_placement_reference")
        target = placement.get("placement_ref")
        target_id = _reference_id(target)
        target_type = target.get("type") if isinstance(target, dict) else None
        if target_type != "item" or target_id is None:
            continue
        parent_items[item_id] = target_id
        target_entity = by_id.get(target_id)
        target_components = (
            target_entity.get("components", {}) if isinstance(target_entity, dict) else {}
        )
        if not isinstance(target_components, dict) or "container_profile" not in target_components:
            errors.append(
                _issue(
                    "ITEM_PLACED_IN_NON_CONTAINER",
                    f"/entities/{index}/components/current_placement_reference/data/placement_ref",
                    "Item 只能放入具有 ContainerProfile 的目标 Item。",
                )
            )

    for item_id, start_index in entity_positions.items():
        seen: set[str] = set()
        current = item_id
        while current in parent_items:
            if current in seen:
                errors.append(
                    _issue(
                        "ITEM_PLACEMENT_CYCLE",
                        f"/entities/{start_index}/components/current_placement_reference",
                        "Item 放置链不能回到自身或形成容器循环。",
                    )
                )
                break
            seen.add(current)
            current = parent_items[current]
    return errors


def _validate_parent_cycles(
    entities: list[dict[str, Any]],
    *,
    entity_type: str,
    component_name: str,
    reference_field: str,
    code: str,
) -> list[dict[str, str]]:
    """检查 Location 与 Organization 的单父层级不能形成循环。"""

    parents: dict[str, str] = {}
    positions: dict[str, int] = {}
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or entity.get("type") != entity_type:
            continue
        entity_id = entity.get("id")
        if not isinstance(entity_id, str):
            continue
        positions[entity_id] = index
        parent_id = _reference_id(
            _component_data(entity, component_name).get(reference_field)
        )
        if parent_id is not None:
            parents[entity_id] = parent_id

    errors: list[dict[str, str]] = []
    reported: set[str] = set()
    for entity_id, index in positions.items():
        current = entity_id
        seen: set[str] = set()
        while current in parents:
            if current in seen:
                signature = "|".join(sorted(seen))
                if signature not in reported:
                    errors.append(
                        _issue(
                            code,
                            f"/entities/{index}/components/{component_name}",
                            f"{entity_type} 的直接父级引用不能形成循环。",
                        )
                    )
                    reported.add(signature)
                break
            seen.add(current)
            current = parents[current]
    return errors


def _validate_projected_indexes(
    entities: list[dict[str, Any]],
    projected_by_id: dict[str, dict[str, Any]],
    component_names: set[str],
    *,
    require_derived_indexes: bool,
) -> list[dict[str, str]]:
    """把当前 Index 与由权威 Reference 计算的结果比较。"""

    errors: list[dict[str, str]] = []
    for entity_index, entity in enumerate(entities):
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str):
            continue
        projected = projected_by_id.get(entity["id"], {})
        current_components = entity.get("components", {})
        projected_components = projected.get("components", {})
        if not isinstance(current_components, dict) or not isinstance(projected_components, dict):
            continue
        for component_name in sorted(component_names):
            current = current_components.get(component_name)
            expected = projected_components.get(component_name)
            if current is None and expected is None:
                continue
            if current is None:
                if require_derived_indexes:
                    errors.append(
                        _issue(
                            "MISSING_DERIVED_INDEX",
                            f"/entities/{entity_index}/components/{component_name}",
                            f"缺少可由权威 Reference 重建的 {component_name}。",
                        )
                    )
                continue
            if _canonical_component(component_name, current) != _canonical_component(
                component_name, expected
            ):
                errors.append(
                    _issue(
                        "STALE_DERIVED_INDEX",
                        f"/entities/{entity_index}/components/{component_name}",
                        f"{component_name} 与权威 Reference 推导结果不一致，应由固定脚本重建。",
                    )
                )
    return errors


def _replace_simple_index(
    entity: dict[str, Any],
    component_name: str,
    field_name: str,
    target_ids: set[str],
    target_type: str,
) -> None:
    entries = [{"id": target_id, "type": target_type} for target_id in sorted(target_ids)]
    _replace_index_component(entity, component_name, field_name, entries)


def _replace_index_component(
    entity: dict[str, Any],
    component_name: str,
    field_name: str,
    entries: list[dict[str, Any]],
) -> None:
    components = entity.setdefault("components", {})
    if entries:
        components[component_name] = {
            "schema_version": "0.1.0",
            "data": {field_name: entries},
        }
    else:
        components.pop(component_name, None)


def _upsert_history_index(entity: dict[str, Any], event_ids: set[str]) -> None:
    """按权威 Event 引用重建最小历史目录，不持久化可计算分类。"""

    _replace_simple_index(entity, "history_index", "event_refs", event_ids, "event")


def _component_data(entity: dict[str, Any], component_name: str) -> dict[str, Any]:
    components = entity.get("components")
    if not isinstance(components, dict):
        return {}
    component = components.get(component_name)
    if not isinstance(component, dict):
        return {}
    data = component.get("data")
    return data if isinstance(data, dict) else {}


def _reference_id(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _walk_pointer(root: Any, pointer: str) -> list[Any]:
    """读取 Registry 中含 ``*`` 的简化 JSON Pointer。"""

    values = [root]
    for raw_token in pointer.strip("/").split("/") if pointer else []:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        next_values: list[Any] = []
        for value in values:
            if token == "*":
                if isinstance(value, list):
                    next_values.extend(value)
                elif isinstance(value, dict):
                    next_values.extend(value.values())
            elif isinstance(value, dict) and token in value:
                next_values.append(value[token])
        values = next_values
    return values


def _canonical_component(component_name: str, value: Any) -> str:
    """忽略派生目录的无意义排列差异，比较其实际成员与角色。"""

    normalized = copy.deepcopy(value)
    if isinstance(normalized, dict):
        data = normalized.get("data")
        if isinstance(data, dict):
            if component_name in {"memory_index", "event_memory_index"}:
                refs = data.get("memory_refs")
                if isinstance(refs, list):
                    refs.sort(key=lambda item: item.get("id", "") if isinstance(item, dict) else "")
            elif component_name == "relation_index":
                refs = data.get("relation_refs")
                if isinstance(refs, list):
                    refs.sort(key=lambda item: item.get("id", "") if isinstance(item, dict) else "")
            elif component_name == "history_index":
                entries = data.get("event_refs")
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and isinstance(entry.get("index_roles"), list):
                            entry["index_roles"].sort()
                    entries.sort(
                        key=lambda entry: _reference_id(entry.get("event_ref")) or ""
                        if isinstance(entry, dict)
                        else ""
                    )
            elif component_name == "relation_memory_index":
                entries = data.get("memory_refs")
                if isinstance(entries, list):
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        for field in ("related_aspect_ids", "index_roles"):
                            if isinstance(entry.get(field), list):
                                entry[field].sort()
                    entries.sort(
                        key=lambda entry: _reference_id(entry.get("memory_ref")) or ""
                        if isinstance(entry, dict)
                        else ""
                    )
            elif component_name in {
                "child_location_index",
                "contents_index",
                "child_organization_index",
            }:
                field_name = {
                    "child_location_index": "location_refs",
                    "contents_index": "item_refs",
                    "child_organization_index": "organization_refs",
                }[component_name]
                refs = data.get(field_name)
                if isinstance(refs, list):
                    refs.sort(key=lambda item: _reference_id(item) or "")
            elif component_name == "inventory_index":
                entries = data.get("item_refs")
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and isinstance(
                            entry.get("inventory_roles"), list
                        ):
                            entry["inventory_roles"].sort()
                    entries.sort(
                        key=lambda entry: _reference_id(entry.get("item_ref")) or ""
                        if isinstance(entry, dict)
                        else ""
                    )
            elif component_name == "containment_index":
                entries = data.get("entity_refs")
                if isinstance(entries, list):
                    entries.sort(
                        key=lambda entry: _reference_id(entry.get("entity_ref")) or ""
                        if isinstance(entry, dict)
                        else ""
                    )
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return ValidationIssue(code, path, message).to_dict()
