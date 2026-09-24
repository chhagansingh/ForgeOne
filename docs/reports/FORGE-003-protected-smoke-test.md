# FORGE-003 — Protected Inference Smoke Test

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Result:** **PASS**
- **Scope:** ONE small local inference session, entirely through the ForgeOne
  Resource Controller. No agent runtime was started.

> **What this does and does not establish.** This proves the *exercised protected
> path* worked under the observed conditions. It does **not** establish a safe
> maximum context, 8K/16K/32K/64K compatibility, general peak prefill-memory
> requirements, Hermes compatibility, guaranteed OOM prevention, or production
> readiness. See §11.

---

## 1. Preflight results and actual system state

### 1.1 Entry conditions

| Check | Result |
|---|---|
| Canonical path | `$FORGEONE_HOME` (owner-local, redacted) |
| Branch / HEAD | `feat/forge-003-runtime-bakeoff` @ `cd65e51bf2c55a514b3368b6ad887b252b6004e7` |
| Working tree | Clean (0 uncommitted) |
| ForgeOne inference processes running | **None** (0 mlx processes) |
| Port 8082 — `netstat` LISTEN | **0** |
| Port 8082 — real `bind()` probe | **FREE** |
| Credentials inspected | **No** |

### 1.2 Workstation safety gate

Measured over a **12-second window** so that *ongoing* growth is distinguished
from *residual* usage — the distinction the milestone explicitly required.

| Gate | Threshold | Observed | Result |
|---|---|---|---|
| Estimated available memory | ≥ 8 GiB | **9.55 GiB** (free 0.22 + inactive 9.20 + speculative 0.25) | **PASS** |
| Memory pressure / page-out rate | not concerning | **0.0 pages/s** | **PASS** |
| Swap growth | not concerning | **+0.000 GiB over 12.0 s** | **PASS** |
| Projected post-admission reserve | ≥ 4 GiB | **4.85 GiB** | **PASS** |

Residual swap was **4.73 GiB and completely flat**. That is existing swap, not
active pressure — and it is recorded as such rather than treated as a blocker.

**Verdict: PROCEED.**

### 1.3 Serving-path enforcement check

The milestone warned: *"Do not assume rejecting `--seed` alone proves that the
actual serving path has effective cache-byte enforcement."* The following was
established from installed source before any model was loaded:

| Question | Evidence | Answer |
|---|---|---|
| Does the installed server support the flags? | real `--help`: 24 flags parsed, all 4 required present | **Yes** |
| Is `--seed` absent? | `serving_path_is_bounded()` → `True` | **Yes** |
| Which cache does qwen3 use? | `qwen3.py:163` is a bare `nn.Module` with no `make_cache`, so `make_prompt_cache` falls back to the **default KV cache** (`cache.py:22–23`) | `KVCache` |
| Does that cache support `merge`? | `cache.py:397` `def merge` | **Yes** |
| Therefore `is_batchable`? | `draft_model is None` (none passed) **AND** all caches have `merge` (`server.py:371–381`) | **True → batched path** |
| Is `trim_to` on that path? | `server.py:795–798`, guarded by `prompt_cache_bytes is not None`, reached only in `_generate` | **Yes** |

**Conclusion:** the batched path runs and the byte ceiling is applied.

**Honest limit:** this proves the *code path applies* the ceiling. It does **not**
empirically prove a trim occurred during this session — two requests of 181 and
243 tokens never came close to the 512 MiB budget. Empirical trim observation
would require deliberately filling the cache, which is out of scope and would be
a memory stress test.

## 2. Controller and server configuration

**Policy** (approved limits, unchanged):

| Field | Value |
|---|---|
| `max_context_tokens` | 2048 |
| `max_input_tokens` | 512 |
| `reserved_output_tokens` | 128 |
| `min_available_memory_bytes` | 4 GiB |
| `max_retained_cache_bytes` | 512 MiB |
| `transient_reserve_bytes` | 1 GiB |
| `request_timeout_s` | 120 |
| `cooldown_after_abnormal_exit_s` | 60 |
| `telemetry_max_age_s` | 5.0 |
| `max_concurrent_requests` | 1 |
| `allow_context_escalation` | **false** |

