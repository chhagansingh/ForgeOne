# FORGE-003 P0 — Host Memory Incident Report

- **Severity:** **P0 — host-wide impact**
- **Date:** 2026-09-24
- **Milestone:** M1 / FORGE-003 (execution phase)
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Status:** Contained. Root cause identified. Bake-off **suspended** pending the
  Resource Controller design.
- **Author:** implementation agent (self-reported, self-critical)

> **Read this first.** This incident was caused by an agent action, not by a
> defect in Hermes, OpenHands, MLX or macOS. The agent ran a memory-intensive
> stress test on the owner's primary workstation without an admission-control
> guard, without a memory ceiling, and with buffered output that captured **zero
> measurements**. The owner's machine became unusable for several minutes.

---

## 1. Summary

During Phase 2 of FORGE-003, the agent attempted to establish the local model
endpoint's usable context window by issuing progressively larger requests
(8K → 32K → 64K tokens) against a locally hosted MLX-LM server.

The **third request crashed the model server with a Metal GPU out-of-memory
error and drove the host into heavy swap**, stalling all user applications.

Two facts make this worse than a simple test failure:

1. **The server had no memory ceiling configured.** MLX-LM's prompt cache
   accumulated KV caches across every prior request and never released them —
   reaching **6.05 GB of retained cache** before the failing request even began.
2. **The test produced no data.** Output was buffered through a pipe, so when
   the process was killed, not a single measurement had been captured. The
   experiment cost the owner a stalled machine and returned nothing.

The root cause is **absence of admission control**, not insufficient RAM. This
report is the input to the
[Resource Controller design](../architecture/resource-controller-design.md).

---

## 2. Impact

| Impact | Detail |
|---|---|
| **Host usability** | All user applications stalled. Owner reported: *"my whole system get stuk aal apps are stuck"* |
| **Swap consumption** | Peak **7,621 MB used** of 9,216 MB total swap (`vm.swapusage`) |
| **Free memory floor** | Fell far below the 4 GB safety floor the agent should have enforced |
| **Data captured** | **Zero measurements.** Buffered stdout through `grep` meant nothing was written before termination |
| **Bake-off** | Suspended. No agent runtime was installed; no fixture run occurred |
| **Cost of rework** | The intended 64K test remains unexecuted, and must not be attempted in this form |
| **Client assets** | **None affected.** The unrelated client APK that was being served by the terminated process (see §6) and all client material untouched |
| **Repository integrity** | **None affected.** No commit, no push, no history change during the incident |

Severity is **P0** because the blast radius was the entire host, not the
project sandbox. A single agent command degraded the owner's primary machine.

---

## 3. Timeline (reconstructed from server logs)

All timestamps from `storage/logs/mlx-server.log`; wall-clock times are local.

| Time | Event |
|---|---|
| 15:00:56 | MLX-LM server bound on `127.0.0.1:8082` (loopback only — correct) |
| 15:01:38–15:01:48 | **Phase 2 preflight executed: 8/8 PASS**, including real tool calling and both error paths |
| 15:01:48 | Prompt cache: 3 sequences, **0.04 GB** |
| 15:02:22 | Context test begins |
| 15:02:26–15:02:40 | **Request 1 — 8,011 tokens → HTTP 200 SUCCESS.** Cache → 4 sequences, **1.24 GB** |
| 15:02:45–15:04:29 | **Request 2 — 24,611 tokens → HTTP 200 SUCCESS.** Cache → 5 sequences, **6.05 GB** |
| 15:04:43–15:10:17 | **Request 3 — 32,611 tokens → prompt processing reached 32,610/32,611** |
| ~15:10:17 | **`RuntimeError: [METAL] Command buffer execution failed: Insufficient Memory`** |
| — | Owner reports host-wide stall; agent terminates the test and the server |

---

## 4. Root cause

### 4.0 Observed, inferred, and unknown

The distinction matters. This incident produced strong evidence, but not proof
of a single cause.

