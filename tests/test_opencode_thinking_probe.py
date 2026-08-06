from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
MODULE_PATH = TOOLS / "opencode_thinking_probe.py"
SPEC = importlib.util.spec_from_file_location("opencode_thinking_probe", MODULE_PATH)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class OpenCodeThinkingProbeTests(unittest.TestCase):
    def test_no_reasoning_and_formal_content_means_disabled_observed(self) -> None:
        result = PROBE.diagnostic_result(
            metadata={
                "reasoning_chars": 0,
                "content_chars": 2,
                "request_options": {
                    "wire_controls": {
                        "thinking": {"type": "disabled"},
                        "reasoning_effort": "none",
                        "temperature": 0,
                    }
                },
            },
            content="OK",
        )

        self.assertEqual("thinking_disabled_observed", result["verdict"])
        self.assertEqual(0, result["reasoning_chars"])
        self.assertEqual(
            "none",
            result["request_options"]["wire_controls"]["reasoning_effort"],
        )

    def test_any_reasoning_means_toggle_was_not_honored(self) -> None:
        result = PROBE.diagnostic_result(
            metadata={"reasoning_chars": 12, "content_chars": 2},
            content="OK",
        )

        self.assertEqual("thinking_disable_not_honored", result["verdict"])
        self.assertNotIn("reasoning_content", result)


class RuntimeBootstrapTests(unittest.TestCase):
    def test_generated_environment_is_ignored_and_launchers_are_portable(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".venv/", gitignore)
        for name in ("nexus.cmd", "nexus.ps1", "nexus.sh"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("C:\\Development", source)
            self.assertNotIn("C:\\Users", source)


if __name__ == "__main__":
    unittest.main()