**Actual server command line** (built by `ProtectedServerConfig.build_argv()`,
argv list — never a shell string):

```text
<model-venv>/bin/python -m mlx_lm.server \
  --model mlx-community/Qwen3-4B-Instruct-2507-4bit \
  --host 127.0.0.1 --port 8082 \
  --prompt-cache-bytes 536870912 \
  --prompt-cache-size 2
```

`--host 127.0.0.1` (loopback only) · explicit byte **and** sequence budgets ·
no `--seed` · no model switching · no context escalation.

**Model:** `mlx-community/Qwen3-4B-Instruct-2507-4bit` @
`50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`.
`has_chat_template=True`, `has_tool_calling=True`, KV = **147,456 B/token**.

## 3. Cold-start admission decision

| Field | Value |
|---|---|
| Outcome | **ADMITTED** |
| Reason | `protected startup complete` |
| Cold-start estimate | **5,046,586,573 bytes (4.70 GiB)** |
| `model_resident` charged | **`false`** — weights + overhead charged to the cold start |
| Available at decision | 10,389,405,696 bytes |
| Headroom | available − 4 GiB floor |

Order enforced before launch: policy → metadata → **fresh telemetry** →
**watchdog ready** → **port probed free** → cold-start residency → supervised
spawn. The watchdog was confirmed ready (`alive=True samples=1`) **before** the
heavy process started.

## 4. Watchdog startup and liveness

The watchdog ran as its **own process** (`python -u -m
services.resource_controller.watchdog`), launched before the model.

| Item | Value |
|---|---|
| PID | 59801 |
| Readiness | `alive=True samples=1 (need >= 1)` — telemetry had to be *flowing*, not merely started |
| Samples written | **21** |
| Abort records | **0** |
| Exit | `-15` (clean `SIGTERM` during shutdown) |

## 5. Actual token counts

Counted by `HuggingFaceTokenCounter` against the **real** tokenizer and the
checkpoint's `chat_template.jinja`, including tool schemas — the same call the
server makes.

| Request | Input tokens | + reserved output | Total context | Limit | Result |
|---|---|---|---|---|---|
| 1 (tool call) | **181** | 128 | 309 | 2048 | **OK** |
| 2 (tool result) | **243** | 128 | 371 | 2048 | **OK** |

Inputs of 181 and 243 are well inside the 512-token input budget. Request 2 was
re-counted in full (including the assistant tool call and the tool result)
before admission — it was not assumed to fit.

## 6. Request admission decisions

| Event | Outcome | Detail |
|---|---|---|
| `startup_ok` | — | pid recorded, argv recorded |
| `admission` (req 1) | **ADMITTED** | estimated peak 1,656,176,640 B within headroom 3,959,390,208 B |
| `request_ok` (req 1) | **ADMITTED** | executed |
| `admission` (req 2) | **ADMITTED** | estimated peak 1,665,318,912 B within headroom 4,059,054,080 B |
| `request_ok` (req 2) | **ADMITTED** | executed |
| `stopped` | **CANCELLED** | graceful |

Both requests were admitted **before** forwarding; the forwarder is only
reachable through `ProtectedServer.request()`. Reservations were held for the
duration and released on exit.

## 7. Structured tool-call and tool-result evidence

**A plain HTTP 200 was not treated as success.** The assertions below were made
against the parsed response body.

**Request 1** — prompt: *"Call the get_test_summary tool for the suite named
'pricing'."*

```json
{"name": "get_test_summary",
 "arguments_raw": "{\"suite\": \"pricing\"}",
 "arguments_parsed": {"suite": "pricing"},
 "expected_name": "get_test_summary",
 "name_ok": true,
 "args_ok": true}
```

A genuine structured `tool_calls` entry: correct function name, and arguments
that parse as JSON and satisfy the declared schema.

**Local mock execution** (fixed synthetic data, no filesystem writes):

```json
{"suite": "pricing", "tests": 6, "failures": 2, "status": "EXPECTED FAILING BASELINE"}
```

**Request 2** — the assistant tool call and the tool result were appended and
the full conversation re-counted and re-admitted.

**Final answer** (202 chars):

