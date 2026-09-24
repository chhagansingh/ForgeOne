# FORGE-003 — Runtime Bake-off: Progress Report

- **Milestone:** M1 — Agent bake-off
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Bake-off status:** **NOT RUN.** No agent runtime has been installed or
  executed. This is an honest progress report, not a results report.

> **Read this first.** The bake-off did not happen, and this document does not
> pretend otherwise. A host memory incident interrupted the milestone
> ([incident report](FORGE-003-P0-memory-incident.md)), and the remaining work
> was redirected into building the
> [Resource Controller](../architecture/resource-controller-design.md) that
> prevents a recurrence. Nothing here is a benchmark result.

---

## 1. Phase status

| Phase | Description | Status |
|---|---|---|
| 1 | Install toolchain (uv, Python 3.12, MLX-LM, checkpoint) | **DONE** |
| 2 | Inference preflight (8 checks incl. tool calling) | **PASS 8/8** |
| 2 | Context/memory verification | **FAILED — P0 host memory incident** (unprotected) |
| 2b | **Protected inference smoke test** | **PASS** — [report](FORGE-003-protected-smoke-test.md) |
| 3 | Install agent runtimes (Hermes, OpenHands) | **NOT STARTED** |
| 4 | Identical practical bake-off on the fixture | **NOT STARTED** |
| 5 | Measurement and review | **NOT STARTED** |
| 6 | Architecture decision (ADR-0001) | **BLOCKED** |
| 7 | Delivery | **PARTIAL** (this report) |
| — | Resource Controller implementation + hardening | **DONE** (156 tests passing) |
| — | **Bake-off entry gate** | **SATISFIED** — see §10.2 |

---

## 2. What was actually installed

All under Git-ignored `storage/`. No privileges, no system changes, no `sudo`,
no Homebrew, no remote install scripts.

| Artefact | Version | Location | Notes |
|---|---|---|---|
| `uv` | 0.12.18 | `storage/tools/uv/` | **SHA256 verified** against the official `.sha256` |
| Python | 3.12.14 | `storage/tools/python/` | uv-managed; system 3.9.6 untouched |
| `mlx-lm` | 0.31.3 | `storage/bakeoff/model-venv/` | plus `mlx` 0.32.2, `transformers` 5.17.0 |
| Checkpoint | `mlx-community/Qwen3-4B-Instruct-2507-4bit` @ `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b` | `storage/cache/huggingface/` | 2.11 GB on disk |

**Total footprint: 2.6 GB.** Rollback is `rm -rf storage/tools storage/bakeoff
storage/cache` — nothing global was modified, so nothing global needs reverting.

### 2.1 Preserved evidence — inference preflight 8/8

Produced before the incident and **valid**. Full detail in the
[incident report §7](FORGE-003-P0-memory-incident.md).

| # | Check | Result |
|---|---|---|
| 1 | `GET /v1/models` | PASS |
| 2 | `POST /v1/chat/completions` | PASS (usage reported) |
| 3 | Streaming SSE | PASS (11 chunks, TTFT 0.24 s) |
| 4 | **Structured tool calling** | PASS — `tool_calls[0].function.name = "run_tests"`, valid JSON arguments |
| 5 | Tool result → final answer | PASS |
| 6 | Malformed JSON handling | PASS (HTTP 400) |
| 7 | Unknown model rejection | PASS (HTTP 404) |
| 8 | Tool-call structure validity | PASS |

---

## 3. Resource Controller — architecture

```text
                 ┌─────────────────────────────────────────────┐
  request ──────▶│  AdmissionController                        │
                 │   1 policy present?          else REJECT    │
                 │   2 telemetry obtainable?    else REJECT    │
                 │   3 concurrency (max 1)      else REJECT    │
                 │   4 token count (injected)   else REJECT    │
                 │   5 input limit              else REJECT    │
                 │   6 output reservation       else REJECT    │
                 │   7 total context            else REJECT    │
                 │   8 retained cache budget    else REJECT    │
                 │   9 available-memory floor   else REJECT    │
                 │  10 estimate vs headroom     else REJECT    │
                 │  11                          -> ADMITTED    │
                 └───────────────┬─────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
  ProcessSupervisor        Watchdog              TelemetryWriter
  ownership, PID reuse,    available/swap/       unbuffered JSONL,
  bounded shutdown,        pageouts/RSS/wall     flushed per sample
  loopback bind policy     -> abort verdict
```

