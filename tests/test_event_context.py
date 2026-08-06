from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from world_simulator_schema.event_context import build_event_recall_pack


class EventContextTests(unittest.TestCase):
    def event(self, number: int) -> dict:
        return {
            "id": f"event_{number}",
            "status": "finalized",
            "title": f"事件{number}",
            "description": f"事件{number}的检索说明。",
            "story_summary": f"事件{number}的完整故事摘要。",
            "participants": ["character:甲"],
            "source_refs": [f"r{number:04d}.assistant"],
            "source_slices": [{"secret_original": "不得进入常规召回"}],
            "key_details": [
                {
                    "id": f"detail_{number}",
                    "kind": "statement",
                    "content": f"事件{number}的关键原话。",
                    "actor": "character:甲",
                    "source_refs": [f"r{number:04d}.assistant"],
                    "source_unit_refs": [f"r{number:04d}.assistant.u001"],
                }
            ],
        }

    def test_normal_recall_never_exposes_source_records_or_source_refs(self) -> None:
        pack = build_event_recall_pack([self.event(1)])
        rendered = str(pack)
        self.assertNotIn("source_refs", rendered)
        self.assertNotIn("source_slices", rendered)
        self.assertNotIn("secret_original", rendered)
        self.assertIn("事件1的关键原话", rendered)

    def test_formal_event_content_is_read_without_runtime_records(self) -> None:
        formal_event = {
            "id": "event_01JQ7Z0NQH6KV8T6B9X7R2M4CP",
            "type": "event",
            "description": "门前说明来意并等待核验。",
            "components": {
                "event_content": {
                    "schema_version": "0.1.0",
                    "data": {
                        "status": "forming",
                        "story_summary": "甲说明来意，乙开始核验信物。",
                        "key_details": [
                            {
                                "detail_id": "event_detail_01JQ7Z0NQH6KV8T6B9X7R2M4CP",
                                "kind": "statement",
                                "content": "请代我通报一声。",
                                "fidelity": "verbatim",
                            }
                        ],
                    },
                },
                "event_related_entity_reference": {
                    "schema_version": "0.1.0",
                    "data": {
                        "related_entity_refs": [
                            {
                                "id": "character_01JQ7Z0NQH6KV8T6B9X7R2M4CP",
                                "type": "character",
                            }
                        ]
                    },
                },
            },
        }
        pack = build_event_recall_pack([formal_event])
        card = pack["events"][0]
        self.assertEqual("forming", card["status"])
        self.assertIn("甲说明来意", card["story_summary"])
        self.assertEqual(
            "请代我通报一声。", card["key_details"][0]["content"]
        )
        self.assertIn("related_entity_refs", card)

    def test_recall_uses_global_event_and_key_detail_caps(self) -> None:
        pack = build_event_recall_pack(
            [self.event(number) for number in range(1, 7)],
            max_events=3,
            max_key_details=2,
            max_chars=2000,
        )
        self.assertEqual(3, len(pack["events"]))
        self.assertEqual(
            2,
            sum(len(event.get("key_details", [])) for event in pack["events"]),
        )

    def test_small_budget_omits_summary_before_cutting_text(self) -> None:
        event = self.event(1)
        event["story_summary"] = "完整句子。" * 200
        pack = build_event_recall_pack([event], max_chars=400)
        self.assertEqual(["event_1"], pack["omitted"]["summary_event_ids"])
        self.assertNotIn("story_summary", pack["events"][0])

    def test_max_chars_covers_the_complete_recall_object(self) -> None:
        event = self.event(1)
        event["description"] = "用于检索的完整说明。" * 20
        event["story_summary"] = "用于回忆的完整摘要。" * 40
        pack = build_event_recall_pack([event], max_chars=400)
        rendered = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(rendered), 400)
        self.assertEqual(len(rendered), pack["budget"]["used_chars"])


if __name__ == "__main__":
    unittest.main()
