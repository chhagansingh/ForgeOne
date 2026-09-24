# FORGE-003 — OpenHands Payload Compatibility & Compact Coding Profile

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Model loaded:** **No.** **Inference run:** **No.** **Network calls:** none.
- **Result:** gateway compatibility **PASS**; the approved 2K budget **does not**
  support the compact coding workflow — `BLOCKED_CONTEXT`, with a measured
  minimum requirement of **2,446 tokens**.

Evidence labels used throughout:

| Label | Meaning |
|---|---|
| `MOCK_TESTED` | Exercised with fakes/synthetic data |
| `STATICALLY_VERIFIED` | Established by reading installed source |
| `REAL_TOKENIZER_VERIFIED` | Measured with the actual cached tokenizer + chat template |
| `REAL_INFERENCE_NOT_RUN` | Deliberately not executed |
| `BLOCKED` | Cannot proceed under current constraints |

---

## 1. Stock OpenHands complete token footprint

Captured through the SDK's **own serialization path** —
`PromptRegistry.build()` for the prompt and `ToolDefinition.to_openai_tool()`
for the schemas — then measured with the real tokenizer.

| Component | Chars | **Tokens** |
|---|---|---|
| Stock system prompt | 11,017 | **2,318** |
| Dynamic context | 0 | 0 |
| Task message | — | 51 |
| **System + task** | | **2,379** |
| Tool schemas (3 tools) | 13,831 | **3,221** |
| **TOTAL INPUT** | | **5,600** |
| Reserved output | | 128 |
| **REQUIRED CONTEXT** | | **5,728** |

**`REAL_TOKENIZER_VERIFIED`** · Verdict against the approved 2,048-token budget:
**`BLOCKED_CONTEXT`** — 2.7× over.

### 1.1 Correcting the previous measurement

The earlier `BLOCKED_CONTEXT` report recorded **2,366 tokens** for system + task
and stated plainly that tool schemas were **not included**. That was correct as
far as it went, and is now superseded:

| | Previous | Now |
|---|---|---|
| System prompt | 2,318 | 2,318 |
| System + task | 2,366 | 2,379 |
| Tool schemas | **not included** | **3,221** |
| Total input | *(lower bound)* | **5,600** |

The 2,366 figure was indeed a **lower bound**; the true initial request is
**2.4× larger**. The gap was caused by a harness defect — `openhands.sdk.tool.Tool`
is a *spec* (name + params) with no `to_openai_tool`; the schema lives on
`ToolDefinition`. Constructing the definitions directly
(`TerminalTool(description=..., action_type=...)`) fixed it.

## 2. Full tool-schema contribution

| Tool | Schema chars | Tokens | Properties |
|---|---|---|---|
| `terminal` | 3,721 | ~992 | 5 |
| `file_editor` | 3,780 | ~909 | 8 |
| `task_tracker` | 6,324 | ~1,320 | 3 |
| **All three** | **13,831** | **3,221** | |

**Tool schemas are the single largest cost** — larger than the system prompt
(2,318). `task_tracker` alone contributes ~1,320 tokens for a fixture task that
never needs structured task tracking.

**Tools are confirmed present in the serialized request**, not assumed:
`tools_present_in_request == ["terminal", "file_editor", "task_tracker"]`, each
with a populated `parameters` object. `MOCK_TESTED` assertions guard against
regression to treating their cost as zero.

## 3. Compact profile token footprint — `FORGEONE_COMPACT_V1`

Defined in `services/agent_profiles/compact_v1.py`. **The upstream package is
not patched, forked or monkey-patched** — the profile uses the SDK's supported
`Agent(system_prompt=...)` and `Agent(tools=[...])` mechanisms.

| Component | Chars | **Tokens** |
|---|---|---|
| Compact system prompt | 1,109 | **250** |
| Task message | — | 61 |
| Tool schemas (`terminal`, `file_editor`) | 7,505 | **1,901** |
| **TOTAL INPUT** | | **2,212** |
| + reserved output | | 128 |
| **REQUIRED CONTEXT** | | **2,340** |

Verdict: **`BLOCKED_CONTEXT`** — 292 tokens over the 2,048 budget.

### 3.1 `STOCK_OPENHANDS` vs `FORGEONE_COMPACT_V1`