> *"The test suite named 'pricing' has 6 tests in total, with 2 failures. The
> status is marked as \"EXPECTED FAILING BASELINE,\" indicating that the failures
> are anticipated and part of the baseline behavior."*

The model correctly consumed the tool result and reported it. **The round trip
is complete: tool call → local execution → tool result → final answer.**

## 8. Memory and swap observations

| Metric | Before | Minimum during | After |
|---|---|---|---|
| Available memory | 9.68 GiB | **7.52 GiB** | **10.02 GiB** |
| Swap used | 4.73 GiB | **4.73 GiB** | **4.73 GiB** |
| Page-outs | 98,930 | — | 98,956 |

- **Available memory rose** across the session (9.68 → 10.02 GiB) and never
  approached the 2 GiB watchdog abort floor.
- **Swap did not move at all** — 4.73 GiB before, during and after.
- Page-outs increased by **26 over the session (~4/s)** — negligible, versus the
  5,000/s watchdog abort threshold and the 20,000/s configured limit.
- **No abort record was written.** No memory-pressure event occurred.

## 9. Process cleanup and reservation release

| Check | Result |
|---|---|
| Stop outcome | `CANCELLED` (graceful) |
| Owned PIDs after stop | **`[]`** — none remain |
| Reservations held | **0** — released on every exit path |
| `mlx_lm.server` processes | **0** |
| Watchdog processes | **0** |
| Port 8082 LISTEN | **0** |
| Port 8082 `bind()` probe | **FREE** |
| Unrelated processes killed | **None** |
| Unintended filesystem changes | **None** — the fixture at `/tmp/forge002-fixture` is intact; only the controller source changed |

Raw telemetry preserved under Git-ignored `storage/runs/`:
`smoke-telemetry.jsonl` (watchdog), `smoke-admission.jsonl` (controller),
`smoke-result.json`.

## 10. Exact tests, skips and exit codes

| Run | Command | Result | Exit |
|---|---|---|---|
| Resource Controller suite (model venv, 3.12.14 + mlx_lm) | `python -m unittest discover -s tests/unit -t .` | `Ran 156 tests … OK (skipped=1)` | **0** |
| Resource Controller suite (system 3.9.6) | `python3 -m unittest discover -s tests/unit -t .` | `Ran 156 tests … OK (skipped=16)` | **0** |
| **Protected smoke test** | `storage/bakeoff/model-venv/bin/python -u scripts/run_protected_smoke.py` | **PASS** | **0** |

### 10.1 One blocked attempt, reported honestly

The **first** smoke attempt returned **BLOCKED** at the `flag_support` stage
before any model was loaded. The cause was a defect in my harness, not in the
runtime: `--help` was invoked through a shell (`os.popen`), and the interpreter
path contains spaces, so the shell split it and returned an error page that
parsed as "required flags missing".

The fail-closed gate **correctly refused to start the model** on an unverified
condition. Fixed by invoking `--help` with an argv list (no shell), after which
all four required flags were found (24 flags parsed).

## 11. Remaining limitations and safety risks

| # | Limitation |
|---|---|
| 1 | **Safe maximum context remains UNKNOWN.** Only 2048 was exercised, at 309/371 tokens. |
| 2 | **8K / 16K / 32K / 64K compatibility is NOT established.** Nothing here should be read as evidence for any of them. |
| 3 | **General peak prefill memory is NOT characterised.** `transient_reserve_bytes` (1 GiB) remains a conservative allowance, not a measurement. The previous Metal OOM occurred during a 32,611-token prefill — orders of magnitude beyond this session. |
| 4 | **Cache trimming was not empirically observed.** The code path is proven (§1.3); an actual `trim_to` event was not. |
| 5 | **`weights_bytes` / `runtime_overhead_bytes` remain approximate.** |
| 6 | **The watchdog is best-effort.** It cannot guarantee prevention of an OS-level stall. |
| 7 | **No agent runtime was exercised.** Hermes/OpenHands compatibility is untested. |
| 8 | **One tiny session is a functional smoke test**, not evidence of production coding reliability. |

**No guarantee of OOM prevention is offered and no production readiness is
claimed.**
