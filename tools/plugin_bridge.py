"""NexusEntityEngine 宿主桥接入口。

这个文件不实现第二套 Entity、提示词或校验规则。它只把正式 Python 核心中的
Registry、Schema、提示词常量和校验器转换成宿主可以调用的 JSON。SillyTavern
只是其中一个宿主；新增宿主时也应调用这里，而不是复制业务规则。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from world_simulator_schema.entity_ids import new_entity_id  # noqa: E402
from world_simulator_schema.entity_types import (  # noqa: E402
    CURRENT_ENTITY_TYPES,
    CURRENT_ENTITY_TYPE_IDS,
)
from world_simulator_schema.entity_validator import EntityValidator  # noqa: E402
from world_simulator_schema.event_extraction_v2 import (  # noqa: E402
    ENTITY_CREATE_SYSTEM_PROMPT,
    ENTITY_UPDATE_SYSTEM_PROMPT,
    EVENT_CONTENT_SYSTEM_PROMPT,
    NARRATIVE_MAP_SYSTEM_PROMPT,
    RELATION_REFERENCE_SYSTEM_PROMPT,
)
from world_simulator_schema.schema_store import SchemaStore  # noqa: E402


PROMPTS = (
    ("narrative_map", "Narrative Map", NARRATIVE_MAP_SYSTEM_PROMPT, True),
    ("event_content", "Event Content", EVENT_CONTENT_SYSTEM_PROMPT, False),
    ("entity_create", "Entity Create", ENTITY_CREATE_SYSTEM_PROMPT, False),
    ("entity_update", "Entity Update", ENTITY_UPDATE_SYSTEM_PROMPT, False),
    (
        "relation_references",
        "Relation Reference",
        RELATION_REFERENCE_SYSTEM_PROMPT,
        False,
    ),
)


def _store() -> SchemaStore:
    return SchemaStore(PROJECT_ROOT)


def _component_metadata(store: SchemaStore) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for item in store.component_registry.get("components", []):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        version = item.get("current_version")
        schema = store.schema_for_component(name, version)
        data_schema: dict[str, Any] = {}
        for branch in schema.get("allOf", []):
            if not isinstance(branch, dict):
                continue
            candidate = branch.get("properties", {}).get("data")
            if isinstance(candidate, dict):
                data_schema = candidate
        if isinstance(data_schema.get("$ref"), str):
            data_schema = store.schema_by_id(data_schema["$ref"])
        ai = item.get("ai") if isinstance(item.get("ai"), dict) else {}
        components.append(
            {
                "name": name,
                "title": ai.get("title_zh") or schema.get("title") or name,
                "description": ai.get("description_zh") or schema.get("description") or "",
                "usageNotes": ai.get("usage_notes") or [],
                "category": item.get("category"),
                "authority": item.get("authority"),
                "allowedEntityTypes": item.get("allowed_entity_types") or [],
                "schemaVersion": version,
                "schemaId": schema.get("$id"),
                "schema": schema,
                "dataSchema": data_schema,
                "references": item.get("references") or [],
                "editable": item.get("category") != "index" and name != "entity_management",
                "derived": item.get("authority") == "derived",
            }
        )
    return components


def metadata() -> dict[str, Any]:
    store = _store()
    registered_types = {
        entity_type
        for component in store.component_registry.get("components", [])
        if isinstance(component, dict)
        for entity_type in component.get("allowed_entity_types", [])
        if isinstance(entity_type, str)
    }
    entity_types = [dict(item) for item in CURRENT_ENTITY_TYPES if item["id"] in registered_types]
    return {
        "engine": "NexusEntityEngine",
        "registryVersion": store.component_registry.get("registry_version"),
        "entityTypes": entity_types,
        "components": _component_metadata(store),
        "prompts": [
            {
                "id": prompt_id,
                "label": label,
                "systemPrompt": prompt.strip(),
                "lockedFirst": locked,
            }
            for prompt_id, label, prompt, locked in PROMPTS
        ],
    }


def prepare_entity(payload: dict[str, Any]) -> dict[str, Any]:
    entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
    entity = json.loads(json.dumps(entity, ensure_ascii=False))
    entity_type = str(entity.get("type") or payload.get("type") or "").strip()
    if entity_type not in CURRENT_ENTITY_TYPE_IDS:
        raise ValueError(f"未登记的 Entity Type：{entity_type}")
    entity["type"] = entity_type
    entity["id"] = str(entity.get("id") or new_entity_id(entity_type))
    entity["description"] = str(entity.get("description") or "").strip()
    components = entity.get("components")
    if not isinstance(components, dict):
        components = {}
        entity["components"] = components

    now = datetime.now(timezone.utc).isoformat()
    management = components.get("entity_management")
    if not isinstance(management, dict):
        management = {
            "schema_version": "0.2.0",
            "data": {
                "created_at": now,
                "updated_at": now,
                "data_source": "user_created",
                "revision": 1,
                "lifecycle_status": "active",
            },
        }
        components["entity_management"] = management
    else:
        data = management.setdefault("data", {})
        if isinstance(data, dict):
            data["updated_at"] = now
            data["revision"] = int(data.get("revision") or 0) + 1

    name = str(payload.get("name") or "").strip()
    store = _store()
    identity = store.component("identity")
    if identity and entity_type in identity.get("allowed_entity_types", []):
        current = components.get("identity")
        if not isinstance(current, dict):
            components["identity"] = {
                "schema_version": identity["current_version"],
                "data": {"primary_name": name, "aliases": []},
            }
        elif name:
            current.setdefault("data", {})["primary_name"] = name
    return entity


def validate_entity(payload: dict[str, Any]) -> dict[str, Any]:
    entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
    return EntityValidator(PROJECT_ROOT).validate(entity).to_dict()


COMMANDS = {
    "metadata": lambda _payload: metadata(),
    "prepare_entity": prepare_entity,
    "validate_entity": validate_entity,
}


def main() -> int:
    request = json.load(sys.stdin)
    command = str(request.get("command") or "")
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    handler = COMMANDS.get(command)
    if handler is None:
        raise ValueError(f"未知桥接命令：{command}")
    json.dump({"ok": True, "result": handler(payload)}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        raise SystemExit(1)
