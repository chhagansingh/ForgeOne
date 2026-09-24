#!/usr/bin/env python3
"""FORGE-003 — stage 1 of 2: extract the ACTUAL OpenHands request payload.

Runs under the **OpenHands venv** and uses the SDK's own serialization path
(``ToolDefinition.to_openai_tool()``, ``PromptRegistry.build()``) to produce the
real model-facing prompt and tool schemas. Writes JSON for stage 2.

**No model weights are loaded. No inference. No network call.**

    storage/bakeoff/openhands-venv/bin/python scripts/capture_openhands_request.py

Stage 2 (token counting) is ``scripts/measure_openhands_payload.py``, which runs
under the model venv because that is where the real tokenizer lives.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = REPO / "storage/runs" / "openhands-payload.json"

TASK = (
    "In the working directory there is a small Python project. "
    "Read src/pricing.py and tests/test_pricing.py, identify the defect in "
    "tier_price(), apply the smallest correct fix, then run "
    "`python3 -m unittest discover -s tests -v` and report the result."
)

FAILING_OUTPUT = (
    "test_100_units_uses_top_tier ... ok\n"
    "test_10_units_uses_middle_tier ... ok\n"
    "test_1_unit_uses_base_tier ... ok\n"
    "test_negative_units_is_rejected ... FAIL\n"
    "test_order_total_uses_tier_price ... ok\n"
    "test_zero_units_is_rejected ... FAIL\n"
    "Ran 6 tests in 0.001s\n"
    "FAILED (failures=2)"
)


def build_tool_schemas() -> dict:
    """Real tool definitions via the SDK's own to_openai_tool()."""
    from openhands.tools.file_editor.definition import (
        TOOL_DESCRIPTION as FE_DESC, FileEditorAction, FileEditorTool)
    from openhands.tools.task_tracker.definition import (
        TASK_TRACKER_DESCRIPTION as TT_DESC, TaskTrackerAction, TaskTrackerTool)
    from openhands.tools.terminal.definition import TerminalAction, TerminalTool
    from openhands.tools.terminal.descriptions import UNIX_TOOL_DESCRIPTION

    pairs = [
        ("terminal", TerminalTool, TerminalAction, UNIX_TOOL_DESCRIPTION),
        ("file_editor", FileEditorTool, FileEditorAction, FE_DESC),
        ("task_tracker", TaskTrackerTool, TaskTrackerAction, TT_DESC),
    ]
    return {
        name: cls(description=desc, action_type=action).to_openai_tool()
        for name, cls, action, desc in pairs
    }


def render_system_prompt(tool_names):
    from openhands.sdk.context.prompts.presets import create_registry
    from openhands.sdk.context.prompts.section import PromptContext

    ctx = PromptContext(tool_names=tuple(tool_names), working_dir="/workspace")
    static, dynamic = create_registry().build(ctx)
    return static, (dynamic or "")


def main() -> int:
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

    tools = build_tool_schemas()
    stock_names = list(tools)
    stock_static, stock_dynamic = render_system_prompt(stock_names)

    from services.agent_profiles import get_profile

    profile = get_profile("FORGEONE_COMPACT_V1")
    compact_names = list(profile.tool_names)
    compact_tools = [tools[n] for n in compact_names]
    # The compact profile supplies its OWN inline prompt via the SDK's supported
    # Agent(system_prompt=...) mechanism. Rendering the SDK registry here would
    # silently produce the STOCK prompt and measure the wrong thing.
    compact_static = profile.system_prompt
    compact_dynamic = ""

    def msgs(system, dynamic, task=TASK):
        out = [{"role": "system", "content": system}]
        if dynamic:
            out.append({"role": "system", "content": dynamic})
        out.append({"role": "user", "content": task})
        return out

    stock_msgs = msgs(stock_static, stock_dynamic)
    compact_msgs = msgs(compact_static, compact_dynamic)

    # Diagnostic only: the same profile with a single tool. Tool schemas are the
    # dominant cost, so this quantifies the trade-off. It is NOT a second
    # profile -- it exists so the owner can see what staying under a small
    # budget would actually require.
    terminal_only = {
        "tool_names": ["terminal"],
        "system_prompt": compact_static,
        "dynamic_context": "",
        "messages": msgs(compact_static, "", TASK),
        "tools": [tools["terminal"]],
    }

    payload = {
        "task": TASK,
        "failing_output": FAILING_OUTPUT,
        "model_weights_loaded": False,
        "inference_run": False,
        "compact_terminal_only": terminal_only,
        "stock": {
            "system_prompt": stock_static,
            "dynamic_context": stock_dynamic,
            "messages": stock_msgs,
            "tools": [tools[n] for n in stock_names],
            "tool_names": stock_names,
        },
        "compact": {
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "safeguards": list(profile.safeguards),
            "system_prompt": compact_static,
            "dynamic_context": compact_dynamic,
            "messages": compact_msgs,
            "tools": compact_tools,
            "tool_names": compact_names,
        },
        "workflow": {
            "A_initial_coding": compact_msgs,
            "B_file_read": compact_msgs + [
                {"role": "assistant", "content": "Reading the file."},
                {"role": "tool", "tool_call_id": "c1",
                 "content": "def tier_price(units): ..."},
            ],
            "C_test_command": compact_msgs + [
                {"role": "assistant", "content": "Running the tests."},
                {"role": "tool", "tool_call_id": "c2", "content": FAILING_OUTPUT},
            ],
            "D_failing_output": compact_msgs + [
                {"role": "tool", "tool_call_id": "c3", "content": FAILING_OUTPUT},
            ],
            "E_tool_result": compact_msgs + [
                {"role": "tool", "tool_call_id": "c4", "content": "FAILED (failures=2)"},
            ],
            "F_final_answer": compact_msgs + [
                {"role": "assistant", "content": "Fixed tier_price()."},
                {"role": "user", "content": "Summarise the change and the test result."},
            ],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"  stock   system={len(stock_static)} chars  tools={stock_names}  "
          f"tool_chars={len(json.dumps([tools[n] for n in stock_names], sort_keys=True))}")
    print(f"  compact system={len(compact_static)} chars  tools={compact_names}  "
          f"tool_chars={len(json.dumps(compact_tools, sort_keys=True))}")
    print(f"  artifact: {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
