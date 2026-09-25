# FORGE-003 — K2 Horizon Runtime Build

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Result:** **`RUNTIME_BUILT`** — binary produced, Metal backend compiled.
- **Weights loaded:** **No.** **Inference run:** **No.**

---

## 1. Actual source commit

| Item | Value |
|---|---|
| Repository | **`ifm-ai/llama.cpp`** (the linked `MBZUAI-IFM` name 301-redirects here) |
| Branch | `model/K2Horizon` |
| **Checked-out SHA** | **`42adf019f76013dac873b5b43950d54d5ab27216`** — **verified to match the approved pin** |
| Checkout location | `$FORGEONE_HOME/storage/runtimes/k2-llama/` (204 MB) |
| `src/models/k2-horizon.cpp` | **present** |
| `src/llama-arch.cpp` | `k2-horizon` registered |

## 2. Build commands and exit code

**A prerequisite was missing.** `cmake` was absent from every location checked
(`/usr/bin`, `/usr/local/bin`, `/opt/homebrew/bin`, `/Applications/CMake.app`,
CLT, both venvs, pip). `llama.cpp` requires CMake; there is no make-only path.

**cmake 4.4.3 was installed PROJECT-LOCALLY**, not system-wide — the task
prohibits *system-wide* dependency installs, and requires everything newly
installed to live under `$FORGEONE_HOME/storage/`. It is a checksum-verified
official binary release, invoked by absolute path, with no `PATH` change:

```bash
# download + verify (upstream cmake-4.4.3-SHA-256.txt)
shasum -a 256 cmake-4.4.3-macos-universal.tar.gz
# 0c5d65251c14cc884bfa16bdbed3c263ce5bffe2e21c0d0d00962cb0610464fa  VERIFIED
tar -xzf cmake-4.4.3-macos-universal.tar.gz -C storage/tools/cmake --strip-components=1
# -> storage/tools/cmake/CMake.app/Contents/bin/cmake   (265 MB)

# configure
storage/tools/cmake/CMake.app/Contents/bin/cmake \
  -S storage/runtimes/k2-llama -B storage/runtimes/k2-llama/build \
  -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON
# exit 0

# build (max 2 jobs, as required)
... --build storage/runtimes/k2-llama/build -j2
```

| Step | Exit code |
|---|---|
| Configure | **0** |
| **Build** | **0** — **0 error lines** in the log |
| Wall clock | ~180 s |

## 3. Binary path and SHA-256

| Artifact | Value |
|---|---|
| `llama-server` | `storage/runtimes/k2-llama/build/bin/llama-server` |
| Size | **33,472 bytes** (a thin launcher) |
| **SHA-256** | **`b97b4941953a3e4186c35bc4665d1433f03ce4ec12e20d5b2b220c0935433f0d`** |
| Version | `0.3.0-dev (build 1, commit 42adf01)` |
| Toolchain | AppleClang 21.0.0.21000334, **Darwin arm64** |
| Real implementation | `libllama-server-impl.dylib` (6.26 MB), dynamically linked via `@rpath` |
| `llama-cli` | also built (`a993bfc2…`) |

**Note:** the tiny launcher is why a naive `strings` scan finds no Metal — the
code lives in the dylibs. This is expected for this build layout.

## 4. Metal build result — **COMPILED**

| Evidence | Value |
|---|---|
| `CMakeCache.txt` | **`GGML_METAL:BOOL=ON`**, `GGML_METAL_EMBED_LIBRARY:BOOL=ON` |
| **`libggml-metal.0.22.0.dylib`** | **present, 2,061,704 bytes** |
| Metal strings in that library | **2,015** |
| Separate `.metallib` file | none — **deliberately embedded** (`EMBED_LIBRARY=ON`) |
| Also enabled | `GGML_ACCELERATE=ON`, `GGML_BLAS=ON`, `GGML_CPU=ON` |

**Metal backend is compiled and linked.** `llama-server` links
`libggml-metal` through the ggml backend registry.

⚠️ **Not the same as Metal working for K2.** That the backend compiled does not
prove the K2-specific ops (`ATTN_V_GATE`, `ATTN_V_EXP`) are Metal-implemented or
correct — that requires loading the checkpoint, which this task forbids.

## 5. Chat-template and `validate_tools` findings

> **The previous readiness report's blocker was WRONG.**

It stated the template *"calls `validate_tools()`, a custom Jinja function rather
than standard Jinja… a backend that does not supply it will fail."*

**`validate_tools` is a macro defined INSIDE the template**:

