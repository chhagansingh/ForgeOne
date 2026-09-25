# FORGE-003 — K2 Horizon Q6_K Download & Validation

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Result:** **DOWNLOADED** and **INTEGRITY_VERIFIED**. **RUNTIME_COMPATIBLE: no.**
- **Inference run:** **No.** **Model loaded:** **No.** **Runtime installed:** **No.**

---

## 1. Repository, revision, quantization and filename

| Item | Value |
|---|---|
| Repository | `IFM/K2-Horizon-3.7B-GGUF` |
| Source | `https://huggingface.co/IFM/K2-Horizon-3.7B-GGUF` |
| **Pinned revision** | **`a81d5fec318b47b9c7144a839f538f6b9006291c`** |
| Quantization | **Q6_K** |
| **Exact filename** | **`K2-Horizon-4B-Q6_K.gguf`** |
| Licence | **apache-2.0** |
| Public / gated | public / **not gated** |

### 1.1 The filename could not have been guessed

The repository is named **`K2-Horizon-3.7B-GGUF`**, but **every weight file in it
is named `4B`**. The approved filename is `K2-Horizon-4B-Q6_K.gguf` — *not*
`K2-Horizon-3.7B-Q6_K.gguf`. It was resolved from the official API and confirmed
**unambiguous** (exactly one file matches `Q6_K`).

This is precisely why the milestone said not to guess the filename.

### 1.2 Only the approved variant was fetched

The repository contains **six** quantizations totalling ~25 GB. **Single-file
selection was implemented and tested *before* downloading**, so exactly one file
was transferred:

| File | Size | Downloaded |
|---|---|---|
| `K2-Horizon-4B-BF16.gguf` | 9.43 GB | **no** |
| `K2-Horizon-4B-Q8_0.gguf` | 5.02 GB | **no** |
| **`K2-Horizon-4B-Q6_K.gguf`** | **4.16 GB** | **YES** |
| `K2-Horizon-4B-Q5_K_M.gguf` | 3.39 GB | **no** |
| `K2-Horizon-4B-Q5_0.gguf` | 3.33 GB | **no** |
| `K2-Horizon-4B-Q4_K_M.gguf` | 2.94 GB | **no** |

## 2. Exact checkpoint size and checksum

| Item | Value |
|---|---|
| Expected size (upstream) | `4161403264` bytes |
| **Actual size (local)** | **`4161403264` bytes** — **exact match** |
| Displayed as | 4.16 GB decimal / 3.88 GiB |
| **Upstream SHA-256** | `2180f3ca4eb4906a109b364a98740778fd8dcd9969e270b0d34036d18ee33232` |
| **Local SHA-256** | `2180f3ca4eb4906a109b364a98740778fd8dcd9969e270b0d34036d18ee33232` |
| **Comparison** | **IDENTICAL — upstream-verified** |

Upstream LFS integrity metadata **was available**, so this is a genuine
**upstream-verified** match, not merely a locally computed checksum.

## 3. Final resolved model/cache path

```text
$FORGEONE_HOME/storage/cache/huggingface/hub/
  models--IFM--K2-Horizon-3.7B-GGUF/
    snapshots/a81d5fec318b47b9c7144a839f538f6b9006291c/K2-Horizon-4B-Q6_K.gguf
      -> blobs/87/877867b52d926b3f08090fee6a6d8df0cbd088f9ef755990f02d655fbb361ca2
```

A single blob with a snapshot symlink — **no second permanent weight copy**.

## 4. Proof of centralized project-owned storage

Every environment value was resolved with `Path.resolve()` and checked against
the real path, not a prefix:

| Variable | Resolves inside `$FORGEONE_HOME`? |
|---|---|
| `FORGEONE_HOME` | yes |
| `HF_HOME` | **yes** |
| `HF_HUB_CACHE` | **yes** |
| `HF_XET_CACHE` | **yes** |
| `HF_ASSETS_CACHE` | **yes** |
| `TMPDIR` | **yes** |

