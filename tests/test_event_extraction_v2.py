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

        entity_prompt = V2.ENTITY_FACT_SYSTEM_PROMPT
        self.assertIn("facts_patch 是开放事实对象", entity_prompt)
        self.assertNotIn("character_data_patch", entity_prompt)
        self.assertNotIn("domain_data_patch", entity_prompt)

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

    def test_entity_facts_normalize_to_open_patch_and_generic_evidence(self) -> None:
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
                    "facts_patch": {
                        "材质": "凡铁",
                        "当前状况": ["剑刃有缺口", "仍可使用"],
                    },
                }
            ]
        }
        plan = RUNNER.normalize_entity_fact_plan(
            raw, state=LEGACY.initial_unified_state(), rounds=rounds()
        )
        entity = plan["entities"][0]
        self.assertEqual({"材质": "凡铁", "当前状况": ["剑刃有缺口", "仍可使用"]}, entity["facts_patch"])
        self.assertEqual(["r0001.assistant"], entity["evidence_refs"])
        self.assertEqual("甲在驿站", entity["event_link_evidence"][0]["quote"])
        self.assertNotIn("domain_data_patch", entity)

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
