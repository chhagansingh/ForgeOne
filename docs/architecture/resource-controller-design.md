# ForgeOne Resource Controller — Design

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Status:** **Implemented and hardened** — core controller, admission,
  supervisor, watchdog, protected startup/request boundary, cache-budget
  enforcement and real-tokenizer validation, with **156 passing unit tests**.
  **Not yet exercised against a real model server** (that is the
  separately-approved smoke test).
- **Trigger:** [FORGE-003 P0 memory incident](../reports/FORGE-003-P0-memory-incident.md)
- **Scope:** Any ForgeOne workload that allocates significant memory — model
  servers, agent runtimes, media pipelines, ForgeStream experiments

## 0. Implementation status

| Capability | Design | Implemented | Unit-tested | Exercised against a real server |
|---|---|---|---|---|
| Explicit resource policy (fail-closed) | yes | yes | **yes** | no |
| Admission control (12-step ordered gate) | yes | yes | **yes** | no |
| Telemetry freshness gate | yes | yes | **yes** | no |
| Token counting via injected tokenizer | yes | yes | **yes — real tokenizer** | no |
| KV estimation from model metadata | yes | yes | **yes** | no |
| Process supervisor (ownership, PID reuse) | yes | yes | **yes** | partially (trivial `sleep`) |
| Loopback bind enforcement | yes | yes | **yes** | yes |
| Independent watchdog + thresholds | yes | yes | **yes** | no |
| Unbuffered JSONL telemetry | yes | yes | **yes** | no |
| Cooldown after abnormal exit | yes | yes | **yes** | no |
| **Atomic single-request reservation** | added | yes | **yes** | no |
| **Protected startup gate** | added | yes | **yes** | no |
| **Protected request gate (no bypass)** | added | yes | **yes** | no |
| **Cache-budget enforcement adapter** | added | yes | **yes** (static) | no |
| **Port probe (netstat + bind)** | added | yes | **yes** | yes (real sockets) |
| **Unbounded serving-path rejection** | added | yes | **yes** | no |

**Code:** `services/resource_controller/` — Python standard library only, no
third-party dependencies, compatible with Python 3.9+ (verified on 3.9.6 and
3.12.14).

**Test evidence:** 156 tests, exit code 0 on both interpreters. See
[FORGE-003 bake-off report §6](../reports/FORGE-003-runtime-bakeoff.md).

### 0.2 Hardening pass — what the audit found and closed

A read-only audit of the execution path (`request → tokenization → admission →
reservation → protected startup → watchdog → forwarding → cleanup`) found ten
gaps. All ten are now closed in code:

| Gap found | Evidence | Resolution |
|---|---|---|
| `start_supervised` not gated by admission | `controller.py:95–125` | `protected.py:ProtectedServer.start()` |
| No cold-start charge | `controller.py:114` | `admission.py:admit_startup()` |
| No port availability check | absence | `ports.py:RealPortProbe` (netstat + bind) |
| Concurrency caller-supplied → race | `admission.py:33,141` | `reservation.py:ReservationManager` |
| No telemetry freshness check | `telemetry.py:34` | `policy.telemetry_max_age_s` + `admission.py` step 3 |
| No cache-budget adapter | absence | `server_config.py:CacheBudget` |
| No request-forwarding path | absence | `protected.py:ProtectedServer.request()` |
| Tokenizer never validated for real | `tokenization.py:87–121` | `tests/.../test_tokenizer_real.py` |
| No installed-flag support validation | absence | `server_config.py:verify_server_support` |
| No reservation release path | absence | `reservation.py` + `try/finally` in `protected.py` |

### 0.3 mlx-lm 0.31.3 serving paths (source-verified, no model loaded)

| Path | Cache byte ceiling enforced? | Evidence |
|---|---|---|
| Batched (`_generate`, default) | **Yes, but only if `--prompt-cache-bytes` is set** | `server.py:795–798` |
| Batched, flag unset (the default) | **No** | default is `None`, `server.py:1877–1881` |
| `--seed` set → non-batched | **No** | `server.py:685–686`, `_serve_single` at `:922` performs no `trim_to` |

