# FORGE-003 — Model Storage Budget & Local Model Selection

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-25
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Implementation code changed:** **No.** Report only.
- **Weights loaded:** **No.** **Inference run:** **No.** **Anything deleted:** **No.**

---

## 1. Exact current storage inventory

| ASSET | PURPOSE | DISK SIZE | REQUIRED? | REMOVE CANDIDATE? |
|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507-4bit** | **proven** protected inference model (smoke test PASS) | **2.10 GiB** | **yes — regression baseline** | **no** |
| **K2-Horizon-4B-Q6_K.gguf** | K2 compatibility artifact, integrity-verified | **3.88 GiB** | yes — optional runtime | no |
| llama.cpp vocab GGUFs (19 files) | tokenizer data for the fork | 0.06 GiB | yes — part of the runtime | no |
| MLX venv (`storage/bakeoff/model-venv`) | MLX-LM + real tokenizer | 0.34 GiB | yes | no |
| OpenHands venv (`storage/bakeoff/openhands-venv`) | agent SDK | 0.54 GiB | yes | no |
| `ifm-ai/llama.cpp` source + build | compiled `llama-server` + dylibs | 0.33 GiB | yes | no |
| Project tools (uv, Python 3.12, cmake) | toolchain | 0.36 GiB | yes | no |
| **`storage/tmp/cmake.tar.gz`** | **leftover cmake download** (already extracted) | **0.08 GiB** | **no — install is complete** | **YES (approval needed)** |
| `storage/{runs,logs,models,secrets,…}` | telemetry, logs, empty dirs | <0.01 GiB | yes | no |

**Symlinks accounted correctly:** HF snapshot files are symlinks into `blobs/`; `du` reports the **blob bytes once**, and the snapshot entries are links, so **no double-counting**. Blob directory = **6.0 GiB**, matching the two checkpoints' real sizes.

**Shared global caches excluded, not deleted:** `~/.cache/uv` (855 MB) and `~/.cache/huggingface` (136 KB, pre-existing PaddleOCR data) are **not** exclusively ForgeOne-owned and are **not** counted here.

### Totals

| Metric | Value |
|---|---|
| **MODEL_ASSET_TOTAL** (weights + tokenizers) | **≈ 6.04 GiB** |
| **AI_STACK_TOTAL** (models + runtimes + venvs + tools + leftover) | **≈ 7.60 GiB** |

## 2. Comparison against the 12 / 14 GiB budgets

| Budget | Limit | Used | Remaining | Verdict |
|---|---|---|---|---|
| Model-storage target | 12 GiB | **6.04 GiB** | **+5.96 GiB** | **PASS** |
| Model-related hard cap | 14 GiB | **7.60 GiB** (whole AI stack) | **+6.40 GiB** | **PASS** |

**`STORAGE_BUDGET: PASS` — comfortably, on both measures.**

> **This is an important negative result.** Storage is **not** the constraint and has not been at any point. There is enough disk headroom for the Qwen checkpoint, the K2 checkpoint, both runtimes, and a further ~0.9 GiB model without approaching the cap.

## 3. Available runtime-memory budget

| Term | Value | Kind |
|---|---|---|
| Available during normal development | **7.30 GiB** | **measured** |
| Minimum runtime safety reserve | 4.00 GiB | policy floor |
| **Available startup budget** | **3.30 GiB** | derived |
| Qwen 3-4B provisional cold-start | **4.70 GiB** | **provisional estimate** |
| K2 provisional cold-start | **7.00 GiB** | **provisional reservation** |

**Source verification:** `k2_headless_session.py` (`PROVISIONAL_CHARGE = 7 GiB`, `REQUIRED_RESERVE = 4 GiB`, `EFFECTIVE_REQUIRED = 11 GiB`) and the Qwen smoke runner's cold-start estimate (`observed-model-metadata.example.json`). Both are **provisional**, not measured peaks.

**Do not treat the 12–14 GiB SSD allocation as a RAM allowance.** Disk and unified memory are separate budgets; this report keeps them separate throughout.

## 4. Is the current default suitable for parallel development?

| Model | Fits the 3.30 GiB budget? | Verdict |
|---|---|---|
| **Qwen 3-4B** (current default) | **4.70 GiB vs 3.30** | **does NOT fit** |
| **K2 Q6_K** | **7.00 GiB vs 3.30** | **does NOT fit** |