| Statement | Status |
|---|---|
| 8,011-token request succeeded | **OBSERVED** — server log |
| 24,611-token request succeeded | **OBSERVED** — server log |
| 32,611-token request failed with Metal OOM | **OBSERVED** — server log |
| 64K was never reached | **OBSERVED** — the process died at 32,611 tokens |
| 6.05 GB retained prompt cache immediately before the failing request | **OBSERVED** — server log |
| `--prompt-cache-bytes` defaults to `None` (no byte ceiling) | **OBSERVED** — mlx-lm 0.31.3 source |
| `trim_to()` is reached on only one code path | **OBSERVED** — mlx-lm 0.31.3 source |
| Retained cache contributed to the failure | **INFERRED** — strongly supported |
| Retained cache was the *sole* cause | **NOT ESTABLISHED** |
| Transient prefill working set | **UNKNOWN** — never measured |
| True safe context ceiling on this host | **UNKNOWN** |

**Supported explanation:** retained prompt cache **plus** subsequent allocations
under inadequate resource controls. The retained cache is a documented
contributing factor, not a proven sole cause. The Metal command-buffer failure
occurred *during prefill* of the 32,611-token request, and the transient working
set for that prefill was never measured — so any claim that the retained cache
alone caused the crash would overstate the evidence.

### 4.1 Contributing factor — unbounded KV cache accumulation

The MLX-LM prompt cache **retains a KV cache for every distinct request** and
does not release them between requests. The server logs this explicitly, and it
is the single clearest signal in the whole incident:

```text
15:01:38  Prompt Cache: 0 sequences, 0.00 GB
15:01:42  Prompt Cache: 2 sequences, 0.01 GB
15:01:43  Prompt Cache: 3 sequences, 0.04 GB
15:02:40  Prompt Cache: 4 sequences, 1.24 GB   <- after the  8,011-token request
15:04:29  Prompt Cache: 5 sequences, 6.05 GB   <- after the 24,611-token request
15:10:17  [METAL] Insufficient Memory          <- during the 32,611-token request
```

By the time the failing request began, **6.05 GB was already retained** by cache
entries from earlier requests. The new request then needed its own KV cache
*on top of that*, plus transient attention buffers during prefill.

**The agent never set `--prompt-cache-bytes` or `--prompt-cache-size`.** The
server exposes both options precisely to bound this behaviour; leaving them
unset removed the only built-in ceiling.

**Source inspection of the installed mlx-lm 0.31.3** (read-only; no model
loaded) sharpens this into a precise finding:

| Flag | Default | Effect |
|---|---|---|
| `--prompt-cache-size` | **10** sequences | Bounds the *count* of retained KV caches — **not their bytes** |
| `--prompt-cache-bytes` | **`None`** | The byte ceiling. When unset, **no byte bound exists at all** |

`trim_to(n_bytes=...)` is called from exactly **one** location
(`server.py:798`), inside the *batched* generation path, and only when
`prompt_cache_bytes is not None`. Consequences:

1. With the default configuration, the byte ceiling is **never applied**.
2. A count limit of 10 says nothing about size: ten large caches can dwarf one
   enormous cache.
3. The `_serve_single` path has no cache trimming at all.

This does not prove the retained cache caused the crash (§4.0), but it does
establish that **no byte-level ceiling existed to prevent it**.

### 4.2 Contributing factor — the memory arithmetic was never enforced

Theoretical KV cost, derived from the model config
(36 layers × 8 KV heads × 128 head_dim × 2 (K+V) × 2 bytes):