**`--seed` is therefore rejected by `ProtectedServerConfig`** unless unbounded
serving is explicitly opted into — the controller will not silently build a
command line whose cache ceiling does not apply.

### 0.1 Corrections applied during implementation

Three design assumptions did not survive contact with the code and were
corrected rather than worked around:

1. **Residency double-counting.** The design charged model weights to every
   request. That is wrong when the model is already loaded — its footprint is
   already reflected in `available_bytes`. `Estimate.model_resident` now
   controls whether weights and overhead are charged, so starting a server
   (non-resident) costs more than serving a request through it.
2. **`Pages free` is not the signal.** Empirically, a healthy macOS host on this
   machine reports ~0.25 GB free while holding 9.7 GB of reclaimable inactive
   and speculative pages. Admission and the watchdog use
   `available = free + inactive + speculative`.
3. **Configuration is JSON, not YAML.** YAML would require a third-party
   dependency, which contradicts "small and runtime-independent". JSON is
   stdlib-parseable. See §10.

## 1. Purpose

ForgeOne runs on the owner's **primary workstation**, shared with IDEs, browsers
and interactive work. The P0 incident proved that a single unguarded workload
can render that machine unusable.

The Resource Controller is the layer that makes this structurally impossible
rather than a matter of operator discipline. Its job:

> **No ForgeOne workload may start unless its peak memory requirement has been
> estimated, compared against a reserved budget, and a live watchdog can abort
> it before the host degrades.**

This is a **host-protection** component, not a performance optimiser.

## 2. Design principles

| # | Principle | Rationale |
|---|---|---|
| 1 | **Estimate before allocate** | The incident's core failure. If peak memory is not estimated, the workload does not run |
| 2 | **Reserve the host first** | IDEs and the OS are not negotiable. Budget is computed *after* subtracting a reserve |
| 3 | **Ceilings are explicit, never defaulted** | MLX-LM's prompt cache grew unbounded because `--prompt-cache-bytes` was unset |
| 4 | **Abort beats degrade** | Killing one workload is always preferable to stalling the machine |
| 5 | **Evidence survives failure** | Results are flushed incrementally, so a killed run still yields data |
| 6 | **Escalate stepwise, stop on first pressure** | Never jump to a large configuration |
| 7 | **Fail closed** | Unknown memory requirement ⇒ refuse to start |
| 8 | **The host is production** | Treat the workstation as a system that must stay usable |

## 3. Architecture

```text
                    ┌──────────────────────────────────────┐
   workload request │        RESOURCE CONTROLLER           │
   ────────────────▶│                                      │
                    │  1. Classify workload + host         │
                    │  2. Estimate peak memory             │
                    │  3. Admission control  ──refuse──▶   │  (fail closed)
                    │  4. Apply hard ceilings              │
                    │  5. Start under supervision          │
                    │  6. Watchdog  ──breach──▶ abort      │
                    │  7. Emit evidence incrementally      │
                    └──────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            model servers                    agent runtimes
            (MLX-LM, llama.cpp)              (Hermes, OpenHands)
```

### 3.1 Components

| Component | Responsibility |
|---|---|
| **Host Profiler** | Reads total RAM, free memory, inactive/reclaimable pages, swap usage, page-out rate |
| **Workload Classifier** | Maps a workload to a known profile (model server, agent runtime, media job, experiment) |
| **Budget Calculator** | Computes the usable budget from host state minus the reserve |
| **Estimator** | Produces a peak-memory estimate per workload profile |
| **Admission Gate** | Approves or refuses based on estimate vs budget |
| **Ceiling Injector** | Sets mandatory flags (e.g. `--prompt-cache-bytes`) on the launched process |
| **Watchdog** | Samples RSS, system free memory and swap; aborts on breach |
| **Evidence Writer** | Appends results incrementally, flushed, so partial runs still yield data |
| **Incident Recorder** | Writes a post-mortem record on any abort |

