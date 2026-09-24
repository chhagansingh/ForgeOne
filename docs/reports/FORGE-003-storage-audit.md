# FORGE-003 — Storage Audit & Model Registry

- **Milestone:** M1 / FORGE-003
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Inference run:** **No.** **OpenHands retried:** **No.** **Models downloaded:** **None.**
- **Result:** storage is already ~97% centralized; one 855 MB cache identified
  for owner-approved cleanup; registry and controlled manager implemented.

Paths below use `$FORGEONE_HOME` (the repository root). Exact absolute paths are
recorded in the private evidence under `storage/runs/`.

---

## 1. Exact current installation and model paths

| Asset | Resolved location | Size | Inside `$FORGEONE_HOME`? | Git | In use by |
|---|---|---|---|---|---|
| `uv` binary 0.12.18 | `storage/tools/uv` | 36 MB | **yes** | ignored | all env management |
| Python 3.12.14 (uv-managed) | `storage/tools/python` | 71 MB | **yes** | ignored | both venvs |
| MLX-LM env (0.31.3) | `storage/bakeoff/model-venv` | 336 MB | **yes** | ignored | tokenizer + protected inference |
| OpenHands SDK env (1.49.5) | `storage/bakeoff/openhands-venv` | 550 MB | **yes** | ignored | agent SDK |
| Qwen3-4B checkpoint | `storage/cache/huggingface/hub` | **2.1 GB** | **yes** | ignored | protected smoke test |
| HF hub cache | `storage/cache/huggingface/hub` | 2.1 GB | **yes** | ignored | — |
| Xet cache | `storage/cache/xet` | <1 MB | **yes** | ignored | — |
| Runtime logs | `storage/logs` | 8 KB | **yes** | ignored | — |
| Telemetry / captures | `storage/runs` | 104 KB | **yes** | ignored | — |
| Synthetic fixture | `/tmp/forge002-fixture` | 8 KB | **no** | n/a | **disposable by design** |
| OpenHands workspace | `/tmp/forge003-oh-workspace` | — | **no** | n/a | disposable |
| **uv download cache** | `~/.cache/uv` | **855 MB** | **no** | n/a | uv only |
| User HF cache | `~/.cache/huggingface` | 136 KB | **no** | n/a | **NOT ForgeOne-owned** |

**Total under `$FORGEONE_HOME/storage/`: 3.1 GB.**

**Symlink audit:** **zero** symlinks escape `storage/`. The checkpoint's
snapshot links resolve to blobs **inside** the same tree — so snapshot files and
their backing blobs are the *same* bytes and are **not** double-counted. Blob
directory alone is 4.3 MB (small files); the 2.1 GB is the weight shard.

## 2. Assets already inside the desired folder

Everything that matters: `uv`, the managed Python, both virtual environments,
the checkpoint, the HF/Xet caches, logs and telemetry. **~97% of ForgeOne-owned
bytes are already centralized** — no action needed.

## 3. Assets currently outside it

| Asset | Size | ForgeOne-owned? | Safe to migrate? |
|---|---|---|---|
| `~/.cache/uv` | **855 MB** | **yes** (created by our uv usage) | **Yes** — pure download cache, no absolute paths inside |
| `~/.cache/huggingface` | 136 KB | **No** — pre-existing PaddleOCR configs | **No — never migrate** |
| `/tmp/forge002-fixture`, `/tmp/forge003-oh-workspace` | tiny | yes, but **disposable by design** | n/a — deliberately outside the repo |

**Virtual environments were deliberately NOT moved.** Python venvs embed
absolute interpreter paths in `bin/*` scripts and `pyvenv.cfg`; moving one
breaks it silently. Both venvs are *already* inside `storage/`, so there is
nothing to migrate — and any future relocation must be a **recreate**, not a
move.

## 4. Storage configuration implemented

| File | Purpose |
|---|---|
| `configs/storage-policy.yaml` | Canonical locations, env vars, guardrails, per-asset disposition |
| `scripts/forgeone-env.sh` | Exports the policy into any shell + children |

