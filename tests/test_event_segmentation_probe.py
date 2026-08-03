from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "event_segmentation_probe.py"
GOLD_PATH = ROOT / "tests" / "fixtures" / "event_boundaries_lantern_rounds_1_20.json"
SPEC = importlib.util.spec_from_file_location("event_segmentation_probe", MODULE_PATH)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def sample_rounds() -> list[dict]:
    return [
        {
            "round": 1,
            "user": {
                "ref": "r0001.user",
                "role": "user",
                "name": "甲",
                "source_line": 2,
                "content": "甲拔剑。",
            },
            "assistant": {
                "ref": "r0001.assistant",
                "role": "assistant",
                "name": "叙事者",
                "source_line": 3,
                "content": "乙后退，甲收剑。",
            },
        }
    ]


def anchors(*items: tuple[str, str]) -> list[dict]:
    return [
        {"source_ref": source_ref, "start_quote": start_quote}
        for source_ref, start_quote in items
    ]


def update(slot: str, description: str) -> dict:
    return {
        "slot": slot,
        "title": description,
        "description": description,
        "story_summary": description,
        "participants_add": ["甲", "乙"],
        "participants_remove": [],
        "locations_add": [],
        "locations_remove": [],
        "unresolved_add": [],
        "unresolved_resolve": [],
        "key_details_keep": [],
        "new_key_details": [],
    }