## 4. The memory budget model

### 4.1 Budget

```text
total_ram            = sysctl hw.memsize
reserve              = max(RESERVE_ABS, RESERVE_PCT × total_ram)
usable_budget        = (free + reclaimable_inactive) − reserve
admissible_peak      = usable_budget × SAFETY_FACTOR
```

**Proposed defaults for this host (24 GB):**

| Parameter | Value | Rationale |
|---|---|---|
| `RESERVE_ABS` | 6 GB | Keeps IDEs, browser and the OS responsive |
| `RESERVE_PCT` | 25 % | Scales to other machines |
| `SAFETY_FACTOR` | 0.70 | Absorbs estimation error — the incident showed real usage exceeded theory |
| Minimum free floor | 4 GB | Hard abort floor, independent of budget |

> On this host: reserve = 6 GB, and with ~12 GB free the admissible peak is
> ≈ 4.2 GB. **That would have refused the 32K test outright.** That is the
> intended behaviour.

### 4.2 Peak estimate for a model server

```text
peak = weights
     + retained_cache_cap          # bounded by --prompt-cache-bytes
     + new_sequence_kv(context)
     + prefill_working_set
     + runtime_overhead
```

```text
new_sequence_kv(context) =
    2 × num_hidden_layers × num_key_value_heads × head_dim
      × bytes_per_element × context × concurrent_sequences
```

For the FORGE-003 checkpoint (36 layers, 8 KV heads, 128 head_dim, fp16):

| Term | 8K ctx | 16K ctx | 32K ctx |
|---|---|---|---|
| `new_sequence_kv` | 1.12 GB | 2.25 GB | 4.50 GB |
| `weights` (measured) | ~2.2 GB | ~2.2 GB | ~2.2 GB |
| `runtime_overhead` (measured) | ~1.5 GB | ~1.5 GB | ~1.5 GB |
| `prefill_working_set` | **to be measured** | — | — |
| **Total (excl. retained cache)** | **~4.8 GB** | ~6.0 GB | ~8.2 GB |

`[UNT]` `prefill_working_set` is **not yet measured** — this is the term that
most likely caused the incident, since transient attention buffers during
prefill can exceed the steady-state cache. **It must be measured before the
controller is considered trustworthy.** Until then the estimator must apply a
conservative multiplier and the controller must remain in refuse-by-default mode
for contexts above a small, measured-safe value.

### 4.3 Cumulative-request accounting

The incident was caused by **cache retention across requests**, so the
estimator must track cumulative state, not just the current request:

```text
retained_cache = Σ kv_size(sequence) for sequences currently held
```

`retained_cache` is **unbounded unless `--prompt-cache-bytes` is set**. The
Ceiling Injector therefore treats that flag as **mandatory**, with a proposed
default of **2 GB**.

## 5. Hard ceilings

Mandatory flags the controller injects — a workload that cannot accept them is
not admitted:

| Workload | Mandatory ceiling | Proposed default |
|---|---|---|
| MLX-LM server | `--prompt-cache-bytes` | 2 GB |
| MLX-LM server | `--prompt-cache-size` | 8 sequences |
| MLX-LM server | `--host` | `127.0.0.1` (never `0.0.0.0`) |
| Any server | port | must be verified free via `netstat`, not `lsof` alone |

> **Port-verification note.** The incident surfaced a second, unrelated defect:
> `lsof` without root does **not** enumerate other users' sockets. Port 8081
> appeared free by `lsof` but was bound by an invisible root-owned service.
> The controller must verify ports with `netstat -an` **plus** an actual
> `bind()` probe.

## 6. Runtime watchdog

Sampled every **250 ms**. On any breach the controller sends `SIGTERM`, waits
2 s, then `SIGKILL`, then records an incident.

| Signal | Warn | **Abort** |
|---|---|---|
| Workload RSS | > 80 % of estimate | **> 120 % of estimate** |
| System free memory | < 6 GB | **< 4 GB** |
| Swap used | > 2 GB | **> 4 GB** |
| Page-out rate | > 1,000 pages/s | **> 10,000 pages/s** |
| Time to first token | > 60 s | **> 180 s** |

