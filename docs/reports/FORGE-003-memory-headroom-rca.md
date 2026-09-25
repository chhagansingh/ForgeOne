# FORGE-003 — Memory Headroom RCA & Local Execution Feasibility

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Inference run:** **No.** **Model loaded:** **No.** **Thresholds changed:** **No.**

**Status:**
- `MEMORY_ACCOUNTING_VALID` — the formula is correct and consistent, but **optimistic** (see §2)
- `BUG_FOUND` — a separate latent defect in the K2 session (§5)
- `LOCAL_K2_FEASIBLE` — plausible, and the blocker is identifiable (§6)

---

## 1. Actual memory-accounting implementation

| Item | Evidence |
|---|---|
| Formula | `telemetry.py:37-39` — `available_bytes = free_bytes + inactive_bytes + speculative_bytes` |
| Source values | `telemetry.py:111-113` — `Pages free`, `Pages inactive`, `Pages speculative` from `vm_stat` × `hw.pagesize` |
| Swap | `telemetry.py` reads `vm.swapusage` (`used = N M`) |
| Page-outs | `vm_stat` `Pageouts` counter |
| Freshness | `MemorySnapshot.is_fresh()` (`telemetry.py:45`) — stale snapshots are treated as **no snapshot** |
| Admission use | `admission.py` step 3 (freshness), step 10 (floor), step 11 (estimate vs headroom) |

**Not read — correctly:** `Pages wired down` (wired memory is **not** reclaimable, so excluding it is right).

**Not read — an accuracy gap:** `Pages purgeable` (~0.06 GiB, minor) and `Pages occupied by compressor` (**6.84 GiB**, significant).

**No double-charging found.** The cold-start charge and the reserve are applied to *different* terms: the reserve is the `min_available_memory_bytes` floor, the charge is the `transient_reserve_bytes` component of the estimate. They are not summed twice. Units are bytes throughout; no GiB/GiB-vs-GB confusion in the arithmetic.

**Memory pressure is measured indirectly** — inferred from available bytes, swap and page-out rate. macOS exposes a direct signal (`memory_pressure` / `kern.memorystatus_vm_pressure_level`) that is **not** consulted.

## 2. Reconciliation with macOS diagnostics (same instant)

| Measure | Value |
|---|---|
| **Controller formula** (free + inactive + speculative) | **6.64 GiB** |
| + purgeable | 6.70 GiB (+0.06) |
| **File-backed pages — genuinely reclaimable without swapping** | **3.35 GiB** |
| Anonymous pages — reclaiming these **requires swap** | 9.45 GiB |
| Compressor occupied | 6.84 GiB |
| macOS `memory_pressure` verdict | **56 % free** |

**The formula overstates true headroom by roughly 2×.** `Pages inactive` is a *mixture* of file-backed pages (instantly reclaimable) and anonymous pages (which must be written to swap first). Counting all of `inactive` as "available" is **optimistic, not conservative**.

**This matters for the conclusion:** the gate is measured on a **generous** metric and *still* fails. The real situation is **worse** than the reported 6.6–6.8 GiB, not better. Any argument that "the policy is being unfairly conservative" is **refuted by the data**.

## 3. Exact 11 GiB admission calculation

| Term | Value | Kind |
|---|---|---|
| Provisional cold-start charge | **7 GiB** | **conservative provisional reservation** (`k2_headless_session.py:52`) |
| Required post-admission reserve | **4 GiB** | policy floor (`:53`) |
| **Effective requirement** | **11 GiB** | `required = PROVISIONAL_CHARGE + REQUIRED_RESERVE` (`:92`) |

| Distinction | Value |
|---|---|
| **Measured** | checkpoint on disk = 4,161,403,264 B (3.88 GiB); KV = 147,456 B/token (formula verified) |
| **Derived** | KV @1024 ≈ 0.14 GiB |
| **Provisional** | the 7 GiB charge — explicitly **not** a measured peak |
| **Unknown** | llama.cpp runtime overhead; Metal allocations; transient prefill peak |

**The calculation is arithmetically correct.** The 7 GiB is a deliberate conservative reservation covering the three unknowns, and the task correctly forbids replacing it with the 3.88 GiB checkpoint size. **No bug in the 11 GiB arithmetic.**

## 4. Major observed memory consumers

Grouped process RSS, one lightweight observation. **RSS is not physical memory usage** — shared pages are counted per process, so the total overstates.

| Group | RSS |
|---|---|
| **iOS Simulator** | **6.48 GB** |
| **Devin (this IDE)** | **4.39 GB** |
| **Brave** | **2.98 GB** |
| macOS system | 2.95 GB |
| other | 1.96 GB |
| Java / JDK | 1.65 GB |
| Microsoft / Teams | 1.07 GB |
| **Total** | **21.58 GB** (of 24 GB) |

