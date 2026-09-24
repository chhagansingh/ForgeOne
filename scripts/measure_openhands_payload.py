#!/usr/bin/env python3
"""FORGE-003 — stage 2 of 2: measure the captured payload with the REAL tokenizer.

Runs under the **model venv** (it has ``mlx_lm``). Loads only tokenizer files —
``mlx_lm.utils.load_tokenizer`` restricts its read patterns to ``*.json``,
``*.txt``, ``*.jinja``, ``*.model`` and similar, excluding ``*.safetensors``.

**No model weights are loaded. No inference. No network call.**

    storage/bakeoff/model-venv/bin/python scripts/measure_openhands_payload.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAYLOAD = REPO / "storage/runs" / "openhands-payload.json"
OUT = REPO / "storage/runs" / "openhands-request-capture.json"

CHECKPOINT = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
SNAP = (REPO / "storage/cache/huggingface/hub"
        / f"models--{CHECKPOINT.replace('/', '--')}" / "snapshots" / REVISION)

BUDGET_CONTEXT = 2048
BUDGET_OUTPUT = 128


def main() -> int:
    os.environ.setdefault("HF_HOME", str(REPO / "storage/cache/huggingface"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    from mlx_lm.utils import load_tokenizer

    tokenizer = load_tokenizer(SNAP)  # tokenizer files only
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))

    def count(messages, tools=None) -> int:
        kw = {"add_generation_prompt": True, "tokenize": True}
        if tools:
            kw["tools"] = tools
        return len(tokenizer.apply_chat_template(messages, **kw))

    def verdict(tokens: int) -> str:
        return "FITS" if tokens + BUDGET_OUTPUT <= BUDGET_CONTEXT else "BLOCKED_CONTEXT"

    report = {
        "checkpoint": CHECKPOINT,
        "revision": REVISION,
        "tokenizer": "real (checkpoint chat_template.jinja)",
        "model_weights_loaded": False,
        "inference_run": False,
        "budget": {"total_context": BUDGET_CONTEXT, "reserved_output": BUDGET_OUTPUT},
    }

    for key in ("stock", "compact", "compact_terminal_only"):
        p = payload[key]
        msgs, tools = p["messages"], p["tools"]
        empty = [{"role": "system", "content": ""}, {"role": "user", "content": ""}]
        report[key] = {
            "system_prompt_chars": len(p["system_prompt"]),
            "dynamic_context_chars": len(p.get("dynamic_context") or ""),
            "system_prompt_tokens": count([{"role": "system", "content": p["system_prompt"]}]),
            "system_plus_task_tokens": count(msgs),
            "tool_names": p["tool_names"],
            "tool_schema_chars": len(json.dumps(tools, sort_keys=True)),
            "tool_schema_tokens": count(empty, tools) - count(empty),
            "total_input_tokens": count(msgs, tools),
            "tools_present_in_request": [t["function"]["name"] for t in tools],
        }
        report[key]["verdict"] = verdict(report[key]["total_input_tokens"])
        if key == "compact":
            report[key]["profile_id"] = p["profile_id"]
            report[key]["profile_version"] = p["profile_version"]
            report[key]["safeguards"] = p["safeguards"]

    workflow = {}
    for label, msgs in payload["workflow"].items():
        n = count(msgs, payload["compact"]["tools"])
        workflow[label] = {"input_tokens": n, "verdict": verdict(n)}
    report["COMPACT_WORKFLOW"] = workflow

    fits = [v["input_tokens"] for v in workflow.values()]
    report["MINIMUM_REQUIRED_CONTEXT"] = {
        "max_workflow_input_tokens": max(fits),
        "plus_reserved_output": max(fits) + BUDGET_OUTPUT,
        "note": (
            "WORKLOAD REQUIREMENT for this synthetic workflow, NOT a verified "
            "safe hardware context ceiling. The safe ceiling remains unmeasured; "
            "the prior incident occurred at a 32,611-token prefill."
        ),
    }

    OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    s, c = report["stock"], report["compact"]
    print(f"  STOCK   sys={s['system_prompt_tokens']:>5}  +task={s['system_plus_task_tokens']:>5}  "
          f"tools={s['tool_schema_tokens']:>5} ({len(s['tool_names'])})  TOTAL={s['total_input_tokens']:>5}  {s['verdict']}")
    print(f"  COMPACT sys={c['system_prompt_tokens']:>5}  +task={c['system_plus_task_tokens']:>5}  "
          f"tools={c['tool_schema_tokens']:>5} ({len(c['tool_names'])})  TOTAL={c['total_input_tokens']:>5}  {c['verdict']}")
    print(f"  tools present in request: {c['tools_present_in_request']}")
    print(f"  workflow max input={max(fits)} (+{BUDGET_OUTPUT} out = {max(fits)+BUDGET_OUTPUT})")
    print(f"  artifact: {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
