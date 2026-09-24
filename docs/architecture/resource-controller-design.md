# ForgeOne Resource Controller — Design

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Status:** **Design only. Not implemented.**
- **Trigger:** [FORGE-003 P0 memory incident](../reports/FORGE-003-P0-memory-incident.md)
- **Scope:** Any ForgeOne workload that allocates significant memory — model
  servers, agent runtimes, media pipelines, ForgeStream experiments

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

## 10. Configuration schema

```yaml
# storage/config/resource-controller.yaml  (Git-ignored)
host:
  class: workstation
  total_ram_gb: 24
  reserve_abs_gb: 6
  reserve_pct: 0.25
  safety_factor: 0.70
  min_free_gb: 4

watchdog:
  sample_interval_ms: 250
  rss_warn_ratio: 0.80
  rss_abort_ratio: 1.20
  swap_warn_gb: 2
  swap_abort_gb: 4
  ttft_warn_s: 60
  ttft_abort_s: 180

ceilings:
  model_server:
    prompt_cache_bytes: 2_000_000_000
    prompt_cache_size: 8
    bind: 127.0.0.1

evidence:
  dir: storage/runs
  flush_immediately: true

escalation:
  sizes: [2048, 4096, 8192, 16384, 32768]
  stop_on_first_pressure: true
```

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

| # | Criterion | Status |
|---|---|---|
| 1 | Refuses a workload whose estimate exceeds the budget | NOT_IMPLEMENTED |
| 2 | Refuses when no estimate can be produced | NOT_IMPLEMENTED |
| 3 | Aborts on a simulated free-memory breach | NOT_IMPLEMENTED |
| 4 | Aborts on a simulated swap breach | NOT_IMPLEMENTED |
| 5 | Injects `--prompt-cache-bytes` and rejects a workload that cannot accept it | NOT_IMPLEMENTED |
| 6 | Produces a complete evidence file for an **aborted** run | NOT_IMPLEMENTED |
| 7 | Verifies ports with `netstat` + `bind()` probe | NOT_IMPLEMENTED |
| 8 | Records an incident automatically on abort | NOT_IMPLEMENTED |
| 9 | `prefill_working_set` measured empirically for the checkpoint in use | NOT_RUN |
| 10 | Safe context ceiling established under a bounded cache | NOT_RUN |

**Criteria 9 and 10 are prerequisites for resuming the FORGE-003 bake-off.**

## 15. Open questions

| # | Question |
|---|---|
| 1 | What is the true `prefill_working_set` multiplier for MLX-LM on this hardware? |
| 2 | Should the controller live in the control plane, or as a standalone supervisor usable today? |
| 3 | Is 4 GB the right hard free-memory floor, or should it scale with host RAM? |
| 4 | Should heavy workloads require an explicit owner acknowledgement each time, or a standing approval with limits? |
| 5 | Given 24 GB shared with IDEs, is local inference the right architecture at all, or should the bake-off use a hosted endpoint? |
