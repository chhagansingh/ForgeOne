"""Real OpenHands request-capture tests.

These exercise the SDK's *actual* serialization path (``to_openai_tool()``) and
the *real* tokenizer. Both live in separate venvs, so each group skips cleanly
when its dependency is absent — the suite still runs everywhere.

**No model weights are loaded. No inference. No network.**

To exercise both groups in one run, use the OpenHands venv and add the model
venv's site-packages, or run the capture scripts and assert on the artifact
(the ``CapturedArtifactTests`` group, which needs neither import).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PAYLOAD = REPO / "storage/runs" / "openhands-payload.json"
CAPTURE = REPO / "storage/runs" / "openhands-request-capture.json"

try:
    from openhands.tools.file_editor.definition import (
        TOOL_DESCRIPTION as FE_DESC, FileEditorAction, FileEditorTool)
    from openhands.tools.terminal.definition import TerminalAction, TerminalTool
    from openhands.tools.terminal.descriptions import UNIX_TOOL_DESCRIPTION

    OPENHANDS_AVAILABLE = True
    OPENHANDS_ERROR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    OPENHANDS_AVAILABLE = False
    OPENHANDS_ERROR = f"{type(exc).__name__}: {exc}"


@unittest.skipUnless(OPENHANDS_AVAILABLE, f"OpenHands SDK unavailable ({OPENHANDS_ERROR})")
class RealToolSchemaTests(unittest.TestCase):
    """Cases 1-2: real SDK request capture and actual tool-schema inclusion."""

    def test_tool_schema_is_real_and_non_empty(self):
        td = TerminalTool(description=UNIX_TOOL_DESCRIPTION, action_type=TerminalAction)
        schema = td.to_openai_tool()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "terminal")
        self.assertTrue(schema["function"]["description"])
        params = schema["function"]["parameters"]
        self.assertIn("properties", params)
        self.assertGreater(len(params["properties"]), 0)

    def test_file_editor_schema_is_real(self):
        td = FileEditorTool(description=FE_DESC, action_type=FileEditorAction)
        schema = td.to_openai_tool()
        self.assertEqual(schema["function"]["name"], "file_editor")
        self.assertGreater(len(schema["function"]["parameters"]["properties"]), 0)

    def test_schemas_serialize_to_json(self):
        td = TerminalTool(description=UNIX_TOOL_DESCRIPTION, action_type=TerminalAction)
        blob = json.dumps(td.to_openai_tool(), sort_keys=True)
        self.assertGreater(len(blob), 1000)
        self.assertIn("terminal", blob)


@unittest.skipUnless(PAYLOAD.is_file(), f"capture payload missing ({PAYLOAD})")
class CapturedPayloadTests(unittest.TestCase):
    """Cases 1-2 against the persisted capture — needs neither import."""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))

    def test_tools_are_actually_present_in_the_request(self):
        """The prior measurement wrongly treated missing schemas as zero cost."""
        for key in ("stock", "compact"):
            with self.subTest(profile=key):
                tools = self.payload[key]["tools"]
                self.assertTrue(tools, f"{key} has no tool schemas")
                names = [t["function"]["name"] for t in tools]
                self.assertEqual(names, self.payload[key]["tool_names"])
                for t in tools:
                    self.assertIn("parameters", t["function"])

    def test_stock_has_more_tools_than_compact(self):
        self.assertGreater(
            len(self.payload["stock"]["tools"]), len(self.payload["compact"]["tools"])
        )

    def test_compact_prompt_is_shorter_than_stock(self):
        self.assertLess(
            len(self.payload["compact"]["system_prompt"]),
            len(self.payload["stock"]["system_prompt"]),
        )

    def test_compact_prompt_is_the_profile_prompt_not_the_stock_render(self):
        """Guards the bug where the registry silently rendered the stock prompt."""
        from services.agent_profiles import FORGEONE_COMPACT_V1

        self.assertEqual(
            self.payload["compact"]["system_prompt"],
            FORGEONE_COMPACT_V1.system_prompt,
        )
        self.assertNotEqual(
            self.payload["compact"]["system_prompt"],
            self.payload["stock"]["system_prompt"],
        )

    def test_workflow_covers_the_required_steps(self):
        wf = self.payload["workflow"]
        for step in ("A_initial_coding", "B_file_read", "C_test_command",
                     "D_failing_output", "E_tool_result", "F_final_answer"):
            with self.subTest(step=step):
                self.assertIn(step, wf)
                self.assertTrue(wf[step])

    def test_failing_output_is_the_expected_baseline(self):
        """Case 8: failed-test output accounting uses the real baseline text."""
        self.assertIn("FAILED (failures=2)", self.payload["failing_output"])
        self.assertIn("Ran 6 tests", self.payload["failing_output"])

    def test_no_weights_or_inference_claimed(self):
        self.assertFalse(self.payload["model_weights_loaded"])
        self.assertFalse(self.payload["inference_run"])


@unittest.skipUnless(CAPTURE.is_file(), f"capture artifact missing ({CAPTURE})")
class CapturedMeasurementTests(unittest.TestCase):
    """Case 3: real-tokenizer agreement, asserted on the measured artifact."""

    @classmethod
    def setUpClass(cls):
        cls.capture = json.loads(CAPTURE.read_text(encoding="utf-8"))

    def test_measurements_are_positive_and_ordered(self):
        for key in ("stock", "compact", "compact_terminal_only"):
            with self.subTest(profile=key):
                m = self.capture[key]
                self.assertGreater(m["system_prompt_tokens"], 0)
                self.assertGreater(m["tool_schema_tokens"], 0)
                self.assertGreaterEqual(m["total_input_tokens"], m["system_plus_task_tokens"])

    def test_tool_schemas_make_a_material_contribution(self):
        """Guards against treating tool cost as zero."""
        self.assertGreater(self.capture["stock"]["tool_schema_tokens"], 500)
        self.assertGreater(self.capture["compact"]["tool_schema_tokens"], 500)

    def test_compact_is_cheaper_than_stock(self):
        self.assertLess(
            self.capture["compact"]["total_input_tokens"],
            self.capture["stock"]["total_input_tokens"],
        )

    def test_terminal_only_is_cheaper_still(self):
        self.assertLess(
            self.capture["compact_terminal_only"]["total_input_tokens"],
            self.capture["compact"]["total_input_tokens"],
        )

    def test_every_workflow_step_records_a_verdict(self):
        wf = self.capture["COMPACT_WORKFLOW"]
        self.assertEqual(len(wf), 6)
        for step, v in wf.items():
            with self.subTest(step=step):
                self.assertGreater(v["input_tokens"], 0)
                self.assertIn(v["verdict"], ("FITS", "BLOCKED_CONTEXT"))

    def test_minimum_required_context_includes_output_reservation(self):
        """Case 6: complete input + output reservation."""
        m = self.capture["MINIMUM_REQUIRED_CONTEXT"]
        self.assertEqual(
            m["plus_reserved_output"],
            m["max_workflow_input_tokens"] + self.capture["budget"]["reserved_output"],
        )
        self.assertIn("NOT a verified", m["note"])

    def test_no_inference_was_run(self):
        self.assertFalse(self.capture["model_weights_loaded"])
        self.assertFalse(self.capture["inference_run"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
