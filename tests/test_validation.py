from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from world_simulator_schema.validator import ComponentValidator
from world_simulator_schema.entity_ids import is_valid_entity_id


class ComponentSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = ComponentValidator(ROOT)

    @staticmethod
    def load(relative_path: str) -> dict:
        return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))

    def test_registry_is_consistent(self) -> None:
        self.assertEqual([], self.validator.check_registry())

    def test_valid_examples(self) -> None:
        cases = [
            ("identity", "examples/valid/identity.json"),
            (
                "current_location_reference",
                "examples/valid/current_location_reference.json",
            ),
            ("history_index", "examples/valid/history_index.json"),
            ("event_time", "examples/valid/event_time.json"),
            (
                "event_location_reference",
                "examples/valid/event_location_reference.json",
            ),
            (
                "event_related_entity_reference",
                "examples/valid/event_related_entity_reference.json",
            ),
        ]
        for component, path in cases:
            with self.subTest(component=component):
                report = self.validator.validate(component, self.load(path))
                self.assertTrue(report.valid, report.errors)

    def test_full_character_example_components_are_valid(self) -> None:
        entity = self.load("examples/entities/character_linghuchong.json")
        self.assertEqual("character", entity["type"])
        self.assertTrue(is_valid_entity_id(entity["id"], entity["type"]))
        self.assertGreaterEqual(
            len(entity["components"]["character_behavior_profile"]["data"]["motivations"]),
            2,
        )
        self.assertGreaterEqual(
            len(entity["components"]["character_objective"]["data"]["objectives"]),
            2,
        )
        for component, instance in entity["components"].items():
            with self.subTest(component=component):
                report = self.validator.validate(component, instance)
                self.assertTrue(report.valid, report.errors)

    def test_repairable_character_behavior_profile_is_corrected(self) -> None:
        report = self.validator.validate(
            "character_behavior_profile",
            self.load("examples/repairable/character_behavior_profile.json"),
            repair=True,
        )
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(
            "朋友受威胁时倾向直接介入。",
            report.instance["data"]["personality"]["behavioral_tendencies"][0],
        )
        self.assertEqual(
            "motivation_01K2ABCDEFGHJKMNPQRSTV0101",
            report.instance["data"]["motivations"][0]["motivation_id"],
        )

    def test_character_objective_rejects_process_steps(self) -> None:
        report = self.validator.validate(
            "character_objective",
            self.load("examples/invalid/character_objective_with_steps.json"),
        )
        self.assertFalse(report.valid)
        self.assertIn("UNKNOWN_FIELD", {item["code"] for item in report.errors})

    def test_motivation_semantics_cannot_be_replaced_by_number(self) -> None:
        report = self.validator.validate(
            "character_behavior_profile",
            self.load("examples/invalid/character_behavior_profile_numeric_motivation.json"),
        )
        self.assertFalse(report.valid)
        self.assertIn("INVALID_TYPE", {item["code"] for item in report.errors})

    def test_memory_index_cannot_copy_owner(self) -> None:
        report = self.validator.validate(
            "memory_index",
            self.load("examples/invalid/memory_index_with_owner.json"),
        )
        self.assertFalse(report.valid)
        self.assertIn("UNKNOWN_FIELD", {item["code"] for item in report.errors})

    def test_repairable_identity_is_corrected_and_valid(self) -> None:
        report = self.validator.validate(
            "identity",
            self.load("examples/repairable/identity.json"),
            repair=True,
        )
        self.assertTrue(report.valid, report.errors)
        self.assertEqual("0.1.0", report.instance["schema_version"])
        self.assertEqual("令狐冲", report.instance["data"]["primary_name"])
        self.assertEqual(["大师兄"], report.instance["data"]["aliases"])
        methods = {item["method"] for item in report.corrections}
        self.assertIn("normalized", methods)
        self.assertIn("alias", methods)

    def test_unknown_field_is_rejected(self) -> None:
        report = self.validator.validate(
            "identity",
            self.load("examples/invalid/identity_unknown_field.json"),
            repair=True,
        )
        self.assertFalse(report.valid)
        self.assertIn("UNKNOWN_FIELD", {item["code"] for item in report.errors})

    def test_reference_values_are_not_fuzzy_corrected(self) -> None:
        report = self.validator.validate(
            "current_location_reference",
            self.load("examples/invalid/current_location_reference_bad_id.json"),
            repair=True,
        )
        self.assertFalse(report.valid)
        self.assertEqual(
            "Location Hua Shan",
            report.instance["data"]["location_ref"]["id"],
        )
        self.assertEqual(
            "locations",
            report.instance["data"]["location_ref"]["type"],
        )

    def test_behavior_local_ids_must_be_unique(self) -> None:
        instance = self.load("examples/entities/character_linghuchong.json")[
            "components"
        ]["character_behavior_profile"]
        instance["data"]["motivations"][1]["motivation_id"] = (
            instance["data"]["motivations"][0]["motivation_id"]
        )

        report = self.validator.validate("character_behavior_profile", instance)

        self.assertFalse(report.valid)
        self.assertIn("DUPLICATE_ITEM_ID", {item["code"] for item in report.errors})

    def test_objective_ids_must_be_unique(self) -> None:
        instance = self.load("examples/entities/character_linghuchong.json")[
            "components"
        ]["character_objective"]
        instance["data"]["objectives"][1]["objective_id"] = (
            instance["data"]["objectives"][0]["objective_id"]
        )

        report = self.validator.validate("character_objective", instance)

        self.assertFalse(report.valid)
        self.assertIn("DUPLICATE_ITEM_ID", {item["code"] for item in report.errors})

    def test_index_roles_must_be_unique(self) -> None:
        instance = self.load("examples/valid/history_index.json")
        instance["data"]["event_refs"][0]["index_roles"] = ["recent", "recent"]
        report = self.validator.validate("history_index", instance)
        self.assertFalse(report.valid)
        self.assertIn("INVALID_VALUE", {item["code"] for item in report.errors})
    def test_unique_high_confidence_typo_is_fuzzy_corrected(self) -> None:
        report = self.validator.validate(
            "identity",
            self.load("examples/repairable/identity_fuzzy.json"),
            repair=True,
        )
        self.assertTrue(report.valid, report.errors)
        self.assertEqual("令狐冲", report.instance["data"]["primary_name"])
        self.assertIn(
            "fuzzy",
            {item["method"] for item in report.corrections},
        )

    def test_ambiguous_field_is_not_guessed(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "source_name": {"type": "string"},
                "source_names": {"type": "string"},
            },
            "additionalProperties": False,
        }
        component = {
            "correction": {
                "key_aliases": [],
                "value_aliases": [],
            }
        }
        instance = {"source_nam": "test"}
        corrected, corrections, issues = self.validator.corrector.correct(
            instance,
            schema,
            component,
        )
        self.assertEqual([], corrections)
        self.assertEqual({"source_nam": "test"}, corrected)
        self.assertEqual("AMBIGUOUS_FIELD", issues[0].code)

    def test_correction_collision_is_rejected(self) -> None:
        instance = self.load("examples/valid/identity.json")
        instance["data"]["primaryName"] = "冲突值"
        report = self.validator.validate("identity", instance, repair=True)
        self.assertFalse(report.valid)
        self.assertIn(
            "CORRECTION_COLLISION",
            {item["code"] for item in report.errors},
        )

    def test_unsupported_version_is_rejected(self) -> None:
        instance = self.load("examples/valid/identity.json")
        instance["schema_version"] = "9.9.9"
        report = self.validator.validate("identity", instance)
        self.assertFalse(report.valid)
        self.assertEqual("UNSUPPORTED_VERSION", report.errors[0]["code"])

    def test_permissions_apply_default_and_field_overrides(self) -> None:
        self.assertIsNone(
            self.validator.check_permission(
                "identity",
                role="ai",
                operation="propose",
                path="/data/primary_name",
            )
        )
        denied_write = self.validator.check_permission(
            "identity",
            role="ai",
            operation="write",
            path="/data/primary_name",
        )
        self.assertEqual("PERMISSION_DENIED", denied_write["code"])
        self.assertIsNone(
            self.validator.check_permission(
                "identity",
                role="system",
                operation="write",
                path="/data/primary_name",
            )
        )
        denied_type_change = self.validator.check_permission(
            "current_location_reference",
            role="ai",
            operation="propose",
            path="/data/location_ref/type",
        )
        self.assertEqual("PERMISSION_DENIED", denied_type_change["code"])

    def test_character_permissions_keep_ai_in_proposal_role(self) -> None:
        self.assertIsNone(
            self.validator.check_permission(
                "character_behavior_profile",
                role="ai",
                operation="propose",
                path="/data/motivations/0/description",
            )
        )
        denied_write = self.validator.check_permission(
            "character_behavior_profile",
            role="ai",
            operation="write",
            path="/data/motivations/0/description",
        )
        self.assertEqual("PERMISSION_DENIED", denied_write["code"])
        self.assertIsNone(
            self.validator.check_permission(
                "character_objective",
                role="projector",
                operation="write",
                path="/data/objectives/0/description",
            )
        )
        denied_index_proposal = self.validator.check_permission(
            "relation_index",
            role="ai",
            operation="propose",
            path="/data/relation_refs",
        )
        self.assertEqual("PERMISSION_DENIED", denied_index_proposal["code"])

    def test_ai_cannot_propose_system_generated_local_ids(self) -> None:
        cases = [
            (
                "character_behavior_profile",
                "/data/motivations/0/motivation_id",
            ),
            (
                "character_behavior_profile",
                "/data/preferences/0/preference_id",
            ),
            (
                "character_objective",
                "/data/objectives/0/objective_id",
            ),
        ]
        for component, path in cases:
            with self.subTest(component=component, path=path):
                denied = self.validator.check_permission(
                    component,
                    role="ai",
                    operation="propose",
                    path=path,
                )
                self.assertEqual("PERMISSION_DENIED", denied["code"])

    def test_event_location_identity_and_sequence_are_system_controlled(self) -> None:
        """AI 可提出地点作用，但不能编写正式 Location ID 或路线顺序。"""

        for path in (
            "/data/location_refs/0/location_ref/id",
            "/data/location_refs/0/location_ref/type",
            "/data/location_refs/0/sequence",
        ):
            with self.subTest(path=path):
                denied = self.validator.check_permission(
                    "event_location_reference",
                    role="ai",
                    operation="propose",
                    path=path,
                )
                self.assertEqual("PERMISSION_DENIED", denied["code"])
        self.assertIsNone(
            self.validator.check_permission(
                "event_location_reference",
                role="ai",
                operation="propose",
                path="/data/location_refs/0/location_roles",
            )
        )

    def test_event_related_entity_type_is_system_controlled(self) -> None:
        """AI 可提出对象作用，但正式 Entity Type 由系统写入。"""

        denied = self.validator.check_permission(
            "event_related_entity_reference",
            role="ai",
            operation="propose",
            path="/data/related_entity_refs/0/related_entity_ref/type",
        )
        self.assertEqual("PERMISSION_DENIED", denied["code"])
        self.assertIsNone(
            self.validator.check_permission(
                "event_related_entity_reference",
                role="ai",
                operation="propose",
                path="/data/related_entity_refs/0/involvement_roles",
            )
        )

    def test_reference_id_prefix_must_match_reference_type(self) -> None:
        instance = self.load("examples/valid/current_location_reference.json")
        instance["data"]["location_ref"]["id"] = (
            "character_01K2ABCDEFGHJKMNPQRSTV0001"
        )

        report = self.validator.validate("current_location_reference", instance)

        self.assertFalse(report.valid)
        self.assertIn("INVALID_REFERENCE_ID", {item["code"] for item in report.errors})

    def test_entity_management_audit_fields_are_system_controlled(self) -> None:
        denied = self.validator.check_permission(
            "entity_management",
            role="ai",
            operation="propose",
            path="/data/revision",
        )
        self.assertEqual("PERMISSION_DENIED", denied["code"])
        self.assertIsNone(
            self.validator.check_permission(
                "entity_management",
                role="resolver",
                operation="propose",
                path="/data/lifecycle_status",
            )
        )

    def test_value_alias_cannot_rewrite_free_text(self) -> None:
        component = self.validator.store.component("identity")
        value_aliases = component["correction"]["value_aliases"]
        value_aliases.append(
            {
                "path": "/data/primary_name",
                "alias": "LHC",
                "canonical": "令狐冲",
            }
        )
        try:
            codes = {
                item["code"] for item in self.validator.check_registry()
            }
            self.assertIn("REGISTRY_VALUE_ALIAS_NON_ENUM", codes)
        finally:
            value_aliases.pop()

    def test_value_alias_cannot_rewrite_protected_reference_type(self) -> None:
        component = self.validator.store.component(
            "current_location_reference"
        )
        value_aliases = component["correction"]["value_aliases"]
        value_aliases.append(
            {
                "path": "/data/location_ref/type",
                "alias": "locations",
                "canonical": "location",
            }
        )
        try:
            codes = {
                item["code"] for item in self.validator.check_registry()
            }
            self.assertIn("REGISTRY_VALUE_ALIAS_PROTECTED", codes)
        finally:
            value_aliases.pop()


if __name__ == "__main__":
    unittest.main()