**Design rules:** estimate before allocate · reserve the host first · ceilings
explicit never defaulted · abort beats degrade · evidence survives failure ·
stepwise only, no automatic escalation · fail closed · the host is production.

### 3.1 Public API

```python
from services.resource_controller import (
    ResourceController, ResourcePolicy, AdmissionRequest, Outcome,
    ModelMetadata, MacOSTelemetrySource, HuggingFaceTokenCounter,
    ProcessSupervisor, SubprocessAdapter, WatchdogThresholds, JsonlTelemetryWriter,
)

policy   = ResourcePolicy.from_json_file("storage/config/resource-policy.json")
counter  = HuggingFaceTokenCounter(tokenizer)      # injected, never loaded here
metadata = ModelMetadata.from_hf_config(config, weight_bytes=..., runtime_overhead_bytes=...)

controller = ResourceController(
    policy, counter, metadata, MacOSTelemetrySource(),
    supervisor=ProcessSupervisor(SubprocessAdapter()),
    telemetry_writer=JsonlTelemetryWriter("storage/runs/telemetry.jsonl"),
    watchdog_thresholds=WatchdogThresholds(
        min_available_bytes=6 * 1024**3,
        max_swap_used_bytes=2 * 1024**3,
        max_pageout_rate=5000.0,
    ),
)

decision = controller.admit_and_record(AdmissionRequest(
    messages=[{"role": "user", "content": "..."}],
    requested_output_tokens=1024,
    retained_cache_bytes=0,
))
if not decision.admitted:
    ...            # decision.outcome names the reason; nothing is silently altered

verdict = controller.run_watchdog()
```

### 3.2 Machine-readable outcomes

`ADMITTED` · `REJECTED_CONTEXT` · `REJECTED_MEMORY` · `REJECTED_CONCURRENCY` ·
`REJECTED_UNVERIFIED_ESTIMATE` · `CANCELLED` · `TIMED_OUT` ·
`ABORTED_MEMORY_PRESSURE` · `PROCESS_FAILED`

Only `ADMITTED` counts as success. The controller never reduces context,
substitutes a model, or reports a rejection as a success.

---

## 4. Admission rules

Ordered so that rejections are deterministic. **First failure wins.**

| # | Rule | Outcome on failure |
|---|---|---|
| 1 | Policy, tokenizer, metadata and telemetry all present | `REJECTED_UNVERIFIED_ESTIMATE` |
| 2 | Telemetry readable | `REJECTED_UNVERIFIED_ESTIMATE` |
| 3 | `active_requests < max_concurrent_requests` (fixed at 1) | `REJECTED_CONCURRENCY` |
| 4 | Tokens countable via injected tokenizer + chat template | `REJECTED_UNVERIFIED_ESTIMATE` |
| 5 | `input_tokens <= max_input_tokens` | `REJECTED_CONTEXT` |
| 6 | `requested_output <= reserved_output_tokens` | `REJECTED_CONTEXT` |
| 7 | `input + output <= max_context_tokens` | `REJECTED_CONTEXT` |
| 8 | `retained_cache_bytes <= max_retained_cache_bytes` | `REJECTED_MEMORY` |
| 9 | `available >= min_available_memory_bytes` | `REJECTED_MEMORY` |
| 10 | `estimate.total <= available - floor` | `REJECTED_MEMORY` |
| 11 | — | `ADMITTED` |

**Memory model**

```text
available = free + inactive + speculative      # NOT "free" alone
headroom  = available - min_available_memory_bytes
estimate  = kv_bytes(context)
          + retained_cache_bytes
          + transient_reserve_bytes
          + (weights + overhead  if the model is NOT already resident)
```

`transient_reserve_bytes` is a **conservative configurable allowance for the
unmeasured prefill working set** — not a measurement. The true prefill peak
remains unknown.

**The incident scenario is now refused.** A 32,611-token request on a host with
9.7 GB available is rejected, not admitted — proven by
`test_the_incident_scenario_is_now_refused`.

---

## 5. Watchdog and process-cleanup behaviour

