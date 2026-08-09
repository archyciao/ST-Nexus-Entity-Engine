from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for import_path in (SRC, TOOLS):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from world_simulator_schema import event_extraction_v2 as V2


RUNNER_SPEC = importlib.util.spec_from_file_location(
    "airp_extraction_v2_probe", TOOLS / "airp_extraction_v2_probe.py"
)
assert RUNNER_SPEC and RUNNER_SPEC.loader
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(RUNNER)
LEGACY = RUNNER.legacy


def sources() -> list[dict]:
    return [
        {
            "ref": "r0001.assistant",
            "content": "甲在驿站与乙交谈。两人谈妥后御剑升空。",
            "narrative": "甲在驿站与乙交谈。两人谈妥后御剑升空。",
        },
        {
            "ref": "r0002.assistant",
            "content": "抵达青山后，甲入住石屋。",
            "narrative": "抵达青山后，甲入住石屋。",
        },
    ]


def rounds() -> list[dict]:
    return [
        {
            "round": 1,
            "user": {
                "ref": "r0001.user",
                "role": "user",
                "name": "甲",
                "speaker": "甲",
                "source_line": 1,
                "content": "继续交谈。",
            },
            "assistant": {
                "ref": "r0001.assistant",
                "role": "assistant",
                "name": "叙事者",
                "speaker": "叙事者",
                "source_line": 2,
                "content": "甲在驿站与乙交谈。两人谈妥后御剑升空。",
            },
        },
        {
            "round": 2,
            "user": {
                "ref": "r0002.user",
                "role": "user",
                "name": "甲",
                "speaker": "甲",
                "source_line": 3,
                "content": "寻找住处。",
            },
            "assistant": {
                "ref": "r0002.assistant",
                "role": "assistant",
                "name": "叙事者",
                "speaker": "叙事者",
                "source_line": 4,
                "content": "抵达青山后，甲入住石屋。",
            },
        },
    ]


