from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from world_simulator_schema.schema_store import SchemaStore


class DocumentationTemplateTests(unittest.TestCase):
    """验证中文说明模板清晰分级，并与运行时文件隔离。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.template_root = ROOT / "docs" / "templates"

    def test_jsonc_templates_use_three_level_comments(self) -> None:
        templates = sorted(self.template_root.glob("*_说明模板.jsonc"))
        self.assertGreaterEqual(len(templates), 3)
        required_markers = (
            "// 一、文件级总览",
            "// 二、结构级说明",
            "// 三、字段级说明",
        )
        for path in templates:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                for marker in required_markers:
                    self.assertIn(marker, source)
                self.assertNotIn("_说明", source)

    def test_jsonc_templates_cannot_be_mistaken_for_runtime_json(self) -> None:
        templates = sorted(self.template_root.glob("*_说明模板.jsonc"))
        for path in templates:
            with self.subTest(path=path.name):
                with self.assertRaises(json.JSONDecodeError):
                    json.loads(path.read_text(encoding="utf-8"))

    def test_python_template_compiles_without_execution(self) -> None:
        templates = sorted(self.template_root.glob("*_说明模板.py"))
        self.assertGreaterEqual(len(templates), 2)
        for path in templates:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                compile(source, str(path), "exec")

    def test_templates_are_outside_runtime_scan_paths(self) -> None:
        store = SchemaStore(ROOT)
        loaded_paths = {
            path.relative_to(ROOT).as_posix()
            for path in store.schema_files.values()
        }
        self.assertFalse(
            any(path.startswith("docs/templates/") for path in loaded_paths)
        )
        self.assertEqual(
            {
                "applicability",
                "character_behavior_profile",
                "character_objective",
                "character_profile",
                "character_state",
                "child_location_index",
                "child_organization_index",
                "concept_definition",
                "concept_rule",
                "container_profile",
                "containment_index",
                "contents_index",
                "current_location_reference",
                "current_placement_reference",
                "character_relation_aspects",
                "character_relation_state",
                "entity_management",
                "environment_profile",
                "event_location_reference",
                "event_memory_index",
                "event_participant_reference",
                "event_related_entity_reference",
                "event_time",
                "history_index",
                "identity",
                "inventory_index",
                "item_characteristic",
                "item_profile",
                "item_state",
                "location_atmosphere",
                "location_profile",
                "location_state",
                "memory_index",
                "memory_owner_reference",
                "memory_reference",
                "organization_culture",
                "organization_objective",
                "organization_profile",
                "organization_state",
                "organization_strategy",
                "organization_structure",
                "parent_location_reference",
                "parent_organization_reference",
                "related_concept_reference",
                "relation_context_reference",
                "relation_endpoint_reference",
                "relation_index",
                "relation_memory_index",
                "skill_characteristic",
                "skill_definition",
                "skill_mechanics",
                "skill_progression",
                "skill_reference",
                "skill_requirement",
                "source_event_reference",
                "stage_framework",
            },
            set(store.components),
        )


if __name__ == "__main__":
    unittest.main()
