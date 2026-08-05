"""Character、Event、Memory 与 Relation 实体网络的自动回归测试。"""

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

from world_simulator_schema.entity_network import (
    EntityNetworkValidator,
    rebuild_derived_indexes,
)


class EntityNetworkTests(unittest.TestCase):
    """验证权威 Reference 能由固定代码形成双向可查的实体网络。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = EntityNetworkValidator(ROOT)
        fixture = json.loads(
            (ROOT / "examples/networks/character_event_memory_relation.json").read_text(
                encoding="utf-8"
            )
        )
        cls.source_entities = fixture["entities"]

    @staticmethod
    def by_id(entities: list[dict]) -> dict[str, dict]:
        return {entity["id"]: entity for entity in entities}

    def test_fixed_projection_builds_complete_valid_network(self) -> None:
        """一次投影应补齐 Character、Location、Event 和 Relation 的反向目录。"""

        projected = rebuild_derived_indexes(self.source_entities)
        report = self.validator.validate(projected, require_derived_indexes=True)
        self.assertTrue(report.valid, report.errors)

        entities = self.by_id(projected)
        character = entities["character_01K2ABCDEFGHJKMNPQRSTV0001"]
        other_character = entities["character_01K2ABCDEFGHJKMNPQRSTV0002"]
        location = entities["location_01K2ABCDEFGHJKMNPQRSTV0008"]
        event = entities["event_01K2ABCDEFGHJKMNPQRSTV0011"]
        relation = entities["character_relation_01K2ABCDEFGHJKMNPQRSTV000H"]

        self.assertEqual(
            character["components"]["memory_index"]["data"]["memory_refs"],
            [{"id": "memory_01K2ABCDEFGHJKMNPQRSTV000S", "type": "memory"}],
        )
        self.assertEqual(
            other_character["components"]["memory_index"]["data"]["memory_refs"],
            [{"id": "memory_01K2ABCDEFGHJKMNPQRSTV000V", "type": "memory"}],
        )
        for participant in (character, other_character):
            self.assertEqual(
                {
                    entry["event_ref"]["id"]
                    for entry in participant["components"]["history_index"]["data"][
                        "event_refs"
                    ]
                },
                {"event_01K2ABCDEFGHJKMNPQRSTV0011"},
            )
        self.assertEqual(
            location["components"]["history_index"]["data"]["event_refs"],
            [
                {
                    "event_ref": {
                        "id": "event_01K2ABCDEFGHJKMNPQRSTV0011",
                        "type": "event",
                    },
                    "index_roles": ["recent"],
                }
            ],
        )
        self.assertEqual(
            character["components"]["relation_index"]["data"]["relation_refs"],
            [
                {
                    "id": "character_relation_01K2ABCDEFGHJKMNPQRSTV000H",
                    "type": "character_relation",
                }
            ],
        )
        self.assertEqual(len(event["components"]["event_memory_index"]["data"]["memory_refs"]), 2)
        self.assertEqual(
            relation["components"]["relation_memory_index"]["data"]["memory_refs"][0][
                "memory_ref"
            ]["id"],
            "memory_01K2ABCDEFGHJKMNPQRSTV000V",
        )

    def test_missing_reference_target_is_rejected(self) -> None:
        """封闭提交中缺少目标 Entity 时不能留下悬空链接。"""

        projected = rebuild_derived_indexes(self.source_entities)
        projected = [
            entity
            for entity in projected
            if entity["id"] != "memory_01K2ABCDEFGHJKMNPQRSTV000S"
        ]
        report = self.validator.validate(projected)
        self.assertIn("MISSING_REFERENCE_TARGET", {item["code"] for item in report.errors})

    def test_character_cannot_pin_another_characters_memory(self) -> None:
        """固定 Memory 是直接链接，但目标 Memory 的 Owner 必须是当前 Character。"""

        entities = copy.deepcopy(self.source_entities)
        character = entities[0]
        character["components"]["memory_reference"]["data"]["memory_refs"][0][
            "memory_ref"
        ]["id"] = "memory_01K2ABCDEFGHJKMNPQRSTV000V"
        report = self.validator.validate(rebuild_derived_indexes(entities))
        self.assertIn(
            "MEMORY_REFERENCE_OWNER_MISMATCH",
            {item["code"] for item in report.errors},
        )

    def test_stale_derived_index_is_rejected(self) -> None:
        """反向目录与权威引用不一致时必须重建，不能两边各自维护。"""

        projected = rebuild_derived_indexes(self.source_entities)
        character = projected[0]
        character["components"]["memory_index"]["data"]["memory_refs"] = []
        report = self.validator.validate(projected, require_derived_indexes=True)
        self.assertIn("STALE_DERIVED_INDEX", {item["code"] for item in report.errors})

    def test_relation_formation_memory_owner_must_match_view_owner(self) -> None:
        """跨 Entity 校验应补上旧单 Entity 校验器无法读取 Memory Owner 的规则。"""

        entities = copy.deepcopy(self.source_entities)
        memory = next(
            entity
            for entity in entities
            if entity["id"] == "memory_01K2ABCDEFGHJKMNPQRSTV000S"
        )
        memory["components"]["relation_context_reference"] = {
            "schema_version": "0.1.0",
            "data": {
                "relation_links": [
                    {
                        "relation_ref": {
                            "id": "character_relation_01K2ABCDEFGHJKMNPQRSTV000H",
                            "type": "character_relation",
                        },
                        "related_aspect_ids": [
                            "relation_aspect_01K2ABCDEFGHJKMNPQRSTV0201"
                        ],
                        "context_roles": ["formation_basis"],
                    }
                ]
            },
        }
        relation = next(
            entity
            for entity in entities
            if entity["id"] == "character_relation_01K2ABCDEFGHJKMNPQRSTV000H"
        )
        relation["components"]["character_relation_aspects"]["data"]["aspects"][0][
            "perspective"
        ] = {
            "mode": "character_view",
            "owner_ref": {
                "id": "character_01K2ABCDEFGHJKMNPQRSTV0002",
                "type": "character",
            },
        }

        report = self.validator.validate(
            rebuild_derived_indexes(entities), require_derived_indexes=True
        )
        self.assertIn(
            "RELATION_MEMORY_OWNER_MISMATCH",
            {item["code"] for item in report.errors},
        )

    def test_event_location_sequence_must_be_unique(self) -> None:
        """固定脚本不能给两次地点经过写入同一个顺序。"""

        entities = copy.deepcopy(self.source_entities)
        event = next(entity for entity in entities if entity["type"] == "event")
        event["components"]["event_location_reference"]["data"][
            "location_refs"
        ].append(
            {
                "location_ref": {
                    "id": "location_01K2ABCDEFGHJKMNPQRSTV0008",
                    "type": "location",
                },
                "location_roles": ["end"],
                "sequence": 0,
            }
        )

        report = self.validator.validate(rebuild_derived_indexes(entities))
        self.assertIn(
            "DUPLICATE_EVENT_LOCATION_SEQUENCE",
            {item["code"] for item in report.errors},
        )

    def test_event_may_return_to_the_same_location(self) -> None:
        """离开后返回原处是合法路线，不能误判为地点重复。"""

        entities = copy.deepcopy(self.source_entities)
        event = next(entity for entity in entities if entity["type"] == "event")
        event["components"]["event_location_reference"]["data"][
            "location_refs"
        ].append(
            {
                "location_ref": {
                    "id": "location_01K2ABCDEFGHJKMNPQRSTV0008",
                    "type": "location",
                },
                "location_roles": ["end"],
                "sequence": 1,
            }
        )

        report = self.validator.validate(
            rebuild_derived_indexes(entities), require_derived_indexes=True
        )
        self.assertTrue(report.valid, report.errors)

    def test_event_related_entity_cannot_be_listed_twice(self) -> None:
        """同一相关对象的多种作用必须合并，不能制造重复引用。"""

        entities = copy.deepcopy(self.source_entities)
        item = json.loads(
            (ROOT / "examples/entities/item_hidden_weapon_pouch.json").read_text(
                encoding="utf-8"
            )
        )
        entities.append(item)
        event = next(entity for entity in entities if entity["type"] == "event")
        related_ref = {"id": item["id"], "type": "item"}
        event["components"]["event_related_entity_reference"] = {
            "schema_version": "0.1.0",
            "data": {
                "related_entity_refs": [
                    {
                        "related_entity_ref": copy.deepcopy(related_ref),
                        "involvement_roles": ["used"],
                    },
                    {
                        "related_entity_ref": copy.deepcopy(related_ref),
                        "involvement_roles": ["damaged"],
                    },
                ]
            },
        }

        report = self.validator.validate(rebuild_derived_indexes(entities))
        self.assertIn(
            "DUPLICATE_EVENT_RELATED_ENTITY",
            {entry["code"] for entry in report.errors},
        )

    def test_witnessed_memory_owner_must_be_an_event_participant(self) -> None:
        """亲历者不能拥有来源 Event 的 Memory，却从该 Event 参与目录中消失。"""

        entities = copy.deepcopy(self.source_entities)
        event = next(entity for entity in entities if entity["type"] == "event")
        entries = event["components"]["event_participant_reference"]["data"][
            "participant_refs"
        ]
        entries[:] = [
            entry
            for entry in entries
            if entry["participant_ref"]["id"]
            != "character_01K2ABCDEFGHJKMNPQRSTV0001"
        ]
        report = self.validator.validate(rebuild_derived_indexes(entities))
        self.assertIn(
            "WITNESSED_MEMORY_OWNER_NOT_EVENT_PARTICIPANT",
            {item["code"] for item in report.errors},
        )


if __name__ == "__main__":
    unittest.main()