| Context | KV cache (theory) |
|---|---|
| 8,192 | 1.12 GB |
| 32,768 | 4.50 GB |
| 65,536 | **9.00 GB** |
| 262,144 (model's declared max) | **36.00 GB — impossible on 24 GB** |

The agent computed these numbers **and then ran the test anyway**, without
converting them into an abort threshold. Knowing the number is not the same as
enforcing it. The measured cache (6.05 GB at 24,611 tokens ≈ 246 KB/token
effective) was **higher** than the theoretical 144 KB/token, because the cache
held *multiple* sequences — a discrepancy that was not reconciled before
proceeding.

### 4.3 Contributing factor — the test could not have succeeded as designed

The script's token estimation was wrong. It assumed linear growth from a
repeated filler string, but BPE merges across repetition boundaries make growth
sublinear. Observed versus intended:

| Intended target | **Actual prompt tokens** | Outcome |
|---|---|---|
| 8,192 | 8,011 | SUCCESS |
| 32,768 | 24,611 | SUCCESS |
| 65,536 | **32,611** | **CRASH** |

**The 64K test never ran.** The process died at ~32.6K tokens. Any report
claiming "64K was tested and failed" would be false.

---

## 5. Contributing factors (agent errors)

Each of these is an independent failure. Any one of them would have prevented
the incident.

| # | Error | Correct behaviour |
|---|---|---|
| 1 | **No admission control.** Ran a memory-intensive test on the owner's primary workstation without first checking available memory against a budget | Estimate peak requirement, compare to free memory, refuse if it does not fit |
| 2 | **No memory ceiling.** Server started without `--prompt-cache-bytes` | Always bound the cache explicitly |
| 3 | **No abort threshold.** No watchdog on server RSS or system free memory | Abort the moment a floor is breached |
| 4 | **Buffered output.** Piped through `grep`, so nothing was flushed | Use `python -u`; write results incrementally to a file |
| 5 | **Aggressive escalation.** Jumped straight to large contexts rather than stepping conservatively and stopping at the first sign of pressure | Stepwise with a hard stop |
| 6 | **Ran on the primary workstation during working hours** | Treat the host as production; schedule heavy work or require explicit approval |
| 7 | **Incorrect token accounting** (§4.3) | Validate actual token counts before issuing the request |

The agent also **misreported the cause initially**, attributing the stall to the
64K test. The server log proves it was the ~32.6K request. That correction is
recorded here.

---

## 6. Containment actions taken

| Action | Result |
|---|---|
| `SIGTERM` → `SIGKILL` to `context_test.py` | Terminated |
| `SIGTERM` → `SIGKILL` to `mlx_lm.server` | Terminated |
| Verified no FORGE-003 process remains | Confirmed clean |
| Verified port 8082 released | Confirmed free |
| Verified system recovery | Free RAM restored to ~7.3 GB; swap draining |
| Verified no unrelated process touched | Confirmed — the owner's own local server on 8083, the IDE, browsers and the client APK all untouched |
| Verified repository integrity | Confirmed — branch and commit unchanged, working tree clean |

**Not** done, deliberately: no further stress testing, no server restart, no
bake-off continuation.

### 6.1 Separate action — A6 security termination

Earlier in FORGE-003, approval decision **A6** authorised terminating an
unrelated stray process. It is recorded here because it also involved
terminating something on this host, though it is **unconnected to the memory
incident**.

- **Process:** PID 28474 — `python3 -m http.server 8080 --bind 0.0.0.0`,
  orphaned (reparented to PID 1), running 3 days 5 hours
- **Finding:** it was serving a **client-project APK** on all network
  interfaces — a genuine LAN exposure of client material
- **Verification before acting:** no active connections to :8080; no Android
  device or emulator attached; parent shell orphaned
- **Action:** graceful `SIGTERM` only. Port 8080 confirmed released; the parent
  shell exited on its own
- **Preservation:** the served artifact was **not** deleted, moved or modified —
  byte size and timestamp unchanged. Only the process was stopped
- **Not touched:** the owner's other local servers on 8000/8083/8765, all IDEs
  and browsers

---

## 7. Preserved evidence — Phase 2 preflight, 8/8 PASS

This evidence is **valid and unaffected** by the incident. It was produced
*before* the context test, at 15:01:38–15:01:48, against
`mlx-community/Qwen3-4B-Instruct-2507-4bit` on `127.0.0.1:8082`.

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | `GET /v1/models` | **PASS** | HTTP 200, `ids=['mlx-community/Qwen3-4B-Instruct-2507-4bit']` |
| 2 | `POST /v1/chat/completions` | **PASS** | HTTP 200, 3.02 s, content `'OK'`, usage `{prompt:13, completion:2, total:15}` |
| 3 | Streaming (SSE) | **PASS** | 11 chunks, **TTFT 0.24 s**, total 0.55 s, text `'1, 2, 3, 4, 5.'` |
| 4 | Structured tool calling | **PASS** | `tool_calls[0].function.name = "run_tests"`, `arguments = '{"path": "tests/test_pricing.py", "verbose": true}'` — **valid JSON, parses cleanly**, id `40bde60b-…` |
| 5 | Tool result → final answer | **PASS** | Tool result returned to model; final answer correctly identified both failing tests |
| 6 | Malformed JSON handling | **PASS** | HTTP **400** with explicit error message |
| 7 | Unknown model rejection | **PASS** | HTTP **404** |
| 8 | *(structure check)* tool-call validity | **PASS** | name matches request; arguments are a parseable object with the required `path` string |

**This satisfies the milestone requirement that a plain HTTP 200 is not
evidence of functional tool calling.** The tool-call response contains a valid
structured `tool_calls` entry, with the expected function name and valid JSON
arguments — and the round-trip (tool result → final answer) completed.

**Conclusion: the inference endpoint is functionally sound for agent use. The
failure was in the agent's test methodology, not in the endpoint.**

## 8. Recorded as UNSAFE / NOT_RUN

| Item | Status | Note |
|---|---|---|
| 64K context | **NOT_RUN** | Never reached. The process died at 32,611 tokens |
| 32K context | **FAILED — UNSAFE** | Metal OOM; must not be retried without admission control |
| 24,611-token request | **OBSERVED SUCCESS** | Succeeded only while the cache held 6.05 GB — not a safe operating point |
| 8,011-token request | **OBSERVED SUCCESS** | Cache 1.24 GB. The only configuration observed to be comfortable |
| Safe context ceiling | **UNKNOWN** | Not established. Must be determined under the Resource Controller, or not at all |

**No context size above 8,011 tokens has been demonstrated safe on this host.**
Any future claim must be backed by a guarded measurement.

---

## 9. Current state

| Item | State |
|---|---|
| Model server | **Stopped** |
| Port 8082 | Free |
| Free RAM | ~7.3 GB |
| Swap | Draining (7,168 MB total / 6,407 MB used, down from 9,216 / 7,621) |
| Agent runtimes | **Not installed** — Phase 3 never began |
| Bake-off | **Not started** |
| Phase 1 artifacts | Intact: uv 0.12.18, Python 3.12.14, mlx-lm 0.31.3, mlx 0.32.2, checkpoint `50d4277…` |
| Storage footprint | 2.6 GB, entirely under Git-ignored `storage/` |
| Repository | Branch `feat/forge-003-runtime-bakeoff`, commit `1672fe7`, clean tree |

---

## 10. Corrective actions

### 10.1 Immediate (applied)

- Bake-off **suspended** pending the Resource Controller design
- No model server will be started without an explicit memory budget and ceiling
- Stress testing on the primary workstation is **prohibited** without owner approval

### 10.2 Structural (designed in this milestone)

The [ForgeOne Resource Controller](../architecture/resource-controller-design.md)
defines the missing control layer:

1. **Admission control** — refuse to start a workload whose estimated peak
   exceeds a configurable fraction of available memory
2. **Hard ceilings** — always set `--prompt-cache-bytes`; never rely on defaults
3. **Runtime watchdog** — abort on RSS or system-free-memory floor breach
4. **Unbuffered, incremental evidence** — results written as they are produced,
   so a killed run still yields data
5. **Stepwise escalation with stop-on-first-pressure** — never jump to a large
   configuration
6. **Workstation classification** — heavy workloads require an explicit budget
   and owner acknowledgement

### 10.3 Process

- Any experiment whose peak memory is not **estimated before** it runs is not
  permitted to run
- Any experiment that cannot abort safely is not permitted to run
- An experiment that yields no data is a failed experiment regardless of
  whether it crashed

---

## 11. Open questions

| # | Question |
|---|---|
| 1 | What is the true safe context ceiling under a bounded cache — 8K? 16K? 24K? |
| 2 | Is a 4B model at a small context sufficient for the bake-off, or does the task need more? |
| 3 | Should the bake-off use a hosted endpoint instead, given 24 GB shared with IDEs? |
| 4 | What is the right `--prompt-cache-bytes` default for this host? |
| 5 | Should heavy workloads be scheduled rather than run interactively? |

---

## 12. Honest assessment

The inference endpoint works — tool calling is genuinely verified. The agent's
test methodology did not. Three things should have been true and were not:

1. The peak memory requirement should have been **estimated and compared to
   free memory before the test ran**.
2. The server should have been started with an **explicit cache ceiling**.
3. The results should have been **captured incrementally**, so that the run
   that crashed the machine at least produced the 8K and 24K measurements it
   had already earned.

None of these is exotic. They are basic resource hygiene, and their absence —
not the model, not MLX, not macOS — is why the owner's machine stalled.
