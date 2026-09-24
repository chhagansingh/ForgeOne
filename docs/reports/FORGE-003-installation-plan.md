# FORGE-003 — Installation and Bake-off Plan

- **Milestone:** M1 — Agent bake-off (execution phase)
- **Issue:** `FORGE-003`
- **Date:** 2026-09-24
- **Branch:** `feat/forge-003-runtime-bakeoff`
- **Base commit:** `2c087bec7b36327443564cfa304430506e124748` (`main`, PR #2 merged)
- **Status:** **SUPERSEDED IN PART — see the status note below.**

Evidence labels as in FORGE-002: `[DOC]` documented, `[OBS]` observed,
`[INF]` inferred, `[UNT]` untested.

> ## 📌 STATUS UPDATE (added after execution began)
>
> This document was written as a **request for approval**. That approval was
> granted (A1–A6) and execution proceeded. **The "nothing has been installed"
> statement in the original gate below is no longer true** and is retained only
> as the historical record of what was requested.
>
> **What actually happened:**
>
> | Phase | Outcome |
> |---|---|
> | Phase 1 — toolchain | **DONE.** uv 0.12.18 (checksum verified), Python 3.12.14, mlx-lm 0.31.3, mlx 0.32.2, checkpoint `mlx-community/Qwen3-4B-Instruct-2507-4bit` @ `50d4277…`. 2.6 GB, all under Git-ignored `storage/`. No privileges, no system changes. |
> | Phase 2 — inference preflight | **8/8 PASS**, including genuine structured tool calling. Evidence preserved in the incident report. |
> | Phase 2 — context/memory verification | **FAILED — P0 host memory incident.** See [`FORGE-003-P0-memory-incident.md`](FORGE-003-P0-memory-incident.md). |
> | Phase 3 — agent runtimes | **NOT STARTED** |
> | Phase 4 — bake-off | **NOT STARTED** |
> | A6 — security action | **DONE.** Verified PID 28474 (`http.server 8080 --bind 0.0.0.0`, serving a client APK) terminated gracefully; port released; client artifact untouched. |
>
> **Execution is suspended** pending the
> [Resource Controller](../architecture/resource-controller-design.md).
>
> ### Original approval gate (historical)

> ## ⛔ APPROVAL GATE
>
> This document is a **request for approval**, not a record of work done.
> **No package, runtime, toolchain or model has been installed or downloaded.**
> Section 8 lists exactly what needs approving. See §9 for what was executed
> (read-only inspection only).

---

## 1. Phase 0 — source of truth verification

| Check | Result |
|---|---|
| `origin/main` contains FORGE-002 | **YES** |
| Commit `f317b5e2310b0d8599538485fa448809143dc2e6` is an ancestor of `origin/main` | **YES** (`git merge-base --is-ancestor` → true) |
| `origin/main` HEAD | `2c087bec7b36327443564cfa304430506e124748` — *"Merge pull request #2 from chhagansingh/feat/forge-002-runtime-evaluation"* |
| FORGE-002 deliverables present on `main` | **All four** (`FORGE-002-runtime-evaluation.md`, `prompts-chat-integration.md`, `prompt-intelligence-design.md`, `specs/001-agent-runtime/spec.md`) |
| Canonical path | `$FORGEONE_HOME` (owner-local, redacted) |
| Working tree | **Clean** — 0 uncommitted entries |
| Branch created | `feat/forge-003-runtime-bakeoff` from `2c087be` |
| Credentials | **Not inspected, printed or probed at any point** |

---

## 2. Phase 1 — live environment inventory

Re-measured on this host, not carried over from FORGE-002.

### 2.1 Toolchain

| Tool | Status | Version |
|---|---|---|
| Python | **3.9.6 only** (Xcode-bundled) | `3.10`–`3.14` all **ABSENT** |
| `uv` / `uvx` / `pipx` / `conda` / `poetry` | **All ABSENT** | — |
| Node / npm / npx | **All ABSENT** | — |
| Docker / podman / colima / lima | **All ABSENT** | — |
| Git | Present | 2.54.0 (Apple Git-157) |
| Xcode | Present | 27.0 |
| Java | Present | OpenJDK 21.0.11 |
| Free disk | — | **724 Gi** available of 926 Gi |
| Memory | — | 24 GB unified |

### 2.2 Model servers and inference runtimes

| Runtime | Status |
|---|---|
| Ollama, llama-server, llama-cli, `mlx_lm.server`, vLLM, LM Studio | **All ABSENT** |
| Cached LLM checkpoints | **None.** `~/.cache/huggingface` is 136 KB and contains only four small PaddleOCR model directories |
| Model weights >100 MB anywhere in `$HOME` | **None found** |
| LLM provider credentials in the agent environment | **None** |

`[INF]` There is **no usable inference endpoint of any kind** on this machine
today, and no credential for a hosted one. Phase 2 must therefore provision one.

### 2.3 Port conflicts and security observations ⚠️

Three `python3 -m http.server` processes were found already listening. These are
**not** agent runtimes or inference endpoints — they are static directory-listing
servers, but two of them collide with ports this project intends to use.

| Port | Process | Serves | Uptime | Conflict |
|---|---|---|---|---|
| **8000** | `python3 -m http.server 8000` | `$HOME/Desktop` | ~3 days | **OpenHands Agent Canvas default port** |
| **8080** | `python3 -m http.server 8080 --bind 0.0.0.0` | (all interfaces) | ~3 days | General |
| **8765** | `python3 -m http.server 8765` | an Android APK debug output directory | ~1 day | **ForgeOne's proposed custom port** (blueprint §2) |

None responded to `/v1/models` (HTTP 404 / connection failure), confirming they
are not inference servers.

**Security observation — flagged, not acted on:** the process on port 8080 is
bound to `0.0.0.0`, meaning a directory listing is exposed to the local network.
Two others are also reachable off-loopback by default. This contradicts the
blueprint's "default API bind to loopback" rule.

**I did not stop, kill or modify any of these processes** — they are not mine and
the action was not authorised. Disposition is requested in §8.

`[INF]` For the bake-off, the practical consequence is that **port 8000 and port
8765 are unavailable**. The plan uses explicit alternative ports (§4.4).

### 2.4 Re-verified upstream requirements — corrections to FORGE-002

Per instruction, upstream requirements were re-checked rather than assumed.
**Three findings differ from the FORGE-002 report:**

| Item | FORGE-002 said | Re-verified now | Action |
|---|---|---|---|
| OpenHands SDK Python | 3.13 | PyPI `requires_python = ">=3.12"` (3.13 is the repo's *dev* `.python-version`) | **Correction** — Python 3.12 suffices |
| Hermes version | 0.21.4 | GitHub repo `0.21.4`; **PyPI `hermes-agent` is 0.19.0** | **New finding** — PyPI lags upstream by 2 minor versions |
| Hermes install | `curl \| bash` only | Still the documented path; PyPI wheel (9.67 MB) and shallow-clone-from-source are viable alternatives | **New options** |

Unchanged and re-confirmed:

- Hermes `requires-python = ">=3.11,<3.14"`, `.python-version` = 3.11 `[OBS]`
- Hermes official installer is still `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash` `[DOC]`
- OpenHands Agent Canvas still requires Node ≥24 `[OBS]`
- Hermes repo checkout ≈ 1.0 GB `[OBS]`

### 2.5 Inference-endpoint compatibility — VERIFIED for both runtimes

Phase 2's central question: *can one endpoint serve both runtimes?* **Yes.**

| Runtime | Mechanism | Evidence |
|---|---|---|
| **OpenHands SDK** | `LLM(...)` exposes `base_url: str \| None` documented as *"Custom API base URL"* | `[OBS]` read from `openhands-sdk/openhands/sdk/llm/llm.py` |
| **Hermes** | `ProviderProfile` dataclass with a `base_url` field and `env_vars` incl. `*_BASE_URL`; **38 bundled provider plugins including one named `custom`**; user profiles can be dropped into `$HERMES_HOME/plugins/model-providers/<name>/` | `[OBS]` read from `providers/README.md`, `plugins/model-providers/README.md` |

`[INF]` A single OpenAI-compatible endpoint is therefore sufficient for both.
This is the assumption that makes the bake-off comparable — and it is now
verified, not assumed.

**Not assumed:** the IDE's own AI subscription is *not* treated as an available
API endpoint. No such endpoint was found, and none was probed.

---

## 3. Installation plan

**Guiding constraint:** project-isolated, no system Python modification, no
global framework installs, no Homebrew, no `sudo`, no remote shell scripts.

### 3.1 Bootstrap — `uv` as a standalone binary (no installer script)

`[OBS]` `uv` 0.12.18 publishes a macOS arm64 tarball with a SHA256 sidecar.
`uv` itself declares `requires_python >= 3.8`, but the standalone binary needs
**no Python at all** — which is exactly why it is the right bootstrap.

```bash
TOOLS="$FORGEONE_HOME/storage/tools"
mkdir -p "$TOOLS/uv"

curl -fsSL -o /tmp/uv.tar.gz \
  https://github.com/astral-sh/uv/releases/download/0.12.18/uv-aarch64-apple-darwin.tar.gz
curl -fsSL -o /tmp/uv.tar.gz.sha256 \
  https://github.com/astral-sh/uv/releases/download/0.12.18/uv-aarch64-apple-darwin.tar.gz.sha256

# verify BEFORE extracting
cd /tmp && shasum -a 256 -c uv.tar.gz.sha256

tar -xzf /tmp/uv.tar.gz -C "$TOOLS/uv" --strip-components=1
"$TOOLS/uv/uv" --version
```

- **Footprint:** 16.18 MB compressed `[OBS]`
- **Privileges:** none
- **PATH:** *not* modified — `uv` is always invoked by absolute path
- **Location:** `$FORGEONE_HOME/storage/tools/` → inside the Git-ignored
  `storage/` tree, so nothing enters version control
- **Why not `curl | sh`:** that is the official Astral installer, but it is a
  remote script. Downloading a checksum-verified release artefact is strictly
  safer and fully reversible.

### 3.2 Python 3.12 — uv-managed, project-isolated

```bash
export UV_PYTHON_INSTALL_DIR="$FORGEONE_HOME/storage/tools/python"
"$TOOLS/uv/uv" python install 3.12
"$TOOLS/uv/uv" python list
```

- Installs a managed CPython **only** under `storage/tools/python`
- The system Python 3.9.6 is **never touched**
- `UV_PYTHON_INSTALL_DIR` is exported per-command, not written to any shell rc

### 3.3 OpenHands SDK environment

Deliberately the **SDK, not Agent Canvas** — this avoids Node ≥24, Docker and a
68.6 MB / 11,007-file npm package entirely.

```bash
export UV_PYTHON_INSTALL_DIR="$FORGEONE_HOME/storage/tools/python"
VENV="$FORGEONE_HOME/storage/bakeoff/openhands-venv"

"$TOOLS/uv/uv" venv --python 3.12 "$VENV"
"$TOOLS/uv/uv" pip install \
  --python "$VENV/bin/python" \
  openhands-sdk openhands-agent-server openhands-tools openhands-workspace
```

`[OBS]` PyPI 1.49.5 — wheel sizes and declared dependency counts:

| Package | Version | Requires-Python | Wheel | Direct deps |
|---|---|---|---|---|
| `openhands-sdk` | 1.49.5 | `>=3.12` | 0.83 MB | 21 |
| `openhands-agent-server` | 1.49.5 | `>=3.12` | 0.31 MB | 12 |
| `openhands-tools` | 1.49.5 | `>=3.12` | 0.19 MB | 10 |
| `openhands-workspace` | 1.49.5 | `>=3.12` | 0.04 MB | 4 |

**Estimated venv on disk: 300–500 MB** `[INF]` — the four wheels are only
~1.4 MB, but 47 direct dependencies (plus transitives: pydantic, httpx, litellm,
rich, etc.) dominate. Exact size will be measured and recorded at install time.

### 3.4 Hermes environment

The official `curl | bash` installer is **not used**. Two alternatives:

**Option H1 (recommended) — shallow clone, current code:**

```bash
SRC="$FORGEONE_HOME/storage/bakeoff/hermes-src"
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git "$SRC"
cd "$SRC" && git rev-parse HEAD   # record the exact revision

export UV_PYTHON_INSTALL_DIR="$FORGEONE_HOME/storage/tools/python"
VENV="$FORGEONE_HOME/storage/bakeoff/hermes-venv"
"$TOOLS/uv/uv" venv --python 3.12 "$VENV"
"$TOOLS/uv/uv" pip install --python "$VENV/bin/python" -e "$SRC"
```

- Gets **0.21.4** (current upstream), pinned by recorded commit SHA
- `--depth 1` keeps the download far below the full ~1.0 GB history `[INF]`

**Option H2 (lighter) — PyPI wheel:** `uv pip install hermes-agent==0.19.0`
→ 9.67 MB wheel `[OBS]`, but **two minor versions behind upstream**.

**Recommendation: H1.** The bake-off should compare current releases; H2 is
recorded as a fallback if H1's dependency resolution proves heavy.

`[INF]` Estimated venv: 500 MB – 1 GB. Hermes declares many optional extras and
exact-pinned dependencies. Exact size measured at install time.

### 3.5 Summary

| Item | Footprint | Privileges | Location | IDE impact | Rollback |
|---|---|---|---|---|---|
| `uv` binary | 16.18 MB | none | `storage/tools/uv/` | none | `rm -rf storage/tools/uv` |
| Python 3.12 | ~50–70 MB `[INF]` | none | `storage/tools/python/` | none | `rm -rf storage/tools/python` |
| OpenHands venv | 300–500 MB `[INF]` | none | `storage/bakeoff/openhands-venv/` | none | `rm -rf` the venv |
| Hermes source | ~100–300 MB `[INF]` | none | `storage/bakeoff/hermes-src/` | none | `rm -rf` the source dir |
| Hermes venv | 500 MB – 1 GB `[INF]` | none | `storage/bakeoff/hermes-venv/` | none | `rm -rf` the venv |
| **Total** | **≈ 1–2 GB** | **none** | all under Git-ignored `storage/` | **none** | delete `storage/` subdirs |

**No admin privileges are required for any step. No existing IDE is modified.**
Every artefact lives under `storage/`, which `.gitignore` excludes in full —
so none of it can be committed, and none of it touches the system.

---

## 4. Phase 2 — inference endpoint plan

### 4.1 What exists today

**Nothing.** No local server, no cached LLM, no provider credential (§2.2).
An endpoint must be provisioned.

### 4.2 Recommended option — MLX-LM local server

`[OBS]` `mlx-lm` 0.31.3, `requires_python >=3.8`, 16 declared deps. Apple-native,
pip-installable through the same `uv` we already need, and ships
`mlx_lm.server` which exposes an **OpenAI-compatible** HTTP API — exactly what
both runtimes need.

```bash
VENV="$FORGEONE_HOME/storage/bakeoff/model-venv"
"$TOOLS/uv/uv" venv --python 3.12 "$VENV"
"$TOOLS/uv/uv" pip install --python "$VENV/bin/python" mlx-lm

# port 8081 chosen deliberately - 8000, 8080 and 8765 are all occupied (§2.3)
"$VENV/bin/python" -m mlx_lm.server \
  --model <MODEL_ID> --host 127.0.0.1 --port 8081
```

**Model options** — smallest-first, all requiring approval before download:

| Option | Approx. download | Memory at 32k ctx `[INF]` | Tool calling |
|---|---|---|---|
| Small instruct model, 4-bit, ~4B params | ~2.2–2.5 GB | ~4 GB | **Must be verified** `[UNT]` |
| 8-bit / larger 4B variant | ~4–5 GB | ~6 GB | **Must be verified** `[UNT]` |
| Coder-tuned ~7B, 4-bit | ~4.5–5 GB | ~7 GB | **Must be verified** `[UNT]` |

`[UNT]` **Tool-calling support is not asserted for any specific model here.** It
is a hard requirement for an agent runtime and will be validated with a direct
`/v1/chat/completions` tool-call probe **before** the bake-off begins. A model
that fails the probe disqualifies itself — it will not be papered over.

**Cost:** zero marginal cost. All local, loopback-only, no cloud service, no
paid resource. 24 GB unified memory and 724 Gi free disk are comfortably
sufficient.

### 4.3 Alternative — approved hosted endpoint

If the owner prefers, a single approved provider key would remove the model
download entirely. This requires an explicit data-classification decision: the
bake-off fixture is disposable test code, **not** client material, so it is
low-risk — but the choice is the owner's, and no key was sought or used.

### 4.4 Explicitly rejected

| Option | Why rejected |
|---|---|
| Ollama | No Homebrew, and the macOS app is a large install; adds nothing over MLX |
| Agent Canvas (`npm i -g @openhands/agent-canvas`) | Needs Node ≥24, 68.6 MB / 11,007 files; the SDK answers the runtime question |
| Docker sandbox | Docker absent; requires Docker Desktop (large, licensed, privileged) |
| Remote install scripts | Prohibited without approval; the artefact route is safer |
| Production-scale models | Out of scope and disproportionate to a 2-file fixture |

---

## 5. Phase 3 — evaluation design

### 5.1 Fixture and isolation

The FORGE-002 fixture is **intact and re-verified** at `/tmp/forge002-fixture`
@ `9157d02`, with a reproduced baseline of **2 failures / 6 tests / exit 1**.

Isolation plan:

```text
/tmp/forge003-hermes/       <- independent worktree, Hermes ONLY
/tmp/forge003-openhands/    <- independent worktree, OpenHands ONLY
```

Both are created fresh from the fixture baseline commit. **Neither runtime may
read or write the other's worktree.** Neither may touch any client repository.
The fixture is outside the ForgeOne repo and outside Git, so the repository
cannot be polluted by a misbehaving agent.

### 5.2 Procedure (identical for both runtimes)

1. Fresh worktree from baseline `9157d02`.
2. Record the **initial failing test result separately** — this is the
   pre-existing state, not a regression.
3. Run the agent with the same instruction, same model, same endpoint.
4. Let it read the repository, identify the defect, and produce a minimal patch.
5. Run the real test suite. Repair if necessary (bounded retries).
6. Rerun tests; record the **final** result separately from the initial one.
7. Inspect `git diff` — minimality, unrelated edits.
8. Produce the evidence report.

### 5.3 Measurements

| Category | Metric |
|---|---|
| Initialisation | Time from command to agent ready |
| Tool execution | Tool calls made; correct / malformed / repeated; recovery after failure |
| Task success | Final suite result, exit code, diff minimality |
| Errors | Full error log; any crash, hang or timeout |
| Timing | Wall clock, end to end |
| Memory | Peak RSS of the runtime **and** the model server, separately |
| Model usage | Tokens in/out and request count where the endpoint exposes them |
| Custom validators | Whether the runtime can invoke ForgeOne's own test command as a verification tool |
| Isolation | Confirmed: primary checkout and the other worktree untouched |

**Reporting rule:** the initial failing result and the final result are recorded
as **two distinct rows**. A runtime that fixes nothing is reported as having
fixed nothing.

### 5.4 GUI usability vs SDK capability — separate categories

These are **not** interchangeable, and a headless SDK success will never be
reported as a GUI success:

| Category | What is assessed | Note |
|---|---|---|
| **A. SDK / headless capability** | Can the runtime complete the workflow programmatically? | The primary decision input |
| **B. GUI usability** | Operator experience, progress visibility, diff review, cancellation | Assessed **separately**; a failure here does not invalidate A |

For this milestone the recommended scope is **Category A only**. Agent Canvas
(OpenHands' GUI) is beta and needs Node ≥24; Hermes' GUI surface is broad and
would require its own install. Category B can be a later, separately-approved
milestone. This keeps the decision focused and the footprint small.

---

## 6. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Local model lacks reliable tool calling | **High** | Explicit tool-call probe before the bake-off; disqualify rather than work around |
| 2 | Model context window too small for agent runtimes | High | Verify declared context ≥32k; measure actual behaviour |
| 3 | Hermes dependency resolution pulls a large/conflicting tree | Medium | Isolated venv; H2 PyPI fallback; exact-pinned deps are a positive signal |
| 4 | Ports 8000/8765 already occupied | Medium | Use 8081/8082 explicitly; documented in §2.3 |
| 5 | Two runtimes could interfere | **High** | Strictly separate worktrees and venvs; sequential execution only, never concurrent |
| 6 | Disk growth beyond estimate | Low | 724 Gi free; ~1–2 GB estimated; measured and reported at install time |
| 7 | PyPI `hermes-agent` version drift (0.19.0 vs 0.21.4) | Low | H1 shallow clone pins the revision by SHA |
| 8 | Unrelated `http.server` processes expose directories on the LAN | Medium | **Flagged** (§2.3). Disposition requested; not acted on |
| 9 | Repository is public | Low | Bake-off artefacts stay in `/tmp`; no model weights or client data committed |
| 10 | A runtime silently "succeeds" without fixing the defect | Medium | Independent test execution; diff review; two-row reporting (§5.3) |

---

## 7. Rollback

Every change is confined to Git-ignored `storage/` plus two `/tmp` worktrees.

```bash
# remove all FORGE-003 toolchain and runtime artefacts
rm -rf "$FORGEONE_HOME/storage/tools" \
       "$FORGEONE_HOME/storage/bakeoff"

# remove the disposable worktrees and fixture
rm -rf /tmp/forge003-hermes /tmp/forge003-openhands /tmp/forge002-fixture
```

**Nothing global is modified**, therefore nothing global needs reverting:

- System Python 3.9.6 — untouched
- No shell rc file modified
- No `PATH` change
- No Homebrew, no `sudo`, no launchd agent, no system preference
- No existing IDE configuration changed

Verification after rollback: `which uv` → not found;
`python3 --version` → `3.9.6` unchanged; `git status` → clean.

---

## 8. Approval request

**Nothing proceeds until the owner approves.** Requested decisions:

| # | Decision | Detail |
|---|---|---|
| **A1** | Install `uv` binary + Python 3.12, project-local | ~16 MB + ~60 MB, no privileges, fully reversible |
| **A2** | Create the two runtime venvs | ~1–2 GB under `storage/`, no privileges |
| **A3** | Install MLX-LM and **download one small test model** | ~2.2–2.5 GB download, local, zero cost — **model choice requires your selection** |
| **A4** | Ports 8081 (model) and 8082 (runtime) | Chosen because 8000/8080/8765 are occupied |
| **A5** | Scope = SDK/headless only, GUI deferred | Keeps footprint minimal and the decision focused |
| **A6** | Disposition of the three stray `http.server` processes | **Recommend leaving them alone**; if you want them stopped, that is your call |

**Explicitly NOT requested and NOT performed:** Docker, Homebrew, Node, `sudo`,
remote install scripts, paid cloud resources, Agent Canvas, model weights in Git.

---

## 9. What was actually executed for this plan

Read-only inspection only. No installation, no download, no mutation.

| Activity | Result |
|---|---|
| Git fetch / `ls-remote` / ancestry check | `f317b5e` confirmed in `origin/main` |
| Branch creation | `feat/forge-003-runtime-bakeoff` from `2c087be` |
| Local toolchain inventory | §2.1 |
| `lsof` port inspection + loopback HTTP probes | §2.3 — identified 3 static servers, none an inference endpoint |
| Upstream requirement re-verification (GitHub raw + PyPI JSON) | §2.4, §3 |
| Fixture integrity re-check + baseline rerun | 6 tests, 2 failures, exit 1 — reproduced |
| Credential handling | **None.** No credential or Keychain content was read, printed or probed |

**Estimated total install footprint if approved: ~1–2 GB for toolchains plus
~2.2–2.5 GB for one test model. Zero cost. No privileges. Fully reversible.**

**STOPPING HERE FOR APPROVAL.** No installation or bake-off begins until the
owner responds to §8.
