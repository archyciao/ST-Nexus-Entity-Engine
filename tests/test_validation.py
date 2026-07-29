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
        ]
        for component, path in cases:
            with self.subTest(component=component):
                report = self.validator.validate(component, self.load(path))
                self.assertTrue(report.valid, report.errors)

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