- **All new files landed under `storage/`** — verified.
- **`~/.cache/huggingface` untouched**: 136 KB before and after (it is
  pre-existing PaddleOCR data, **not** ForgeOne's).
- **`~/.cache/uv` untouched** — deliberately preserved.
- **No symlink points outside `storage/`**; **0 broken symlinks**.
- **Git-ignored**: `git check-ignore` confirms the new cache is excluded.

*Not claimed:* OS-managed swap and macOS internal temporary files cannot be
forced into ForgeOne storage. Only project-owned persistent writes are
constrained.

## 5. GGUF header validity and metadata

Parsed from the file header — **no weights loaded**:

| Field | Value |
|---|---|
| Magic | **`GGUF`** — valid |
| Version | **3** |
| Tensor count | **327** |
| Metadata entries | 31 |
| **Architecture** | **`k2-horizon`** |
| Internal name | `Checkpoint_0010000` |
| Tokenizer model | **`gpt2`** (BPE) |
| **Chat template** | **ABSENT** |
| Companion files | **None required** — single self-contained GGUF |

## 6. Runtime compatibility — research only, **NOT COMPATIBLE**

| Question | Finding |
|---|---|
| Does **upstream** llama.cpp support `k2-horizon`? | **No.** `ggml-org/llama.cpp` `gguf-py/gguf/constants.py` lists **128 architectures**; **none is `k2-horizon`**. |
| Is a K2-specific fork still required? | **Almost certainly yes** — an architecture absent from `MODEL_ARCH` cannot be loaded by upstream llama.cpp. |
| Apple Metal support | **Unverified** — cannot be assessed until a working runtime exists. |
| Tokenizer handling | `gpt2` BPE — a standard tokenizer type, so this part is likely portable. |
| **Chat template** | **ABSENT from the GGUF.** A template would have to be supplied externally. |
| Tool-calling format | **Unverified** — and with no embedded chat template, tool calling cannot be assumed. |
| Can MLX-LM execute it? | **No.** MLX-LM does not execute GGUF checkpoints; it expects MLX safetensors. |
| Can it be served through a future `InferenceAdapter`? | **Not yet** — it requires a llama.cpp-class backend that supports `k2-horizon`. |

**No runtime was installed, compiled or launched.** Xinference was **not**
installed. Future inference must still pass through
`Agent → ProtectedGateway → Resource Controller → InferenceAdapter → approved backend`,
preserving admission control, the watchdog and explicit cache limits.

**The model's architectural context is NOT a verified safe context on this
24 GB machine.**

## 7. Updated registry states

| State | Value |
|---|---|
| `REGISTERED` | **true** |
| `DOWNLOAD_APPROVED` | **true** (Q6_K only) |
| `DOWNLOADED` | **true** |
| `INTEGRITY_VERIFIED` | **true** |
| `RUNTIME_COMPATIBLE` | **false** |
| `INFERENCE_VALIDATED` | **false** |

**A successful download does not imply compatibility.** The last two states were
deliberately left false and are tracked independently.

The registry was reconciled: the previously unverified `IFM/K2-Horizon-3.7B`
entry became the verified `IFM/K2-Horizon-3.7B-GGUF` entry;
`IFM/K2-Horizon-0.9B-GGUF` is marked **SUPERSEDED**; all other candidates keep
**no download approval**.

## 8. Existing Qwen installation preserved

| Check | Result |
|---|---|
| Qwen checkpoint directory | present, **4.3 MB** of metadata + links |
| Qwen weight blob resolves | **yes** — intact |
| MLX-LM environment | untouched |
| OpenHands environment | untouched |
| Resource Controller files | untouched |

## 9. Test results

| Run | Result | Exit |
|---|---|---|
| Resource Controller + gateway + profile suite (3.12.14) | `Ran 209 tests … OK (skipped=4)` | **0** |
| Same suite (3.9.6 system) | `Ran 209 tests … OK (skipped=19)` | **0** |

No inference, no weight loading, no context stress test.

## 10. Remaining requirements for protected K2 inference

1. **A runtime that supports `k2-horizon`.** Upstream llama.cpp does not. This
   needs either a K2-specific fork (reviewed and pinned) or upstream support —
   **a separate, owner-approved milestone**. Nothing was installed.
2. **A chat template.** The GGUF carries none, so one must be supplied and
   version-pinned before any agent use.
3. **Tool-calling verification.** Cannot be assumed; must be probed the way the
   Qwen endpoint was.
4. **An `InferenceAdapter`** implementing the protected path, with admission,
   watchdog and explicit cache budgets — the existing gateway already provides
   the contract.
5. **A guarded memory measurement** before any long-context claim.

## 11. What this report does NOT claim

- It does **not** claim the model is runnable — **it is not**.
- It does **not** claim `RUNTIME_COMPATIBLE` or `INFERENCE_VALIDATED`.
- It does **not** claim a safe context on this hardware.
- It does **not** claim upstream llama.cpp support that does not exist.
- No weights were loaded and no inference was run.