| Dimension | `STOCK_OPENHANDS` | `FORGEONE_COMPACT_V1` |
|---|---|---|
| System prompt | 11,017 chars / 2,318 tok | **1,109 chars / 250 tok** |
| Tools | `terminal`, `file_editor`, `task_tracker` | `terminal`, `file_editor` |
| Tool schema cost | 3,221 tok | **1,901 tok** |
| **Total input** | **5,600 tok** | **2,212 tok** (−60%) |
| Safeguards | upstream defaults | **all 10 restated in the prompt** |
| Prompt source | SDK registry render | ForgeOne inline prompt |
| Upstream modified | — | **No** |

**What was reduced:** prompt verbosity and one unused tool.
**What was NOT reduced:** every mandatory safeguard. `AgentProfile.__post_init__`
**fails construction** if any safeguard is missing from the declared list *or*
absent from the rendered prompt text. That guard caught a real defect during
development — a line-wrapped sentence meant `no_direct_model_endpoint_access`
was not present verbatim, and the profile refused to load.

This is a **limited ForgeOne integration profile, not a benchmark of stock
OpenHands.** Measurements taken with it must never be reported as stock results.

### 3.2 Diagnostic — single-tool variant

Tool schemas dominate, so a terminal-only variant was measured for comparison.
**This is not a second profile**; it quantifies the trade-off.

| Variant | System | Tools | **Total input** | +128 output | Verdict |
|---|---|---|---|---|---|
| Stock (3 tools) | 2,318 | 3,221 | 5,600 | 5,728 | BLOCKED_CONTEXT |
| Compact (2 tools) | 250 | 1,901 | 2,212 | 2,340 | BLOCKED_CONTEXT |
| **Compact, terminal only** | 250 | **992** | **1,303** | **1,431** | **FITS** |

`terminal` alone still satisfies read / edit / test / diff / report via the
shell, but it removes the structured edit tool. That is a real capability
trade-off and the owner's decision — not something to make silently.

## 4. Gateway and streaming compatibility

Inspected in `openhands/sdk/llm/llm.py` (`STATICALLY_VERIFIED`):

| SDK field | SDK default | Gateway requirement | Compatible? |
|---|---|---|---|
| `stream` | **`False`** | non-streaming only | **YES** |
| `requires_streaming` | `self._is_subscription` → False for local | — | **YES** |
| `api_mode` | `"auto"` | must force `"chat"` | **YES, if set** |
| `num_retries` | **`5`** | must be `0` | **NO, must override** |
| `timeout` | `300` | policy `request_timeout_s` | **NO, must set** |
| `max_output_tokens` | `None` | ≤ reserved output | **NO, must set** |

**No streaming adapter is required.** The SDK defaults to `stream=False`, and
`requires_streaming` is true only for subscription auth — so the gateway's
refusal of `stream=True` is compatible rather than a limitation.

**Three defaults must be overridden, and one is a genuine safety conflict:**
`num_retries=5` would silently retry five times after a resource rejection,
which the milestone explicitly forbids. `validate_llm_kwargs()` now **fails
closed** unless `api_mode="chat"`, `stream=False`, `num_retries=0`, a positive
`timeout`, and a positive `max_output_tokens` are all supplied. `MOCK_TESTED`.

Routing is unchanged and non-bypassable:

```text
agent SDK → ProtectedGateway → atomic reservation → tokenizer admission
          → supervised MLX-LM server → response → reservation release
```

The gateway remains loopback-only and rejects over-budget `max_tokens` rather
than clamping.

## 5. Does the 2K budget support the limited coding workflow?

**No.** Measured with the real tokenizer across the full workflow
(`REAL_TOKENIZER_VERIFIED`):

| Step | Input tokens | +128 output | Verdict |
|---|---|---|---|
| A — initial coding request (all required tool schemas) | 2,212 | 2,340 | **BLOCKED_CONTEXT** |
| B — file-read continuation | 2,235 | 2,363 | **BLOCKED_CONTEXT** |
| C — test-command continuation | 2,318 | **2,446** | **BLOCKED_CONTEXT** |
| D — representative failing-test output | 2,309 | 2,437 | **BLOCKED_CONTEXT** |
| E — tool-result continuation | 2,227 | 2,355 | **BLOCKED_CONTEXT** |
| F — final-answer request | 2,236 | 2,364 | **BLOCKED_CONTEXT** |

Every step exceeds the budget. Nothing was silently truncated and no instruction
was dropped to force a fit.

## 6. Minimum measured context

