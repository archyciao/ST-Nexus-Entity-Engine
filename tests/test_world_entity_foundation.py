from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from world_simulator_schema.entity_extraction import (
    plan_entity_enrichment_jobs,
    resolve_entity_directory,
)
from world_simulator_schema.entity_ids import new_entity_id, new_local_id
from world_simulator_schema.entity_network import (
    EntityNetworkValidator,
    rebuild_derived_indexes,
)
from world_simulator_schema.entity_validator import EntityValidator
from world_simulator_schema.stage_context import (
    StageContextError,
    build_stage_context,
    validate_stage_framework,
)


def envelope(data: dict, version: str = "0.1.0") -> dict:
    return {"schema_version": version, "data": data}


def entity(entity_type: str, name: str, components: dict | None = None) -> dict:
    return {
        "id": new_entity_id(entity_type),
        "type": entity_type,
        "description": f"用于验证{name}的稳定说明。",
        "components": {
            "entity_management": envelope(
                {
                    "created_at": "2026-08-05T10:00:00+08:00",
                    "updated_at": "2026-08-05T10:00:00+08:00",
                    "data_source": "system_generated",
                    "revision": 1,
                    "lifecycle_status": "active",
                },
                "0.2.0",
            ),
            "identity": envelope({"primary_name": name, "aliases": []}),
            **(components or {}),
        },
    }


def numeric_framework() -> dict:
    binding_id = new_local_id("stage_numeric_binding")
    stage_ids = [new_local_id("concept_stage") for _ in range(3)]
    return {
        "framework_summary": "以 MUV 信任值投影当前关系阶段。",
        "ordered": True,
        "stage_evaluation": {
            "mode": "numeric_derived",
            "numeric_bindings": [
                {
                    "numeric_binding_id": binding_id,
                    "provider": "muv",
                    "variable_key": "trust",
                    "value_source": "host",
                }
            ],
        },
        "stages": [
            {
                "stage_id": stage_ids[0],
                "name": "敌视",
                "order": 0,
                "description": "把对方视作现实威胁。",
                "numeric_guidance": [
                    {
                        "numeric_binding_id": binding_id,
                        "min_inclusive": -100,
                        "max_exclusive": 0,
                    }
                ],
                "context_blocks": [
                    {
                        "stage_context_id": new_local_id("concept_stage_context"),
                        "purpose": "narration",
                        "content": "叙述中保持明显戒备。",
                    }
                ],
            },
            {
                "stage_id": stage_ids[1],
                "name": "戒备",
                "order": 1,
                "description": "可以合作，但仍保留重要信息。",
                "numeric_guidance": [
                    {
                        "numeric_binding_id": binding_id,
                        "min_inclusive": 0,
                        "max_exclusive": 50,
                    }
                ],
                "context_blocks": [
                    {
                        "stage_context_id": new_local_id("concept_stage_context"),
                        "purpose": "interaction",
                        "content": "交谈时保持试探，不主动交付高风险秘密。",
                    },
                    {
                        "stage_context_id": new_local_id("concept_stage_context"),
                        "purpose": "narration",
                        "content": "用犹疑和保留表现尚未建立信任。",
                    },
                ],
            },
            {
                "stage_id": stage_ids[2],
                "name": "信任",
                "order": 2,
                "description": "愿意分享重要信息并承担合作风险。",
                "numeric_guidance": [
                    {
                        "numeric_binding_id": binding_id,
                        "min_inclusive": 50,
                        "max_exclusive": 101,
                    }
                ],
                "context_blocks": [],
            },
        ],
    }


class StageContextTests(unittest.TestCase):
    def test_current_stage_recall_only_returns_matching_stage_and_purpose(self) -> None:
        framework = numeric_framework()
        binding_id = framework["stage_evaluation"]["numeric_bindings"][0][
            "numeric_binding_id"
        ]
        result = build_stage_context(
            framework,
            numeric_values={binding_id: 36},
            purpose="interaction",
        )

        self.assertEqual("current_stage", result["recall_mode"])
        self.assertEqual(1, len(result["stages"]))
        self.assertEqual("戒备", result["stages"][0]["name"])
        self.assertEqual(1, len(result["stages"][0]["context_blocks"]))
        self.assertNotIn("numeric_values", result)

    def test_missing_purpose_does_not_load_every_context_block(self) -> None:
        framework = numeric_framework()
        binding_id = framework["stage_evaluation"]["numeric_bindings"][0][
            "numeric_binding_id"
        ]
        result = build_stage_context(
            framework,
            numeric_values={binding_id: 20},
        )
        self.assertNotIn("context_blocks", result["stages"][0])

    def test_unresolved_numeric_stage_never_falls_back_to_all_stages(self) -> None:
        with self.assertRaises(StageContextError) as caught:
            build_stage_context(numeric_framework(), numeric_values={})
        self.assertEqual("NUMERIC_STAGE_UNRESOLVED", caught.exception.code)

    def test_numeric_ranges_must_not_overlap_or_leave_internal_gaps(self) -> None:
        framework = numeric_framework()
        framework["stages"][1]["numeric_guidance"][0]["max_exclusive"] = 60
        codes = {issue.code for issue in validate_stage_framework(framework)}
        self.assertIn("OVERLAPPING_STAGE_RANGE", codes)


