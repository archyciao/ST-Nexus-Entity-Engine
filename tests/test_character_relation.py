"""Character Relation Schema 与跨组件规则的自动质检清单。

这些测试不运行世界模拟。它们通过修改一份完整关系样例，确认系统能够接受正确
结构，并拒绝端点错位、单方认知缺少 Memory 依据、重复维度和提前固定的数值字段。
"""

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

from world_simulator_schema.entity_validator import EntityValidator
from world_simulator_schema.validator import ComponentValidator


class CharacterRelationTests(unittest.TestCase):
    """验证 Character Relation 第一版已确认的不变量。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.component_validator = ComponentValidator(ROOT)
        cls.entity_validator = EntityValidator(ROOT)
        cls.example = json.loads(
            (ROOT / "examples/entities/character_relation_linghu_yuebuqun.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def load(relative_path: str) -> dict:
        """读取测试样例，避免测试代码重复文件路径与编码处理。"""

        return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))

    def test_full_character_relation_is_valid(self) -> None:
        """完整样例的 Component 与跨组件关系应全部通过。"""

        report = self.entity_validator.validate(copy.deepcopy(self.example))
        self.assertTrue(report.valid, report.errors)

    def test_memory_relation_context_reference_is_valid(self) -> None:
        """Memory 可以权威地指向 Relation 及具体 Aspect。"""

        report = self.component_validator.validate(
            "relation_context_reference",
            self.load("examples/valid/relation_context_reference.json"),
        )
        self.assertTrue(report.valid, report.errors)

    def test_relation_endpoints_must_use_canonical_order(self) -> None:
        """无序人物对必须按 ID 排序，才能由存储层可靠去重。"""

        entity = copy.deepcopy(self.example)
        refs = entity["components"]["relation_endpoint_reference"]["data"]["participant_refs"]
        refs.reverse()
        report = self.entity_validator.validate(entity)
        self.assertIn(
            "NON_CANONICAL_RELATION_ENDPOINT_ORDER",
            {item["code"] for item in report.errors},
        )

    def test_aspect_participants_must_match_relation_endpoints(self) -> None:
        """关系面向不能悄悄引入第三个人物。"""

        entity = copy.deepcopy(self.example)
        role = entity["components"]["character_relation_aspects"]["data"]["aspects"][0][
            "participant_roles"
        ][1]
        role["character_ref"]["id"] = "character_01K2ABCDEFGHJKMNPQRSTV0003"
        report = self.entity_validator.validate(entity)
        self.assertIn(
            "RELATION_ASPECT_PARTICIPANT_MISMATCH",
            {item["code"] for item in report.errors},
        )

    def test_character_view_aspect_requires_formation_memory(self) -> None:
        """单方认知型关系面向必须保存重要 Memory 形成依据。"""

        entity = copy.deepcopy(self.example)
        memory_entries = entity["components"]["relation_memory_index"]["data"]["memory_refs"]
        memory_entries[0]["index_roles"] = ["current_basis"]
        report = self.entity_validator.validate(entity)
        self.assertIn(
            "RELATION_ASPECT_WITHOUT_MEMORY_BASIS",
            {item["code"] for item in report.errors},
        )

    def test_shared_fact_aspect_does_not_require_memory_index(self) -> None:
        """共同事实型关系面向不因无人持有 Memory 而失效。"""

        entity = copy.deepcopy(self.example)
        aspects = entity["components"]["character_relation_aspects"]["data"]["aspects"]
        aspects[:] = [aspect for aspect in aspects if aspect["perspective"]["mode"] == "shared_fact"]
        entity["components"].pop("relation_memory_index")
        report = self.entity_validator.validate(entity)
        self.assertTrue(report.valid, report.errors)

    def test_character_view_owner_must_be_relation_endpoint(self) -> None:
        """单方认知只能归属于当前 Relation 的参与者。"""

        entity = copy.deepcopy(self.example)
        perspective = entity["components"]["character_relation_aspects"]["data"]["aspects"][1][
            "perspective"
        ]
        perspective["owner_ref"]["id"] = "character_01K2ABCDEFGHJKMNPQRSTV0003"
        report = self.entity_validator.validate(entity)
        self.assertIn(
            "RELATION_PERSPECTIVE_OWNER_MISMATCH",
            {item["code"] for item in report.errors},
        )

    def test_character_view_requires_owner(self) -> None:
        """单方认知必须明确是谁持有这项看法。"""

        entity = copy.deepcopy(self.example)
        perspective = entity["components"]["character_relation_aspects"]["data"]["aspects"][1][
            "perspective"
        ]
        perspective.pop("owner_ref")
        report = self.entity_validator.validate(entity)
        self.assertFalse(report.valid)

    def test_shared_fact_cannot_claim_perspective_owner(self) -> None:
        """共同事实不能同时伪装成某一个人的主观看法。"""

        entity = copy.deepcopy(self.example)
        perspective = entity["components"]["character_relation_aspects"]["data"]["aspects"][0][
            "perspective"
        ]
        perspective["owner_ref"] = {
            "id": "character_01K2ABCDEFGHJKMNPQRSTV0001",
            "type": "character",
        }
        report = self.entity_validator.validate(entity)
        self.assertFalse(report.valid)

    def test_same_memory_is_not_split_into_parallel_entries(self) -> None:
        """同一 Memory 的面向与用途必须集中在一项中。"""

        entity = copy.deepcopy(self.example)
        memory_entries = entity["components"]["relation_memory_index"]["data"]["memory_refs"]
        duplicate = copy.deepcopy(memory_entries[0])
        duplicate["index_roles"] = ["turning_point"]
        memory_entries.append(duplicate)
        report = self.entity_validator.validate(entity)
        self.assertIn(
            "DUPLICATE_RELATION_MEMORY",
            {item["code"] for item in report.errors},
        )

    def test_numeric_trust_cannot_replace_semantic_state(self) -> None:
        """第一版不允许用固定 trust 数值替代自然语义状态。"""

        report = self.component_validator.validate(
            "character_relation_state",
            self.load("examples/invalid/character_relation_state_with_numeric_trust.json"),
        )
        self.assertFalse(report.valid)
        self.assertIn("UNKNOWN_FIELD", {item["code"] for item in report.errors})

    def test_entity_management_rejects_single_creation_or_end_event(self) -> None:
        """Entity 的累积历史不能重新退化为单一创建或终止 Event 字段。"""

        for field_name in ("created_by_event_ref", "ended_by_event_ref"):
            with self.subTest(field_name=field_name):
                component = copy.deepcopy(self.example["components"]["entity_management"])
                component["data"][field_name] = {
                    "id": "event_01K2ABCDEFGHJKMNPQRSTV0011",
                    "type": "event",
                }
                report = self.component_validator.validate("entity_management", component)
                self.assertFalse(report.valid)
                self.assertIn("UNKNOWN_FIELD", {item["code"] for item in report.errors})


if __name__ == "__main__":
    unittest.main()
