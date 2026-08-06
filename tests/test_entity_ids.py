from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from world_simulator_schema.entity_ids import (
    is_valid_entity_id,
    is_valid_local_id,
    new_entity_id,
    new_local_id,
)


class EntityIDTests(unittest.TestCase):
    """验证正式 ID 由系统生成，并严格遵守 type_series。"""

    def test_system_generates_type_series_id(self) -> None:
        entity_id = new_entity_id("character")

        self.assertTrue(is_valid_entity_id(entity_id, "character"))
        self.assertFalse(is_valid_entity_id(entity_id, "item"))

    def test_name_based_and_mismatched_ids_are_rejected(self) -> None:
        self.assertFalse(is_valid_entity_id("character_linghuchong", "character"))
        self.assertFalse(
            is_valid_entity_id(
                "character_01K2ABCDEFGHJKMNPQRSTV0001",
                "item",
            )
        )

    def test_invalid_entity_type_is_rejected_before_generation(self) -> None:
        with self.assertRaises(ValueError):
            new_entity_id("Character")

    def test_system_generates_registered_local_ids(self) -> None:
        for kind in (
            "motivation",
            "preference",
            "objective",
            "relation_aspect",
            "location_state",
            "item_state",
            "organization_role",
            "organization_direction",
            "organization_state",
            "skill_mechanic",
            "skill_requirement",
            "skill_numeric_binding",
            "skill_stage",
            "skill_stage_context",
            "concept_rule",
            "stage_numeric_binding",
            "concept_stage",
            "concept_stage_context",
            "event_detail",
        ):
            with self.subTest(kind=kind):
                local_id = new_local_id(kind)
                self.assertTrue(is_valid_local_id(local_id, kind))

    def test_process_is_not_a_component_local_id(self) -> None:
        with self.assertRaises(ValueError):
            new_local_id("process")
        self.assertFalse(
            is_valid_local_id(
                "process_01K2ABCDEFGHJKMNPQRSTV0107",
                "process",
            )
        )


if __name__ == "__main__":
    unittest.main()