def fixed_segments() -> list[dict]:
    return [
        {
            "partition_key": "v2_segment_001",
            "slot": "new_1",
            "source_refs": ["r0001.assistant", "r0002.assistant"],
            "assigned_messages": [
                {"ref": "r0001.assistant", "content": sources()[0]["content"]},
                {"ref": "r0002.assistant", "content": sources()[1]["content"]},
            ],
            "entity_roster": [
                {
                    "entity_key": "character:甲",
                    "type": "character",
                    "primary_name": "甲",
                    "aliases": [],
                    "record_action": "create",
                    "evidence": [
                        {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                    ],
                },
                {
                    "entity_key": "location:青山",
                    "type": "location",
                    "primary_name": "青山",
                    "aliases": [],
                    "record_action": "create",
                    "evidence": [
                        {"source_ref": "r0002.assistant", "quote": "抵达青山后"}
                    ],
                },
            ],
        }
    ]


class EventExtractionV2Tests(unittest.TestCase):
    def test_prompts_keep_confirmed_event_baseline_and_remove_redundant_fields(self) -> None:
        prompt = V2.NARRATIVE_MAP_SYSTEM_PROMPT
        definition = prompt.index("一个 Event 是故事中发生的一件事")
        boundary = prompt.index("【怎样判断首尾】")
        examples = prompt.index("【粒度校准】")
        roster = prompt.index("【Event 内实体名录】")

        self.assertLess(definition, boundary)
        self.assertLess(boundary, examples)
        self.assertLess(examples, roster)
        self.assertIn("围绕同一问题的发问、解释、追问、争论和最后决定", prompt)
        self.assertIn("一项交涉已有决定", prompt)
        self.assertIn("一段旅程已经抵达并安顿", prompt)
        self.assertNotIn("event_focus", prompt)
        self.assertNotIn("involvement", prompt)
        self.assertNotIn("actual | mentioned", prompt)
        self.assertNotIn("middle_with_right", prompt)

        content_prompt = V2.EVENT_CONTENT_SYSTEM_PROMPT
        self.assertIn("通常写约 300 个汉字", content_prompt)
        self.assertIn("原则上不超过 500 个汉字", content_prompt)
        self.assertNotIn("title", content_prompt)

        create_prompt = V2.ENTITY_CREATE_SYSTEM_PROMPT
        update_prompt = V2.ENTITY_UPDATE_SYSTEM_PROMPT
        self.assertIn("semantic_fields", create_prompt)
        self.assertIn("只更新 record_action=update 的对象", update_prompt)
        self.assertIn("supplement", update_prompt)
        self.assertIn("revise", update_prompt)
        self.assertNotIn("character_data_patch", create_prompt)
        self.assertNotIn("domain_data_patch", update_prompt)

        relation_prompt = V2.RELATION_REFERENCE_SYSTEM_PROMPT
        self.assertIn("event_locations", relation_prompt)
        self.assertIn("不判断人物、物品或组织是否实际参与", relation_prompt)
        self.assertNotIn("involvement", relation_prompt)

    def test_user_prompt_keeps_story_continuous_and_context_separate(self) -> None:
        user_prompt = V2.build_narrative_map_user_prompt(
            batch_number=1,
            story_text="第一句。第二句。",
            continuity_context={},
            existing_entity_previews={},
        )
        self.assertIn("本批连续故事：\n第一句。第二句。", user_prompt)
        self.assertIn("按顺序排列的相邻前文", user_prompt)
        self.assertNotIn('"story":', user_prompt)

    def test_continuity_context_uses_old_event_text_without_title(self) -> None:
        context = V2.build_continuity_context(
            existing_event_tail={
                "pending_event": {
                    "id": "event_001",
                    "title": "旧标题不应进入",
                    "description": "甲乙在驿站谈妥安排。",
                    "source_refs": ["r0001.assistant"],
                },
                "forming_event": {
                    "id": "event_002",
                    "description": "甲乙离开驿站御剑赶路。",
                    "source_refs": ["r0002.assistant"],
                },
            },
            source_text_by_ref={
                "r0001.assistant": "驿站中，甲乙最终谈妥安排。",
                "r0002.assistant": "两人离开驿站，御剑前往青山。",
            },
            current_story="抵达青山后，甲入住石屋。",
        )
        self.assertEqual(
            ["previous_pending_tail", "previous_forming"],
            [item["part_key"] for item in context["ordered_parts"]],
        )
        self.assertNotIn("title", context["ordered_parts"][0]["event_preview"])

    def test_script_maps_window_starts_to_event_states(self) -> None:
        state = LEGACY.initial_unified_state()
        state["events"] = [
            {"status": "pending_finalization"},
            {"status": "forming"},
        ]
        raw = {
            "events": [
                {
                    "start_quote": "驿站中，甲乙最终谈妥安排",
                    "entity_roster": [],
                }
            ]
        }
        context = {
            "ordered_parts": [
                {
                    "part_key": "previous_pending_tail",
                    "text": "驿站中，甲乙最终谈妥安排。",
                },
                {
                    "part_key": "previous_forming",
                    "text": "两人离开驿站，御剑前往青山。",
                },
            ]
        }
        normalized = V2.normalize_narrative_map(raw, state, sources(), context)
        plan = V2.build_boundary_plan(normalized, sources())
        self.assertEqual("merge_into_pending", normalized["old_forming_disposition"])
        self.assertTrue(normalized["events"][0]["continues_previous"])
        self.assertEqual("pending_tail", plan["segments"][0]["slot"])
        self.assertNotIn("event_focus", plan["segments"][0])

    def test_roster_follows_its_evidence_without_participation_classification(self) -> None:
        raw = {
            "events": [
                {
                    "start_quote": "甲在驿站与乙交谈",
                    "entity_roster": [
                        {
                            "type": "character",
                            "primary_name": "甲",
                            "aliases": [],
                            "evidence_quote": "甲在驿站与乙交谈",
                        },
                        {
                            "type": "location",
                            "primary_name": "青山",
                            "aliases": [],
                            "evidence_quote": "抵达青山后",
                        },
                    ],
                },
                {
                    "start_quote": "抵达青山后",
                    "entity_roster": [],
                },
            ]
        }
        normalized = V2.normalize_narrative_map(
            raw, LEGACY.initial_unified_state(), sources()
        )
        self.assertEqual(
            {"character:甲"},
            {item["entity_key"] for item in normalized["events"][0]["entity_roster"]},
        )
        second = normalized["events"][1]["entity_roster"][0]
        self.assertEqual("location:青山", second["entity_key"])
        self.assertEqual("create", second["record_action"])
        self.assertNotIn("involvement", second)

    def test_unlocatable_boundary_merges_but_keeps_verified_roster(self) -> None:
        raw = {
            "events": [
                {"start_quote": "甲在驿站与乙交谈", "entity_roster": []},
                {
                    "start_quote": "原文不存在的开头",
                    "entity_roster": [
                        {
                            "type": "location",
                            "primary_name": "青山",
                            "aliases": [],
                            "evidence_quote": "抵达青山后",
                        }
                    ],
                },
            ]
        }
        normalized = V2.normalize_narrative_map(
            raw, LEGACY.initial_unified_state(), sources()
        )
        self.assertEqual(1, len(normalized["events"]))
        self.assertEqual(
            ["location:青山"],
            [item["entity_key"] for item in normalized["events"][0]["entity_roster"]],
        )

    def test_cross_type_name_conflict_stays_unresolved_even_with_exact_key(self) -> None:
        state = LEGACY.initial_unified_state()
        state["entity_candidates"] = {
            "location:青山": {
                "entity_key": "location:青山",
                "type": "location",
                "primary_name": "青山",
                "aliases": [],
            },
            "organization:青山": {
                "entity_key": "organization:青山",
                "type": "organization",
                "primary_name": "青山",
                "aliases": [],
            },
        }
        raw = {
            "events": [
                {
                    "start_quote": "甲在驿站与乙交谈",
                    "entity_roster": [
                        {
                            "type": "location",
                            "primary_name": "青山",
                            "aliases": [],
                            "evidence_quote": "抵达青山后",
                        }
                    ],
                }
            ]
        }

        normalized = V2.normalize_narrative_map(raw, state, sources())
        roster = normalized["events"][0]["entity_roster"][0]

        self.assertEqual("unresolved", roster["record_action"])
        self.assertNotIn("existing_entity", roster)

    def test_unique_verbatim_fragment_recovers_boundary(self) -> None:
        raw = {
            "events": [
                {"start_quote": "甲在驿站与乙交谈", "entity_roster": []},
                {
                    "start_quote": "不存在的过渡句。抵达青山后，甲入住石屋。",
                    "entity_roster": [],
                },
            ]
        }
        normalized = V2.normalize_narrative_map(
            raw, LEGACY.initial_unified_state(), sources()
        )
        self.assertEqual(2, len(normalized["events"]))
        self.assertEqual("抵达青山后，甲入住石屋", normalized["events"][1]["start_quote"])

    def test_player_identity_replaces_contextual_character_names(self) -> None:
        records = [
            {"chat_metadata": {"last_user_persona": {"name": "王岳青"}}}
        ]
        with tempfile.TemporaryDirectory() as directory:
            chat = Path(directory) / "chat.jsonl"
            chat.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            identity = RUNNER.load_player_identity(chat)
        state = LEGACY.initial_unified_state()
        RUNNER.seed_player_identity(state, identity)
        canonical = V2.canonicalize_contextual_character_references(
            {
                "actor_key": "character:你",
                "entities": [
                    {
                        "entity_key": "character:我",
                        "type": "character",
                        "primary_name": "你",
                        "aliases_add": ["主角", "王岳青", "王师弟"],
                    }
                ],
            },
            state,
        )
        self.assertEqual("character:王岳青", canonical["actor_key"])
        self.assertEqual("王岳青", canonical["entities"][0]["primary_name"])
        self.assertEqual(["王师弟"], canonical["entities"][0]["aliases_add"])

    def test_entity_create_normalizes_named_open_components_and_evidence(self) -> None:
        raw = {
            "entities": [
                {
                    "entity_key": "item:旧剑",
                    "type": "item",
                    "primary_name": "旧剑",
                    "aliases_add": ["佩剑"],
                    "description": "甲长期使用的一柄旧剑。",
                    "evidence": [
                        {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                    ],
                    "semantic_fields": {
                        "item_profile": {"材质": "凡铁"},
                        "item_characteristic": {
                            "当前状况": ["剑刃有缺口", "仍可使用"]
                        },
                    },
                }
            ]
        }
        plan = RUNNER.normalize_entity_create_plan(
            raw,
            state=LEGACY.initial_unified_state(),
            rounds=rounds(),
            allowed_entity_keys=frozenset({"item:旧剑"}),
        )
        entity = plan["entities"][0]
        updates = {
            item["field_name"]: item["value"]
            for item in entity["semantic_field_updates"]
        }
        self.assertEqual({"材质": "凡铁"}, updates["item_profile"])
        self.assertEqual(
            {"当前状况": ["剑刃有缺口", "仍可使用"]},
            updates["item_characteristic"],
        )
        self.assertEqual(["r0001.assistant"], entity["evidence_refs"])
        self.assertEqual("甲在驿站", entity["event_link_evidence"][0]["quote"])
        self.assertNotIn("facts_patch", entity)
        self.assertNotIn("domain_data_patch", entity)

    def test_entity_update_is_field_level_and_preserves_revision_history(self) -> None:
        state = LEGACY.initial_unified_state()
        state["entity_candidates"]["item:旧剑"] = {
            "entity_key": "item:旧剑",
            "type": "item",
            "primary_name": "旧剑",
            "aliases": [],
            "description": "一柄旧剑。",
            "semantic_fields": {
                "item_profile": {"材质": "凡铁", "形制": "长剑"},
                "item_characteristic": {"状态": "尚可使用"},
            },
        }
        raw = {
            "entities": [
                {
                    "entity_key": "item:旧剑",
                    "aliases_add": [],
                    "field_updates": [
                        {
                            "field_name": "item_characterstic",
                            "relationship_to_old": "supplement",
                            "value": {"用途": "练剑"},
                            "evidence": [
                                {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                            ],
                        },
                        {
                            "field_name": "item_profile",
                            "relationship_to_old": "revise",
                            "value": {"材质": "精钢"},
                            "evidence": [
                                {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                            ],
                        },
                        {
                            "field_name": "misc_notes",
                            "relationship_to_old": "supplement",
                            "value": {"记录": "无法可靠归类"},
                            "evidence": [
                                {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                            ],
                        },
                    ],
                }
            ]
        }

        plan = RUNNER.normalize_entity_update_plan(
            raw,
            state=state,
            rounds=rounds(),
            allowed_entity_keys=frozenset({"item:旧剑"}),
        )
        updated = LEGACY.apply_entity_candidates(state, plan)
        candidate = updated["entity_candidates"]["item:旧剑"]
        self.assertEqual(
            {"状态": "尚可使用", "用途": "练剑"},
            candidate["semantic_fields"]["item_characteristic"],
        )
        self.assertEqual(
            {"材质": "精钢"}, candidate["semantic_fields"]["item_profile"]
        )
        self.assertEqual(
            {"材质": "凡铁", "形制": "长剑"},
            candidate["semantic_field_revisions"][0]["previous_value"],
        )
        self.assertEqual(
            "misc_notes",
            candidate["unclassified_field_updates"][0]["proposed_field_name"],
        )

        network, report = LEGACY.materialize_network(updated)
        self.assertTrue(report["valid"], report)
        item = next(entity for entity in network if entity["type"] == "item")
        self.assertEqual(
            "0.2.0", item["components"]["item_profile"]["schema_version"]
        )
        self.assertIn("entity_field_maintenance", item["components"])

    def test_protected_reference_like_values_are_isolated_from_open_facts(self) -> None:
        raw = {
            "entities": [
                {
                    "entity_key": "item:旧剑",
                    "type": "item",
                    "primary_name": "旧剑",
                    "aliases_add": [],
                    "description": "一柄旧剑。",
                    "evidence": [
                        {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                    ],
                    "semantic_fields": {
                        "item_profile": {
                            "材质": "凡铁",
                            "owner_ref": {"id": "character_fake", "type": "character"},
                        }
                    },
                }
            ]
        }
        plan = RUNNER.normalize_entity_create_plan(
            raw,
            state=LEGACY.initial_unified_state(),
            rounds=rounds(),
            allowed_entity_keys=frozenset({"item:旧剑"}),
        )
        entity = plan["entities"][0]
        self.assertEqual(
            {"材质": "凡铁"}, entity["semantic_field_updates"][0]["value"]
        )
        self.assertEqual(
            "protected_identity_or_reference_field",
            entity["unclassified_field_updates"][0]["reason"],
        )

    def test_entity_workloads_and_reasoning_defaults_are_explicit(self) -> None:
        segments = [
            {
                "partition_key": "segment_1",
                "assigned_messages": [],
                "entity_roster": [
                    {"entity_key": "character:甲", "record_action": "create"},
                    {"entity_key": "location:山门", "record_action": "update"},
                    {"entity_key": "item:同名物", "record_action": "unresolved"},
                ],
            }
        ]
        workloads = V2.split_entity_workloads(segments)
        self.assertEqual(
            ["character:甲"],
            [item["entity_key"] for item in workloads["create"][0]["entity_roster"]],
        )
        self.assertEqual(
            ["location:山门"],
            [item["entity_key"] for item in workloads["update"][0]["entity_roster"]],
        )
        args = RUNNER.parse_args(
            ["chat.jsonl", "--endpoint", "https://example.test", "--model", "test", "--output", "out.md"]
        )
        self.assertEqual(4, args.stage2_workers)
        self.assertEqual(
            {"high"},
            {args.map_thinking, args.event_thinking, args.entity_thinking, args.relation_thinking},
        )

    def test_roster_covers_skill_and_concept_before_identity_resolution(self) -> None:
        self.assertIn("skill", V2.ROSTER_TYPES)
        self.assertIn("concept", V2.ROSTER_TYPES)
        self.assertIn("skill、concept 六者之一", V2.NARRATIVE_MAP_SYSTEM_PROMPT)

    def test_entity_create_cannot_add_an_object_outside_resolved_directory(self) -> None:
        raw = {
            "entities": [
                {
                    "entity_key": "skill:目录外剑法",
                    "type": "skill",
                    "primary_name": "目录外剑法",
                    "aliases_add": [],
                    "description": "不应绕过目录建立。",
                    "evidence": [
                        {"source_ref": "r0001.assistant", "quote": "甲在驿站"}
                    ],
                    "semantic_fields": {"skill_definition": {"说明": "剑法"}},
                }
            ]
        }

        plan = RUNNER.normalize_entity_create_plan(
            raw,
            state=LEGACY.initial_unified_state(),
            rounds=rounds(),
            allowed_entity_keys=frozenset({"item:旧剑"}),
        )

        self.assertEqual([], plan["entities"])
        self.assertTrue(
            any("目录外实体" in warning for warning in plan["script_normalization_warnings"])
        )

    def test_relation_task_keeps_actual_location_separate_from_generic_roster(self) -> None:
        state = LEGACY.initial_unified_state()
        raw = {
            "reference_updates": [
                {
                    "entity_key": "character:甲",
                    "source_ref": "r0002.assistant",
                    "evidence_quote": "甲入住石屋",
                    "current_location_key": "location:青山",
                }
            ],
            "event_locations": [
                {
                    "partition_key": "v2_segment_001",
                    "location_key": "location:青山",
                    "source_ref": "r0002.assistant",
                    "evidence_quote": "抵达青山后",
                }
            ],
            "relations": [],
        }
        plan = RUNNER.normalize_relation_reference_plan(
            raw,
            event_rosters=fixed_segments(),
            state=state,
            rounds=rounds(),
        )
        character = next(item for item in plan["entities"] if item["entity_key"] == "character:甲")
        location = next(item for item in plan["entities"] if item["entity_key"] == "location:青山")
        self.assertEqual(
            "location:青山", character["character_data_patch"]["current_location_key"]
        )
        self.assertEqual("抵达青山后", location["event_location_evidence"][0]["quote"])
        self.assertTrue(location["event_link_evidence"])

    def test_roster_location_alone_cannot_become_current_location(self) -> None:
        raw = {
            "reference_updates": [
                {
                    "entity_key": "character:甲",
                    "source_ref": "r0001.assistant",
                    "evidence_quote": "甲在驿站",
                    "current_location_key": "location:青山",
                }
            ],
            "event_locations": [],
            "relations": [],
        }
        plan = RUNNER.normalize_relation_reference_plan(
            raw,
            event_rosters=fixed_segments(),
            state=LEGACY.initial_unified_state(),
            rounds=rounds(),
        )
        character = next(item for item in plan["entities"] if item["entity_key"] == "character:甲")
        self.assertNotIn("current_location_key", character.get("character_data_patch", {}))

    def test_relation_task_cannot_create_an_object_outside_resolved_directory(self) -> None:
        raw = {
            "reference_updates": [
                {
                    "entity_key": "item:目录外宝物",
                    "source_ref": "r0001.assistant",
                    "evidence_quote": "甲在驿站",
                }
            ],
            "event_locations": [],
            "relations": [],
        }

        plan = RUNNER.normalize_relation_reference_plan(
            raw,
            event_rosters=fixed_segments(),
            state=LEGACY.initial_unified_state(),
            rounds=rounds(),
        )

        self.assertFalse(
            any(item["entity_key"] == "item:目录外宝物" for item in plan["entities"])
        )
        self.assertTrue(
            any("目录外实体" in warning for warning in plan["script_normalization_warnings"])
        )

    def test_richer_entity_content_wins_over_relation_roster_fallback(self) -> None:
        state = LEGACY.initial_unified_state()
        relation_plan = {
            "entities": [
                {
                    "entity_key": "item:旧剑",
                    "type": "item",
                    "primary_name": "旧剑",
                    "description": "本批故事中形成清楚事实的物品“旧剑”。",
                    "aliases_add": [],
                    "evidence_refs": ["r0001.assistant"],
                }
            ],
            "relations": [],
        }
        create_plan = {
            "entities": [
                {
                    "entity_key": "item:旧剑",
                    "type": "item",
                    "primary_name": "旧剑",
                    "description": "甲长期使用、剑刃已有缺口的凡铁长剑。",
                    "aliases_add": ["佩剑"],
                    "evidence_refs": ["r0001.assistant"],
                }
            ],
            "relations": [],
        }

        updated = RUNNER.apply_stage2_entity_plans(
            state,
            relation_plan=relation_plan,
            create_plan=create_plan,
        )

        self.assertEqual(
            "甲长期使用、剑刃已有缺口的凡铁长剑。",
            updated["entity_candidates"]["item:旧剑"]["description"],
        )
        self.assertEqual(
            ["佩剑"], updated["entity_candidates"]["item:旧剑"]["aliases"]
        )

    def test_key_detail_actor_is_not_cleared_by_participation_classification(self) -> None:
        boundary = {
            "old_forming_disposition": "absent",
            "segments": [
                {
                    "partition_key": "v2_segment_001",
                    "slot": "new_1",
                    "source_refs": ["r0001.assistant", "r0002.assistant"],
                    "start_anchors": [
                        {
                            "source_ref": "r0001.assistant",
                            "start_quote": "甲在驿站与乙交谈",
                        }
                    ],
                    "entity_roster": [],
                }
            ],
            "boundary_decisions": [],
        }
        raw = {
            "event_updates": [
                {
                    "partition_key": "v2_segment_001",
                    "story_summary_add": "甲乙交谈并谈妥安排。",
                    "description": "甲乙在驿站谈妥安排。",
                    "new_key_details": [
                        {
                            "kind": "action",
                            "content": "甲在驿站与乙交谈。",
                            "actor_key": "character:甲",
                        }
                    ],
                }
            ]
        }
        plan = RUNNER.normalize_event_content_plan(
            raw,
            boundary_plan=boundary,
            state=LEGACY.initial_unified_state(),
            rounds=rounds(),
        )
        self.assertEqual(
            "character:甲",
            plan["event_updates"][0]["new_key_details"][0]["actor_key"],
        )

    def test_open_facts_materialize_without_fixed_profile_templates(self) -> None:
        state = LEGACY.initial_unified_state()
        state = LEGACY.apply_entity_candidates(
            state,
            {
                "entities": [
                    {
                        "entity_key": "location:青山",
                        "type": "location",
                        "primary_name": "青山",
                        "aliases_add": [],
                        "description": "故事中出现的青山。",
                        "evidence_refs": ["r0002.assistant"],
                        "event_link_evidence": [],
                        "facts_patch": {
                            "地貌": "群峰连绵",
                            "可见设施": ["山门", "石屋"],
                        },
                    }
                ],
                "relations": [],
            },
        )
        network, report = LEGACY.materialize_network(state)
        self.assertTrue(report["valid"], report["errors"])
        location = next(item for item in network if item["type"] == "location")
        self.assertEqual(
            {"地貌": "群峰连绵", "可见设施": ["山门", "石屋"]},
            location["components"]["entity_facts"]["data"]["facts"],
        )
        self.assertNotIn("location_profile", location["components"])

    def test_event_summary_status_and_details_enter_formal_network(self) -> None:
        state = LEGACY.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "status": "forming",
                "description": "两人在门前说明来意。",
                "story_summary": "甲在门前向乙说明来意，乙听后开始核验信物，能否放行仍未决定。",
                "key_details": [
                    {
                        "id": "detail_probe_001",
                        "kind": "statement",
                        "content": "请代我通报一声。",
                        "fidelity": "verbatim",
                        "actor": "",
                        "source_refs": ["r0001.assistant"],
                    }
                ],
                "source_refs": ["r0001.assistant"],
                "source_slices": [],
                "location_occurrences": [],
                "related_entity_keys": [],
                "related_relation_pairs": [],
            }
        ]
        network, report = LEGACY.materialize_network(state)
        self.assertTrue(report["valid"], report["errors"])
        event = next(item for item in network if item["type"] == "event")
        content = event["components"]["event_content"]["data"]
        self.assertEqual("forming", content["status"])
        self.assertIn("甲在门前", content["story_summary"])
        self.assertEqual("statement", content["key_details"][0]["kind"])
        self.assertNotIn("source_refs", content["key_details"][0])

    def test_event_content_merge_cannot_reintroduce_title(self) -> None:
        boundary = {
            "old_forming_disposition": "absent",
            "segments": [{"partition_key": "p1", "slot": "new_1"}],
        }
        merged = V2.merge_event_content_with_boundaries(
            {
                "event_updates": [
                    {
                        "partition_key": "p1",
                        "title": "模型多返回的旧字段",
                        "description": "短检索说明",
                        "story_summary_add": "完整梗概。",
                    }
                ]
            },
            boundary,
        )
        # 运行记录仍保存原回复；进入世界候选前由固定脚本丢弃旧字段。
        self.assertNotIn("title", merged["event_updates"][0])
        self.assertNotIn("title", V2.EVENT_CONTENT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
