# FORGE-003 — K2 Horizon Runtime Readiness

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Weights loaded:** **No.** **Inference run:** **No.** **Runtime built:** **No.**

---

## 1. Source repository, branch and pinned commit

| Item | Value |
|---|---|
| Publisher fork (linked) | `MBZUAI-IFM/llama.cpp` → **redirects to `ifm-ai/llama.cpp`** |
| Upstream parent | `ggml-org/llama.cpp` |
| Licence | **MIT** |
| Branch | **`model/K2Horizon`** |
| **Pinned commit** | **`42adf019f76013dac873b5b43950d54d5ab27216`** (2026-09-17) |
| Archived | No |

The linked URL is **not** the live repository — it returns **301 Moved
Permanently**. The live fork is `ifm-ai/llama.cpp`. A build plan that used the
old name would fail.

## 2. Actual architecture-support evidence

**Not inferred from `constants.py` alone** — the loading and model
implementation paths were inspected directly.

| Evidence | Finding |
|---|---|
| `gguf-py/gguf/constants.py` (fork) | `K2HORIZON = auto()`, `MODEL_ARCH.K2HORIZON: "k2-horizon"`, plus K2-specific ops `ATTN_V_GATE`, `ATTN_V_EXP` |
| `src/llama-arch.cpp` | `{ LLM_ARCH_K2_HORIZON, "k2-horizon" }` at line 157 |
| **`src/models/k2-horizon.cpp`** | **Dedicated model implementation present** (153 model files total) |
| `gguf-py/gguf/tensor_mapping.py` | K2 tensor mappings present |
| **Upstream `ggml-org/llama.cpp`** | **128 architectures — none is `k2-horizon`** |

**Status: `SOURCE_SUPPORT_FOUND`.** A K2-specific fork **is** required;
upstream does not support the architecture. `SOURCE_BUILDABLE_IN_PRINCIPLE` is
plausible but **not verified** — no build was attempted.

## 3. Apple Metal compatibility

**UNVERIFIED.** Metal support cannot be assessed without building. The fork is a
standard `ggml-org/llama.cpp` fork, so the Metal backend is inherited, but
**inheriting it does not prove the K2 ops are Metal-implemented.** The K2-specific
ops (`ATTN_V_GATE`, `ATTN_V_EXP`) would need CPU fallback if not.

## 4. GGUF chat-template discrepancy — **RESOLVED**

> **The previous report was WRONG.** It stated the chat template was
> **ABSENT**. It is **PRESENT**.

**Cause:** the earlier parser had a type-handling bug in its value reader and
aborted before reaching `tokenizer.chat_template`. The milestone's instruction
to *"use a GGUF metadata reader that supports all encountered types"* was
exactly right — a corrected parser finds the template immediately.

| Question | Answer |
|---|---|
| A. Does the GGUF embed a usable template? | **Yes — 43,040 characters** |
| B. Did the previous parser miss it? | **Yes — parser bug, now fixed** |
| C. Is an external official template required? | **No** |
| D. Which template is compatible? | **The embedded one** |
| E. Message serialization | `<\|ifm\|im_start\|>{role}\n{content}<\|ifm\|im_end\|>` |
| F. Turn-ending / generation tokens | End `<\|ifm\|im_end\|>`; generation opens `<\|ifm\|im_start\|>assistant\n<ifm\|think>\n` |
| G. Can the backend apply it? | **Not yet proven** — see the caveat below |

**The template is IFM-native, not ChatML** — delimiters are `<|ifm|im_start|>` /
`<|ifm|im_end|>`. It has **full tool-calling support**: `tool_presentation_format`
(json/xml/markdown), `tool_call_format` (json/xml/xml_typed), and renders calls as
`<ifm|tool_calls>` / `<ifm|tool_call>` / `<ifm|arg_key>` / `<ifm|arg_value>`. It
also implements thinking modes (`ifm|think`, `ifm|think_fast`, `ifm|think_faster`)
selected by `reasoning_effort`.

⚠️ **Blocker found:** the template calls **`validate_tools(...)`**, a *custom
Jinja function* rather than standard Jinja. A serving backend that does not
provide that global will **fail on any tool-calling request**. Whether the fork
supplies it is **unverified** and is a prerequisite for agent use.

**No generic ChatML template was invented and Qwen's template was not reused.**

## 5. Tokenizer / accounting status

| Property | Value |
|---|---|
| Tokenizer model | **`gpt2`** (BPE) |
| Pre-tokenizer | **`k2-horizon`** |
| Vocabulary | **250,624 tokens** |
| BOS / EOS | **0 / 1** (`add_bos_token=True`) |
| Special token style | `<\|ifm\|begin_of_text\|>`, `<\|ifm\|endoftext\|>` |

**Status: `TOKENIZER_VERIFIED`** (metadata level). **K2 token counts must never
be derived from Qwen's tokenizer** — the two share no vocabulary or
pre-tokenizer. The adapter enforces this: `require_k2_counter()` **rejects** any
counter whose `model_family` is not `k2-horizon`.

Exact K2 token accounting for tool schemas remains **not yet achievable** — it
requires the real tokenizer through a working runtime. The adapter **fails
closed** rather than approximating.

## 6. Protected adapter — implemented, mock-tested