**Neither current model fits a Profile A budget.** The previous strategy report reached the same conclusion; this report confirms it with the storage question now settled.

### The dominant term is not the weights

This is the key engineering insight:

```text
Qwen3-4B provisional:  weights ~2.2  +  runtime overhead ~1.5  +  transient ~1.0  = 4.70 GiB
```

Runtime overhead and transient reserve are **largely fixed** — they do not shrink with the model. So **shrinking the model has diminishing returns**, and a floor of roughly **2.5–3.0 GiB** applies regardless of parameter count.

**A smaller parameter count does not automatically mean sufficient runtime headroom.** That is why the candidates below are assessed on *provisional cold-start*, not on parameter count.

## 5. Verified smaller-model candidates

All three verified against the **official Hugging Face API** (exact revision, size, licence). **No unverified benchmark scores are presented.**

| Candidate | Revision | Licence | Repo size | Shards |
|---|---|---|---|---|
| **`mlx-community/Qwen3-1.7B-4bit`** | `3b1b1768f8f8cf8351c712464f906e86c2b8269e` | **apache-2.0** | **0.92 GiB** | 1 |
| `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` | `b3252a2f97102b1fb1571fec2c9b27219a8536be` | apache-2.0 | 0.82 GiB | 1 |
| `mlx-community/Qwen3-0.6B-4bit` | `73e3e38d981303bc594367cd910ea6eb48349da8` | apache-2.0 | 0.33 GiB | 1 |

| Candidate | Provisional cold-start | Fits 3.30 GiB? |
|---|---|---|
| Qwen3-1.7B-4bit | ≈ **3.45 GiB** (0.95 w + 1.5 oh + 1.0 tr) | **marginal — ~0.15 GiB over** |
| Qwen2.5-Coder-1.5B-4bit | ≈ 3.40 GiB | marginal |
| **Qwen3-0.6B-4bit** | ≈ **2.85 GiB** (0.35 w + 1.5 oh + 1.0 tr) | **fits** |

**These overhead/transient figures are themselves carried over from the Qwen3-4B estimate and are NOT measured for the smaller models.** They are the least reliable numbers in this report.

## 6. Selected default candidate

> ### `mlx-community/Qwen3-1.7B-4bit` — revision `3b1b1768f8f8cf8351c712464f906e86c2b8269e`

**Rationale:**

1. **Same family as the validated baseline.** The existing `HuggingFaceTokenCounter`, chat-template handling and tool-calling path are already proven on **Qwen3**. A Qwen3 replacement introduces **no new tokenizer or template integration risk** — the single most valuable property here.
2. **Apache-2.0**, exact revision pinned, 0.92 GiB, single shard.
3. **Provisional ~3.45 GiB** — marginally over the 3.30 GiB budget, **but that estimate is the least reliable number available**, and the whole point of Step 1 is to replace it with a measurement.
4. **Fallback is explicit and cheap:** if measurement confirms it does not fit, drop to **Qwen3-0.6B-4bit** (~2.85 GiB provisional), which should fit with margin.

**Why not Qwen2.5-Coder-1.5B?** It is purpose-built for code, but it is a **different family** — new tokenizer, new template, new tool-call format. Given the current blocker is *resources*, not model quality, taking on fresh integration risk for a marginal size difference is the wrong trade. Revisit it later if coding quality proves to be the binding constraint.

**This selection replaces an oversized model rather than sacrificing the IDE, browser or Xcode workflow** — which is the explicit preference.

## 7. Exact files proposed for removal

| Path | Identity | Reclaimable | Referenced by | Shared? |
|---|---|---|---|---|
| `storage/tmp/cmake.tar.gz` | leftover CMake 4.4.3 download | **0.08 GiB** | **nothing** — cmake is already extracted and working at `storage/tools/cmake/` | no |
| `storage/tmp/cmake.tar.gz.sha256` | its checksum file | ~0 | nothing | no |

**Resulting inventory after removal:** model assets unchanged at 6.04 GiB; AI stack **7.60 → 7.52 GiB**.

> **`REMOVAL_REQUIRED: NO`.** The 12 GiB budget is already met with **+5.96 GiB** headroom. These 85 MB are genuine clutter, not a budget necessity.