**Watchdog** — runs as a **separate lightweight process**
(`python -m services.resource_controller.watchdog`), so it survives the workload
it supervises. Samples at 250 ms and aborts on any of:

| Signal | Abort condition |
|---|---|
| Available memory | below the configured floor |
| Swap used | above the limit |
| Page-out rate | above the limit (pages/s) |
| Owned-process RSS | above its own ceiling, if configured |
| Wall clock | above the timeout, if configured |
| Telemetry loss | immediately — it never flies blind |

**Explicitly best-effort.** A watchdog cannot prevent a stall caused by an
allocation completing faster than the sampling interval. It narrows the window;
it does not eliminate it. And **RSS is never the only signal** — a small
owned-process RSS still aborts when the host is under pressure
(`test_rss_is_not_the_only_signal`).

**Supervisor** — only ForgeOne-owned processes are ever signalled:

- Identity is `(pid, start_marker, cmdline_fingerprint)`, re-verified
  immediately before any signal. A mismatch raises `OwnershipMismatchError` and
  **no signal is sent** — this is the PID-reuse defence.
- Duplicate service tags are refused.
- Non-loopback `--host`/`--bind` is refused **before** spawn.
- Shutdown is bounded: `SIGTERM` to the process group, then `SIGKILL` after the
  grace period.
- `cleanup_all()` terminates every owned process and nothing else, and is
  idempotent.

---

## 6. Test evidence

**Actual results. Both interpreters. Exit codes as observed.**

| Module | Tests | Note |
|---|---|---|
| `test_policy.py` | 9 | original |
| `test_estimator.py` | 12 | original |
| `test_admission.py` | 17 | original |
| `test_watchdog.py` | 13 | original |
| `test_supervisor.py` | 16 | original |
| `test_controller.py` | 12 | original |
| `test_server_config.py` | 21 | **new** — cache budget, argv, serving paths |
| `test_ports.py` | 5 | **new** — netstat + bind probing |
| `test_reservation.py` | 10 | **new** — atomic reservation |
| `test_protected.py` | 21 | **new** — startup/request gating |
| `test_tokenizer_real.py` | 20 | **new** — real tokenizer (1 skip) |
| **Total** | **156** | 79 original preserved, 77 added |

| Interpreter | Command | Result | Exit code |
|---|---|---|---|
| Python **3.12.14** + mlx_lm (model venv) | `python -m unittest discover -s tests/unit -t .` | `Ran 156 tests … OK (skipped=1)` | **0** |
| Python **3.9.6** (system) | `python3 -m unittest discover -s tests/unit -t .` | `Ran 156 tests … OK (skipped=16)` | **0** |

The single skip under the model venv is
`test_empty_message_list` — the checkpoint's chat template rejects an empty
conversation, so the test skips rather than asserting a fabricated count. Under
the system interpreter the 16 additional skips are the real-tokenizer tests,
which require `mlx_lm` (absent from the system Python).

**Real-tokenizer validation is real, not mocked:** 19 of 20 tests in
`test_tokenizer_real.py` executed against the actual Qwen3 tokenizer and the
checkpoint's `chat_template.jinja`. Included is
`test_naive_concatenation_disagrees_with_the_template`, which proves the
agreement assertions are not vacuous — a naive counter produces a *different*
number.

### 6.1 What each layer actually verifies

| Layer | Verified by | Status |
|---|---|---|
| Mock-tested controller logic | 156 tests incl. reservation/protected/config | **REAL** |
| Static server-configuration validation | `test_server_config.py` (no server started) | **REAL (static)** |
| Real-tokenizer validation | `test_tokenizer_real.py` vs the checkpoint's tokenizer | **REAL** |
| **Real protected inference** | [protected smoke test](FORGE-003-protected-smoke-test.md) — 1 session, 2 requests | **PASS** |
| Real agent-runtime execution | — | **NOT_TESTED** |
| GUI validation | — | **NOT_TESTED** |

**Required-case coverage** (all 20 from the milestone brief):