> **2,446 tokens** — the largest workflow step (C, 2,318 input) plus the 128-token
> output reservation.

**This is a WORKLOAD REQUIREMENT for this synthetic workflow, NOT a verified
safe hardware context ceiling.** The safe ceiling remains **unmeasured**, and
the prior incident occurred during a **32,611-token prefill** — more than an
order of magnitude beyond this figure. Nothing here should be read as evidence
that 2,446 tokens is safe on this machine; only that the workflow needs it.

**Recommended next context, if approved: 4,096 tokens.** It clears the measured
2,446 requirement with ~40% headroom for a slightly larger task, while remaining
~8× below the size that caused the incident. It must be established by a
*guarded, incremental* measurement under the Resource Controller — never by
assuming it.

## 7. Tests, skips, failures, exit codes

| Run | Result | Exit |
|---|---|---|
| Full suite — Python 3.12.14 + mlx_lm | `Ran 209 tests … OK (skipped=4)` | **0** |
| Full suite — Python 3.9.6 (system) | `Ran 209 tests … OK (skipped=19)` | **0** |

**209 tests** — 171 preserved, **38 added** (`test_agent_profiles.py` 24,
`test_openhands_capture.py` 14). **Zero failures.**

Skips are environmental and reported, not hidden: the OpenHands-SDK group needs
the OpenHands venv, the real-tokenizer group needs the model venv, and one test
skips because the checkpoint's chat template rejects an empty conversation.

Required-case coverage: real SDK request capture ✔ · actual tool-schema
inclusion ✔ · real tokenizer agreement ✔ · compact-profile selection ✔ ·
mandatory safety-instruction preservation ✔ · complete input + output
reservation ✔ · tool-call/tool-result continuation ✔ · failed-test output
accounting ✔ · protected gateway routing ✔ · streaming/non-streaming
compatibility ✔ · reservation release on success ✔ · on failure/cancellation ✔ ·
context rejection before forwarding ✔ · no direct unprotected backend access ✔.

**Safety:** no model weights loaded, no inference, no context stress test, no
additional model download, no cloud call. Verified after: 0 mlx processes,
checkpoint unchanged at 2.1 GB.

## 8. Hermes status

| Item | Finding |
|---|---|
| Installed | **No** |
| Upstream version | **0.21.5** (was 0.21.4) |
| `requires-python` | `>=3.11,<3.14` |
| **Documented minimum context** | **None found** in `hermes_constants.py`, `COMPAT_MANIFEST.md`, `README.md`, `hermes_cli/config.py`, or the configuration/providers docs pages |
| Local execution | **`BLOCKED`** — not installed, and the same endpoint budget applies |
| False context advertised | **No** — no 64K claim was made anywhere |

Hermes was **not installed and not executed.**

## 9. Remaining blockers and safety limitations

| # | Blocker |
|---|---|
| 1 | **The 2K budget is too small for the compact workflow** (needs 2,446) |
| 2 | **The safe hardware context ceiling is still UNKNOWN** — 2,446 is a workload requirement, not a safety finding |
| 3 | **Prefill peak remains unmeasured** — the term that most likely caused the incident |
| 4 | **No agent runtime has executed a coding task** — `REAL_INFERENCE_NOT_RUN` |
| 5 | Single-tool variant trades away the structured edit tool |
| 6 | `weights_bytes` / `runtime_overhead_bytes` remain approximate |
| 7 | Watchdog remains best-effort |

## 10. Recommended next execution route

1. **Owner decision:** approve a larger context. **4,096 is recommended** —
   clears the measured 2,446 with headroom.
2. **Guarded escalation measurement**, not an assumption: step 2K → 3K → 4K
   under the Resource Controller with the watchdog live, aborting on first
   pressure, telemetry flushed per sample. This also finally measures
   `transient_reserve_bytes`.
3. **Then** the OpenHands compact session against the FORGE-002 fixture, with
   `api_mode="chat"`, `num_retries=0`, `stream=False`, all inference through the
   protected gateway.
4. **Then** Hermes, under the same endpoint and budget.

## 11. What this report does NOT claim

- It does **not** claim an agent completed a coding task. None ran.
- It does **not** claim OpenHands is unsuitable. The blocker is the budget.
- It does **not** claim 2,446 tokens is a safe context.
- It does **not** claim stock OpenHands performance from compact-profile numbers.
- It is **not** a comparative Hermes-vs-OpenHands bake-off.