`services/resource_controller/k2_adapter.py`

```text
ForgeOne Agent / OpenHands → ProtectedGateway → Resource Controller
                           → K2InferenceAdapter → supervised llama.cpp backend
```

Enforced: exact approved identity (repo, revision, filename, size, architecture)
· checkpoint path containment via **realpath**, so a symlink escaping storage is
refused · **chat-template presence** (absence is a hard failure) · K2-specific
token counting with Qwen rejection · reservation, admission, single-request
concurrency, timeout and release-on-every-path inherited from `ProtectedServer`
· no direct backend bypass.

**Mock transport only.** The existing Qwen protected path is untouched.

## 7. K2 resource profile

`configs/k2-resource-profile.yaml` — separate from Qwen; the Qwen cold-start
estimate is **not reused**.

| Term | Value |
|---|---|
| Checkpoint on disk | 4,161,403,264 bytes (4.16 GB) |
| Weights in memory (approx) | ~4.2 GiB |
| Runtime overhead | unknown — **must be measured** |
| KV bytes/token | 2 × 36 layers × 8 kv_heads × 128 × 2 = **147,456** |
| Prefill / transient | **unknown — the term that likely caused the earlier OOM** |
| Architectural max context | **524,288** — **NOT** a safe context on 24 GB |

**The safe local K2 context is UNKNOWN** and no peak-memory figure is invented.
All existing safety controls are retained unchanged: fresh telemetry, cold-start
admission, memory floor, post-admission reserve, macOS pressure check, swap and
page-out checks, independent watchdog before load, explicit budgets, owned-process
shutdown. **No threshold was lowered.**

## 8. Future build plan (project-local, nothing executed)

```text
source     ifm-ai/llama.cpp @ 42adf019f76013dac873b5b43950d54d5ab27216
           branch model/K2Horizon
clone to   $FORGEONE_HOME/storage/runtimes/k2-llama/
build deps cmake, Xcode CLT (present), Metal toolchain — all already available
           (no Homebrew, no global install, no curl-pipe-shell)
build      cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
           cmake --build build --config Release -j
binary     $FORGEONE_HOME/storage/runtimes/k2-llama/build/bin/llama-server
template   $FORGEONE_HOME/storage/runs/k2-chat-template.jinja (extracted)
rollback   rm -rf $FORGEONE_HOME/storage/runtimes/k2-llama
```

Expected clone footprint ~300–500 MB (shallow clone); build output ~50–150 MB.
**Not executed.** `cmake` availability is assumed from Xcode CLT and must be
confirmed before the build milestone.

## 9. Tests

| Run | Result | Exit |
|---|---|---|
| Full suite, Python 3.12.14 | `Ran 228 tests … OK (skipped=4)` | **0** |
| Full suite, Python 3.9.6 | `Ran 228 tests … OK (skipped=19)` | **0** |

**228 tests** — 209 preserved, **19 added** (`test_k2_adapter.py`), zero
failures. No weights loaded, no inference, no stress test.

## 10. Remaining blockers

| # | Blocker |
|---|---|
| 1 | **No runtime built** — `RUNTIME_BUILT` false |
| 2 | **`validate_tools()` Jinja global unverified** — tool calling will fail without it |
| 3 | **Metal support for K2-specific ops unverified** |
| 4 | **No checkpoint load validated** — `MODEL_LOAD_VALIDATED` false |
| 5 | **No inference or tool calling validated** |
| 6 | **Safe K2 context unknown**; architectural max 524,288 is not a safe context |
| 7 | Runtime overhead and prefill peak unmeasured |
| 8 | `cmake` availability unconfirmed |

## 11. Registry states

| State | Value |
|---|---|
| `DOWNLOADED` | **true** |
| `INTEGRITY_VERIFIED` | **true** |
| `BACKEND_SOURCE_VERIFIED` | **true** |
| `CHAT_TEMPLATE_VERIFIED` | **true** |
| `TOKENIZER_VERIFIED` | **true** |
| `ADAPTER_MOCK_TESTED` | **true** |
| `RUNTIME_BUILT` | **false** |
| `MODEL_LOAD_VALIDATED` | **false** |
| `INFERENCE_VALIDATED` | **false** |
| `TOOL_CALLING_VALIDATED` | **false** |

The last four are **deliberately false** and were not set during this task.

## 12. Next step for first protected K2 inference

**A separate, owner-approved build milestone**, in this order:

1. Confirm `cmake` availability.
2. Shallow-clone `ifm-ai/llama.cpp` at the pinned commit into
   `storage/runtimes/k2-llama/` and record the checkout SHA.
3. Build with Metal enabled; record the exact binary path and SHA.
4. **Verify `validate_tools()` is available** to the template — a hard gate for
   tool calling.
5. Load the checkpoint and record actual peak memory, with the watchdog live.
6. Only then, a guarded first inference through the protected adapter.

**The safe context must be measured, not assumed.** The architectural maximum is
**not** an approved context for this 24 GB Mac.

## 13. What this report does NOT claim

- It does **not** claim the runtime is buildable on this machine.
- It does **not** claim Metal works for the K2 ops.
- It does **not** claim the checkpoint loads or generates.
- It does **not** claim tool calling works.
- It does **not** claim a safe K2 context.
