from __future__ import annotations

import importlib.util
import html
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
MODULE_PATH = TOOLS / "airp_extraction_probe.py"
SPEC = importlib.util.spec_from_file_location("airp_extraction_probe", MODULE_PATH)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def sample_round(number: int = 1, user: str = "甲走进山门。", assistant: str = "乙问：‘来者何人？’") -> dict:
    return {
        "round": number,
        "user": {
            "ref": f"r{number:04d}.user",
            "role": "user",
            "name": "甲",
            "source_line": number * 2,
            "content": user,
        },
        "assistant": {
            "ref": f"r{number:04d}.assistant",
            "role": "assistant",
            "name": "叙事者",
            "source_line": number * 2 + 1,
            "content": assistant,
        },
    }


def event_update(slot: str, source_ref: str, quote: str) -> dict:
    return {
        "slot": slot,
        "title": "山门相遇",
        "description": "甲走入山门并与守门的乙交谈。",
        "story_summary": "甲走进山门，守门的乙出声询问其身份，双方开始交谈。",
        "event_beats_add": [
            {
                "content": f"本批事实涉及：{quote}。",
                "source_refs": [source_ref],
            }
        ],
        "participants_add": ["character:甲", "character:乙"],
        "participants_remove": [],
        "location_occurrences_add": [
            {
                "entity_key": "location:山门",
                "roles": ["primary"],
                "source_refs": [source_ref],
            }
        ],
        "locations_remove": [],
        "related_entity_keys_add": [],
        "related_entity_keys_remove": [],
        "event_time": None,
        "unresolved_add": [],
        "unresolved_resolve": [],
        "retire_detail_ids": [],
        "new_key_details": [
            {
                "kind": "statement",
                "content": quote,
                "actor_key": "character:乙",
                "source_refs": [source_ref],
            }
        ],
    }