| # | Case | Test |
|---|---|---|
| 1 | Safe request admission | `test_safe_request_admitted` |
| 2 | Input limit exceeded | `test_input_limit_exceeded` |
| 3 | Output reservation exceeded | `test_output_reservation_exceeded` |
| 4 | KV estimate exceeds budget | `test_kv_estimate_exceeds_budget` |
| 5 | Retained cache exceeds budget | `test_retained_cache_exceeds_budget` |
| 6 | Insufficient available memory | `test_insufficient_available_memory` |
| 7 | Critical memory pressure | `test_critical_memory_pressure_aborts` |
| 8 | Rapid swap growth | `test_rapid_swap_growth_via_pageout_rate_aborts`, `test_swap_threshold_aborts` |
| 9 | Concurrent request rejection | `test_concurrent_request_rejected` |
| 10 | Missing tokenizer | `test_missing_tokenizer`, `test_unavailable_tokenizer` |
| 11 | Missing model metadata | `test_missing_model_metadata`, `test_missing_metadata_raises` |
| 12 | Missing critical policy | `test_missing_critical_field_raises`, `test_every_critical_field_is_required` |
| 13 | Timeout | `test_timeout_escalates_to_sigkill`, `test_wall_clock_timeout_aborts` |
| 14 | Cancellation | `test_cancel_owned_process`, `test_start_and_cancel_supervised` |
| 15 | Watchdog threshold | `test_owned_rss_threshold_aborts`, `test_wall_clock_timeout_aborts` |
| 16 | Unexpected process exit | `test_unexpected_process_exit` |
| 17 | Cooldown | `test_cooldown_after_abnormal_exit`, `test_abort_triggers_cooldown` |
| 18 | Owned-process cleanup | `test_owned_process_cleanup`, `test_cleanup_terminates_all_owned` |
| 19 | PID reuse protection | `test_pid_reuse_protection`, `test_dead_process_is_not_signalled` |
| 20 | Persistent telemetry after simulated failure | `test_aborts_and_persists_telemetry`, `test_jsonl_writer_flushes_to_disk` |

**Safety of the test run itself.** No model was imported or loaded; no inference
was run; no context stress test was performed; the checkpoint was not read. The
only real process spawned was a trivial `sleep 60`, cleaned up by the
supervisor. Verified afterwards: zero leftover processes, 9.7 GB still
available, 0 `mlx` processes.

---

## 7. Limitations and unimplemented requirements

Recorded honestly rather than glossed over.

Every previously open limitation, re-assessed after the hardening pass.
Status vocabulary: **FIXED** / **PARTIALLY FIXED** / **STILL POSSIBLE** / **NOT_TESTED**.

| # | Limitation | Status | Evidence |
|---|---|---|---|
| 1 | `HuggingFaceTokenCounter` unvalidated against a real tokenizer | **FIXED** | `tests/.../test_tokenizer_real.py` — 19 real tests vs the checkpoint tokenizer; `tokenization.py:87–121` |
| 2 | `--prompt-cache-bytes` not injected into server argv | **FIXED** | `server_config.py:ProtectedServerConfig.build_argv()` always emits it; `test_correct_argv_is_built` |
| 3 | No port-availability probe | **FIXED** | `ports.py:RealPortProbe` (netstat **and** bind); `test_ports.py` incl. a real bound socket |
| 4 | `transient_reserve_bytes` is an allowance, not a measurement | **STILL POSSIBLE** | No guarded inference run has been permitted. **The true prefill peak remains unknown.** |
| 5 | `weights_bytes` / `runtime_overhead_bytes` approximate | **PARTIALLY FIXED** | `observed-model-metadata.example.json` records observed config + measured RSS; exact values still unmeasured |
| 6 | Watchdog is best-effort | **STILL POSSIBLE** (by nature) | Cannot guarantee prevention of an OS-level stall. Documented in `protected.py` module docstring |
| 7 | No integration test against a live model server | **FIXED** | `transport.py:HttpRequestForwarder` (loopback-only, stdlib) + [protected smoke test](FORGE-003-protected-smoke-test.md) — 2 real requests gated end to end. Unit tests still use a double, correctly. |
| 8 | Safe context ceiling unknown | **STILL POSSIBLE** | Deliberately not invented. `test_context_boundary_is_measured_not_assumed` measures but asserts no ceiling |
| 9 | Not wired into any agent runtime | **STILL POSSIBLE** | Hermes/OpenHands integration is future work |
| 10 | `start_supervised` bypassable (no admission gate) | **FIXED** | `protected.py:ProtectedServer.start()` is the gated path; `test_missing_policy_blocks_startup` |
| 11 | Concurrency race (caller-supplied `active_requests`) | **FIXED** | `reservation.py:ReservationManager`; `test_races_are_atomic` (8 threads, exactly 1 wins) |
| 12 | No telemetry freshness check | **FIXED** | `policy.telemetry_max_age_s`; `test_stale_telemetry_blocks_startup` |
| 13 | No cold-start charging | **FIXED** | `admission.py:admit_startup()`; `test_cold_start_charges_residency` |
| 14 | No installed-flag support validation | **FIXED** | `server_config.py:verify_server_support`; `test_missing_flag_fails_closed` |
| 15 | No reservation release on failure paths | **FIXED** | `try/finally` in `protected.py:request()`; `test_reservation_released_after_forward_failure` |