**Twelve variables** are exported, all resolving under `storage/` — verified
reaching child processes:

`HF_HOME` · `HF_HUB_CACHE` · `HF_XET_CACHE` · `HF_ASSETS_CACHE` ·
`UV_CACHE_DIR` · `UV_PYTHON_INSTALL_DIR` · `UV_PYTHON_BIN_DIR` · `UV_TOOL_DIR` ·
`PIP_CACHE_DIR` · `TMPDIR` · `XINFERENCE_HOME` · `FORGEONE_HOME`

`XINFERENCE_HOME` is **documented for a future milestone only** — Xinference is
**not installed** and this task did not install it.

**Nothing global was touched:** no shell profile, no `HOME`, no system Python,
no macOS swap, no unrelated application setting. The launcher sets variables for
the current shell and its children only.

**Directories created:** `storage/{models/{llm,image,video,vision,adapters},cache/{huggingface,uv,pip,xet,assets},tools,environments,runtimes,runs,logs,tmp,secrets,worktrees,artifacts,benchmarks}`.
`storage/secrets` is `chmod 700`.

## 5. Safe migrations completed

**None were necessary** — the required destinations already existed and are in
use. Creating the empty canonical directories is the only filesystem change.

## 6. Migrations requiring approval

| # | Item | Proposal | Risk |
|---|---|---|---|
| 1 | `~/.cache/uv` (855 MB) | The launcher now redirects **new** cache writes to `storage/cache/uv`. Once you are satisfied nothing needs the old cache, **delete** `~/.cache/uv`. | **Very low** — it is a pure download cache; uv re-fetches on demand. **Not deleted or moved**, because deletion is irreversible and the milestone forbids deleting cache data without approval. |
| 2 | Virtual environment relocation | **Not proposed.** Recreate rather than move if ever required. | High if moved |
| 3 | `/tmp` fixtures | Keep disposable; they are intentionally outside Git. | None |

## 7. Model registry and actual download status

`configs/model-registry.yaml` — **8 entries, 0 approved for download, 0 downloaded.**

| Model | Role | Download | Verification | Approval |
|---|---|---|---|---|
| `mlx-community/Qwen3-4B-Instruct-2507-4bit` | inference test model | **installed** | **verified** — real tokenizer, tool calling, protected session PASS | approved & installed |
| `IFM/K2-Horizon-3.7B` | compact coding challenger | not downloaded | **unverified** | NOT approved |
| `IFM/K2-Horizon-0.9B-GGUF` | lightweight specialist | not downloaded | **unverified** | NOT approved |
| `Qwen3.8-27B` | future larger candidate | not downloaded | **unverified** | NOT approved |
| `FLUX` | image adapter (blueprint M8) | not downloaded | **unverified** | NOT approved |
| `LTX` | video adapter (blueprint M8) | not downloaded | **unverified** | NOT approved |
| `JEV` | future Decision Layer candidate | not downloaded | **unverified** | NOT approved |
| `LAYA` | future Decision Layer candidate | not downloaded | **unverified** | NOT approved |

**Honest gaps, stated plainly:**

- **No repository IDs were invented.** `IFM/K2-Horizon-3.7B`,
  `IFM/K2-Horizon-0.9B-GGUF` and `Qwen3.8-27B` were named by you but their
  upstream repository, revision, format and quantization are **unconfirmed**.
  They are recorded as `unverified` and the manager **refuses to download them**.
- **`FLUX` and `LTX` are blueprint *families*, not checkpoints.** The blueprint
  says "media adapter: FLUX / LTX via compatible runtime" — it names no exact
  repository. Multiple FLUX variants exist with differing licences, so no ID was
  fabricated.
- **`JEV` is not assumed to be a downloadable checkpoint**, per the milestone.
- **`Qwen3.8-27B` feasibility is doubtful on 24 GB.** A 27B model at 4-bit is
  roughly 15 GiB of weights before KV cache, which would breach the
  one-heavy-model-at-a-time rule alongside IDEs.

