# FORGE-003 — Real-World Parallel Workload Architecture

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-25
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Implementation code changed:** **No.** Report only.
- **Weights loaded:** **No.** **Inference run:** **No.**

---

## 0. The correction this document makes

Every K2 attempt so far has been judged against **Profile D** — an *isolated*
runtime validation on a machine with unrelated applications closed. That is a
**compatibility test**, and it is being mistaken for the product requirement.

The product requirement is **Profile A/B**: ForgeOne running **while the owner
uses their normal development tools**. A model that only works after closing the
entire development environment is **not production-ready for this use case**.

**This report re-anchors the strategy on that requirement.**

## 1. Actual daily-use requirement

The owner runs a **24 GB unified-memory Apple Silicon Mac** and needs:

| Profile | Concurrent workload |
|---|---|
| **A — Normal coding** | IDE + browser + ForgeOne + local inference |
| **B — iOS development** | IDE + **Xcode** + **iOS Simulator** + ForgeOne + inference |
| **C — Heavy generation** | Coding tools + **one** scheduled GPU-intensive image/video task |
| **D — Isolated validation** | Headless model load, unrelated apps closed |

**Profile D is a compatibility test only. It must never substitute for A or B.**

## 2. Current measured constraints

| Measurement | Value | Kind |
|---|---|---|
| Available during normal development | **~7.3 GiB** | **measured, just now** |
| Available after closing Brave + Simulator | **9.82 GiB sustained** | **measured** |
| File-backed (truly reclaimable) | 4.59 GiB | **measured** |
| Compressor occupied | 5.94 GiB | **measured** |
| macOS pressure verdict | 61 % free | **measured** |
| Swap | 1,890 MB of 3,072 MB | **measured** |
| Qwen 3-4B cold-start estimate | **4.70 GiB** | **provisional estimate** |
| K2 admission requirement | **11.00 GiB** | **provisional reservation** |
| **K2 runtime peak** | **UNKNOWN** | **never measured** |
| **K2 Metal behaviour** | **UNKNOWN** | **never exercised** |
| Qwen under a parallel workload | **UNKNOWN** | **never validated** |

### The constraint is not K2-specific

This is the most important finding, and it reframes everything:

> **At 7.30 GiB available, even the already-proven Qwen stack would block.**
> Qwen's own smoke-runner policy requires ~8.70 GiB (4.70 GiB cold-start +
> 4 GiB reserve). The machine currently sits **1.4 GiB below that**.

So the repeated BLOCKED results are **not a K2 problem**. They are a statement
about this machine running a 4B model *alongside the full development
environment under a 4 GiB reserve*. **No runtime choice fixes that.**

## 3. Isolated test vs production readiness

| | Isolated (Profile D) | Production (Profile A/B) |
|---|---|---|
| Qwen 3-4B protected path | **PASS** — real tool-call round trip | **NOT YET VALIDATED** |
| K2 Q6_K | **NOT_REACHED** | **NOT_REACHED** |
| Resource headroom | irrelevant (apps closed) | **the binding constraint** |

**A passing isolated smoke test is not evidence of parallel-workload
performance.** Concurrency, contention and the owner's real application mix were
never present in that test.

## 4. Proposed default local inference strategy

**Default candidate: the existing Qwen 3-4B MLX stack.** It is the only stack
with validated functionality (protected smoke test PASS, real structured
tool-call round trip, working gateway, watchdog and admission path).

But it must first be **right-sized** for Profile A/B. The honest arithmetic:

```text
available (measured, Profile A)   7.30 GiB
less reserve floor                4.00 GiB
= budget for the model            3.30 GiB
Qwen cold-start estimate          4.70 GiB
=> does NOT fit at present
```

**Three legitimate levers — none of which is "lower the threshold":**

1. **Measure the real footprint** instead of relying on the provisional 4.70 GiB
   estimate. The estimate is deliberately conservative and may overstate.
2. **Reduce the model's footprint** (smaller quantisation) so it fits the
   available budget rather than the budget fitting the model.
3. **Route heavy work remotely** where local cannot fit (see §7).

## 5. K2's role

**K2 remains optional and Profile-D-only** until two things exist:

1. **Actual runtime measurements** — llama.cpp overhead, Metal allocation
   behaviour, transient prefill peak. All three are currently **UNKNOWN**, and
   the 7 GiB provisional charge exists precisely to cover that ignorance.
2. **A representative workload test** — not an isolated load.

Its GGUF architecture (`k2-horizon`) also requires a publisher fork of
llama.cpp, and the K2-specific Metal ops have **never been exercised**. K2 is
the more ambitious runtime, and it is currently the less proven one.

**Do not replace K2 merely because it has fewer parameters than another model.**
That would be a decision made on parameter count rather than evidence.