```jinja
{%- macro validate_tools(tools_list, classify=true) -%}   <-- line 239
```

It is used at line 794 as `{{- validate_tools(available_tools, ...) }}`, and it
is **self-contained** — the template also defines `validate_schema` (line 163)
and 30+ helper macros, with **no imports or includes**. The template is
self-sufficient and requires **no external Jinja global**.

**The tool-calling blocker is RESOLVED.**

Confirmed on the built server: `llama-server --help` reports
**`--jinja, --no-jinja   whether to use jinja template engine for chat (default: enabled)`**.
The embedded template will be applied by default. `--chat-template-kwargs` is
available for template parameters.

## 6. Tokenizer / API compatibility

| Item | Finding |
|---|---|
| `llama-tokenize` | **built** |
| Tokenizer in GGUF | `gpt2` BPE, **250,624 tokens**, `pre = k2-horizon` |
| `--ctx-size` | supported (`-c`) |
| `--host` / `--port` | supported |
| `--jinja` | **default enabled** |
| Server CLI flags surfaced | 10 top-level |

Full token-level validation requires loading the checkpoint, which is forbidden
here. **`TOKENIZER_VERIFIED` remains metadata-level only.**

## 7. Adapter status

`services/resource_controller/k2_adapter.py` — unchanged and still correct. The
compiled backend contract matches it: `llama-server` accepts host/port/ctx-size
and applies the embedded template, so the adapter's identity, path-containment,
template-presence and K2-only-counting checks remain valid.

**One correction carried into the adapter's documentation:** the template
blocker is gone, so `chat_template` presence is satisfied by the real artefact.

**No code changes were required.**

## 8. Corrected memory profile

`configs/k2-resource-profile.yaml` updated with **verified** values:

| Term | Value | Status |
|---|---|---|
| Weight file | **4,161,403,264 bytes = 3.88 GiB** | **verified** |
| Layers / kv_heads / key_len / value_len | 36 / 8 / 128 / 128 | **verified from GGUF** |
| **KV bytes/token** | **(128+128) × 36 × 8 × 2 = 147,456** | **formula verified** |
| KV @ 2048 | 0.28 GiB | derived |
| KV @ 4096 | 0.56 GiB | derived |
| KV @ 8192 | 1.12 GiB | derived |
| Architectural max 524,288 | **72.0 GiB** — **definitively not runnable** | derived |
| Runtime overhead | **UNKNOWN** | **not invented** |
| Transient prefill reserve | **UNKNOWN** | **not invented** |

**Qwen's memory costs were NOT reused.** The two backends differ (MLX vs
llama.cpp), so any reuse would be meaningless. The two unknown terms stay
`null` and the profile **fails closed** until measured.

**The safe K2 context remains UNKNOWN.** The 524,288 architectural maximum
requires 72 GiB of KV cache alone — it is emphatically not an approved context
for a 24 GB machine.

## 9. Test results

| Run | Result | Exit |
|---|---|---|
| Full suite, Python 3.12.14 | `Ran 228 tests … OK (skipped=4)` | **0** |
| Full suite, Python 3.9.6 | `Ran 228 tests … OK (skipped=19)` | **0** |

No weights loaded, no inference, no stress test.

## 10. All new files under ForgeOne storage — confirmed

| Path | Size |
|---|---|
| `storage/tools/cmake/` | 265 MB |
| `storage/runtimes/k2-llama/` (source + build) | 343 MB |
| `storage/logs/k2-*.log` | small |
| `storage/tmp/` | emptied |

**0 files tracked under `storage/`** — `git ls-files storage/` returns 0.
`git check-ignore` confirms all four paths ignored. No symlink escapes storage.
Disk: **711 GiB free**.

## 11. Remaining blockers before first protected K2 inference

| # | Blocker |
|---|---|
| 1 | **No checkpoint load validated** — `MODEL_LOAD_VALIDATED` false |
| 2 | **Metal support for the K2-specific ops unproven** |
| 3 | **No inference or tool calling validated** |
| 4 | **Safe K2 context unknown**; runtime overhead and prefill peak unmeasured |
| 5 | Resource profile fails closed until (4) is measured |

**Next step:** a separately approved, guarded **first load + tiny inference**
through the protected adapter, with the watchdog live, recording actual peak
memory and confirming Metal actually engages for `k2-horizon`.

## 12. What this report does NOT claim

- It does **not** claim the checkpoint loads or generates.
- It does **not** claim Metal works for the K2 ops — only that the backend compiled.
- It does **not** claim tool calling works end-to-end.
- It does **not** claim a safe context.