**No ForgeOne-owned model process was running.** Nothing was killed or closed.

## 5. Confirmed defects

### BUG — the K2 session passes `metadata=None`

`scripts/k2_headless_session.py:280`:

```python
controller = ResourceController(
    policy, MockK2TokenCounter(), None, MacOSTelemetrySource(), ...
    #                             ^^^^ metadata
```

`admission.py:admit_startup()` requires model metadata and returns
`REJECTED_UNVERIFIED_ESTIMATE` when it is absent — by design (fail closed).

**Consequence: even if the memory gate passed, the protected backend startup
would be REJECTED.** The K2 path has never reached that point because the
preflight blocks first, so this was latent. It is a genuine defect and the
**next fix required** before any real attempt.

### LIMITATION — optimistic availability metric

Not a bug in the sense of being wrong or inconsistent, but the metric
overstates reclaimable memory (§2). A stricter metric would fail **earlier**,
not later. No correction is proposed here; changing it would alter every
existing admission decision and needs its own scoped change.

## 6. Feasibility

| Question | Answer |
|---|---|
| **A. Is 11 GiB calculated correctly?** | **Yes.** Arithmetic verified; the 7 GiB is a deliberate conservative reservation for three genuinely unknown terms. |
| **B. Is pressure concerning, or is policy too conservative?** | **Neither, strictly.** Swap is flat and page-outs are ~0, so the machine is *not* under active pressure. But the block is **not** over-conservatism either: the metric is *optimistic* and still fails. The block is **insufficient headroom**. |
| **C. Which owner groups could change it?** | **iOS Simulator (6.48 GB)**, **Devin (4.39 GB)**, **Brave (2.98 GB)**. Together ≈ **13.8 GB RSS**. |
| **D. Is 11 GiB plausible with IDE and browser closed?** | **Yes, plausibly.** Simulator + Devin + Brave freed ≈13.8 GB RSS. Even allowing for RSS overstating physical usage, that is comfortably the order of magnitude needed to lift available memory from ~6.6 GiB toward the 11 GiB requirement. **Not guaranteed** — the simulator is the single biggest item and the owner must judge whether it can be closed. |
| **E. Alternatives if it cannot be reached** | see below |

### Alternatives, honestly assessed

| Option | Assessment |
|---|---|
| **Existing Qwen 3-4B (MLX)** | **Already works.** The protected smoke test PASSED and the endpoint is proven. It needs far less headroom than the K2 provisional charge. **This is the pragmatic path** for continuing agent work now. |
| **A smaller K2 checkpoint** | Would reduce the *weight* term, but the **7 GiB provisional charge is dominated by the unknowns** (runtime + Metal + prefill), not by 3.88 GiB of weights. A smaller quant would not materially move the 11 GiB requirement. **Low value.** |
| **Remote inference endpoint** | Sidesteps local memory entirely, but requires separate owner approval, a data-classification decision, and is **not** proposed here. |
| **Higher-memory machine** | The only option that removes the constraint rather than working around it. |

**Recommendation: fix the `metadata=None` defect, then use the Qwen path for
agent work, and treat K2 as a machine-capacity experiment to run when the
Simulator and IDE can be closed.**

## 7. One concrete next execution strategy

1. **Fix `metadata=None`** in the K2 session by supplying a real `ModelMetadata`
   built from the checkpoint's own GGUF geometry (already extracted: 36 layers,
   8 kv_heads, 128/128). Without this, startup fails even if memory passes.
2. **Quit iOS Simulator, Devin and Brave**, then run `--check`.
3. Only if `--check` prints PASS, run `--execute`.
4. If it still blocks, **stop pursuing local K2 on this machine** and continue
   with the proven Qwen protected path.

**No threshold is lowered, and none should be.**

## 8. Remaining unknowns

| # | Unknown |
|---|---|
| 1 | llama.cpp runtime overhead for this build |
| 2 | Metal allocation behaviour for the K2-specific ops |
| 3 | Transient prefill peak — the term that likely caused the earlier MLX OOM |
| 4 | Whether Metal even engages correctly for `k2-horizon` (never exercised) |
| 5 | Whether the backend exposes a usable `/tokenize` endpoint (the session fails closed if not) |
| 6 | True physical (non-RSS) footprint of the grouped consumers |

## 9. What this RCA does NOT claim

- It does **not** claim the 11 GiB requirement is wrong — it is correct and conservative by design.
- It does **not** claim the machine is under memory pressure — swap and page-outs are flat.
- It does **not** claim freeing applications will definitely succeed.
- It does **not** propose relaxing any safety control.