**Two limitations remain genuinely open and both require a guarded inference
run to close: #4 (transient prefill peak) and #8 (safe context ceiling).**
Neither can be resolved by any amount of unit testing, and inventing either
from mock results is exactly what this milestone forbids.

---

## 8. Configuration example

`services/resource_controller/policy.example.json`:

```json
{
  "max_context_tokens": 16384,
  "max_input_tokens": 12288,
  "reserved_output_tokens": 4096,
  "min_available_memory_bytes": 6442450944,
  "max_retained_cache_bytes": 2147483648,
  "transient_reserve_bytes": 3221225472,
  "request_timeout_s": 300,
  "cooldown_after_abnormal_exit_s": 120,
  "max_concurrent_requests": 1,
  "allow_context_escalation": false
}
```

The context limits are **deliberately conservative** because the safe ceiling is
not established. They must not be raised without a guarded, admitted
measurement.

---

## 9. Rollback

```bash
# Remove all FORGE-003 toolchain, runtimes and model data
rm -rf "$FORGEONE_HOME/storage/tools" \
       "$FORGEONE_HOME/storage/bakeoff" \
       "$FORGEONE_HOME/storage/cache"

# Remove disposable fixtures and worktrees
rm -rf /tmp/forge002-fixture /tmp/forge003-hermes /tmp/forge003-openhands
```

**Nothing global was modified, so nothing global needs reverting:**

- System Python 3.9.6 — untouched (`python3 --version` still `3.9.6`)
- No shell rc file modified (`grep storage/tools ~/.zshrc` → 0 matches)
- No `PATH` change
- No Homebrew, no `sudo`, no launchd agent, no system preference
- One transient artefact was created and **removed**: uv wrote a
  `~/.local/bin/python3.12` symlink; it was deleted and the directory removed.
  Set `UV_PYTHON_BIN_DIR` inside `storage/` to prevent recreation.
- The Resource Controller source is ordinary repository code; revert it with Git.

---

## 10. Bake-off readiness

**The protected smoke test has now been executed and PASSED.**
See [FORGE-003-protected-smoke-test.md](FORGE-003-protected-smoke-test.md).

### 10.1 Bake-off entry gate — SATISFIED

The gate required a completed protected smoke test with controlled startup,
successful admission, a real structured tool-call round trip, an active
watchdog, recorded telemetry, clean shutdown and no unresolved memory-pressure
event. All were observed:

| Gate requirement | Evidence |
|---|---|
| Controlled model startup | cold start **ADMITTED**, argv carries explicit cache budgets, loopback only |
| Successful admission | 2 requests **ADMITTED** before forwarding |
| Structured tool-call round trip | `get_test_summary` with valid JSON args → local execution → result → final answer |
| Watchdog active | separate process, **21 samples**, **0 aborts**, ready before load |
| Telemetry recorded | `storage/runs/smoke-telemetry.jsonl`, `smoke-admission.jsonl` |
| Clean process shutdown | owned PIDs `[]`, reservations `0`, port 8082 free |
| No unresolved memory-pressure event | swap **flat at 4.73 GiB** (all 21 samples); page-outs **+26 cumulative** (~4.3/s average, one 79/s burst at model load, vs a 5,000/s default threshold); available **rose** to 10.02 GiB |

**The Hermes/OpenHands entry gate is now satisfied.** The bake-off itself
remains **NOT RUN** — that is a separate, separately-approved milestone.

