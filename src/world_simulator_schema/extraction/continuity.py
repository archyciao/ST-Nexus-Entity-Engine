"""Reuse stable IDs and preserve authoritative fields outside an extraction delta."""
from __future__ import annotations

from copy import deepcopy
from . import legacy
from world_simulator_schema.event_extraction_v2 import OPEN_SEMANTIC_COMPONENTS
from world_simulator_schema.entity_network import EntityNetworkValidator, rebuild_derived_indexes


def load_state(snapshot: dict) -> dict:
    state = deepcopy(snapshot.get("state") or legacy.initial_unified_state())
    records = snapshot.get("entities", [])
    network = [record.get("entity", record) for record in records]
    reverse = {value: key for key, value in state["id_maps"]["entity"].items()}
    for entity in network:
        kind = entity.get("type")
        if kind not in legacy.ALLOWED_ENTITY_TYPES:
            continue
        identity = entity.get("components", {}).get("identity", {}).get("data", {})
        name = identity.get("primary_name") or entity["id"]
        key = reverse.get(entity["id"]) or f"{kind}:{name}"
        if key in state["id_maps"]["entity"] and state["id_maps"]["entity"][key] != entity["id"]:
            key = f"{kind}:{entity['id']}"
        state["id_maps"]["entity"][key] = entity["id"]
        candidate = state["entity_candidates"].setdefault(key, {})
        candidate.update({"type": kind, "primary_name": name, "aliases": identity.get("aliases", []), "description": entity.get("description", "")})
        candidate["semantic_fields"] = {component: deepcopy(entity["components"][component]["data"]) for component in OPEN_SEMANTIC_COMPONENTS.get(kind, ()) if component in entity.get("components", {})}
    # References and non-extracted components stay in the authoritative network.
    state["base_network"] = deepcopy(network)
    return state


def overlay_network(state: dict, projected: list[dict], previous: list[dict]) -> tuple[list[dict], dict]:
    """Apply only changed fields; unrelated manually authored components are never replaced."""
    base = {item["id"]: deepcopy(item) for item in state.get("base_network", [])}
    before = {item["id"]: item for item in previous}
    missing = object()

    def merge(current, old, new):
        if old is not missing and old == new:
            return deepcopy(current)
        if isinstance(new, dict):
            result = deepcopy(current) if isinstance(current, dict) else {}
            for key, value in new.items():
                if key == "entity_management" and key in result:
                    continue
                result[key] = merge(result.get(key), old.get(key, missing) if isinstance(old, dict) else missing, value)
            return result
        return deepcopy(new)

    for entity in projected:
        current = base.get(entity["id"])
        base[entity["id"]] = merge(current, before.get(entity["id"], missing), entity)
    network = rebuild_derived_indexes(list(base.values()))
    # rebuild_derived_indexes returns the network, and validation owns reference closure.
    report = EntityNetworkValidator(legacy.ROOT).validate(network, require_derived_indexes=True)
    return network, report.to_dict()