Each entry records the full required field set: ID, source, role, architecture,
parameters, format, quantization, backend, licence, revision, disk footprint,
runtime-memory estimate, context and tool-calling requirements, destination,
download status, verification status and approval status.

## 8. Model manager

`scripts/model-manager.py` — **dry-run by default**, registry-driven.

| Rule | Enforced by |
|---|---|
| Registry-based selection only | `find()` rejects any ID not in the registry |
| Dry-run before download | `--execute` required; otherwise prints the plan |
| Explicit destination | `resolve_destination()` → under `storage/models/` |
| Disk-space check | refuses below the **150 GiB** guardrail |
| Revision pinning | pinned revision verified locally |
| Resumable download | delegated to `huggingface_hub.snapshot_download` |
| Completeness verification | `verify` — file count, shards, broken links, size |
| No concurrent large downloads | `max_concurrent_downloads: 1` |
| No auto-load after download | `auto_load_after_download: false` |
| No silent global-cache fallback | refuses unless `HF_HOME` resolves under `storage/` |
| No substitution without approval | refuses anything outside `approved_download_batch` |

**Demonstrated refusal:** `download IFM/K2-Horizon-3.7B` → `BLOCKED: entry is
unverified — its repository, revision, format or quantization is not confirmed.`

## 9. Tests and exit codes

| Check | Result |
|---|---|
| Resource Controller + gateway suite (3.12.14) | `Ran 209 tests … OK (skipped=4)` — **exit 0** |
| Resource Controller + gateway suite (3.9.6) | `Ran 209 tests … OK (skipped=19)` — **exit 0** |
| Qwen checkpoint completeness | **COMPLETE** — 11 files, 1 weight shard, 2.12 GiB, **no broken links** |
| MLX-LM env functional **without loading weights** | **OK** — config + tokenizer; `has_chat_template=True`, `has_tool_calling=True` |
| OpenHands SDK imports | **OK** |
| Env vars reach child processes | **PASS** — all 8 checked resolve under `storage/` |
| Model-manager dry-run destinations | **PASS**; unverified entries correctly blocked |
| Nothing generated is Git-tracked | **PASS** — 0 `storage/` files staged |
| No unexpected external destination | **PASS** — 0 escaping symlinks |

## 10. Current disk usage and remaining space

| | |
|---|---|
| Volume | 926 GiB |
| Used | 182 GiB |
| **Available** | **714 GiB** |
| Guardrail | 150 GiB minimum free |
| Headroom above guardrail | **564 GiB** |
| ForgeOne under `storage/` | **3.1 GB** |

## 11. Proposed first controlled model-download batch

**Nothing is proposed for immediate download.** Every candidate except the
already-installed Qwen3-4B is `unverified`, and downloading an unverified model
would mean guessing a repository ID.

Recommended **verification-first** sequence:

1. **You supply exact repository IDs** (or approve me looking them up) for the
   models you actually want next. Most useful first: the **compact coding
   challenger** — the current blocker is agent-loop context, and a small coder
   would not fix that, so **`IFM/K2-Horizon-0.9B-GGUF` is the lowest-risk first
   candidate** if its ID can be confirmed (it is small, and GGUF is cheap to
   verify).
2. Verify ID → revision → format → licence against the upstream source.
3. Record verification in the registry and set `approval`.
4. Add it to `approved_download_batch`.
5. Run `model-manager.py download <id>` **without** `--execute` to review the plan.
6. Then `--execute`, with the disk guardrail and single-download rule active.

**A 27B model should not be in the first batch** — feasibility on 24 GB is
unresolved.

## 12. What this report does NOT claim

- It does **not** claim any model beyond Qwen3-4B is available or approved.
- It does **not** claim the 855 MB uv cache has been removed — it has not.
- It does **not** claim storage is 100% centralized; it is ~97%, with the
  remainder requiring owner approval.
- It does **not** claim Xinference is installed or configured — only documented.
