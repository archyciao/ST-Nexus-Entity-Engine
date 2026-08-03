"""Entity 之间的引用校验与可重建索引投影。

文件功能：把一组完整 Entity 当作一个封闭测试网络，检查每条引用是否真的指向
存在且类型一致的目标，并用权威 Reference 自动重建 Character、Location、Event、
Relation 侧的反向 Index。它解决单个 EntityValidator 无法读取目标 Entity 的问题。

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
        errors.extend(_validate_memory_network_requirements(entity_list, by_id))
        errors.extend(_validate_memory_reference_ownership(entity_list, by_id))
        errors.extend(_validate_witnessed_memory_participation(entity_list, by_id))
        errors.extend(_validate_relation_memory_ownership(entity_list, by_id))
        errors.extend(_validate_event_participant_uniqueness(entity_list))
        errors.extend(_validate_event_location_sequence_uniqueness(entity_list))

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

    当前稳定闭环包括：Memory Owner → Character MemoryIndex、Event Participant →
    Character HistoryIndex、Event Location → Location HistoryIndex、Relation Endpoint
    → Character RelationIndex、Memory Source Event → EventMemoryIndex，以及 Memory
    Relation Context → RelationMemoryIndex。HistoryIndex 还可能来自未来的状态变化
    引用，因此函数只更新当前已实现来源的 ``recent`` 角色，保留其他检索角色。
    """

    result = copy.deepcopy(list(entities))
    by_id = {
        item.get("id"): item
        for item in result
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    memories_by_owner: dict[str, set[str]] = defaultdict(set)
    memories_by_event: dict[str, set[str]] = defaultdict(set)
    events_by_character: dict[str, set[str]] = defaultdict(set)
    events_by_location: dict[str, set[str]] = defaultdict(set)
    relations_by_character: dict[str, set[str]] = defaultdict(set)
    relation_memory_links: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(dict)

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
            for entry in _component_data(entity, "event_participant_reference").get(
                "participant_refs", []
            ):
                participant_id = _reference_id(
                    entry.get("participant_ref") if isinstance(entry, dict) else None
                )
                if participant_id is not None:
                    events_by_character[participant_id].add(entity_id)
            for entry in _component_data(entity, "event_location_reference").get(
                "location_refs", []
            ):
                location_id = _reference_id(
                    entry.get("location_ref") if isinstance(entry, dict) else None
                )
                if location_id is not None:
                    events_by_location[location_id].add(entity_id)

        elif entity_type == "character_relation":
            for participant_ref in _component_data(
                entity, "relation_endpoint_reference"
            ).get("participant_refs", []):
                participant_id = _reference_id(participant_ref)
                if participant_id is not None:
                    relations_by_character[participant_id].add(entity_id)

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
            _upsert_history_index(entity, events_by_character.get(entity_id, set()))

        elif entity_type == "location":
            _upsert_history_index(entity, events_by_location.get(entity_id, set()))

        elif entity_type == "event":
            _replace_simple_index(
                entity,
                "event_memory_index",
                "memory_refs",
                memories_by_event.get(entity_id, set()),
                "memory",
            )

        elif entity_type == "character_relation":
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


def _validate_witnessed_memory_participation(
    entities: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """亲历型 Memory 的 Owner 必须出现在来源 Event 的参与者或观察者目录中。"""

    errors: list[dict[str, str]] = []
    for entity_index, memory in enumerate(entities):
        if not isinstance(memory, dict) or memory.get("type") != "memory":
            continue
        source = _component_data(memory, "source_event_reference")
        if source.get("acquisition_mode") != "witnessed_event":
            continue
        owner_id = _reference_id(
            _component_data(memory, "memory_owner_reference").get("owner_ref")
        )
        if owner_id is None:
            continue
        for source_index, event_ref in enumerate(source.get("event_refs", [])):
            event_id = _reference_id(event_ref)
            event = by_id.get(event_id) if event_id is not None else None
            if event is None or event.get("type") != "event":
                continue
            participant_ids = {
                _reference_id(entry.get("participant_ref"))
                for entry in _component_data(
                    event, "event_participant_reference"
                ).get("participant_refs", [])
                if isinstance(entry, dict)
            }
            participant_ids.discard(None)
            if owner_id not in participant_ids:
                errors.append(
                    _issue(
                        "WITNESSED_MEMORY_OWNER_NOT_EVENT_PARTICIPANT",
                        f"/entities/{entity_index}/components/source_event_reference/data/event_refs/{source_index}",
                        "亲历型 Memory 的 Owner 必须作为参与者、观察者或接收者出现在来源 Event 中。",
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


def _validate_event_participant_uniqueness(
    entities: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """同一 Event 不得把同一 Character 拆成多个参与者条目。"""

    errors: list[dict[str, str]] = []
    for entity_index, event in enumerate(entities):
        if not isinstance(event, dict) or event.get("type") != "event":
            continue
        seen: set[str] = set()
        entries = _component_data(event, "event_participant_reference").get(
            "participant_refs", []
        )
        for participant_index, entry in enumerate(entries):
            participant_id = _reference_id(
                entry.get("participant_ref") if isinstance(entry, dict) else None
            )
            if participant_id is None:
                continue
            if participant_id in seen:
                errors.append(
                    _issue(
                        "DUPLICATE_EVENT_PARTICIPANT",
                        f"/entities/{entity_index}/components/event_participant_reference/data/participant_refs/{participant_index}/participant_ref",
                        "同一 Character 在 Event 参与者中只能出现一次；角色应合并。",
                    )
                )
            seen.add(participant_id)
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
    """更新已实现 Reference 产生的近期 Event，保留其他检索角色。"""

    components = entity.setdefault("components", {})
    current_entries = _component_data(entity, "history_index").get("event_refs", [])
    entries: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for entry in current_entries:
        if not isinstance(entry, dict):
            continue
        event_id = _reference_id(entry.get("event_ref"))
        if event_id is None or event_id in seen_event_ids:
            continue
        roles = [
            role
            for role in entry.get("index_roles", [])
            if isinstance(role, str) and (role != "recent" or event_id in event_ids)
        ]
        if event_id in event_ids and "recent" not in roles:
            roles.append("recent")
        if roles:
            updated = copy.deepcopy(entry)
            updated["index_roles"] = roles
            entries.append(updated)
            seen_event_ids.add(event_id)
    for event_id in sorted(event_ids - seen_event_ids):
        entries.append(
            {
                "event_ref": {"id": event_id, "type": "event"},
                "index_roles": ["recent"],
            }
        )
    if entries:
        components["history_index"] = {
            "schema_version": "0.1.0",
            "data": {"event_refs": entries},
        }
    else:
        components.pop("history_index", None)


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
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return ValidationIssue(code, path, message).to_dict()