**No checkpoint is proposed for removal.** In particular, **K2 Q6_K stays**: it is integrity-verified, its retained footprint fits the budget easily, and the strategy keeps K2 as an optional experimental runtime. Removing it would free disk we do not need while discarding a validated artifact.

**No shared global cache is proposed for removal or modification.**

## 8. Exact model proposed for download

| | |
|---|---|
| Repository | **`mlx-community/Qwen3-1.7B-4bit`** |
| Revision | `3b1b1768f8f8cf8351c712464f906e86c2b8269e` (pinned) |
| **Expected download** | **≈ 0.92 GiB** |
| Licence | apache-2.0 |
| Destination | `$FORGEONE_HOME/storage/cache/huggingface/hub/` |
| Resulting model assets | 6.04 → **6.96 GiB** (well under 12) |

## 9. Next bounded step

> **STEP 1 — Measure, do not assume.** Download the selected candidate and **measure its real cold-start footprint under Profile A** (IDE + browser open).

1. Owner approves the download (~0.92 GiB) and the 85 MB tmp cleanup.
2. Download via the existing `model-manager.py` with the revision pinned and a **dry run first**.
3. Register as a **new, separately versioned registry entry**. **Do not change the existing default.**
4. Run the existing protected smoke procedure against it with **IDE and browser open**, recording **actual peak memory**.
5. If it fits with the reserve intact → promote to default after its own regression + real-workload validation.
6. If it does not fit → fall back to **Qwen3-0.6B-4bit** and repeat.

## 10. Profile A and B acceptance criteria

**Profile A** — all must hold: IDE **open** · browser **open** · **4 GiB reserve intact** · protected inference succeeds · **real tokenizer accounting** succeeds · **structured tool-call round trip** succeeds · memory telemetry and watchdog healthy · unload and reservation cleanup succeed · **owner's applications remain responsive**.

**Profile B** — additionally **Xcode + a booted iOS Simulator**, measured **separately**. **A passing Profile A test is not proof of Profile B.**

**If admission blocks: record `BLOCKED` and do not force inference.**

## 11. Parallel execution design

| Operation | Concurrency |
|---|---|
| Agent planning / orchestration | concurrent (CPU) |
| File reading / editing | concurrent (CPU/IO) |
| Git operations | concurrent (IO) |
| Bounded build / test subprocesses | concurrent, subject to admission |
| **Local model inference** | **one shared protected endpoint** |

**No separate resident model copy per agent or tool.** One loaded copy, one active completion; additional model requests **queue** when the slot is busy. Multiple simultaneous heavy GPU jobs only after separate validation.

```text
REQUEST -> FRESH ADMISSION -> LOAD IF NECESSARY -> SERVE -> IDLE UNLOAD -> RELEASE RESERVATION
```

When Xcode or Simulator raises memory use, **admit new work only if the safety policy still holds**. **Never force a model into memory.**

## 12. Freeze boundaries

| Frozen | Status |
|---|---|
| K2 implementation + mock-tested path | **frozen** — not modified to make another model fit |
| Qwen protected behaviour | **regression baseline** — unchanged |
| Resource Controller / gateway / watchdog | **frozen** |
| 262-test baseline | **preserved** |

The new model enters as a **separately versioned registry entry**. **The existing default does not change until the replacement passes its own regression and real-workload validation.**

---

## FINAL STATUS

```text
STORAGE_BUDGET          : PASS   (6.04 GiB model assets / 7.60 GiB AI stack, vs 12/14 GiB)
CURRENT_DEFAULT_PROFILE_A: BLOCKED  (Qwen 3-4B needs 4.70 GiB, budget is 3.30 GiB)
SELECTED_REPLACEMENT    : mlx-community/Qwen3-1.7B-4bit @ 3b1b1768f8f8cf8351c712464f906e86c2b8269e
REMOVAL_REQUIRED        : NO     (budget already met with +5.96 GiB headroom)
OWNER_APPROVAL_REQUIRED : (1) download Qwen3-1.7B-4bit, ~0.92 GiB
                          (2) optionally remove storage/tmp/cmake.tar.gz, ~0.08 GiB
```

**No weights loaded, no inference, nothing downloaded, nothing deleted, no threshold lowered, no application killed.**