The hardening pass closed the bypasses that made an earlier smoke test unsafe:
launch and forwarding are now gated, concurrency is reserved atomically, the
cache ceiling is injected and its enforcement path verified, telemetry must be
fresh, the port must be proven free, and the watchdog must be live *before* the
heavy process starts.

Two prerequisites for the **bake-off** remain, both requiring a separately
approved, guarded run:

1. Measure the transient prefill working set (design criterion 9).
2. Establish a safe context ceiling under a bounded cache (criterion 10).

Both are cheap to obtain **once the controller is gating the run** — which is
precisely the point of building it first.

### 10.2 Honest scope of what is proven

| Claim | Proven? |
|---|---|
| Admission logic rejects over-budget requests | **Yes** — 156 unit tests |
| Token counting matches the server's prompt construction | **Yes** — real tokenizer, 19 tests |
| The controller builds a command line with an enforced cache ceiling | **Yes** — static validation |
| The controller refuses `--seed` (unbounded) paths | **Yes** — static validation |
| Startup and forwarding cannot bypass admission | **Yes** — unit tests + real session |
| The controller has gated a real inference request | **Yes** — 2 requests, protected smoke test |
| The protected path works under the observed conditions | **Yes** — smoke test PASS |
| A safe maximum context | **No** — UNKNOWN; only 2048 was exercised |
| 8K/16K/32K/64K compatibility | **No** — nothing here is evidence for any of them |
| General peak prefill memory requirement | **No** — still unmeasured |
| Hermes or OpenHands compatibility | **No** — no agent runtime has been run |
| The system cannot OOM | **No** — no such claim is made |

**No production readiness is claimed and no guarantee of OOM prevention is
offered.** The watchdog is best-effort; the controller narrows risk, it does
not eliminate it.

**Is a single fixture run meaningful?** A successful fixture run would be a
**functional smoke test, not evidence of production-level agent reliability.**
One small deterministic bug-fix task cannot characterise an agent runtime. The
evaluation dataset in the blueprint (20–30 representative tasks) remains the
real measure.

---

## 11. First protected agent coding loop — OpenHands

**Result: `BLOCKED_CONTEXT`.** The session did not start and no model was
loaded. The context-footprint gate caught a genuine incompatibility before any
resource was consumed.

### 11.1 Entry gate — PASSED

| Requirement | Evidence |
|---|---|
| Protected smoke test present with PASS evidence | `FORGE-003-protected-smoke-test.md`; `storage/runs/smoke-result.json` |
| Cold-start admission | `ADMITTED`, `model_resident_charged: false` |
| Structured tool-call round trip | `get_test_summary`, valid JSON args, correct continuation |
| Watchdog active | separate process, 21 samples, 0 aborts |
| Persistent telemetry | `storage/runs/smoke-telemetry.jsonl` |
| Clean shutdown | owned PIDs `[]`, reservations `0` |
| No unresolved memory-pressure event | swap flat 4.73 GiB; page-outs +26 |
| No ForgeOne model process running | 0 mlx processes |
| Ports available | 8082 free (netstat + bind probe) |

### 11.2 Smoke-test evidence corrections

Two claims in the smoke-test report were corrected — one of them wrong.

**(A) Page-outs — the report was WRONG.** It stated the rate was "0.0/s
throughout". The precise accounting:

| Measurement | Value |
|---|---|
| Preflight window (12 s) | 0 page-outs → 0.0/s |
| **Cumulative delta across the session** | **+26 page-outs** |
| Session elapsed | ~6.0 s |
| Average rate | **~4.3/s** |
| Watchdog **sampled** rate (21 samples @ 0.25 s) | 0.0/s in 20 samples, **79.06/s peak in 1** |
| Abort threshold (design default / configured) | 5,000/s / 20,000/s |

All 26 occurred in one ~0.33 s window during model load. The sampled rate reads
0.0/s elsewhere because page-outs did not change *within* those windows. Both
statements are true; reporting only the sampled zero was misleading. The peak
was ~63× below the design-default threshold.

**(B) Shutdown semantics — clarified.** `CANCELLED` was the **cleanup** label
for a graceful, intentional post-success shutdown — **not** a cancelled
inference. Recorded independently: **inference outcome = SUCCESS**, **cleanup
outcome = CANCELLED**.