class EventSegmentationProbeTests(unittest.TestCase):
    def test_clean_assistant_message_keeps_scene_and_content_only(self) -> None:
        raw = (
            "```山道·子时```<content><!-- internal -->甲拔剑。<b>乙后退。</b></content>"
            "<UpdateVariable>secret state</UpdateVariable>"
        )
        cleaned = PROBE.clean_message(raw, "assistant")
        self.assertIn("山道·子时", cleaned)
        self.assertIn("甲拔剑。乙后退。", cleaned)
        self.assertNotIn("internal", cleaned)
        self.assertNotIn("secret state", cleaned)

    def test_jsonl_is_paired_as_streaming_rounds(self) -> None:
        records = [
            {"chat_metadata": {}, "user_name": "甲"},
            {"name": "甲", "is_user": True, "mes": "第一问"},
            {"name": "叙事者", "is_user": False, "mes": "<content>第一答</content>"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.jsonl"
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            rounds = list(PROBE.iter_rounds(path))
        self.assertEqual(1, len(rounds))
        self.assertEqual("r0001.user", rounds[0]["user"]["ref"])
        self.assertEqual("第一答", rounds[0]["assistant"]["content"])

    def test_boundary_and_content_are_applied_in_separate_stages(self) -> None:
        state = PROBE.initial_state()
        boundary_plan = {
            "old_forming_disposition": "absent",
            "boundaries": [
                {
                    "slot": "new_2",
                    "before_anchors": anchors(("r0001.assistant", "乙后退")),
                    "signal": "交手结束",
                }
            ],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                },
                {
                    "slot": "new_2",
                    "source_refs": ["r0001.assistant"],
                    "start_anchors": anchors(("r0001.assistant", "乙后退")),
                },
            ],
        }
        segmented, _, slot_ids = PROBE.apply_boundary_plan(state, boundary_plan)
        self.assertEqual("", segmented["events"][0]["description"])
        result = PROBE.apply_content_plan(
            segmented,
            {"event_updates": [update("new_1", "交手"), update("new_2", "收尾")]},
            slot_ids,
        )
        self.assertEqual(
            ["pending_finalization", "forming"],
            [event["status"] for event in result["events"]],
        )
        self.assertEqual(["交手", "收尾"], [event["description"] for event in result["events"]])

    def test_merge_rebinds_old_forming_memory_without_ai_instruction(self) -> None:
        state = PROBE.initial_state()
        first_boundary = {
            "old_forming_disposition": "absent",
            "boundaries": [
                {
                    "slot": "new_2",
                    "before_anchors": anchors(("old.forming", "余波")),
                    "signal": "结果出现",
                }
            ],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["old.pending"],
                    "start_anchors": anchors(("old.pending", "交手")),
                },
                {
                    "slot": "new_2",
                    "source_refs": ["old.forming"],
                    "start_anchors": anchors(("old.forming", "余波")),
                },
            ],
        }
        state, _, slot_ids = PROBE.apply_boundary_plan(state, first_boundary)
        state = PROBE.apply_content_plan(
            state,
            {"event_updates": [update("new_1", "交手"), update("new_2", "余波")]},
            slot_ids,
        )
        old_forming_id = state["events"][-1]["id"]
        pending_id = state["events"][-2]["id"]
        state["memories"].append(
            {
                "id": "memory_probe_001",
                "owner": "甲",
                "event_id": old_forming_id,
                "summary": "记得余波",
                "source_refs": ["old.forming"],
            }
        )
        merge_plan = {
            "old_forming_disposition": "merge_into_pending",
            "boundaries": [
                {
                    "slot": "new_1",
                    "before_anchors": anchors(("r0001.assistant", "乙后退")),
                    "signal": "开始审问",
                }
            ],
            "segments": [
                {
                    "slot": "pending_tail",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                },
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.assistant"],
                    "start_anchors": anchors(("r0001.assistant", "乙后退")),
                },
            ],
        }
        result, operations, _ = PROBE.apply_boundary_plan(state, merge_plan)
        self.assertNotIn(old_forming_id, {event["id"] for event in result["events"]})
        self.assertEqual(pending_id, result["memories"][0]["event_id"])
        self.assertTrue(any("换绑 1 条 Memory" in item for item in operations))

    def test_structured_event_deltas_are_merged_by_script(self) -> None:
        state = PROBE.initial_state()
        first_boundary = {
            "old_forming_disposition": "absent",
            "boundaries": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                }
            ],
        }
        state, _, slot_ids = PROBE.apply_boundary_plan(state, first_boundary)
        first = update("new_1", "初稿")
        first["locations_add"] = ["山门"]
        first["unresolved_add"] = ["是否入门"]
        state = PROBE.apply_content_plan(state, {"event_updates": [first]}, slot_ids)

        second_boundary = {
            "old_forming_disposition": "keep_distinct",
            "boundaries": [],
            "segments": [
                {
                    "slot": "forming_existing",
                    "source_refs": ["r0001.assistant"],
                    "start_anchors": anchors(("r0001.assistant", "乙后退")),
                }
            ],
        }
        state, _, slot_ids = PROBE.apply_boundary_plan(state, second_boundary)
        second = update("forming_existing", "合并后的完整说明")
        second["participants_add"] = ["甲", "丙"]
        second["participants_remove"] = ["乙"]
        second["locations_add"] = ["山门", "石屋"]
        second["locations_remove"] = ["山门"]
        second["unresolved_add"] = ["何时授业"]
        second["unresolved_resolve"] = ["是否入门"]
        state = PROBE.apply_content_plan(state, {"event_updates": [second]}, slot_ids)
        event = state["events"][0]
        self.assertEqual("合并后的完整说明", event["description"])
        self.assertEqual(["甲", "丙"], event["participants"])
        self.assertEqual(["石屋"], event["locations"])
        self.assertEqual(["何时授业"], event["unresolved"])

    def test_boundary_validation_rejects_old_content(self) -> None:
        plan = {
            "old_forming_disposition": "absent",
            "boundaries": [
                {
                    "slot": "new_1",
                    "before_anchors": anchors(("old.round.assistant", "旧内容")),
                    "signal": "目标变化",
                }
            ],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                }
            ],
        }
        errors = PROBE.validate_boundary_plan(plan, PROBE.initial_state(), sample_rounds())
        self.assertTrue(any("不在本批新增消息中" in error for error in errors))

    def test_boundary_validation_rejects_unaligned_start(self) -> None:
        plan = {
            "old_forming_disposition": "absent",
            "boundaries": [
                {
                    "slot": "new_2",
                    "before_anchors": anchors(("r0001.assistant", "甲收剑")),
                    "signal": "目标变化",
                }
            ],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                },
                {
                    "slot": "new_2",
                    "source_refs": ["r0001.assistant"],
                    "start_anchors": anchors(("r0001.assistant", "乙后退")),
                },
            ],
        }
        errors = PROBE.validate_boundary_plan(plan, PROBE.initial_state(), sample_rounds())
        self.assertTrue(any("没有对齐 new_2" in error for error in errors))

    def test_boundaries_are_derived_from_new_segment_starts(self) -> None:
        plan = {
            "old_forming_disposition": "absent",
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                },
                {
                    "slot": "new_2",
                    "source_refs": ["r0001.assistant"],
                    "start_anchors": anchors(("r0001.assistant", "乙后退")),
                },
            ],
        }
        normalized = PROBE.normalize_boundary_plan(
            plan, PROBE.initial_state(), sample_rounds()
        )
        self.assertEqual(1, len(normalized["boundaries"]))
        self.assertEqual("new_2", normalized["boundaries"][0]["slot"])
        self.assertEqual(
            anchors(("r0001.assistant", "乙后退")),
            normalized["boundaries"][0]["before_anchors"],
        )
        self.assertNotIn("end_before_anchors", normalized["segments"][0])
        self.assertEqual(
            [],
            PROBE.validate_boundary_plan(
                normalized, PROBE.initial_state(), sample_rounds()
            ),
        )

    def test_content_messages_are_physically_trimmed_at_internal_boundary(self) -> None:
        rounds = sample_rounds()
        rounds[0]["user"]["content"] = "饮尽养气粥，推门走出石屋。"
        rounds[0]["assistant"]["content"] = "他喝完粥，随后起身出门。"
        plan = {
            "old_forming_disposition": "absent",
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": anchors(
                        ("r0001.user", "饮尽养气粥"),
                        ("r0001.assistant", "他喝完粥"),
                    ),
                },
                {
                    "slot": "new_2",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": anchors(
                        ("r0001.user", "推门走出石屋"),
                        ("r0001.assistant", "起身出门"),
                    ),
                },
            ],
        }
        normalized = PROBE.normalize_boundary_plan(
            plan, PROBE.initial_state(), rounds
        )
        self.assertEqual(2, len(normalized["segments"][0]["end_before_anchors"]))
        self.assertEqual(
            [],
            PROBE.validate_boundary_plan(normalized, PROBE.initial_state(), rounds),
        )
        bounded = PROBE.bounded_segments_for_content(normalized, rounds)
        first_text = "".join(
            message["content"] for message in bounded[0]["assigned_messages"]
        )
        second_text = "".join(
            message["content"] for message in bounded[1]["assigned_messages"]
        )
        self.assertIn("饮尽养气粥", first_text)
        self.assertIn("他喝完粥", first_text)
        self.assertNotIn("推门走出石屋", first_text)
        self.assertNotIn("起身出门", first_text)
        self.assertIn("推门走出石屋", second_text)
        self.assertIn("起身出门", second_text)

    def test_shared_messages_each_require_their_own_boundary_anchor(self) -> None:
        plan = {
            "old_forming_disposition": "absent",
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                },
                {
                    "slot": "new_2",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                },
            ],
        }
        normalized = PROBE.normalize_boundary_plan(
            plan, PROBE.initial_state(), sample_rounds()
        )
        errors = PROBE.validate_boundary_plan(
            normalized, PROBE.initial_state(), sample_rounds()
        )
        self.assertTrue(any("缺少共用消息" in error for error in errors))

    def test_key_detail_budget_is_warning_not_retry_error(self) -> None:
        boundary_plan = {
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                }
            ]
        }
        item = update("new_1", "交手")
        item["new_key_details"] = [
            {
                "kind": "action",
                "content": "甲拔剑",
                "actor": "甲",
                "source_refs": ["r0001.user"],
            }
            for _ in range(6)
        ]
        errors, warnings = PROBE.validate_content_plan(
            {"event_updates": [item]}, boundary_plan, sample_rounds()
        )
        self.assertEqual([], errors)
        self.assertTrue(any("超过 5 条" in warning for warning in warnings))

    def test_key_details_are_reselected_for_whole_event_instead_of_accumulated(self) -> None:
        state = PROBE.initial_state()
        boundary = {
            "old_forming_disposition": "absent",
            "boundaries": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                }
            ],
        }
        state, _, slot_ids = PROBE.apply_boundary_plan(state, boundary)
        first = update("new_1", "初稿")
        first["new_key_details"] = [
            {
                "kind": "action",
                "content": "甲拔剑",
                "actor": "甲",
                "source_refs": ["r0001.user"],
            }
        ]
        state = PROBE.apply_content_plan(state, {"event_updates": [first]}, slot_ids)
        first_id = state["events"][0]["key_details"][0]["id"]

        second = update("new_1", "定稿")
        second["key_details_keep"] = []
        second["new_key_details"] = [
            {
                "kind": "action",
                "content": "乙后退",
                "actor": "乙",
                "source_refs": ["r0001.assistant"],
            }
        ]
        state = PROBE.apply_content_plan(state, {"event_updates": [second]}, slot_ids)
        details = state["events"][0]["key_details"]
        self.assertEqual(1, len(details))
        self.assertNotEqual(first_id, details[0]["id"])
        self.assertEqual("乙后退", details[0]["content"])

    def test_summary_length_is_warning_not_retry_error(self) -> None:
        boundary_plan = {
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": anchors(("r0001.user", "甲拔剑")),
                }
            ]
        }
        item = update("new_1", "交手")
        item["story_summary"] = "事" * 501
        errors, warnings = PROBE.validate_content_plan(
            {"event_updates": [item]}, boundary_plan, sample_rounds()
        )
        self.assertEqual([], errors)
        self.assertTrue(any("超过 500 字" in warning for warning in warnings))

    def test_human_boundary_fixture_can_score_future_runs_without_judge_model(self) -> None:
        gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
        state = PROBE.initial_state()
        state["events"] = [
            {
                "id": f"event_probe_{index:03d}",
                "status": expected["status"],
                "source_slices": [
                    {"start_anchors": expected["start_anchors"]}
                ],
            }
            for index, expected in enumerate(gold["expected_events"], start=1)
        ]
        passed = PROBE.evaluate_state_against_gold(state, gold, processed_round_end=20)
        self.assertTrue(passed["passed"])

        state["events"].pop()
        failed = PROBE.evaluate_state_against_gold(state, gold, processed_round_end=20)
        self.assertFalse(failed["passed"])
        self.assertTrue(any("Event 数量" in item for item in failed["mismatches"]))

    def test_gold_anchor_allows_a_longer_quote_from_the_same_start(self) -> None:
        self.assertTrue(
            PROBE.equivalent_anchor_signatures(
                [("r0001.user", "茫然地看着自己的手")],
                [("r0001.user", "茫然地看着自己的手常年跟在一起的铁剑已碎")],
            )
        )
        self.assertFalse(
            PROBE.equivalent_anchor_signatures(
                [("r0001.user", "茫然地看着自己的手")],
                [("r0001.user", "铁剑已经碎裂")],
            )
        )

    def test_full_record_contains_prompts_raw_reply_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "batch_01_boundary_system_prompt.txt").write_text(
                "系统提示词", encoding="utf-8"
            )
            (output / "batch_01_boundary_candidate_1_user_prompt.txt").write_text(
                "输入提示词", encoding="utf-8"
            )
            (output / "batch_01_boundary_candidate_1_transport_1_raw.txt").write_text(
                '{"answer":"原回复"}', encoding="utf-8"
            )
            state = PROBE.initial_state()
            run = {
                "model": "test-model",
                "input_file": "test.jsonl",
                "rounds_per_batch": 1,
                "batches": [
                    {
                        "batch_number": 1,
                        "round_start": 1,
                        "round_end": 1,
                        "input_chars": 10,
                        "boundary": {
                            "ok": True,
                            "plan": {
                                "old_forming_disposition": "absent",
                                "decision_reason": "首次整理",
                                "boundaries": [],
                            },
                            "candidate_attempts": [{}],
                            "transport_failures": [],
                        },
                        "content": None,
                        "operations": [],
                        "failure": None,
                        "state_after": state,
                    }
                ],
                "final_state": state,
            }
            record = PROBE.render_full_record(run, output)
        self.assertIn("系统提示词", record)
        self.assertIn("输入提示词", record)
        self.assertIn("原回复", record)
        self.assertIn("本批机器结果", record)

    def test_prompts_keep_boundary_and_content_responsibilities_separate(self) -> None:
        self.assertNotIn('"event_updates"', PROBE.BOUNDARY_SYSTEM_PROMPT)
        self.assertNotIn('"boundaries"', PROBE.CONTENT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