class EntityExtractionPlanningTests(unittest.TestCase):
    def test_discovery_creates_one_batch_job_per_record_action(self) -> None:
        discovery = {
            "entities": [
                {
                    "entity_key": "location:青竹客栈",
                    "type": "location",
                    "evidence_refs": ["r1"],
                    "changed_sections": ["profile"],
                },
                {
                    "entity_key": "location:青竹客栈大堂",
                    "type": "location",
                    "evidence_refs": ["r2"],
                    "event_link_evidence": [{"source_ref": "r5"}],
                    "changed_sections": ["atmosphere"],
                },
                {
                    "entity_key": "item:旧剑",
                    "type": "item",
                    "evidence_refs": ["r3"],
                    "changed_sections": [],
                },
            ]
        }
        jobs = plan_entity_enrichment_jobs(
            discovery,
            source_messages=[
                {"ref": "r1", "content": "客栈"},
                {"ref": "r2", "content": "大堂"},
                {"ref": "r3", "content": "旧剑"},
                {"ref": "r4", "content": "无关原文"},
                {"ref": "r5", "content": "事件实际发生在大堂"},
            ],
            existing_previews=[
                {"entity_key": "location:青竹客栈"},
                {"entity_key": "location:青竹客栈大堂"},
                {"entity_key": "item:旧剑"},
            ],
        )

        self.assertEqual(["update"], [job.record_action for job in jobs])
        self.assertEqual(["all"], [job.entity_type for job in jobs])
        self.assertEqual(3, len(jobs[0].payload["candidates"]))
        self.assertEqual(
            {"r1", "r2", "r3"},
            {item["ref"] for item in jobs[0].payload["source_messages"]},
        )

    def test_directory_resolution_separates_create_update_and_ambiguity(self) -> None:
        resolved = resolve_entity_directory(
            {
                "entities": [
                    {
                        "entity_key": "character:大师兄",
                        "type": "character",
                        "primary_name": "大师兄",
                        "aliases_add": [],
                    },
                    {
                        "entity_key": "item:新剑",
                        "type": "item",
                        "primary_name": "新剑",
                        "aliases_add": [],
                    },
                    {
                        "entity_key": "location:青云",
                        "type": "location",
                        "primary_name": "青云",
                        "aliases_add": [],
                    },
                ]
            },
            existing_previews=[
                {
                    "entity_key": "character:令狐冲",
                    "type": "character",
                    "primary_name": "令狐冲",
                    "aliases": ["大师兄"],
                },
                {
                    "entity_key": "location:青云",
                    "type": "location",
                    "primary_name": "青云",
                    "aliases": [],
                },
                {
                    "entity_key": "organization:青云",
                    "type": "organization",
                    "primary_name": "青云",
                    "aliases": [],
                },
            ],
        )

        self.assertEqual("character:令狐冲", resolved["update"][0]["entity_key"])
        self.assertEqual("item:新剑", resolved["create"][0]["entity_key"])
        self.assertEqual(
            "cross_type_name_conflict",
            resolved["unresolved"][0]["resolution_reason"],
        )
        self.assertEqual(
            {"location:青云", "organization:青云"},
            set(resolved["unresolved"][0]["candidate_entity_keys"]),
        )


class WorldEntityValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entity_validator = EntityValidator(ROOT)
        cls.network_validator = EntityNetworkValidator(ROOT)

    def test_item_stack_requires_quantity(self) -> None:
        item = entity(
            "item",
            "飞针",
            {
                "item_profile": envelope(
                    {
                        "item_kind": "hidden_weapon",
                        "instance_mode": "stack",
                        "primary_functions": ["投掷"],
                        "materials": ["钢"],
                        "form_description": "一组形制相同的细长飞针。",
                    }
                ),
                "item_state": envelope({"states": []}),
            },
        )
        codes = {item["code"] for item in self.entity_validator.validate(item).errors}
        self.assertIn("STACK_ITEM_QUANTITY_REQUIRED", codes)

    def test_character_stage_state_must_match_target_framework(self) -> None:
        framework = numeric_framework()
        binding_id = framework["stage_evaluation"]["numeric_bindings"][0][
            "numeric_binding_id"
        ]
        concept = entity(
            "concept",
            "信任阶段",
            {"stage_framework": envelope(framework)},
        )
        skill = entity("skill", "青山剑法")
        character = entity(
            "character",
            "甲",
            {
                "skill_reference": envelope(
                    {
                        "skill_refs": [
                            {
                                "skill_ref": {"id": skill["id"], "type": "skill"},
                                "proficiency_description": "正在练习基础招式。",
                                "stage_state": {
                                    "evaluation_mode": "numeric_derived",
                                    "framework_ref": {
                                        "id": concept["id"],
                                        "type": "concept",
                                    },
                                    "numeric_values": [
                                        {
                                            "numeric_binding_id": binding_id,
                                            "value": 20,
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                    "0.2.0",
                )
            },
        )

        valid = self.network_validator.validate([character, skill, concept])
        self.assertTrue(valid.valid, valid.errors)

        stale = copy.deepcopy(character)
        stale["components"]["skill_reference"]["data"]["skill_refs"][0][
            "stage_state"
        ]["current_stage_id"] = framework["stages"][2]["stage_id"]
        codes = {
            item["code"]
            for item in self.network_validator.validate([stale, skill, concept]).errors
        }
        self.assertIn("STALE_NUMERIC_STAGE_CACHE", codes)

    def test_five_world_entity_examples_are_valid(self) -> None:
        for filename in (
            "location_qingzhu_inn_hall.json",
            "item_hidden_weapon_pouch.json",
            "organization_huashan_school.json",
            "skill_dugu_nine_swords.json",
            "concept_trust_stage_framework.json",
        ):
            with self.subTest(filename=filename):
                example = json.loads(
                    (ROOT / "examples" / "entities" / filename).read_text(
                        encoding="utf-8"
                    )
                )
                report = self.entity_validator.validate(example)
                self.assertTrue(report.valid, report.errors)

    def test_item_placement_builds_inventory_contents_and_location_indexes(self) -> None:
        room = entity("location", "大堂")
        character = entity(
            "character",
            "甲",
            {
                "current_location_reference": envelope(
                    {"location_ref": {"id": room["id"], "type": "location"}}
                )
            },
        )
        bag = entity(
            "item",
            "暗器囊",
            {
                "container_profile": envelope(
                    {
                        "capacity_description": "可放置轻小暗器。",
                        "allowed_contents": ["轻小暗器"],
                        "forbidden_contents": ["长兵器"],
                        "stable_capabilities": ["分隔存放"],
                        "access_requirements": [],
                    }
                ),
                "current_placement_reference": envelope(
                    {
                        "placement_ref": {
                            "id": character["id"],
                            "type": "character",
                        },
                        "placement_role": "worn",
                    }
                ),
            },
        )
        needle = entity(
            "item",
            "飞针",
            {
                "current_placement_reference": envelope(
                    {
                        "placement_ref": {"id": bag["id"], "type": "item"},
                        "placement_role": "contained",
                    }
                )
            },
        )
        projected = rebuild_derived_indexes([room, character, bag, needle])
        report = self.network_validator.validate(
            projected, require_derived_indexes=True
        )
        self.assertTrue(report.valid, report.errors)
        by_id = {item["id"]: item for item in projected}
        self.assertEqual(
            bag["id"],
            by_id[character["id"]]["components"]["inventory_index"]["data"][
                "item_refs"
            ][0]["item_ref"]["id"],
        )
        self.assertEqual(
            needle["id"],
            by_id[bag["id"]]["components"]["contents_index"]["data"][
                "item_refs"
            ][0]["id"],
        )
        self.assertEqual(
            {"character"},
            {
                entry["entity_ref"]["type"]
                for entry in by_id[room["id"]]["components"]["containment_index"][
                    "data"
                ]["entity_refs"]
            },
        )

    def test_item_container_cycle_is_rejected(self) -> None:
        first = entity(
            "item",
            "甲袋",
            {
                "container_profile": envelope(
                    {
                        "capacity_description": "小袋。",
                        "allowed_contents": [],
                        "forbidden_contents": [],
                        "stable_capabilities": [],
                        "access_requirements": [],
                    }
                )
            },
        )
        second = copy.deepcopy(first)
        second["id"] = new_entity_id("item")
        second["components"]["identity"]["data"]["primary_name"] = "乙袋"
        first["components"]["current_placement_reference"] = envelope(
            {
                "placement_ref": {"id": second["id"], "type": "item"},
                "placement_role": "contained",
            }
        )
        second["components"]["current_placement_reference"] = envelope(
            {
                "placement_ref": {"id": first["id"], "type": "item"},
                "placement_role": "contained",
            }
        )
        report = self.network_validator.validate([first, second])
        self.assertIn(
            "ITEM_PLACEMENT_CYCLE", {item["code"] for item in report.errors}
        )


if __name__ == "__main__":
    unittest.main()