class AirpExtractionProbeTests(unittest.TestCase):
    def test_first_batch_failure_can_recover_from_initial_checkpoint(self) -> None:
        event_plan = {
            "old_forming_disposition": "absent",
            "segments": [{"source_refs": ["r0001.user"]}],
            "event_updates": [],
        }
        memory_plan = {
            "memories": [
                {"evidence": [{"source_ref": "r0001.assistant", "quote": "问"}]}
            ]
        }
        record = "\n".join(
            [
                f"<pre>{html.escape(json.dumps({'parsed_plan': event_plan}))}</pre>",
                f"<pre>{html.escape(json.dumps({'parsed_plan': memory_plan}))}</pre>",
            ]
        )
        state, recovered_event, recovered_memory = PROBE.recovery_inputs_from_record(
            record
        )
        self.assertEqual([], state["events"])
        self.assertEqual(event_plan, recovered_event)
        self.assertEqual(memory_plan, recovered_memory)

    def test_recovery_selects_only_plans_after_last_committed_round(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {"id": "event_probe_001", "source_refs": ["r0004.assistant"]}
        ]
        old_memory = {
            "parsed_plan": {
                "memories": [
                    {"evidence": [{"source_ref": "r0004.assistant", "quote": "旧"}]}
                ]
            }
        }
        new_event = {
            "parsed_plan": {
                "old_forming_disposition": "keep_distinct",
                "segments": [{"source_refs": ["r0005.user"]}],
                "event_updates": [],
            }
        }
        new_entity = {
            "parsed_plan": {
                "entities": [
                    {
                        "entity_key": "character:甲",
                        "type": "character",
                        "evidence_refs": ["r0005.assistant"],
                    }
                ],
                "relations": [],
            }
        }
        objects = [
            {"state_after_batch": state},
            old_memory,
            new_event,
            new_entity,
        ]
        record = "\n".join(
            f"<pre>{html.escape(json.dumps(obj))}</pre>" for obj in objects
        )
        recovered_state, plans = PROBE.recovery_state_and_current_plans(record)
        self.assertEqual(4, PROBE._processed_round_end(recovered_state))
        self.assertEqual({"event", "entity"}, set(plans))

    def test_recovery_reads_latest_checkpoint_from_single_file_appendix(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {"id": "event_probe_003", "source_refs": ["r0012.assistant"]}
        ]
        prior_objects = [
            {"state_after_batch": state},
            {
                "parsed_plan": {
                    "old_forming_disposition": "keep_distinct",
                    "segments": [{"source_refs": ["r0013.user"]}],
                    "event_updates": [],
                }
            },
        ]
        prior = "# 240 Event、Memory 与全 Entity 单次提取复测完整记录\n" + "\n".join(
            f"<pre>{html.escape(json.dumps(obj))}</pre>" for obj in prior_objects
        )
        record = (
            "# 240 Event、Memory 与全 Entity 单次提取复测完整记录\n"
            "## 附录：本次复测启动前的校准或未提交记录\n"
            f"<pre>{html.escape(prior)}</pre>"
        )
        recovered_state, plans = PROBE.recovery_state_and_current_plans(record)
        self.assertEqual(12, PROBE._processed_round_end(recovered_state))
        self.assertIn("event", plans)

        completed = PROBE.initial_unified_state()
        completed["events"] = [
            {"id": "event_probe_004", "source_refs": ["r0016.assistant"]}
        ]
        record_with_completed_state = (
            "# 240 Event、Memory 与全 Entity 单次提取复测完整记录\n"
            f"<pre>{html.escape(json.dumps({'state_after_batch': completed}))}</pre>\n"
            "## 附录：本次复测启动前的校准或未提交记录\n"
            f"<pre>{html.escape(prior)}</pre>"
        )
        before_16, plans = PROBE.recovery_state_and_current_plans(
            record_with_completed_state, target_round_end=16
        )
        self.assertEqual(12, PROBE._processed_round_end(before_16))
        self.assertIn("event", plans)

    def test_record_history_is_flattened_instead_of_recursively_duplicated(self) -> None:
        oldest = (
            "# 240 Event、Memory 与全 Entity 单次提取复测完整记录\n"
            f"<pre>{html.escape(json.dumps({'oldest_marker': 1}))}</pre>"
        )
        middle = (
            "# 240 Event、Memory 与全 Entity 单次提取复测完整记录\n"
            f"<pre>{html.escape(json.dumps({'middle_marker': 1}))}</pre>\n"
            "## 附录：本次复测启动前的校准或未提交记录\n"
            f"<pre>{html.escape(oldest)}</pre>"
        )
        newest = (
            "# 240 Event、Memory 与全 Entity 单次提取复测完整记录\n"
            f"<pre>{html.escape(json.dumps({'newest_marker': 1}))}</pre>\n"
            "## 附录：本次复测启动前的校准或未提交记录\n"
            f"<pre>{html.escape(middle)}</pre>"
        )
        flattened = PROBE.flatten_record_history(newest)
        self.assertNotIn("## 附录：", flattened)
        self.assertEqual(1, flattened.count("oldest_marker"))
        self.assertEqual(1, flattened.count("middle_marker"))
        self.assertEqual(1, flattened.count("newest_marker"))

    def test_near_source_anchor_is_repaired_without_model_retry(self) -> None:
        source = "高空的罡风吹得我脸上生痛，我低头看向脚下。"
        self.assertEqual(
            "高空的罡风吹得我脸上生痛",
            PROBE._nearest_source_quote(source, "高空的罡风刮得我脸上生痛"),
        )

    def test_attention_prompt_does_not_force_a_boundary(self) -> None:
        self.assertNotIn("记全 → 分准 → 不漏分", PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn("本身不要求合并", PROBE.EVENT_ATTENTION_GUIDE)
        self.assertIn("本身也不形成边界", PROBE.EVENT_ATTENTION_GUIDE)
        self.assertIn("不为数量调整结果", PROBE.EVENT_ATTENTION_GUIDE)
        self.assertIn("中断后恢复", PROBE.EVENT_ATTENTION_GUIDE)
        self.assertNotIn("抵达、治疗、邀约", PROBE.EVENT_ATTENTION_GUIDE)
        self.assertNotIn("驿站", PROBE.EVENT_ATTENTION_GUIDE)
        prompt = PROBE.build_event_prompt(
            PROBE.initial_unified_state(), [sample_round()], 1
        )
        self.assertTrue(prompt.endswith(PROBE.EVENT_ATTENTION_GUIDE))

    def test_event_prompt_renders_single_json_braces_and_short_description_target(self) -> None:
        self.assertIn('"segments": [{', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn("{{", PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn("六十至一百二十个汉字", PROBE.EVENT_SYSTEM_PROMPT)

    def test_event_task_set_does_not_build_an_external_memory_or_entity_call(self) -> None:
        args = PROBE.parse_args(
            [
                "chat.jsonl",
                "--endpoint",
                "https://example.invalid/v1/chat/completions",
                "--model",
                "test-model",
                "--output",
                "record.md",
                "--task-set",
                "event",
            ]
        )
        specs = PROBE.build_task_specs(
            args, PROBE.initial_unified_state(), [sample_round()], 1
        )
        self.assertEqual(["event"], list(specs))
        self.assertEqual(32768, args.event_max_tokens)
        self.assertEqual(90.0, args.stream_idle_timeout)

    def test_event_only_record_does_not_claim_entity_network_validation(self) -> None:
        record = PROBE.render_record(
            {
                "task_set": "event",
                "batches": [],
                "gold_evaluation": {},
                "final_state": PROBE.initial_unified_state(),
            }
        )
        self.assertIn("Event 边界与内容单路短回归", record)
        self.assertNotIn("最终正式 Entity 网络", record)

    def test_event_prompt_delegates_entity_references_to_script(self) -> None:
        self.assertNotIn('"participants_add"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"location_occurrences_add"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"related_entity_keys_add"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn("由独立 Entity 任务提取一次", PROBE.EVENT_SYSTEM_PROMPT)

    def test_entity_evidence_repairs_event_links_without_another_model_call(self) -> None:
        rounds = [
            sample_round(
                assistant="守门弟子乙拦在门前问：‘来者何人？’"
            )
        ]
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "source_refs": ["r0001.assistant"],
                "participants": [],
                "locations": [],
                "location_occurrences": [],
                "related_entity_keys": [],
            }
        ]
        state["entity_candidates"] = {
            "character:送物弟子": {
                "type": "character",
                "event_link_evidence": [
                    {"source_ref": "r0001.assistant", "quote": "守门弟子乙拦在门前"}
                ],
            },
            "item:身份玉牌": {
                "type": "item",
                "event_link_evidence": [
                    {"source_ref": "r0001.assistant", "quote": "守门弟子乙拦在门前问"}
                ],
            },
        }
        warnings = PROBE.reconcile_entity_event_links(state, rounds)
        event = state["events"][0]
        self.assertIn("character:送物弟子", event["participants"])
        self.assertIn("item:身份玉牌", event["related_entity_keys"])
        self.assertEqual(1, len(warnings))

    def test_entity_evidence_moves_to_unique_source_despite_punctuation(self) -> None:
        rounds = [
            sample_round(
                1,
                "甲在屋内。",
                "一道年轻嗓音响起：\n“王岳青师弟可在？”",
            ),
            sample_round(2, "甲开门。", "门外站着一名年轻弟子。"),
        ]
        plan = PROBE.normalize_entity_plan(
            {
                "entities": [
                    {
                        "entity_key": "character:送物弟子",
                        "type": "character",
                        "primary_name": "送物弟子",
                        "description": "前来送物的年轻弟子。",
                        "evidence_refs": ["r0001.assistant"],
                        "event_link_evidence": [
                            {
                                "source_ref": "r0002.assistant",
                                "quote": "一道年轻嗓音响起：‘王岳青师弟可在？’",
                            }
                        ],
                    }
                ],
                "relations": [],
            },
            rounds=rounds,
        )
        self.assertEqual(
            "r0001.assistant",
            plan["entities"][0]["event_link_evidence"][0]["source_ref"],
        )

    def test_sparse_character_patch_preserves_unmentioned_nested_fields(self) -> None:
        state = PROBE.initial_unified_state()
        state["entity_candidates"]["character:甲"] = {
            "entity_key": "character:甲",
            "type": "character",
            "primary_name": "甲",
            "aliases": [],
            "description": "甲正在山门值守。",
            "evidence_refs": ["r0001.assistant"],
            "event_link_evidence": [],
            "character_data": {
                "profile": {"species": "人族", "background": "青山派弟子"},
                "state": {"physical_condition": "健康", "mental_condition": "平静"},
            },
        }
        plan = PROBE.normalize_entity_plan(
            {
                "entities": [
                    {
                        "entity_key": "character:甲",
                        "type": "character",
                        "evidence_refs": ["r0002.assistant"],
                        "character_data_patch": {
                            "state": {"physical_condition": "轻伤"}
                        },
                    }
                ],
                "relations": [],
            }
        )
        next_state = PROBE.apply_entity_candidates(state, plan)
        data = next_state["entity_candidates"]["character:甲"]["character_data"]
        self.assertEqual("人族", data["profile"]["species"])
        self.assertEqual("青山派弟子", data["profile"]["background"])
        self.assertEqual("轻伤", data["state"]["physical_condition"])
        self.assertEqual("平静", data["state"]["mental_condition"])

    def test_existing_character_patch_gets_exact_speaker_evidence(self) -> None:
        state = PROBE.initial_unified_state()
        state["entity_candidates"]["character:甲"] = {
            "entity_key": "character:甲",
            "type": "character",
            "primary_name": "甲",
            "aliases": [],
            "description": "甲。",
        }
        rounds = [sample_round(2, "我负伤退后。", "乙上前查看。")]
        normalized = PROBE.normalize_entity_plan(
            {
                "entities": [
                    {
                        "entity_key": "character:甲",
                        "type": "character",
                        "character_data_patch": {
                            "state": {"physical_condition": "负伤"}
                        },
                    }
                ],
                "relations": [],
            },
            state,
            rounds,
        )
        self.assertEqual(
            ["r0002.user"], normalized["entities"][0]["evidence_refs"]
        )

    def test_exact_existing_alias_is_resolved_without_agent(self) -> None:
        state = PROBE.initial_unified_state()
        state["entity_candidates"]["location:边境古道驿站"] = {
            "entity_key": "location:边境古道驿站",
            "type": "location",
            "primary_name": "边境古道驿站",
            "aliases": ["驿站"],
            "description": "边境古道上的驿站。",
        }
        normalized = PROBE.normalize_entity_plan(
            {
                "entities": [
                    {
                        "entity_key": "location:驿站",
                        "type": "location",
                        "primary_name": "驿站",
                        "description": "驿站已被毁坏。",
                        "evidence_refs": ["r0001.assistant"],
                    }
                ],
                "relations": [],
            },
            state,
        )
        candidate = normalized["entities"][0]
        self.assertEqual("location:边境古道驿站", candidate["entity_key"])
        self.assertNotIn("primary_name", candidate)
        self.assertIn("驿站", candidate["aliases_add"])

    def test_memory_alias_is_normalized_without_model_retry(self) -> None:
        plan = {
            "memories": [
                {
                    "acquisition_mode": "inferred_from_event",
                }
            ]
        }
        normalized = PROBE.normalize_memory_plan(plan)
        self.assertEqual(
            "inferred_from_events",
            normalized["memories"][0]["acquisition_mode"],
        )
        self.assertEqual(
            "inferred_from_event", plan["memories"][0]["acquisition_mode"]
        )

    def test_first_batch_can_remain_one_forming_event(self) -> None:
        rounds = [sample_round()]
        state = PROBE.initial_unified_state()
        plan = {
            "old_forming_disposition": "absent",
            "decision_reason": "双方仍在同一场相遇中，没有自然转折。",
            "boundary_uncertainties": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲走进山门"}
                    ],
                    "segment_summary": "甲进入山门并与乙开始交谈。",
                }
            ],
            "event_updates": [
                event_update("new_1", "r0001.assistant", "来者何人")
            ],
        }
        normalized = PROBE.normalize_event_plan(plan, state, rounds)
        errors, warnings = PROBE.validate_event_plan(normalized, state, rounds)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        applied, _, _ = PROBE.apply_event_candidate(state, normalized)
        self.assertEqual(1, len(applied["events"]))
        self.assertEqual("forming", applied["events"][0]["status"])

    def test_existing_segment_anchor_is_aligned_to_first_source_by_script(self) -> None:
        first_rounds = [sample_round()]
        state = PROBE.initial_unified_state()
        first = {
            "old_forming_disposition": "absent",
            "decision_reason": "首次记录。",
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲走进山门"}
                    ],
                }
            ],
            "event_updates": [
                event_update("new_1", "r0001.assistant", "来者何人")
            ],
        }
        first = PROBE.normalize_event_plan(first, state, first_rounds)
        state, _, _ = PROBE.apply_event_candidate(state, first)

        rounds = [sample_round(2, "甲继续向前。", "乙点头放行。")]
        continuation = {
            "old_forming_disposition": "keep_distinct",
            "decision_reason": "继续前一行动。",
            "segments": [
                {
                    "slot": "forming_existing",
                    "source_refs": ["r0002.user", "r0002.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0002.assistant", "start_quote": "乙点头放行"}
                    ],
                }
            ],
            "event_updates": [
                event_update("forming_existing", "r0002.assistant", "乙点头放行")
            ],
        }
        normalized = PROBE.normalize_event_plan(continuation, state, rounds)
        anchor = normalized["segments"][0]["start_anchors"][0]
        self.assertEqual("r0002.user", anchor["source_ref"])
        self.assertEqual("甲继续向前。", anchor["start_quote"])

    def test_key_details_append_without_resending_old_details(self) -> None:
        rounds = [sample_round()]
        state = PROBE.initial_unified_state()
        first = {
            "old_forming_disposition": "absent",
            "decision_reason": "首次记录。",
            "boundary_uncertainties": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲走进山门"}
                    ],
                    "segment_summary": "相遇。",
                }
            ],
            "event_updates": [
                event_update("new_1", "r0001.assistant", "来者何人")
            ],
        }
        first = PROBE.normalize_event_plan(first, state, rounds)
        state, _, _ = PROBE.apply_event_candidate(state, first)
        old_id = state["events"][0]["key_details"][0]["id"]

        second_rounds = [sample_round(2, "甲答道：‘山下散修。’", "乙点头放行。")]
        second_update = event_update(
            "forming_existing", "r0002.user", "山下散修"
        )
        second_update["location_occurrences_add"] = []
        second = {
            "old_forming_disposition": "keep_distinct",
            "decision_reason": "身份确认仍是山门相遇的直接延续。",
            "boundary_uncertainties": [],
            "segments": [
                {
                    "slot": "forming_existing",
                    "source_refs": ["r0002.user", "r0002.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0002.user", "start_quote": "甲答道"}
                    ],
                    "segment_summary": "甲回答身份后获准进入。",
                }
            ],
            "event_updates": [second_update],
        }
        second = PROBE.normalize_event_plan(second, state, second_rounds)
        errors, _ = PROBE.validate_event_plan(second, state, second_rounds)
        self.assertEqual([], errors)
        state, _, _ = PROBE.apply_event_candidate(state, second)
        details = state["events"][0]["key_details"]
        self.assertEqual(2, len(details))
        self.assertEqual(old_id, details[0]["id"])
        self.assertEqual(2, len(state["events"][0]["event_beats"]))
        self.assertIn("山下散修", state["events"][0]["story_summary"])

    def test_memory_quote_binds_inside_shared_message_boundary(self) -> None:
        rounds = [sample_round(1, "甲旁观。", "旧事结束。新谈判开始。")]
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "status": "pending_finalization",
                "title": "旧事",
                "description": "旧事结束。",
                "story_summary": "旧事结束。",
                "participants": ["character:甲"],
                "locations": [],
                "unresolved": [],
                "source_refs": ["r0001.assistant"],
                "source_slices": [
                    {
                        "source_refs": ["r0001.assistant"],
                        "start_anchors": [
                            {
                                "source_ref": "r0001.assistant",
                                "start_quote": "旧事结束",
                            }
                        ],
                        "end_before_anchors": [
                            {
                                "source_ref": "r0001.assistant",
                                "before_quote": "新谈判开始",
                            }
                        ],
                    }
                ],
                "key_details": [],
            },
            {
                "id": "event_probe_002",
                "status": "forming",
                "title": "谈判",
                "description": "新谈判开始。",
                "story_summary": "新谈判开始。",
                "participants": ["character:甲"],
                "locations": [],
                "unresolved": [],
                "source_refs": ["r0001.assistant"],
                "source_slices": [
                    {
                        "source_refs": ["r0001.assistant"],
                        "start_anchors": [
                            {
                                "source_ref": "r0001.assistant",
                                "start_quote": "新谈判开始",
                            }
                        ],
                        "end_before_anchors": [],
                    }
                ],
                "key_details": [],
            },
        ]
        memory = {
            "evidence": [
                {"source_ref": "r0001.assistant", "quote": "新谈判开始"}
            ]
        }
        event_ids, warnings = PROBE.bind_memory_to_events(memory, state, rounds)
        self.assertEqual(["event_probe_002"], event_ids)
        self.assertEqual([], warnings)

    def test_all_implemented_entity_types_form_a_valid_network(self) -> None:
        state = PROBE.initial_unified_state()
        candidates = {
            "character:甲": ("character", "甲"),
            "character:乙": ("character", "乙"),
            "location:山门": ("location", "山门"),
            "item:旧剑": ("item", "旧剑"),
            "organization:青山派": ("organization", "青山派"),
            "skill:青山剑法": ("skill", "青山剑法"),
            "concept:剑心": ("concept", "剑心"),
        }
        for key, (entity_type, name) in candidates.items():
            state["entity_candidates"][key] = {
                "entity_key": key,
                "type": entity_type,
                "primary_name": name,
                "aliases": [],
                "description": f"测试候选：{name}。",
                "evidence_refs": ["r0001.assistant"],
                "character_data": {} if entity_type == "character" else None,
                "stub": False,
            }
        state["entity_candidates"]["character:甲"]["character_data"] = {
            "current_location_key": "location:山门",
            "inventory": [{"item_key": "item:旧剑", "roles": ["carried"]}],
            "skills": [
                {
                    "skill_key": "skill:青山剑法",
                    "proficiency_description": "初步掌握。",
                }
            ],
        }
        state["events"] = [
            {
                "id": "event_probe_001",
                "status": "forming",
                "title": "山门相遇",
                "description": "甲与乙在山门相遇。",
                "story_summary": "甲携旧剑来到青山派山门，与乙相遇。",
                "participants": ["character:甲", "character:乙"],
                "locations": ["location:山门"],
                "location_occurrences": [
                    {
                        "entity_key": "location:山门",
                        "roles": ["primary"],
                        "source_refs": ["r0001.assistant"],
                    }
                ],
                "related_entity_keys": [
                    "item:旧剑",
                    "organization:青山派",
                    "skill:青山剑法",
                    "concept:剑心",
                ],
                "unresolved": [],
                "source_refs": ["r0001.assistant"],
                "source_slices": [],
                "key_details": [],
            }
        ]
        pair = PROBE.relation_key(["character:甲", "character:乙"])
        state["relation_candidates"][pair] = {
            "participant_keys": ["character:甲", "character:乙"],
            "description": "甲与乙是初次见面的同门。",
            "evidence_refs": ["r0001.assistant"],
            "aspects": [
                {
                    "kind": "fellow_disciples",
                    "description": "双方同属青山派。",
                    "status": "active",
                    "visibility": "public",
                    "participant_roles": [
                        {"character_key": "character:甲", "roles": ["disciple"]},
                        {"character_key": "character:乙", "roles": ["disciple"]},
                    ],
                }
            ],
            "directional_states": [
                {
                    "from_key": "character:甲",
                    "toward_key": "character:乙",
                    "summary": "把乙视作刚认识的同门。",
                    "tags": ["neutral"],
                }
            ],
        }
        state["memories"] = [
            {
                "id": "memory_probe_001",
                "owner_key": "character:甲",
                "description": "甲记得自己在山门遇见乙。",
                "acquisition_mode": "witnessed_event",
                "importance": "recent",
                "evidence": [],
                "related_character_keys": ["character:乙"],
                "related_relation_pairs": [["character:甲", "character:乙"]],
                "event_ids": ["event_probe_001"],
                "event_id": "event_probe_001",
            }
        ]
        network, report = PROBE.materialize_network(state)
        self.assertTrue(report["valid"], report["errors"])
        types = {entity["type"] for entity in network}
        self.assertTrue(
            {
                "character",
                "location",
                "item",
                "organization",
                "skill",
                "concept",
                "event",
                "memory",
                "character_relation",
            }.issubset(types)
        )

    def test_witnessed_memory_repairs_missing_event_participant(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [{"id": "event_probe_001", "participants": []}]
        state["memories"] = [
            {
                "id": "memory_probe_001",
                "owner_key": "character:甲",
                "acquisition_mode": "witnessed_event",
                "event_ids": ["event_probe_001"],
            }
        ]
        warnings = PROBE.reconcile_witnessed_memory_participants(state)
        self.assertEqual(["character:甲"], state["events"][0]["participants"])
        self.assertEqual(1, len(warnings))

    def test_key_detail_actor_repairs_missing_event_participant(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "participants": [],
                "key_details": [{"actor": "character:乙", "content": "来者何人"}],
            }
        ]
        warnings = PROBE.reconcile_key_detail_actors(state)
        self.assertEqual(["character:乙"], state["events"][0]["participants"])
        self.assertEqual(1, len(warnings))


if __name__ == "__main__":
    unittest.main()