The incident breached the abort thresholds on **free memory** and **swap** and
had no watchdog to notice.

## 7. Evidence capture

The incident produced **zero data** because output was buffered through a pipe.

**Rules:**

1. Every measurement is appended to a JSONL file and **flushed immediately**.
2. Runner processes are launched with **unbuffered** output (`python -u`).
3. Never pipe a long-running measurement through a filter that buffers
   (`grep`, `head`) before the results are persisted.
4. A run that is aborted must still have written every measurement completed
   before the abort.
5. The evidence file records: timestamp, request parameters, **actual** token
   count (not the intended one), latency, RSS before/after, cache size.

> Rule 5 exists because the incident's script reported *intended* targets
> (8,192 / 32,768 / 65,536) while the **actual** prompts were
> 8,011 / 24,611 / 32,611. The discrepancy was only discovered in the server
> log afterwards. Measure what happened, not what was planned.

## 8. Stepwise escalation protocol

The incident jumped straight to large contexts. The controller enforces:

```text
for size in [2K, 4K, 8K, 16K, 32K]:
    if not admitted(size): STOP - do not attempt this size or larger
    run(size)
    if aborted or warned: STOP - do not escalate
    if free_memory_drop > 50% of that step's estimate: STOP
    persist(measurement)
```

**Stop on first pressure.** Escalation never continues past a warning.

## 9. Host classification

| Class | Definition | Policy |
|---|---|---|
| **Workstation** (this host) | Interactive machine shared with IDEs | Reserve 6 GB; abort aggressively; heavy work requires explicit owner approval |
| **Dedicated worker** | Machine provisioned for ForgeOne | Smaller reserve; higher ceilings permitted |
| **CI runner** | Ephemeral, isolated | Enforce limits at the container level |

The controller reads the class from configuration; it never infers it.

## 10. Configuration

**JSON, deliberately.** YAML would require a third-party dependency
(`PyYAML`), contradicting the "small, runtime-independent" requirement. JSON is
stdlib-parseable, so the controller has **zero dependencies**.

Two example files ship with the implementation:

| File | Purpose |
|---|---|
| [`policy.example.json`](../../services/resource_controller/policy.example.json) | The resource budget. Copy to a Git-ignored path and adjust. |
| [`observed-model-metadata.example.json`](../../services/resource_controller/observed-model-metadata.example.json) | Observed checkpoint geometry (`config.json` values, KV bytes/token, measured footprint). |

Policy shape (`ResourcePolicy.from_mapping`):

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

Watchdog thresholds are a separate structure
(`WatchdogThresholds`): `min_available_bytes`, `max_swap_used_bytes`,
`max_pageout_rate`, `sample_interval_s`, and optional `max_owned_rss_bytes` and
`max_wall_clock_s`.

**All eight core policy fields are required.** A missing, non-numeric or
non-positive field raises `PolicyError` and the workload does not run. There is
no fallback default — an unknown budget is treated as an unsafe budget.

## 11. Failure modes and responses

| Failure | Response |
|---|---|
| Estimate cannot be produced | **Refuse to start** (fail closed) |
| Requested size not yet measured safe | Refuse, or run only under explicit owner approval |
| Watchdog breach | SIGTERM → 2 s → SIGKILL → incident record |
| Watchdog itself dies | Workload must not outlive it — controller runs as the supervisor; no detach without a supervisor |
| Port appears free but bind fails | Treat as occupied; verify with `netstat` + `bind()` probe; never fall back silently to a different port without recording it |
| Evidence file unwritable | **Refuse to start** — a run that cannot produce evidence is not permitted |

## 12. Integration points

