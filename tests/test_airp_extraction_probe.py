from __future__ import annotations

import importlib.util
import html
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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
                "actor_key": "character:乙",
                "content": quote,
                "source_ref": source_ref,
            }
        ],
    }


def partition_event_update(partition_key: str, source_ref: str, quote: str) -> dict:
    update = event_update("", source_ref, quote)
    update.pop("slot")
    update["partition_key"] = partition_key
    return update


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

    def test_failed_processed_plan_recovers_from_raw_model_reply(self) -> None:
        raw_plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "event_updates": [
                partition_event_update(
                    "round_0001", "r0001.assistant", "“原始关键对白。”"
                )
            ],
        }
        damaged_plan = json.loads(json.dumps(raw_plan))
        damaged_plan["event_updates"][0]["new_key_details"] = []
        failed_result = {
            "validation_errors": ["旧脚本处理失败"],
            "parsed_plan": damaged_plan,
        }
        record = "\n".join(
            [
                "<details><summary>第 1 次候选：模型完整正式回复</summary>",
                f"<pre>```json\n{html.escape(json.dumps(raw_plan))}\n```</pre>",
                "</details>",
                f"<pre>{html.escape(json.dumps(failed_result))}</pre>",
            ]
        )

        state, plans = PROBE.recovery_state_and_current_plans(record)

        self.assertEqual([], state["events"])
        self.assertEqual(raw_plan, plans["event"])

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

    def test_recovery_reads_event_only_record_from_single_file_appendix(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {"id": "event_probe_001", "source_refs": ["r0008.assistant"]}
        ]
        prior = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            f"<pre>{html.escape(json.dumps({'state_after_batch': state}))}</pre>"
        )
        record = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            "## 附录：本次复测启动前的校准或未提交记录\n"
            f"<pre>{html.escape(prior)}</pre>"
        )
        recovered_state, _ = PROBE.recovery_state_and_current_plans(record)
        self.assertEqual(8, PROBE._processed_round_end(recovered_state))

    def test_recovery_does_not_borrow_an_older_completed_boundary_plan(self) -> None:
        latest = PROBE.initial_unified_state()
        latest["events"] = [
            {"id": "event_latest", "source_refs": ["r0004.assistant"]}
        ]
        older = PROBE.initial_unified_state()
        older["events"] = [
            {"id": "event_older", "source_refs": ["r0008.assistant"]}
        ]
        older_plan = {
            "parsed_plan": {
                "old_forming_disposition": "keep_distinct",
                "event_updates": [],
                "segments": [{"source_refs": ["r0008.assistant"]}],
            }
        }
        latest_run = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            f"<pre>{html.escape(json.dumps({'state_after_batch': latest}))}</pre>"
        )
        older_run = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            f"<pre>{html.escape(json.dumps({'state_after_batch': older}))}</pre>\n"
            f"<pre>{html.escape(json.dumps(older_plan))}</pre>"
        )
        flattened = latest_run + "\n\n---\n\n" + older_run

        recovered_state, plans = PROBE.recovery_state_and_current_plans(flattened)

        self.assertEqual(4, PROBE._processed_round_end(recovered_state))
        self.assertNotIn("event", plans)

    def test_recovery_understands_flattened_history_inside_appendix(self) -> None:
        latest = PROBE.initial_unified_state()
        latest["events"] = [
            {"id": "event_latest", "source_refs": ["r0004.assistant"]}
        ]
        older = PROBE.initial_unified_state()
        older["events"] = [
            {"id": "event_older", "source_refs": ["r0008.assistant"]}
        ]
        latest_run = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            f"<pre>{html.escape(json.dumps({'state_after_batch': latest}))}</pre>"
        )
        older_run = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            f"<pre>{html.escape(json.dumps({'state_after_batch': older}))}</pre>"
        )
        history = latest_run + "\n\n---\n\n" + older_run
        current = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            "## 附录：本次复测启动前的校准或未提交记录\n"
            f"<pre>{html.escape(history)}</pre>"
        )

        recovered_state, _ = PROBE.recovery_state_and_current_plans(current)

        self.assertEqual(4, PROBE._processed_round_end(recovered_state))

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

    def test_first_segment_anchor_is_fixed_to_batch_opening(self) -> None:
        rounds = [sample_round()]
        plan = {
            "old_forming_disposition": "absent",
            "decision_reason": "本批是一段连续的山门相遇。",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {
                            "source_ref": "r0001.assistant",
                            "start_quote": "乙问",
                        }
                    ],
                }
            ],
            "event_updates": [event_update("new_1", "r0001.user", "甲走进山门")],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        first_anchor = normalized["segments"][0]["start_anchors"][0]
        self.assertEqual("r0001.user", first_anchor["source_ref"])
        self.assertEqual("甲走进山门。", first_anchor["start_quote"])
        errors, _ = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)

    def test_near_verbatim_key_detail_is_repaired_to_source_text(self) -> None:
        rounds = [
            sample_round(
                user="甲御风而行。",
                assistant="高空的罡风吹得我脸上生痛，我低头看向脚下。",
            )
        ]
        update = event_update(
            "new_1", "r0001.assistant", "高空的罡风刮得我脸上生痛"
        )
        plan = {
            "old_forming_disposition": "absent",
            "decision_reason": "御风途中保持连续。",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲御风而行"}
                    ],
                }
            ],
            "event_updates": [update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        detail = normalized["event_updates"][0]["new_key_details"][0]
        self.assertEqual(
            "高空的罡风吹得我脸上生痛",
            detail["content"],
        )
        self.assertEqual(["r0001.assistant"], detail["source_refs"])
        self.assertEqual("verbatim", detail["fidelity"])
        self.assertNotIn("source_unit_refs", detail)

    def test_semantic_key_detail_is_kept_as_an_explicit_paraphrase(self) -> None:
        rounds = [sample_round(assistant="乙冷着脸拒绝了甲的请求。")]
        update = event_update(
            "new_1", "r0001.assistant", "乙态度冷淡地回绝了甲"
        )
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲走进山门"}
                    ],
                }
            ],
            "event_updates": [update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        detail = normalized["event_updates"][0]["new_key_details"][0]
        self.assertEqual("乙态度冷淡地回绝了甲", detail["content"])
        self.assertEqual("paraphrase", detail["fidelity"])
        self.assertEqual(["r0001.assistant"], detail["source_refs"])

    def test_unverifiable_key_detail_is_soft_dropped(self) -> None:
        rounds = [sample_round()]
        update = event_update("new_1", "r0001.user", "甲走进山门")
        update["new_key_details"][0]["source_ref"] = "r9999.user"
        plan = {
            "old_forming_disposition": "absent",
            "decision_reason": "本批保持连续。",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲走进山门"}
                    ],
                }
            ],
            "event_updates": [update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], normalized["event_updates"][0]["new_key_details"])
        self.assertEqual(1, len(normalized["script_detail_drops"]))
        errors, warnings = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)
        self.assertTrue(any("软性丢弃" in warning for warning in warnings))

    def test_source_records_are_saved_once_and_events_only_keep_bookmarks(self) -> None:
        rounds = [
            sample_round(
                user="甲进门查看空屋。",
                assistant="乙随后到场递交书信。",
            )
        ]
        first_update = event_update("new_1", "r0001.user", "甲进门查看空屋")
        second_update = event_update(
            "new_2", "r0001.assistant", "乙随后到场递交书信"
        )
        plan = {
            "old_forming_disposition": "absent",
            "decision_reason": "查看空屋落地后，递信开启新的局部互动。",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲进门查看空屋"}
                    ],
                },
                {
                    "slot": "new_2",
                    "source_refs": ["r0001.assistant"],
                    "start_anchors": [
                        {
                            "source_ref": "r0001.assistant",
                            "start_quote": "乙随后到场递交书信",
                        }
                    ],
                },
            ],
            "event_updates": [first_update, second_update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        errors, _ = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)
        state, _, _ = PROBE.apply_event_candidate(
            PROBE.initial_unified_state(), normalized, rounds
        )
        self.assertEqual(2, len(state["source_records"]))
        self.assertEqual(
            "甲进门查看空屋。", state["source_records"]["r0001.user"]["content"]
        )
        self.assertNotIn("units", state["source_records"]["r0001.user"])
        self.assertNotIn("order", state["source_records"]["r0001.user"])
        self.assertNotIn("content", state["events"][0]["source_slices"][0])
        first_hash = state["source_records"]["r0001.user"]["content_sha256"]
        state, _, _ = PROBE.apply_event_candidate(state, normalized, rounds)
        self.assertEqual(
            first_hash, state["source_records"]["r0001.user"]["content_sha256"]
        )

    def test_database_and_runtime_checkpoint_have_distinct_persistence_scope(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {"id": "event_probe_001", "status": "forming", "source_refs": ["r0001.user"]}
        ]
        state["source_records"] = {
            "r0001.user": {
                "source_ref": "r0001.user",
                "view_version": "test",
                "order": 1,
                "role": "user",
                "speaker": "甲",
                "source_line": 1,
                "content": "甲进门。",
                "content_sha256": "hash",
                "units": [{"unit_ref": "r0001.user.u001"}],
            }
        }
        state["boundary_workspace"] = [{"candidate_id": "before_round_0002"}]
        state["boundary_workspace_history"] = [{"candidate_id": "old"}]
        state["warnings"] = ["过程告警"]

        database = PROBE.database_snapshot(state)
        checkpoint = PROBE.runtime_checkpoint(state)

        self.assertEqual({"events", "memories", "source_records"}, set(database))
        self.assertNotIn("units", database["source_records"]["r0001.user"])
        self.assertNotIn("order", database["source_records"]["r0001.user"])
        self.assertIn("boundary_workspace", checkpoint)
        self.assertIn("id_maps", checkpoint)
        self.assertNotIn("boundary_workspace_history", checkpoint)
        self.assertNotIn("warnings", checkpoint)

    def test_recovery_prefers_new_single_checkpoint_object(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {"id": "event_probe_001", "source_refs": ["r0004.assistant"]}
        ]
        record = (
            "# 240 Event 边界与内容单路短回归完整记录\n"
            f"<pre>{html.escape(json.dumps({'checkpoint_state': state}))}</pre>"
        )

        recovered, _ = PROBE.recovery_state_and_current_plans(record)

        self.assertEqual(4, PROBE._processed_round_end(recovered))

    def test_record_renders_compact_database_view_and_one_recovery_checkpoint(self) -> None:
        state = PROBE.initial_unified_state()
        state["source_records"] = {
            "r0001.user": {
                "source_ref": "r0001.user",
                "view_version": "test",
                "role": "user",
                "speaker": "甲",
                "source_line": 1,
                "content": "唯一原文标记。",
                "content_sha256": "hash",
            }
        }
        record = PROBE.render_record(
            {
                "task_set": "event",
                "checkpoint_round_end": 1,
                "batches": [
                    {
                        "batch_number": 1,
                        "round_start": 1,
                        "round_end": 1,
                        "status": "committed",
                        "tasks": {},
                        "operations": [],
                        "database_state_summary": PROBE.state_report_summary(state),
                    }
                ],
                "gold_evaluation": {},
                "final_state": PROBE.database_snapshot(state),
                "checkpoint_state": PROBE.runtime_checkpoint(state),
            }
        )

        self.assertIn("数据库候选快照（召回与维护数据）", record)
        self.assertIn("恢复检查点（运行记录，不是数据库）", record)
        self.assertIn("database_state_summary", record)
        self.assertNotIn("state_after_batch", record)
        self.assertEqual(1, record.count("唯一原文标记。"))

    def test_separate_event_updates_override_a_legacy_continue_decision(self) -> None:
        rounds = [
            sample_round(1, "甲走进山门。", "乙询问来意。"),
            sample_round(2, "甲说明来意。", "乙放行。"),
        ]
        first = partition_event_update("round_0001", "r0001.user", "甲走进山门")
        first["title"] = "走入山门"
        first["description"] = "甲走入山门，守门人询问来意。"
        second = partition_event_update("round_0002", "r0002.user", "甲说明来意")
        second["title"] = "说明来意并获准"
        second["description"] = "甲说明来意后获准进入。"
        first["story_summary"] = "甲走入山门，守门人询问来意。"
        second["story_summary"] = "甲说明来意，守门人随后放行。"
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [first, second],
        }

        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(2, len(normalized["event_updates"]))
        self.assertEqual(
            ["round_0001", "round_0002"],
            [update["partition_key"] for update in normalized["event_updates"]],
        )
        self.assertEqual(
            ["new_1", "new_2"],
            [update["slot"] for update in normalized["event_updates"]],
        )
        self.assertEqual(
            "new_event",
            normalized["script_partition"]["boundary_decisions"][
                "before_round_0002"
            ],
        )
        self.assertEqual([], normalized["script_update_coalesces"])
        errors, warnings = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)
        self.assertTrue(any("当前分段以 Event 分项为准" in warning for warning in warnings))

    def test_selected_event_start_overrides_the_same_unresolved_candidate(self) -> None:
        rounds = [sample_round(1), sample_round(2)]
        plan = {
            "old_forming_disposition": "absent",
            "unresolved_candidate_ids": ["before_round_0002"],
            "event_updates": [
                partition_event_update("round_0001", "r0001.assistant", "乙发问"),
                partition_event_update("round_0002", "r0002.assistant", "乙发问"),
            ],
        }

        segments, partition = PROBE.derive_event_segments(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(2, len(segments))
        self.assertEqual([], partition["unresolved_boundary_ids"])
        self.assertTrue(any("最终分项为准" in item for item in partition["warnings"]))

    def test_unassigned_update_without_batch_facts_is_dropped(self) -> None:
        rounds = [sample_round(1, "甲走进山门。", "乙询问来意。")]
        empty_old_tail = partition_event_update(
            "existing_forming_tail", "r0001.user", "不会保留"
        )
        empty_old_tail["new_key_details"] = []
        actual = partition_event_update(
            "round_0001", "r0001.user", "甲走进山门"
        )
        state = PROBE.initial_unified_state()
        state["events"] = [{"status": "forming"}]
        plan = {
            "old_forming_disposition": "keep_distinct",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0001",
                    "decision": "new_event",
                    "basis_code": "distinct_local_activity",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [empty_old_tail, actual],
        }

        normalized = PROBE.normalize_event_plan(plan, state, rounds)

        self.assertEqual(1, len(normalized["event_updates"]))
        self.assertEqual(
            "round_0001", normalized["event_updates"][0]["partition_key"]
        )
        self.assertEqual(
            [
                {
                    "partition_key": "existing_forming_tail",
                    "reason": "not_in_final_partition_and_no_batch_delta",
                }
            ],
            normalized["script_noop_update_drops"],
        )

    def test_unassigned_old_tail_with_only_prior_source_details_is_dropped(self) -> None:
        rounds = [sample_round(5, "甲另起一问。", "乙回答新问题。")]
        stale_old_tail = partition_event_update(
            "existing_forming_tail", "r0004.assistant", "旧问题的回答"
        )
        actual = partition_event_update(
            "round_0005", "r0005.assistant", "乙回答新问题"
        )
        state = PROBE.initial_unified_state()
        state["events"] = [{"status": "forming"}]
        plan = {
            "old_forming_disposition": "keep_distinct",
            "event_updates": [stale_old_tail, actual],
        }

        normalized = PROBE.normalize_event_plan(plan, state, rounds)

        self.assertEqual(1, len(normalized["event_updates"]))
        self.assertEqual("round_0005", normalized["event_updates"][0]["partition_key"])
        self.assertEqual(
            "not_in_final_partition_and_no_batch_delta",
            normalized["script_noop_update_drops"][0]["reason"],
        )

    def test_unassigned_update_with_batch_fact_is_not_silently_dropped(self) -> None:
        rounds = [sample_round(1, "甲走进山门。", "乙询问来意。")]
        unassigned = partition_event_update(
            "existing_forming_tail", "r0001.user", "甲走进山门"
        )
        actual = partition_event_update(
            "round_0001", "r0001.assistant", "乙询问来意"
        )
        state = PROBE.initial_unified_state()
        state["events"] = [{"status": "forming"}]
        plan = {
            "old_forming_disposition": "keep_distinct",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0001",
                    "decision": "new_event",
                    "basis_code": "distinct_local_activity",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [unassigned, actual],
        }

        normalized = PROBE.normalize_event_plan(plan, state, rounds)

        self.assertEqual(2, len(normalized["event_updates"]))
        self.assertEqual([], normalized["script_noop_update_drops"])

    def test_attention_prompt_uses_short_framework_without_a_thinking_chain(self) -> None:
        system = PROBE.EVENT_SYSTEM_PROMPT
        guide = PROBE.EVENT_ATTENTION_GUIDE
        self.assertNotIn("记全 → 分准 → 不漏分", system)
        self.assertIn("连续故事完整读完", guide)
        self.assertIn("按发生顺序整理 Event", guide)
        self.assertIn("一次问答中的追问", guide)
        self.assertIn("不要用长期目标", guide)
        self.assertNotIn("余波", guide)
        self.assertNotIn("收尾", guide)
        self.assertNotIn("按以下顺序", guide)
        self.assertNotIn("交叉检查", guide)
        prompt = PROBE.build_event_prompt(
            PROBE.initial_unified_state(), [sample_round(1), sample_round(2)], 1
        )
        self.assertIn("本批连续叙事", prompt)
        self.assertIn("直接返回最终 JSON", prompt)
        self.assertNotIn("r0001.assistant", prompt)
        self.assertNotIn('"round_partitions"', prompt)
        self.assertNotIn('"source_blocks"', prompt)
        self.assertNotIn('"boundary_candidates"', prompt)
        self.assertNotIn('"internal_start_candidates"', prompt)

    def test_script_marks_scene_place_change_as_a_boundary_clue(self) -> None:
        rounds = [
            sample_round(
                1,
                "甲在驿站收剑。",
                "[场景时间：边境古道·天元243年3月1日]\n甲随乙离开。",
            ),
            sample_round(
                2,
                "甲在高空发问。",
                "[场景时间：神州·苍穹·天元243年3月1日]\n乙回答。",
            ),
        ]
        blocks = PROBE.event_source_blocks(rounds)
        candidates = PROBE.event_boundary_candidates(
            PROBE.initial_unified_state(), blocks
        )
        self.assertEqual("边境古道", blocks[0]["scene_places"][0])
        self.assertEqual("神州·苍穹", blocks[1]["scene_places"][0])
        self.assertIn("乙回答", blocks[1]["closing"]["quote"])
        self.assertEqual("before_round_0002", candidates[0]["candidate_id"])
        self.assertEqual(
            "scene_place_change", candidates[0]["script_clues"][0]["kind"]
        )
        self.assertEqual("unresolved", candidates[0]["fallback_action"])
        self.assertIn("甲在高空发问", candidates[0]["right_context"]["quote"])
        self.assertIn("甲随乙离开", candidates[0]["left_context"]["quote"])
        self.assertNotIn("merge_policy", candidates[0])

    def test_scene_change_is_a_soft_clue_and_can_continue_in_current_batch(self) -> None:
        rounds = [
            sample_round(
                1,
                "甲在驿站收剑。",
                "[场景时间：边境古道·天元243年3月1日]\n甲随乙离开。",
            ),
            sample_round(
                2,
                "甲在高空发问。",
                "[场景时间：神州·苍穹·天元243年3月1日]\n乙回答。",
            ),
        ]
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                }
            ],
            "internal_start_decisions": [],
        }

        segments, partition = PROBE.derive_event_segments(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(1, len(segments))
        self.assertEqual(
            "continue", partition["boundary_decisions"]["before_round_0002"]
        )
        self.assertEqual([], partition["unresolved_boundary_ids"])

    def test_script_derives_segments_from_explicit_boundary_decisions(self) -> None:
        rounds = [
            sample_round(1, "甲在驿站醒来。", "乙毁去甲的旧剑。"),
            sample_round(2, "甲决定随乙离开。", "乙答应收徒。"),
            sample_round(3, "甲走出驿站。", "两人御空离开。"),
            sample_round(4, "甲在云海发问。", "乙开始解释剑骨。"),
        ]
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                },
                {
                    "candidate_id": "before_round_0003",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                },
                {
                    "candidate_id": "before_round_0004",
                    "decision": "new_event",
                    "basis_code": "distinct_local_activity",
                },
            ],
            "internal_start_decisions": [],
            "event_updates": [
                partition_event_update("round_0001", "r0001.assistant", "乙毁去甲的旧剑"),
                partition_event_update("round_0004", "r0004.assistant", "乙开始解释剑骨"),
            ],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(["new_1", "new_2"], [item["slot"] for item in normalized["segments"]])
        self.assertEqual(
            ["round_0001", "round_0004"],
            [item["partition_key"] for item in normalized["segments"]],
        )
        self.assertEqual(
            ["new_1", "new_2"],
            [item["slot"] for item in normalized["event_updates"]],
        )
        self.assertEqual(
            {
                "before_round_0002": "continue",
                "before_round_0003": "continue",
                "before_round_0004": "new_event",
            },
            normalized["script_partition"]["boundary_decisions"],
        )
        errors, _ = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)

    def test_script_partition_can_be_rebuilt_without_changing_source(self) -> None:
        rounds = [sample_round(number) for number in range(1, 5)]
        state = PROBE.initial_unified_state()
        decided_plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {"candidate_id": "before_round_0002", "decision": "continue"},
                {"candidate_id": "before_round_0003", "decision": "continue"},
                {"candidate_id": "before_round_0004", "decision": "new_event"},
            ],
            "internal_start_decisions": [],
        }
        first, first_snapshot = PROBE.derive_event_segments(
            decided_plan, state, rounds
        )
        default, _ = PROBE.derive_event_segments(
            {
                "old_forming_disposition": "absent",
                "boundary_decisions": [
                    {
                        "candidate_id": f"before_round_{number:04d}",
                        "decision": "new_event",
                    }
                    for number in range(2, 5)
                ],
                "internal_start_decisions": [],
            },
            state,
            rounds,
        )
        rebuilt, rebuilt_snapshot = PROBE.derive_event_segments(
            decided_plan, state, rounds
        )

        expected_refs = [
            message["ref"] for message in PROBE.batch_messages(rounds)
        ]
        self.assertEqual(2, len(first))
        self.assertEqual(4, len(default))
        self.assertEqual(first, rebuilt)
        self.assertEqual(first_snapshot, rebuilt_snapshot)
        self.assertEqual(
            expected_refs,
            list(dict.fromkeys(ref for segment in first for ref in segment["source_refs"])),
        )

    def test_later_clear_boundary_resolves_earlier_uncertainty_without_suppressing_it(self) -> None:
        rounds = [sample_round(number) for number in range(1, 5)]
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "unresolved",
                    "basis_code": "insufficient_context",
                },
                {
                    "candidate_id": "before_round_0003",
                    "decision": "new_event",
                    "basis_code": "distinct_local_activity",
                },
                {
                    "candidate_id": "before_round_0004",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                },
            ],
            "internal_start_decisions": [],
            "event_updates": [
                partition_event_update("round_0001", "r0001.assistant", "乙发问"),
                partition_event_update("round_0003", "r0003.assistant", "乙转入新事"),
            ],
        }
        state = PROBE.initial_unified_state()
        normalized = PROBE.normalize_event_plan(plan, state, rounds)

        self.assertEqual(2, len(normalized["segments"]))
        self.assertEqual(
            [], normalized["script_partition"]["unresolved_boundary_ids"]
        )
        self.assertEqual(
            [
                {
                    "candidate_id": "before_round_0002",
                    "resolution": "continued_until_later_confirmed_boundary",
                    "resolved_by": "before_round_0003",
                }
            ],
            normalized["script_partition"]["resolved_unresolved"],
        )
        applied, _, _ = PROBE.apply_event_candidate(state, normalized, rounds)
        self.assertEqual([], applied["boundary_workspace"])
        self.assertNotIn("boundary_workspace_history", applied)

    def test_next_batch_clear_boundary_closes_prior_boundary_workspace(self) -> None:
        state = PROBE.initial_unified_state()
        first_rounds = [sample_round(1), sample_round(2)]
        first = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "unresolved",
                    "basis_code": "insufficient_context",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [
                partition_event_update("round_0001", "r0001.assistant", "乙发问")
            ],
        }
        first = PROBE.normalize_event_plan(first, state, first_rounds)
        state, _, _ = PROBE.apply_event_candidate(state, first, first_rounds)
        self.assertEqual(
            ["before_round_0002"],
            [item["candidate_id"] for item in state["boundary_workspace"]],
        )

        second_rounds = [sample_round(3, "甲另起话题。", "乙开始处理新事。")]
        second = {
            "old_forming_disposition": "keep_distinct",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0003",
                    "decision": "new_event",
                    "basis_code": "distinct_interaction",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [
                partition_event_update("round_0003", "r0003.assistant", "开始处理新事")
            ],
        }
        second = PROBE.normalize_event_plan(second, state, second_rounds)
        state, _, _ = PROBE.apply_event_candidate(state, second, second_rounds)
        self.assertEqual([], state["boundary_workspace"])
        self.assertNotIn("boundary_workspace_history", state)

    def test_forming_event_keeps_supported_start_time_but_drops_end_time(self) -> None:
        rounds = [
            sample_round(
                1,
                "[场景时间：天元二年]甲走进山门。",
                "乙问：‘来者何人？’",
            )
        ]
        update = partition_event_update(
            "round_0001", "r0001.assistant", "来者何人"
        )
        update["event_time"] = {
            "start_time": {"expression": "天元二年", "precision": "exact"},
            "end_time": {"expression": "天元二年", "precision": "exact"},
        }
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "event_updates": [update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        event_time = normalized["event_updates"][0]["event_time"]
        self.assertEqual("天元二年", event_time["start_time"]["expression"])
        self.assertNotIn("end_time", event_time)
        self.assertEqual(
            "forming_event_has_no_end_time",
            normalized["script_time_repairs"][0]["reason"],
        )

    def test_existing_event_append_cannot_move_its_start_time_later(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "status": "forming",
                "event_time": {
                    "start_time": {
                        "expression": "天元二年·丑时初刻",
                        "precision": "exact",
                    }
                },
            }
        ]
        plan = {
            "event_updates": [
                {
                    "slot": "forming_existing",
                    "event_time": {
                        "start_time": {
                            "expression": "天元二年·卯时三刻",
                            "precision": "exact",
                        }
                    },
                }
            ]
        }

        PROBE._preserve_existing_start_times(plan, state)

        self.assertEqual(
            "天元二年·丑时初刻",
            plan["event_updates"][0]["event_time"]["start_time"]["expression"],
        )
        self.assertEqual(
            "existing_boundary_start_unchanged",
            plan["script_time_preservations"][0]["reason"],
        )

    def test_time_expression_can_skip_an_interposed_weekday(self) -> None:
        rounds = [
            sample_round(
                1,
                "[场景时间：天元243年3月1日 星期一 丑时初刻]甲走进山门。",
                "乙问：‘来者何人？’",
            )
        ]
        update = partition_event_update(
            "round_0001", "r0001.assistant", "来者何人"
        )
        update["event_time"] = {
            "start_time": {
                "expression": "天元243年3月1日·丑时初刻",
                "precision": "exact",
            }
        }
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "event_updates": [update],
        }

        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(
            "天元243年3月1日·丑时初刻",
            normalized["event_updates"][0]["event_time"]["start_time"][
                "expression"
            ],
        )
        self.assertFalse(
            PROBE._time_expression_supported(
                "天元243年3月1日·卯时",
                "[场景时间：天元243年3月1日]\n经过许久。\n[时间：卯时]",
            )
        )

    def test_script_can_add_a_new_event_start_inside_one_message(self) -> None:
        rounds = [
            sample_round(1, "甲旁观。", "旧事结束。门外传来脚步声，新谈判开始。")
        ]
        candidate_id = "inside_r0001.assistant.u002"
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [
                {
                    "candidate_id": candidate_id,
                    "decision": "new_event",
                    "basis_code": "distinct_interaction",
                }
            ],
            "event_updates": [
                partition_event_update("round_0001", "r0001.assistant", "旧事结束"),
                partition_event_update(
                    candidate_id, "r0001.assistant", "新谈判开始"
                ),
            ],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(["new_1", "new_2"], [item["slot"] for item in normalized["segments"]])
        self.assertEqual(
            ["r0001.assistant"],
            normalized["segments"][1]["source_refs"],
        )
        self.assertEqual(
            "门外传来脚步声，新谈判开始。",
            normalized["segments"][1]["start_anchors"][0]["start_quote"],
        )
        errors, _ = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)

    def test_script_marks_arrival_transition_inside_message_as_candidate(self) -> None:
        rounds = [
            sample_round(
                9,
                "二人继续赶路。",
                "云海向后退去。遁光开始下降。白清弦介绍剑庐门规。",
            )
        ]

        candidates = PROBE.event_internal_start_candidates(rounds)

        self.assertEqual(1, len(candidates))
        self.assertEqual(
            "inside_r0009.assistant.u002", candidates[0]["candidate_id"]
        )
        self.assertEqual(["arrival_transition"], candidates[0]["script_clues"])
        self.assertIn("云海向后退去", candidates[0]["left_quote"])

    def test_completed_arrival_is_a_hard_internal_boundary(self) -> None:
        rounds = [
            sample_round(
                9,
                "二人继续赶路。",
                "云海向后退去。遁光开始下降。片刻后，二人落在剑庐广场，白清弦领他入内。",
            )
        ]
        candidate_id = "inside_r0009.assistant.u002"
        candidates = PROBE.event_internal_start_candidates(rounds)

        self.assertTrue(candidates[0]["hard_boundary"])
        self.assertEqual(
            [candidate_id],
            [item["candidate_id"] for item in PROBE.hard_event_starts(
                PROBE.initial_unified_state(), rounds
            )],
        )

        plan = {
            "old_forming_disposition": "absent",
            "event_updates": [
                partition_event_update(
                    "round_0009", "r0009.user", "二人继续赶路"
                )
            ],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        errors, _ = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )

        self.assertEqual(2, len(normalized["segments"]))
        self.assertIn(candidate_id, normalized["script_partition"]["hard_boundary_ids"])
        self.assertIn("缺少 Event 内容：new_2", errors)

    def test_arrival_during_one_continuing_activity_remains_soft(self) -> None:
        rounds = [
            sample_round(
                9,
                "二人继续赶路。",
                "云海向后退去。遁光开始下降。二人落在剑庐广场，继续谈论刚才的剑意。",
            )
        ]

        candidates = PROBE.event_internal_start_candidates(rounds)

        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0]["hard_boundary"])

    def test_return_to_room_and_begin_recovery_is_a_hard_round_boundary(self) -> None:
        rounds = [
            sample_round(10, "甲仍在广场。", "乙交代完住处。"),
            sample_round(11, "甲回到石屋，盘腿调息。", "甲开始检查伤势。"),
        ]
        state = PROBE.initial_unified_state()
        blocks = PROBE.event_source_blocks(rounds)
        candidates = PROBE.event_boundary_candidates(state, blocks)

        self.assertTrue(candidates[0]["hard_boundary"])
        self.assertEqual("before_round_0011", candidates[0]["candidate_id"])

        plan = {
            "old_forming_disposition": "absent",
            "event_updates": [
                partition_event_update(
                    "round_0010", "r0010.assistant", "乙交代完住处"
                )
            ],
        }
        normalized = PROBE.normalize_event_plan(plan, state, rounds)

        self.assertEqual(
            ["round_0010", "round_0011"],
            [segment["partition_key"] for segment in normalized["segments"]],
        )

    def test_location_change_does_not_hard_split_a_continuing_conversation(self) -> None:
        rounds = [
            sample_round(
                1,
                "甲在院中发问。",
                "[场景时间：剑庐院中·天元二年]乙开始回答。",
            ),
            sample_round(
                2,
                "甲走进房间坐下，继续谈论刚才的问题。",
                "[场景时间：剑庐石屋·天元二年]乙接着回答。",
            ),
        ]
        candidates = PROBE.event_boundary_candidates(
            PROBE.initial_unified_state(), PROBE.event_source_blocks(rounds)
        )

        self.assertFalse(candidates[0]["hard_boundary"])

    def test_missing_event_content_is_repaired_from_locked_segments_and_source(self) -> None:
        rounds = [
            sample_round(1, "甲旁观。", "旧事结束。门外传来脚步声，新谈判开始。")
        ]
        candidate_id = "inside_r0001.assistant.u002"
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [
                {
                    "candidate_id": candidate_id,
                    "decision": "new_event",
                    "basis_code": "distinct_interaction",
                }
            ],
            "event_updates": [
                partition_event_update(
                    "round_0001", "r0001.assistant", "新谈判开始"
                )
            ],
        }

        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        errors, _ = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual(["缺少 Event 内容：new_2"], errors)

        repair_prompt = PROBE.build_event_content_repair_prompt(
            normalized, errors, PROBE.initial_unified_state(), rounds
        )
        self.assertIsNotNone(repair_prompt)
        self.assertIn("边界、partition_key 和来源范围均已锁定", repair_prompt)
        self.assertIn("门外传来脚步声，新谈判开始", repair_prompt)
        self.assertNotIn("previous_event_updates", repair_prompt)
        self.assertNotIn('"source_messages":', repair_prompt)
        repair_payload = json.loads(repair_prompt.rsplit("\n", 1)[-1])
        self.assertEqual(2, len(repair_payload["fixed_segments"]))
        self.assertEqual(
            "旧事结束。",
            repair_payload["fixed_segments"][0]["assigned_messages"][1][
                "content"
            ],
        )
        self.assertEqual(
            "门外传来脚步声，新谈判开始。",
            repair_payload["fixed_segments"][1]["assigned_messages"][0][
                "content"
            ],
        )
        self.assertIsNotNone(
            PROBE.build_event_content_repair_prompt(
                normalized,
                [*errors, "new_1 缺少本批新增故事摘要"],
                PROBE.initial_unified_state(),
                rounds,
            )
        )

        repair_response = {
            "event_updates": [
                partition_event_update(
                    "round_0001", "r0001.assistant", "旧事结束"
                ),
                partition_event_update(
                    candidate_id, "r0001.assistant", "新谈判开始"
                ),
            ]
        }
        repaired = PROBE.merge_event_content_repair(normalized, repair_response)
        repaired = PROBE.normalize_event_plan(
            repaired, PROBE.initial_unified_state(), rounds
        )
        repaired_errors, _ = PROBE.validate_event_plan(
            repaired, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], repaired_errors)
        self.assertEqual(2, len(repaired["segments"]))
        self.assertEqual(
            "new_event",
            repaired["script_partition"]["internal_start_decisions"][candidate_id],
        )

    def test_missing_summary_alone_uses_locked_segment_content_repair(self) -> None:
        rounds = [sample_round(1, "甲走进山门。", "乙询问来意。")]
        plan = {
            "old_forming_disposition": "absent",
            "event_updates": [
                partition_event_update(
                    "round_0001", "r0001.assistant", "乙询问来意"
                )
            ],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )

        repair_prompt = PROBE.build_event_content_repair_prompt(
            normalized,
            ["new_1 缺少本批新增故事摘要"],
            PROBE.initial_unified_state(),
            rounds,
        )

        self.assertIsNotNone(repair_prompt)
        repair_payload = json.loads(repair_prompt.rsplit("\n", 1)[-1])
        self.assertEqual(["round_0001"], repair_payload["missing_partition_keys"])

    def test_event_time_without_start_is_removed_as_unknown(self) -> None:
        rounds = [
            sample_round(1, "甲走进山门。", "乙询问来意。"),
            sample_round(2, "甲说明来意。", "乙让他进入。"),
        ]
        update = partition_event_update(
            "round_0001", "r0001.assistant", "乙询问来意"
        )
        update["event_time"] = {
            "end_time": {"expression": "乙询问来意", "precision": "relative"}
        }
        plan = {
            "old_forming_disposition": "absent",
            "event_updates": [
                update,
                partition_event_update(
                    "round_0002", "r0002.assistant", "乙让他进入"
                ),
            ],
        }

        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )

        self.assertIsNone(normalized["event_updates"][0]["event_time"])
        self.assertEqual(
            "incomplete_without_start_time",
            normalized["script_time_repairs"][-1]["reason"],
        )

    def test_model_task_uses_one_locked_boundary_content_repair(self) -> None:
        rounds = [
            sample_round(1, "甲旁观。", "旧事结束。门外传来脚步声，新谈判开始。")
        ]
        candidate_id = "inside_r0001.assistant.u002"
        first_response = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [],
            "internal_start_decisions": [
                {
                    "candidate_id": candidate_id,
                    "decision": "new_event",
                    "basis_code": "distinct_interaction",
                }
            ],
            "event_updates": [
                partition_event_update(
                    "round_0001", "r0001.assistant", "新谈判开始"
                )
            ],
        }
        repair_response = {
            "event_updates": [
                partition_event_update(
                    "round_0001", "r0001.assistant", "旧事结束"
                ),
                partition_event_update(
                    candidate_id, "r0001.assistant", "新谈判开始"
                ),
            ]
        }
        metadata = {"finish_reason": "stop", "reasoning_chars": 0}

        with patch.object(
            PROBE,
            "call_chat_completion",
            side_effect=[
                (json.dumps(first_response, ensure_ascii=False), metadata),
                (json.dumps(repair_response, ensure_ascii=False), metadata),
            ],
        ) as mocked_call:
            result = PROBE.run_model_task(
                task="event",
                endpoint="https://example.invalid",
                api_key="secret",
                model="test-model",
                system_prompt=PROBE.EVENT_SYSTEM_PROMPT,
                user_prompt=PROBE.build_event_prompt(
                    PROBE.initial_unified_state(), rounds, 1
                ),
                timeout=10,
                max_tokens=4096,
                candidate_attempt_limit=1,
                normalizer=lambda value: PROBE.normalize_event_plan(
                    value, PROBE.initial_unified_state(), rounds
                ),
                validator=lambda value: PROBE.validate_event_plan(
                    value, PROBE.initial_unified_state(), rounds
                ),
                repair_prompt_builder=lambda value, errors: PROBE.build_event_content_repair_prompt(
                    value, errors, PROBE.initial_unified_state(), rounds
                ),
                repair_plan_merger=PROBE.merge_event_content_repair,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(2, mocked_call.call_count)
        self.assertEqual(
            ["semantic_candidate", "locked_boundary_content_repair"],
            [attempt["attempt_kind"] for attempt in result["attempts"]],
        )
        self.assertEqual(2, len(result["plan"]["segments"]))

    def test_plain_time_header_is_not_mistaken_for_a_location(self) -> None:
        rounds = [
            sample_round(1, "甲等待。", "[时间：寅时]\n风声渐急。"),
            sample_round(2, "甲继续等待。", "[时间：卯时]\n天色渐亮。"),
        ]
        blocks = PROBE.event_source_blocks(rounds)
        candidates = PROBE.event_boundary_candidates(
            PROBE.initial_unified_state(), blocks
        )
        self.assertEqual([], blocks[0]["scene_places"])
        self.assertEqual([], blocks[1]["scene_places"])
        self.assertEqual([], candidates[0]["script_clues"])

    def test_ignored_strong_script_boundary_is_only_a_soft_warning(self) -> None:
        rounds = [
            sample_round(
                1,
                "甲在驿站收剑。",
                "[场景时间：边境古道·天元243年3月1日]\n乙问：‘来者何人？’",
            ),
            sample_round(
                2,
                "甲在高空发问。",
                "[场景时间：神州·苍穹·天元243年3月1日]\n乙回答。",
            ),
        ]
        update = event_update("new_1", "r0001.assistant", "来者何人")
        update.pop("slot")
        update["partition_key"] = "round_0001"
        plan = {
            "old_forming_disposition": "absent",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        errors, warnings = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)
        self.assertTrue(any("跨地点但被判断为连续" in warning for warning in warnings))

    def test_unique_invalid_event_update_slot_is_repaired_without_retry(self) -> None:
        rounds = [sample_round()]
        update = event_update("forming_tests", "r0001.assistant", "来者何人")
        plan = {
            "old_forming_disposition": "absent",
            "decision_reason": "首次记录。",
            "boundary_decisions": [],
            "internal_start_decisions": [],
            "segments": [
                {
                    "slot": "new_1",
                    "source_refs": ["r0001.user", "r0001.assistant"],
                    "start_anchors": [
                        {"source_ref": "r0001.user", "start_quote": "甲走进山门"}
                    ],
                }
            ],
            "event_updates": [update],
        }
        normalized = PROBE.normalize_event_plan(
            plan, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual("new_1", normalized["event_updates"][0]["slot"])
        self.assertEqual(
            [{"model_slot": "forming_tests", "script_slot": "new_1"}],
            normalized["script_slot_repairs"],
        )
        errors, warnings = PROBE.validate_event_plan(
            normalized, PROBE.initial_unified_state(), rounds
        )
        self.assertEqual([], errors)
        self.assertTrue(any("slot 笔误" in warning for warning in warnings))

    def test_event_prompt_renders_single_json_braces_and_short_description_target(self) -> None:
        self.assertIn('"event_updates": [{', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn('"start_quote":', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"boundary_decisions": [{', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"internal_start_decisions": [{', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"source_ref":', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"source_unit_refs": [', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn("event_beats", PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"partition_key":', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"segments": [{', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn("{{", PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn("一两句客观文字", PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn("六十至一百二十个汉字", PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn('"story_summary_add":', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"story_summary": "完整故事摘要"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn("忠实转述", PROBE.EVENT_SYSTEM_PROMPT)

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
        self.assertEqual(90.0, args.content_start_timeout)

    def test_transport_failure_preserves_partial_formal_content_and_metadata(self) -> None:
        failure = PROBE.ChatCompletionTransportError(
            "正式正文启动超时",
            partial_content='{"partial":',
            metadata={
                "reasoning_chars": 25000,
                "content_chars": 11,
                "finish_reason": None,
                "timing_seconds": {"total": 90.0},
            },
        )
        with patch.object(PROBE, "call_chat_completion", side_effect=failure):
            result = PROBE.run_model_task(
                task="event",
                endpoint="https://example.invalid/v1/chat/completions",
                api_key="test-only",
                model="test-model",
                system_prompt="system",
                user_prompt="user",
                timeout=300,
                max_tokens=32768,
                thinking_mode="off",
                response_format="text",
                stream_idle_timeout=90,
                content_start_timeout=90,
                candidate_attempt_limit=1,
                transport_attempt_limit=1,
                normalizer=lambda value: value,
                validator=lambda value: ([], []),
            )
        self.assertFalse(result["ok"])
        attempt = result["attempts"][0]
        self.assertEqual('{"partial":', attempt["raw"])
        self.assertEqual(25000, attempt["api"]["reasoning_chars"])

    def test_event_only_record_does_not_claim_entity_network_validation(self) -> None:
        record = PROBE.render_record(
            {
                "task_set": "event",
                "checkpoint_round_end": 4,
                "batches": [],
                "gold_evaluation": {},
                "final_state": PROBE.initial_unified_state(),
            }
        )
        self.assertIn("Event 边界与内容单路短回归", record)
        self.assertIn("最近已提交检查点：第 4 轮", record)
        self.assertNotIn("最终正式 Entity 网络", record)

    def test_off_mode_uses_zen_dual_disable_controls(self) -> None:
        body = PROBE.chat_completion_request_body(
            "deepseek-v4-flash-free",
            "system",
            "user",
            32768,
            thinking_mode="off",
            response_format="text",
        )

        self.assertEqual({"type": "disabled"}, body["thinking"])
        self.assertEqual("none", body["reasoning_effort"])
        self.assertEqual(0, body["temperature"])
        self.assertNotIn("reasoning", body)
        self.assertNotIn("think", body)
        self.assertNotIn("enable_thinking", body)

    def test_high_mode_uses_enabled_thinking_and_high_effort(self) -> None:
        body = PROBE.chat_completion_request_body(
            "deepseek-v4-flash-free",
            "system",
            "user",
            32768,
            thinking_mode="high",
            response_format="text",
        )

        self.assertEqual({"type": "enabled"}, body["thinking"])
        self.assertEqual("high", body["reasoning_effort"])
        self.assertNotIn("temperature", body)

    def test_gold_scope_must_match_requested_round_range(self) -> None:
        gold = {"source": {"round_end": 16}}
        PROBE.validate_gold_scope(gold, batch_size=4, batch_count=4)
        with self.assertRaisesRegex(ValueError, "样例到第 16 轮，测试到第 20 轮"):
            PROBE.validate_gold_scope(gold, batch_size=4, batch_count=5)

    def test_event_prompt_delegates_entity_references_to_script(self) -> None:
        self.assertNotIn('"participants_add"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"location_occurrences_add"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertNotIn('"related_entity_keys_add"', PROBE.EVENT_SYSTEM_PROMPT)
        self.assertIn("时间、地点、来源编号和候选边界不由你返回", PROBE.EVENT_SYSTEM_PROMPT)

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
        self.assertIn("character:送物弟子", event["related_entity_keys"])
        self.assertIn("item:身份玉牌", event["related_entity_keys"])
        self.assertEqual([], warnings)

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
            "boundary_decisions": [],
            "internal_start_decisions": [],
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
        applied, _, _ = PROBE.apply_event_candidate(state, normalized, rounds)
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
        state, _, _ = PROBE.apply_event_candidate(state, first, first_rounds)

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
            "boundary_decisions": [],
            "internal_start_decisions": [],
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
        state, _, _ = PROBE.apply_event_candidate(state, first, rounds)
        old_id = state["events"][0]["key_details"][0]["id"]

        second_rounds = [sample_round(2, "甲答道：‘山下散修。’", "乙点头放行。")]
        second_update = event_update(
            "forming_existing", "r0002.user", "山下散修"
        )
        second_update["story_summary"] = (
            "甲在山门回答自己是山下散修，守门的乙确认身份后点头放行。"
        )
        second_update["location_occurrences_add"] = []
        second = {
            "old_forming_disposition": "keep_distinct",
            "decision_reason": "身份确认仍是山门相遇的直接延续。",
            "boundary_decisions": [],
            "internal_start_decisions": [],
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
        state, _, _ = PROBE.apply_event_candidate(state, second, second_rounds)
        details = state["events"][0]["key_details"]
        self.assertEqual(2, len(details))
        self.assertEqual(old_id, details[0]["id"])
        self.assertIn("山下散修", state["events"][0]["story_summary"])

    def test_script_drops_copied_pending_event_and_appends_new_summary(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "status": "pending_finalization",
                "title": "驿站冲突",
                "description": "甲在驿站解决冲突并决定离开。",
                "story_summary": "甲在驿站解决冲突，随后随乙离开。",
                "participants": [],
                "locations": [],
                "unresolved": [],
                "source_refs": [],
                "source_slices": [],
                "key_details": [],
            },
            {
                "id": "event_probe_002",
                "status": "forming",
                "title": "高空问因",
                "description": "甲在高空询问乙出手的缘由。",
                "story_summary": "甲在高空向乙追问此前行动的缘由。",
                "participants": [],
                "locations": [],
                "unresolved": [],
                "source_refs": [],
                "source_slices": [],
                "key_details": [],
            },
        ]
        state["next_event_number"] = 3
        rounds = [sample_round(2, "甲继续追问。", "乙回答了新的缘由。")]
        copied_pending = partition_event_update(
            "existing_forming_tail", "r0002.assistant", "乙回答了新的缘由"
        )
        copied_pending["title"] = "驿站冲突"
        copied_pending["description"] = "甲在驿站解决冲突并决定离开。"
        copied_pending.pop("story_summary")
        copied_pending["story_summary_add"] = "甲在驿站解决冲突，随后随乙离开。"
        current = partition_event_update(
            "existing_forming_tail", "r0002.assistant", "乙回答了新的缘由"
        )
        current["title"] = "高空问因"
        current["description"] = "甲继续追问，乙进一步解释行动缘由。"
        current.pop("story_summary")
        current["story_summary_add"] = "甲继续追问，乙回答了新的缘由。"
        plan = {
            "old_forming_disposition": "keep_distinct",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [copied_pending, current],
        }

        normalized = PROBE.normalize_event_plan(plan, state, rounds)
        errors, warnings = PROBE.validate_event_plan(normalized, state, rounds)

        self.assertEqual([], errors)
        self.assertEqual(1, len(normalized["event_updates"]))
        self.assertEqual(
            "event_probe_001",
            normalized["script_stale_event_update_drops"][0]["copied_event_id"],
        )
        summary = normalized["event_updates"][0]["story_summary"]
        self.assertIn("此前行动的缘由", summary)
        self.assertIn("回答了新的缘由", summary)
        self.assertNotIn("驿站解决冲突", summary)
        self.assertTrue(any("误抄其他既有 Event" in item for item in warnings))

    def test_unchanged_summary_add_blocks_silent_batch_omission(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "status": "forming",
                "title": "高空问因",
                "description": "甲在高空询问乙出手的缘由。",
                "story_summary": "甲在高空向乙追问此前行动的缘由。",
                "participants": [],
                "locations": [],
                "unresolved": [],
                "source_refs": [],
                "source_slices": [],
                "key_details": [],
            }
        ]
        rounds = [sample_round(2, "甲继续追问。", "乙回答了新的缘由。")]
        update = partition_event_update(
            "existing_forming_tail", "r0002.assistant", "乙回答了新的缘由"
        )
        update["title"] = "高空问因"
        update["description"] = "甲在高空询问乙出手的缘由。"
        update.pop("story_summary")
        update["story_summary_add"] = "甲在高空向乙追问此前行动的缘由。"
        plan = {
            "old_forming_disposition": "keep_distinct",
            "boundary_decisions": [
                {
                    "candidate_id": "before_round_0002",
                    "decision": "continue",
                    "basis_code": "same_immediate_activity",
                }
            ],
            "internal_start_decisions": [],
            "event_updates": [update],
        }

        normalized = PROBE.normalize_event_plan(plan, state, rounds)
        errors, _ = PROBE.validate_event_plan(normalized, state, rounds)

        self.assertEqual(["forming_existing"], normalized["script_summary_add_noops"])
        self.assertTrue(any("没有包含新增内容" in error for error in errors))

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
        state["entity_candidates"]["location:山门"]["domain_data"] = {
            "profile": {
                "location_kind": "mountain_gate",
                "primary_functions": ["通行", "守卫"],
                "scale_description": "足以供多人并行通过。",
                "spatial_characteristics": ["石阶连接山道"],
            }
        }
        state["entity_candidates"]["item:旧剑"]["domain_data"] = {
            "profile": {
                "item_kind": "sword",
                "instance_mode": "individual",
                "primary_functions": ["近身格斗"],
                "materials": ["钢", "木"],
                "form_description": "一柄剑鞘磨损的旧剑。",
            },
            "placement": {
                "target_key": "character:甲",
                "role": "carried",
                "detail": "负在背后。",
            },
        }
        state["entity_candidates"]["organization:青山派"]["domain_data"] = {
            "profile": {
                "organization_kind": "martial_school",
                "public_role": "传授剑术。",
                "operating_scope": "以青山山门为中心。",
                "continuity_basis": "依靠师徒传承延续。",
            }
        }
        state["entity_candidates"]["skill:青山剑法"]["domain_data"] = {
            "definition": {
                "skill_kind": "sword_art",
                "domain": "剑术攻防。",
                "primary_capabilities": ["基础攻防"],
                "form_description": "以稳健基础剑招构成。",
            }
        }
        state["entity_candidates"]["concept:剑心"]["domain_data"] = {
            "definition": {
                "concept_kind": "doctrine_or_theory",
                "definition": "以心驭剑、临敌守静的一种剑学理论。",
                "epistemic_status": "attributed_theory",
                "operational_role": "interpretive_context",
                "scope_summary": "用于解释部分剑术修习者的理解路径。",
            }
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
        state["events"][0]["related_relation_pairs"] = [
            ["character:甲", "character:乙"]
        ]
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
        by_type = {entity["type"]: entity for entity in network}
        character_a = next(
            entity
            for entity in network
            if entity["type"] == "character"
            and entity["components"]["identity"]["data"]["primary_name"] == "甲"
        )
        self.assertIn("location_profile", by_type["location"]["components"])
        self.assertIn("item_profile", by_type["item"]["components"])
        self.assertIn("organization_profile", by_type["organization"]["components"])
        self.assertIn("skill_definition", by_type["skill"]["components"])
        self.assertIn("concept_definition", by_type["concept"]["components"])
        self.assertEqual(
            "character",
            by_type["item"]["components"]["current_placement_reference"]["data"][
                "placement_ref"
            ]["type"],
        )
        self.assertEqual(
            "负在背后。",
            by_type["item"]["components"]["current_placement_reference"]["data"][
                "placement_detail"
            ],
        )
        self.assertIn("inventory_index", character_a["components"])
        event = by_type["event"]
        related_refs = event["components"]["event_related_entity_reference"][
            "data"
        ]["related_entity_refs"]
        self.assertEqual(
            {"item", "organization", "skill", "concept", "character_relation"},
            {entry["type"] for entry in related_refs},
        )
        event_id = event["id"]
        for entity_type in (
            "item",
            "organization",
            "skill",
            "concept",
            "character_relation",
        ):
            with self.subTest(entity_type=entity_type):
                history_refs = by_type[entity_type]["components"]["history_index"][
                    "data"
                ]["event_refs"]
                self.assertIn(
                    event_id,
                    {entry["id"] for entry in history_refs},
                )

    def test_memory_owner_does_not_rewrite_event_entity_links(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "participants": [],
                "related_entity_keys": [],
            }
        ]
        state["memories"] = [
            {
                "id": "memory_probe_001",
                "owner_key": "character:甲",
                "acquisition_mode": "witnessed_event",
                "event_ids": ["event_probe_001"],
            }
        ]
        self.assertEqual([], state["events"][0]["related_entity_keys"])
        self.assertEqual([], state["events"][0]["participants"])

    def test_key_detail_actor_adds_generic_event_link(self) -> None:
        state = PROBE.initial_unified_state()
        state["events"] = [
            {
                "id": "event_probe_001",
                "participants": [],
                "related_entity_keys": [],
                "key_details": [{"actor": "character:乙", "content": "来者何人"}],
            }
        ]
        warnings = PROBE.reconcile_key_detail_entities(state)
        self.assertEqual(["character:乙"], state["events"][0]["related_entity_keys"])
        self.assertEqual([], state["events"][0]["participants"])
        self.assertEqual(1, len(warnings))


if __name__ == "__main__":
    unittest.main()