### 11.3 Runtime compatibility

| Runtime | Installed | Version | Python | Footprint |
|---|---|---|---|---|
| OpenHands SDK | **Yes** (this milestone) | 1.49.5 (`sdk`, `tools`, `workspace`) | ≥ 3.12 | **511 MB** venv |
| Hermes Agent | **No** | — | — | — |
| MLX-LM | Yes (prior) | 0.31.3 (mlx 0.32.2) | 3.12.14 | — |

OpenHands installation was **within the previously approved isolated scope
(A2)**. Source: PyPI. Target: `storage/bakeoff/openhands-venv` (Git-ignored).
Rollback: `rm -rf storage/bakeoff/openhands-venv`. No global change.

The SDK exposes `base_url` on `LLM` (`llm.py:231`), so it can be pointed at a
local OpenAI-compatible endpoint.

### 11.4 Protected gateway integration

**Gateway integration: PASS.**

An agent SDK must never be pointed straight at an unprotected model server.
`services/resource_controller/gateway.py` now provides the only supported
endpoint, routing every completion through `ProtectedServer.request()`:

```text
agent SDK → ProtectedGateway → atomic reservation → tokenizer admission
          → supervised MLX-LM server → response → reservation release
```

Enforced: **loopback-only bind**; `max_tokens` above the reserved output budget
is **rejected, never silently clamped**; `stream=True` refused (not implemented
on the protected path); single-threaded serving to match
`max_concurrent_requests = 1`; a rejected request returns a structured error and
is **not forwarded**.

**15 new tests, all passing, no model required** (fake transport + synthetic
telemetry). Verified: valid request forwarded once; streaming refused; over-budget
`max_tokens` refused without clamping; `REJECTED_CONTEXT` → 400, `REJECTED_MEMORY`
→ 503, `REJECTED_CONCURRENCY` → 429, each with **zero** forwarder calls;
non-loopback bind refused; bad JSON → 400; unknown path → 404.

### 11.5 Exact initial prompt / tool-schema footprint — **the blocker**

Measured with the **real cached Qwen tokenizer and complete chat template**,
before loading any weights:

| Component | Tokens |
|---|---|
| OpenHands system prompt alone (11,017 chars, rendered from the SDK's own default preset) | **2,318** |
| Task message | 51 |
| **System + task, no tools** | **2,366** |
| Tool schemas | *not included — see below* |

| Approved budget | Tokens |
|---|---|
| Total active context | **2,048** |
| Max input per request | **512** |
| Reserved output | 128 |

**The initial prompt is 4.6× over the input budget and already exceeds the
entire 2,048-token context — before a single tool schema is added.**

This is a **lower bound**: `Tool.to_openai_tool()` (tool.py:744) returned no
schemas in my extraction, so the real footprint is *larger* than 2,366. The
conclusion is unaffected.

Per the instruction — *"If the real initial prompt exceeds this budget, STOP
BEFORE MODEL STARTUP. Report BLOCKED_CONTEXT."* — the session was **not
started**. No context was increased, no instruction was truncated, no security
control was removed, and no false context window was advertised.

### 11.6 Fixture

`/tmp/forge002-fixture` @ `9157d02`, verified intact, **2 files**:

**EXPECTED FAILING BASELINE** — `python3 -m unittest discover -s tests -v`:
**6 tests, 4 passed, 2 failed, exit code 1**. The defect: `tier_price()` falls
through to `return 10.00` for `units < 1` instead of raising `ValueError` as its
docstring contract requires.

### 11.7 Result classification

| Dimension | Status |
|---|---|
| Gateway integration | **PASS** — 15 tests, real listener on loopback |
| OpenHands initialization | **NOT_RUN** — blocked before startup |
| Agent coding task | **BLOCKED_CONTEXT** |
| Test execution (fixture) | 6 tests, 4 passed, 2 failed, **exit 1** (EXPECTED FAILING BASELINE) |
| Hermes local compatibility | **BLOCKED** — not installed, and the same ceiling applies |
| GUI | **NOT_TESTED** |

**A single successful fixture run would be a functional smoke test, not
production reliability evidence. Nothing here is a completed comparative
bake-off.**

### 11.8 Payload compatibility follow-up — superseding numbers

The 2,366-token figure above was a **lower bound**; tool schemas were excluded
because of a harness defect. Full capture is in
[FORGE-003-agent-payload-compatibility.md](FORGE-003-agent-payload-compatibility.md).

| Measurement | Tokens |
|---|---|
| Stock system prompt | 2,318 |
| **Stock total input (system + task + 3 tool schemas)** | **5,600** |
| `FORGEONE_COMPACT_V1` total input (2 tools) | **2,212** |
| Compact, terminal-only diagnostic | 1,303 |
| **Minimum measured context for the compact workflow** | **2,446** |

**Tool schemas are the dominant cost** (3,221 tokens for three tools — more than
the entire system prompt).

**Gateway compatibility: PASS.** The SDK defaults to `stream=False` and
`requires_streaming` is subscription-only, so **no streaming adapter is
needed**. Three defaults must be overridden — and `num_retries=5` is a genuine
safety conflict with the no-auto-retry rule; `validate_llm_kwargs()` now fails
closed unless `api_mode="chat"`, `stream=False`, `num_retries=0`, and explicit
`timeout` / `max_output_tokens` are supplied.

**The 2K budget does not support the compact coding workflow** (needs 2,446).
Recommended next context: **4,096**, to be established by a guarded incremental
measurement — not assumed.

### 11.9 First real coding attempt @ 4K — **BLOCKED at the safety gate**

The owner approved one guarded session at a **4,096-token** context. Phases 0–2
passed. **Phase 3 stopped it before any model was loaded.**

**What passed first:**

| Check | Result |
|---|---|
| Workspace / branch / HEAD / clean tree | MATCH `ec2d394` |
| No ForgeOne model process · ports 8082 & 8084 free | PASS |
| Token budget vs the 4K approval | compact initial **2,212 ≤ 3,072**; +128 = **2,340 ≤ 4,096**; max workflow **2,446 ≤ 4,096** — **FITS** |
| Tool schemas present in the real SDK request | `["terminal", "file_editor"]` |
| SDK configuration dry-run (**no LLM call**) | `api_mode=chat`, `stream=False`, `num_retries=0`, **`uses_responses_api=False`**; `Agent` and `Conversation` constructed |

**What blocked it — the workstation safety gate:**

| Gate | Required | Observed | |
|---|---|---|---|
| Available memory | ≥ 8.00 GiB | **7.65 GiB** | **FAIL** |
| Projected post-admission reserve | ≥ 4.00 GiB | **2.95 GiB** | **FAIL** |
| Ongoing swap growth (12 s) | not concerning | **+0.000 GiB** | PASS |
| Page-out trend | not concerning | **0.0/s** | PASS |

**Sustained, not transient:** 9 samples over 45 s ranged 7.52–7.82 GiB; **0 of 9**
reached 8 GiB. With a 4.70 GiB cold-start estimate, the gate needs **≥ 8.70 GiB**
available — the machine is roughly **1 GiB short**.

**The gate failed on *headroom*, not on *pressure*:** swap was flat at 3.22 GiB
and page-outs were zero throughout. This is not an unwell machine; it is a busy
one.

**Correct action taken:** the model was **not started**, owner applications were
**not closed**, and **no unrelated process was terminated** to force the test
through — exactly as the instruction required.

**Result:** gateway **NOT_RUN** · OpenHands SDK execution **NOT_RUN** · coding
task **BLOCKED** · fixture tests **NOT_RUN** · resource safety **gate PASSED (it
did its job)** · GUI **NOT_TESTED** · Hermes **NOT_RUN**.

**No agent completed a coding task.**

## 12. Next steps

| # | Step | Gate |
|---|---|---|
| 1 | Validate `HuggingFaceTokenCounter` against the real tokenizer | Smoke test |
| 2 | Guarded smoke test: controller gates a **single** small inference request | **Separate owner approval** |
| 3 | Measure `transient_reserve_bytes` empirically under the controller | Same |
| 4 | Establish the safe context ceiling | Same |
| 5 | Resume Phase 3 — install agent runtimes | After 2–4 |
| 6 | Resume Phase 4 — the actual bake-off | After 5 |

**Do not resume the bake-off until the controller has gated at least one real
inference request successfully.** That is the whole point of this milestone's
redirection.