| Point | Contract |
|---|---|
| **Control plane** (M2+) | All workload launches route through the controller |
| **Agent runtimes** (M3+) | An agent's tool execution inherits the controller's budget; an agent may not spawn an unbounded child |
| **Model registry** (M2) | Each entry carries measured `weights`, `runtime_overhead` and `kv_per_token`, so estimates use real numbers |
| **ForgeStream** | Independent, but must use the controller — its experiments are the most resource-hungry planned |
| **Evidence store** | JSONL records land in `storage/runs/`, referenced by the evidence bundle |

## 13. What this design does NOT do

- It does not make large contexts possible on 24 GB. It makes their **refusal**
  automatic and their **failure** survivable.
- It is not a scheduler or a cluster resource manager.
- It does not replace the M2 model gateway; it constrains it.
- It does not remove the need for owner approval before heavy workloads.

## 14. Acceptance criteria

The controller is not trustworthy until all of these are demonstrated:

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Refuses a workload whose estimate exceeds the budget | **PASS** | `test_kv_estimate_exceeds_budget`, `test_non_resident_model_can_be_rejected…` |
| 2 | Refuses when no estimate can be produced | **PASS** | `test_missing_tokenizer`, `test_missing_model_metadata`, `test_missing_critical_policy`, `test_unavailable_telemetry_fails_closed` |
| 3 | Aborts on a simulated available-memory breach | **PASS** | `test_critical_memory_pressure_aborts` |
| 4 | Aborts on a simulated swap breach | **PASS** | `test_swap_threshold_aborts`, `test_rapid_swap_growth_via_pageout_rate_aborts` |
| 5 | Enforces `--prompt-cache-bytes` as mandatory | **PASS** | `server_config.py:build_argv()` always emits it; `test_correct_argv_is_built`, `test_explicit_byte_budget_is_required` |
| 6 | Produces a complete evidence file for an **aborted** run | **PASS** | `test_aborts_and_persists_telemetry`, `test_jsonl_writer_flushes_to_disk` |
| 7 | Verifies ports with `netstat` + `bind()` probe | **PASS** | `ports.py:RealPortProbe`; `test_occupied_port_detected_by_bind`, `test_netstat_unavailable_fails_closed` |
| 8 | Records an incident automatically on abort | **PASS** | `test_abort_triggers_cooldown`, watchdog writes an `abort` record |
| 9 | `prefill_working_set` measured empirically | **NOT_RUN** | Requires a guarded inference run |
| 10 | Safe context ceiling established under a bounded cache | **NOT_RUN** | Requires a guarded inference run |
| 11 | Rejects serving paths where the cache ceiling does not apply | **PASS** | `server_config.py:_reject_unbounded_path`; `test_unbounded_serving_path_rejected` |
| 12 | Startup cannot bypass admission | **PASS** | `protected.py:start()`; `test_missing_policy_blocks_startup` |
| 13 | Forwarding cannot bypass admission | **PASS** | `protected.py:request()`; `test_rejected_request_is_never_forwarded` |
| 14 | Concurrency is reserved atomically | **PASS** | `reservation.py`; `test_races_are_atomic` |
| 15 | Telemetry freshness is enforced | **PASS** | `test_stale_telemetry_blocks_startup` |
| 16 | Cold start is charged residency | **PASS** | `test_cold_start_charges_residency` |
| 17 | Installed server flag support is verified | **PASS** | `verify_server_support`; `test_missing_flag_fails_closed` |

**Criteria 9 and 10 remain prerequisites for resuming the FORGE-003 bake-off.**
They cannot be satisfied by any amount of unit testing — they require a
guarded, separately-approved inference run.

## 15. Open questions

| # | Question |
|---|---|
| 1 | What is the true `prefill_working_set` multiplier for MLX-LM on this hardware? |
| 2 | Should the controller live in the control plane, or as a standalone supervisor usable today? |
| 3 | Is 4 GB the right hard free-memory floor, or should it scale with host RAM? |
| 4 | Should heavy workloads require an explicit owner acknowledgement each time, or a standing approval with limits? |
| 5 | Given 24 GB shared with IDEs, is local inference the right architecture at all, or should the bake-off use a hosted endpoint? |