## 6. Model lifecycle and resource scheduling design

```text
request
  -> ResourcePolicy + fresh telemetry
  -> ADMIT?  no  -> graceful refusal, queue, or approved remote route
             yes -> reservation (atomic)
                 -> watchdog ready
                 -> supervised load (only if not resident)
                 -> serve
                 -> idle timeout
                 -> UNLOAD, release reservation, release memory
```

| Control | Design |
|---|---|
| **D. Demand-based lifecycle** | load → use → idle → **unload**. Idle timeout returns memory to the owner. |
| **E. Admission before load** | explicit policy + fresh telemetry + atomic reservation. Already implemented. |
| **F. Predictable when Xcode/Simulator opens** | the controller re-checks telemetry; if the reserve would break, it **refuses new work** rather than risking an OOM. |
| **G. Graceful refusal / queue / reroute** | never force a model into memory. Report `BLOCKED` with the measured shortfall. |
| **I. Gateway + watchdog** | retained for all local models. |
| **J. No auto-expansion** | no automatic context growth, no unbounded parallel inference. |

**Concurrency without multiple model copies:** an agent's tool operations —
file reads, edits, test runs, `git diff` — are **CPU/subprocess work, not model
work**. They can run concurrently with each other and with a single resident
model. What must stay at **one** is the *model*: one loaded copy, one active
completion. This is already enforced (`max_concurrent_requests = 1`).

> **Honest limit:** unloading and SSD-streaming do **not** remove the memory
> requirement of an *actively executing* model. A model that is generating must
> be resident. Lifecycle management reduces *idle* footprint; it cannot make an
> oversized model fit while it runs.

## 7. Local versus remote decision boundary

| Condition | Route |
|---|---|
| Model fits available budget with the reserve intact | **Local** — protected path |
| Model does not fit, and the work is not confidential | **Remote** — *only with separate owner approval and a data-classification decision* |
| Model does not fit, and the work is confidential | **Refuse and report** — never silently route confidential source to an unapproved provider |
| Profile C (heavy generation) | **One heavyweight workload at a time** unless separately validated |

No cloud endpoint is configured, and none is proposed here.

## 8. Exact next executable engineering task

> **STEP 2 — Validate the existing local Qwen runtime under a representative
> Profile A workload, and MEASURE its real resource footprint.**

**One objective:** convert the Qwen stack's *provisional* 4.70 GiB estimate into
a *measured* footprint while the owner's normal tools are running.

**Why this and not K2:** it uses the **only validated stack**, it answers the
question that actually blocks the product (does local inference coexist with
real development work?), and it replaces an estimate with a measurement.

**Explicitly bounded:** one session, IDE + browser open, the existing protected
path, the existing fixture, no K2, no new model, no threshold change.

## 9. Acceptance criteria for running alongside IDE/Xcode

A Profile A/B validation passes only if **all** hold:

1. Model loads through the protected path with **fresh admission**.
2. Owner's IDE and browser remain open for the whole session.
3. **Actual peak memory is recorded** (not estimated).
4. Swap growth and page-out rate stay within the watchdog thresholds.
5. A real completion is returned **through the gateway**.
6. The owner's applications remain **responsive** throughout.
7. Clean unload; all reservations released; no owned process left.
8. The measured footprint is written back into the resource profile, replacing
   the provisional estimate.

**Failure is an acceptable outcome** — it establishes that this machine needs a
smaller model or remote routing, which is itself a decision-grade answer.

## 10. Remaining unknowns

| # | Unknown |
|---|---|
| 1 | Qwen's **actual** peak memory under a parallel workload |
| 2 | K2 runtime overhead, Metal allocation, transient prefill peak |
| 3 | Whether K2's Metal ops work at all for `k2-horizon` |
| 4 | Whether a smaller quantisation would fit a Profile A budget |
| 5 | True physical (non-RSS) footprint of the owner's application groups |
| 6 | Whether Profile C can coexist with A/B |

## 11. Freeze record

```text
K2_MOCK_PATH_VALIDATED       = true
K2_REAL_INFERENCE_VALIDATED  = false
K2_PARALLEL_WORKLOAD_VALIDATED = false
QWEN_PROTECTED_SMOKE         = PASS (isolated)
QWEN_PARALLEL_WORKLOAD       = NOT_YET_VALIDATED
```

**Preserved, nothing deleted or reinstalled:** K2 Q6_K checkpoint · `ifm-ai/llama.cpp`
fork + compiled `llama-server` (339 MB) · Qwen checkpoint · MLX venv (336 MB) ·
OpenHands venv (550 MB) · Resource Controller (17 modules) · headless launcher ·
**262 passing tests**.

**Recommended next single task: STEP 2 — Qwen Profile A footprint measurement.**
