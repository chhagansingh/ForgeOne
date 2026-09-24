# FORGE-002 — Agent Runtime Bake-off and Prompt Intelligence

- **Milestone:** M1 — Agent bake-off
- **Issue:** `FORGE-002`
- **Date:** 2026-09-24
- **Branch:** `feat/forge-002-runtime-evaluation`
- **Base commit:** `62767f85ddbe53ba31f3a610c4d162095a2ebd70` (`main`)
- **Status:** Static evaluation **complete**; practical bake-off **BLOCKED**

## 0. Evidence discipline

Every claim in this report carries one of four labels. Nothing is upgraded
between labels without new evidence.

| Label | Meaning |
|---|---|
| `[DOC]` | Documented by the upstream project (README, manifest, package metadata) |
| `[OBS]` | Directly observed by this agent on this machine or via the upstream API |
| `[INF]` | Inference drawn from `[DOC]`/`[OBS]` evidence — reasoning, not measurement |
| `[UNT]` | Untested assumption. Explicitly **not** verified |

Popularity metrics (stars, forks, sponsors) are recorded only as repository
metadata and are **not** used as engineering evidence, per the milestone brief.

---

## 1. Executive findings

1. **Both candidates are usable in principle, but neither can be evaluated
   practically on this machine today.** The practical bake-off is `BLOCKED`
   because no supported Python, no Node.js, no Docker and no LLM provider
   credential exist on this host. This is a factual constraint, not a
   preference.

2. **OpenHands has been substantially restructured since the blueprint was
   written.** `OpenHands/OpenHands` is no longer the Python agent — it is
   **Agent Canvas**, a TypeScript/Electron control center marked **beta**. The
   agent runtime now lives in `OpenHands/software-agent-sdk`. The blueprint's
   implicit model of "one OpenHands repository" is out of date.

3. **Hermes ships a deliberately broad surface** (TUI + web UI + desktop app +
   messaging gateway + cron + skills + plugins) and its own internal module
   layout was recently decomposed, with a *temporary* plugin-compatibility
   shim that is already past its removal date. `[OBS]`

4. **Both projects independently converged on ACP** (Agent Client Protocol) as
   the IDE-integration path. Hermes exposes a `hermes-acp` entry point; Agent
   Canvas advertises running "any ACP-compatible agent". This is the most
   promising integration seam for ForgeOne and is a stronger signal than either
   project's own GUI.

5. **prompts.chat is legally ideal and structurally insufficient.** Its prompt
   *data* is dedicated to the public domain under **CC0 1.0**, so ForgeOne can
   vendor selected prompts with no attribution obligation. But the dataset
   carries no taxonomy, no output contracts, no versioning and no per-prompt
   validation state. ForgeOne must supply all of that itself.

6. **No decision is justified yet.** ADR-0001 remains `Proposed`. Selecting a
   runtime on this evidence would be exactly the "preference dressed as
   evaluation" the blueprint forbids.

---

## 2. Actual environment state

Recorded on this host. This is the baseline that a real bake-off must run on.

| Item | Value | Label |
|---|---|---|
| Canonical project root | `$FORGEONE_HOME` (owner-local absolute path redacted from this published copy) | `[OBS]` |
| OS | macOS 27.0 (build 26A428) | `[OBS]` |
| Chip | Apple M5, 10 cores (4P/6E) | `[OBS]` |
| Unified memory | 24 GB | `[OBS]` |
| Free disk | 725 Gi available of 926 Gi | `[OBS]` |
| Git | 2.54.0 (Apple Git-157) | `[OBS]` |
| Python (system) | **3.9.6 only** — Xcode-bundled | `[OBS]` |
| Node.js / npm | **Not installed** | `[OBS]` |
| Docker | **Not installed** | `[OBS]` |
| Homebrew | **Not installed** | `[OBS]` |
| `uv` | **Not installed** | `[OBS]` |
| `gh` CLI | **Not installed** | `[OBS]` |
| Local model runtime | **None** (no Ollama, llama.cpp, MLX) | `[OBS]` |
| LLM provider credential | **None present in the agent environment** | `[OBS]` |
| Xcode / simulators | Xcode 27.0; iOS 27.0 simulators available | `[OBS]` |
| Android SDK | `~/Library/Android/sdk` present; `adb` not on `PATH` | `[OBS]` |
| Java | OpenJDK 21.0.11 (JetBrains Runtime) | `[OBS]` |

### 2.1 Version compatibility — the blocking fact

| Runtime | Required Python | Required Node | Present? |
|---|---|---|---|
| Hermes Agent 0.21.4 | `>=3.11,<3.14` `[DOC]` | bundled by installer `[DOC]` | **No** (have 3.9.6) |
| OpenHands software-agent-sdk | 3.13 `[OBS]` | — | **No** |
| OpenHands-CLI 1.16.0 | `==3.12.*` `[DOC]` | — | **No** |
| OpenHands Agent Canvas 1.23.0 | — | `>=24` `[OBS]` | **No** |

`[INF]` Neither runtime can execute on this host without installing a new
language toolchain. Every available installation path is on the milestone's
prohibited list (see §5).

---

## 3. Hermes Agent — evaluation

Source: `github.com/NousResearch/hermes-agent`. Repository metadata `[OBS]`:
default branch `main`, language Python, MIT, repo size 1,045,378 KB (~1.0 GB),
last push 2026-09-24.

### 3.1 Capability matrix

| Dimension | Finding | Label |
|---|---|---|
| **Local installation on Apple Silicon** | Official path is a remote shell script: `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash`. It installs `uv`, Python 3.11, Node.js, ripgrep and ffmpeg. There is a documented manual fallback using `uv venv` + `uv pip install -e ".[all,dev]"`. A Nix flake (`flake.nix`, `nix/`) also exists. | `[DOC]` |
| **GUI experience** | Three distinct surfaces: full TUI (`hermes`), a Vite/React web app (`web/`), and a desktop app (`apps/desktop`). Plus a messaging gateway (Telegram, Discord, Slack, WhatsApp, Signal, Email). | `[DOC]` `[OBS]` |
| **IDE / OpenAI-compatible integration** | Ships an ACP adapter as a first-class entry point: `hermes-acp = "acp_adapter.entry:main"`. Core dependency is `openai==2.24.0`, and the project is explicitly provider-agnostic ("Nous Portal, OpenRouter, OpenAI, your own endpoint"). A Hermes-native HTTP surface on port 8642 is asserted by the blueprint but **was not confirmed** in this evaluation. | `[DOC]` / port `[UNT]` |
| **Tool calling & execution ownership** | 40+ tools organised into a "toolset system" with `toolsets.py` and `toolset_distributions.py`. Seven terminal backends: local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox. | `[DOC]` `[OBS]` |
| **Repository read/write** | Via its terminal/tool backends. No dedicated Git-native worktree abstraction was found at top level. | `[INF]` |
| **Subagents & delegation** | Documented: "Spawn isolated subagents for parallel workstreams" and "Write Python scripts that call tools via RPC". | `[DOC]` |
| **Persistent memory & resume** | Strongest of the two on paper: agent-curated memory with nudges, FTS5 session search with LLM summarisation, Honcho dialectic user modelling, plus a very large `hermes_state_*` subsystem (~30 modules) covering sessions, rewind, timeline, usage, search and repair. | `[DOC]` `[OBS]` |
| **Local model compatibility** | Provider-agnostic by design; a local model would be reached through an OpenAI-compatible endpoint. Exact context-window expectations are **not documented** in the sources reviewed. | `[DOC]` / `[UNT]` |
| **GUI/API/CLI extensibility** | Explicit plugin system: `plugins/`, `plugin-catalog/`, `hermes_platform/` (`host/`, `resolver/`, `declaration.py`), `optional-mcps/`, `optional-skills/`. MCP server support via the `mcp` extra (`mcp==2.0.0`, `httpx2`, `starlette`). Skills follow the `agentskills.io` open standard. | `[DOC]` `[OBS]` |
| **Workspace isolation & security** | Documented security page covers command approval, DM pairing and container isolation. Isolation depends on the selected terminal backend — the `local` backend has no isolation. | `[DOC]` |
| **Runtime install/storage requirements** | ~1.0 GB repository checkout; managed install under `$HERMES_HOME` (default `~/.hermes`); installer provisions its own Python and Node. | `[OBS]` `[DOC]` |
| **Streaming events & visibility** | TUI provides streaming tool output and interrupt-and-redirect; `/insights`, `/usage`, `/compress` expose run state. | `[DOC]` |
| **Custom build/test validator integration** | MCP support plus a plugin API make this plausible. No built-in validator-contract abstraction was found. | `[INF]` |
| **Licensing & redistribution** | MIT. Redistribution in a public repo is permitted with licence and copyright notice retained. | `[DOC]` |

### 3.2 Engineering risk signals `[OBS]` `[INF]`

- **Recent, large internal decomposition.** `COMPAT_MANIFEST.md` documents that
  "the September 2026 decomposition (PR #102117) split the large modules of
  Hermes Agent into focused files" and states plainly that *"internal import
  paths are not a stable API"*. The compatibility shim was scheduled for removal
  on **2026-09-14** — a date that has already passed at the time of writing.
  `[INF]` Any ForgeOne adapter written against Hermes internals would be
  building on explicitly non-stable ground. An adapter must target the CLI,
  ACP or MCP surfaces, never internal imports.
- **Exact-pinned core dependencies** with a written supply-chain rationale
  (a documented response to the Mini Shai-Hulud worm hitting `mistralai` on
  PyPI). `[INF]` This is a genuine engineering-quality positive: reproducible
  installs and a small blast radius.
- **Breadth is a cost.** TUI + web + desktop + gateway + cron + skills is a very
  large surface to integrate against, and it sharpens the "who owns the GUI?"
  question in ForgeOne's architecture.

---

## 4. OpenHands — evaluation

### 4.1 The repository split `[DOC]` `[OBS]`

This is the single most consequential finding. OpenHands is no longer one
project:

| Repository | Responsibility | Language | Size `[OBS]` |
|---|---|---|---|
| `OpenHands/OpenHands` | **Agent Canvas** — frontend, control center, backend selection, local-stack orchestration. Marked **beta**. | TypeScript | 438,216 KB |
| `OpenHands/software-agent-sdk` | Python SDK, Agent Server, agents, tools, conversations, workspaces, events, canonical server API | Python | 47,786 KB |
| `OpenHands/typescript-client` | Browser TypeScript client for the Agent Server API | TypeScript | — |
| `OpenHands/automation` | Scheduling, webhooks, run history, dispatching | — | — |
| `OpenHands/OpenHands-CLI` | "Lightweight OpenHands CLI in a binary executable" | Python | 4,659 KB |

`[INF]` ForgeOne should integrate against **`software-agent-sdk` + the Agent
Server REST API**, not against Agent Canvas. The control center is a beta
consumer of that API, not a stable foundation.

### 4.2 Capability matrix

| Dimension | Finding | Label |
|---|---|---|
| **Local installation on Apple Silicon** | Three documented paths: (1) `npm install -g @openhands/agent-canvas` with `uv` — no sandbox, **agent gets full filesystem access**; (2) Docker image `ghcr.io/openhands/agent-canvas:1.23.0`; (3) from source. | `[DOC]` |
| **GUI experience** | Agent Canvas is a full self-hosted developer control center (conversations, automations, Slack/GitHub/Linear integrations, multi-backend switching). UI on port 8000. | `[DOC]` |
| **IDE / OpenAI-compatible integration** | "Use with any agent" — runs OpenHands, Claude Code, Codex, Gemini, or **any ACP-compatible agent**. SDK exposes Python, TypeScript and REST APIs. "Bring your own model" via LLM profiles. | `[DOC]` |
| **Tool calling & execution ownership** | Explicit and clean in the SDK: `Agent(llm=..., tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name), Tool(name=TaskTrackerTool.name)])`. Tool ownership is a constructor argument, not an ambient setting. | `[DOC]` |
| **Repository read/write** | `FileEditorTool` + `TerminalTool`; workspace is an explicit `Conversation(agent=..., workspace=cwd)` parameter. | `[DOC]` |
| **Subagents & delegation** | Documented for "major tasks that involve multiple agents, like refactors and rewrites". The Agent Server runs "multiple agents on a single machine". | `[DOC]` |
| **Persistent memory & resume** | Conversation/event model with a canonical server API and an event stream. Session persistence and resume semantics were **not verified**. | `[DOC]` / `[UNT]` |
| **Local model compatibility** | "Bring your own model" via LLM profiles — a local model needs an OpenAI-compatible endpoint. Context requirements not documented in sources reviewed. | `[DOC]` / `[UNT]` |
| **GUI/API/CLI extensibility** | Strongest seam of the two: a documented **REST API** (Agent Server), a published TypeScript client, a Python SDK, and an automation/webhook layer. | `[DOC]` |
| **Workspace isolation & security** | Two modes, explicitly distinguished: local machine as workspace, **or** ephemeral workspaces in Docker/Kubernetes via the Agent Server. The npm path carries an explicit upstream warning about full filesystem access. | `[DOC]` |
| **Runtime install/storage requirements** | Agent Canvas npm package: v1.23.0, **68,618,325 bytes unpacked (~68.6 MB), 11,007 files, 50 direct dependencies**. Plus Node and `uv`. Docker path additionally needs Docker. | `[OBS]` |
| **Streaming events & visibility** | Event stream is core to the SDK/server design; Agent Canvas renders it. | `[DOC]` |
| **Custom build/test validator integration** | `[INF]` The cleanest of the two: ForgeOne can drive the SDK directly in-process (`Conversation(...)`, `conversation.run()`) and wrap validators around it, rather than shelling out to a CLI. | `[INF]` |
| **Licensing & redistribution** | MIT across `OpenHands`, `software-agent-sdk` and `OpenHands-CLI`. Redistribution permitted with notice retained. | `[DOC]` |

### 4.3 Engineering risk signals `[OBS]` `[INF]`

- **Documentation inconsistency on a hard requirement.** The README states
  "Node.js 22.12.x or later"; the published npm manifest declares
  `engines: { node: ">=24" }`. `[OBS]` A prospective integrator following the
  README on Node 22 would hit a runtime that the package itself declares
  unsupported. This is a concrete, reproducible documentation defect worth
  raising upstream.
- **Beta status on the user-facing half.** Agent Canvas is badged "status beta"
  and sits under an incubator programme. `[INF]` ForgeOne must not make its own
  GUI depend on a beta surface.
- **Hard minor-version pin in the CLI.** `OpenHands-CLI` declares
  `requires-python = "==3.12.*"`. `[INF]` An exact-minor pin on a long-lived
  integration is brittle: it forces ForgeOne's environment to a specific Python
  minor and will conflict with the SDK's 3.13 target.

---

## 5. Practical bake-off — BLOCKED

### 5.1 What was actually executed

The deterministic fixture **was** built and **was** run. This is real evidence.

**Fixture** (disposable, outside the ForgeOne repository, no client code):
`/tmp/forge002-fixture/`, git-initialised at baseline commit `9157d02`.

The task: a deliberately broken `tier_price()` whose docstring contract says it
must raise `ValueError` for `units < 1`, but which silently falls through to
`return 10.00`. The minimal correct fix is to raise `ValueError`.

**Executed baseline** — `python3 -m unittest discover -s tests -v`:

```text
test_100_units_uses_top_tier ... ok
test_10_units_uses_middle_tier ... ok
test_1_unit_uses_base_tier ... ok
test_negative_units_is_rejected ... FAIL
test_order_total_uses_tier_price ... ok
test_zero_units_is_rejected ... FAIL

AssertionError: ValueError not raised   (x2)

Ran 6 tests in 0.001s
FAILED (failures=2)
BASELINE_EXIT=1
```

**Result: PASS** — the fixture is valid: 6 tests, 4 pass, 2 fail, exit code 1.
The failure is deterministic and encodes the documented contract. Full fixture
source is in Appendix A.

### 5.2 What was NOT executed, and why

| Bake-off step | Status | Reason |
|---|---|---|
| Install Hermes | **BLOCKED** | Official installer is a remote `curl \| bash` script — explicitly prohibited without owner approval. Manual path needs `uv` + Python 3.11 (neither present). |
| Install OpenHands | **BLOCKED** | Needs Node ≥24 (absent) and a 68.6 MB / 11,007-file npm package — a substantial installation requiring documented approval. Docker path needs Docker (absent). |
| Run either agent against the fixture | **BLOCKED** | No runtime installed **and no LLM credential or local model endpoint exists**. |
| Read broken function / identify cause / minimal fix | **NOT_RUN** | Requires a runtime and a model. |
| Repair, rerun tests, inspect Git diff | **NOT_RUN** | Same. |
| Measure memory, execution time, event visibility | **NOT_RUN** | Same. |
| Compare tool-call correctness | **NOT_RUN** | Same. |

**No benchmark was fabricated and no unexecuted test is reported as passing.**
The fixture and its failing baseline are the only measured artefacts.

### 5.3 Installation requirements, documented before any install

Per the milestone rule that exact packages, sizes and privileges must be
documented **before** a substantial installation. Neither was installed.

**Hermes Agent**

| Item | Value |
|---|---|
| Method (official) | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash` — **remote script** |
| Method (manual) | `uv venv ~/.hermes/venvs/hermes-dev --python 3.11` then `uv pip install -e ".[all,dev]"` |
| Packages provisioned | `uv`, Python 3.11, Node.js, ripgrep, ffmpeg |
| Repo checkout | ~1.0 GB `[OBS]` |
| Install location | `$HERMES_HOME` (default `~/.hermes`) |
| Privileges | User-level; no `sudo` documented. Modifies shell rc files (`source ~/.zshrc`). |
| Global changes | Adds `~/.hermes/bin`-style tooling; installer manages its own Python/Node rather than the system ones |
| Approval needed | **Yes** — remote script execution |

**OpenHands**

| Item | Value |
|---|---|
| Method A | `npm install -g @openhands/agent-canvas` — global npm install |
| Method B | `docker run ... ghcr.io/openhands/agent-canvas:1.23.0` — needs Docker |
| Packages | Node ≥24, `uv`; npm package **68.6 MB unpacked, 11,007 files, 50 deps** `[OBS]` |
| SDK alternative | `software-agent-sdk` (Python 3.13) via `uv` |
| Privileges | User-level npm global; Docker requires daemon (and on macOS, Docker Desktop) |
| Security note | npm path runs the agent server **directly on the host with full filesystem access** `[DOC]` |
| Approval needed | **Yes** — toolchain install (Node/uv) + 68.6 MB package |

### 5.4 Isolation rule respected

Hermes and OpenHands were **never** installed, and therefore never supervised or
edited the same workspace. The rule was not merely observed — it was
unreachable, and that is stated plainly rather than glossed.

---

## 6. Selected / proposed architecture

**No runtime is selected.** ADR-0001 remains `Proposed`. What this milestone
*can* justify is the boundary design that makes the eventual choice cheap and
reversible:

1. **Agent execution ownership** — exactly one runtime owns the loop, behind a
   ForgeOne adapter interface. ForgeOne never drives two executors.
2. **Model gateway boundary** — both candidates reach models through an
   OpenAI-compatible endpoint. That endpoint is ForgeOne's M2 deliverable, so
   the runtime choice is independent of the model choice.
3. **GUI ownership** — ForgeOne owns the product GUI. Neither candidate's GUI
   is adopted as the product surface; Agent Canvas is beta and Hermes' surface
   is too broad to adopt wholesale.
4. **IDE integration** — target **ACP** first, MCP second. Both projects
   converged on ACP independently; it is the lowest-coupling seam available.
5. **Workspace isolation** — one git worktree per run; never the primary
   checkout. Isolation backend is a runtime choice, not a ForgeOne one.
6. **Prompt library** — see
   [`docs/architecture/prompt-intelligence-design.md`](../architecture/prompt-intelligence-design.md).
7. **Independent verification** — the deterministic fixture in §5.1 is the
   reference harness. A model's own claim of success is never the evidence.
8. **Model storage & memory** — one heavy model resident at a time; weights
   under `storage/models/`, never in Git.
9. **ForgeStream stays independent** — nothing in this report constrains it.

---

## 7. Failures, limitations and required follow-up

| # | Item | Type |
|---|---|---|
| 1 | Practical bake-off could not be executed | **BLOCKER** |
| 2 | No LLM credential or local model endpoint available | **BLOCKER** |
| 3 | Previously exposed PAT was **still active** at Phase 0 — rotation outstanding | **SECURITY** |
| 4 | Agent Canvas `engines.node >= 24` contradicts its README's `22.12.x` | Documentation defect upstream |
| 5 | Hermes' asserted API port 8642 unconfirmed | Unverified |
| 6 | OpenHands session resume semantics unverified | Unverified |
| 7 | Repository is still **public**; M1 content is design-only and contains no client data | Ongoing risk |

**Required to unblock (owner decision, one approval):**

- A scoped toolchain install: `uv` + Python 3.11/3.13 (Hermes and OpenHands SDK
  both become reachable this way; Node ≥24 only if Agent Canvas is wanted).
- **One** model endpoint for the bake-off — either a local OpenAI-compatible
  server (M2 work) or a single approved provider key.

---

## 8. Next milestone — recommended FORGE-003 scope

**`FORGE-003: Runtime bake-off execution on approved toolchain`**

1. Obtain explicit owner approval for the scoped toolchain install; record
   exact versions and checksums before installing.
2. Install **one** candidate first (recommended: OpenHands `software-agent-sdk`
   via `uv`, Python 3.13) against the fixture in Appendix A.
3. Provide a single OpenAI-compatible model endpoint; record model, revision,
   quantisation and context window.
4. Execute the full workflow and record: setup effort, tool-call correctness,
   actual test results, event visibility, peak memory, execution time, and the
   produced `git diff`.
5. Repeat identically for Hermes. **Never concurrently.**
6. Update ADR-0001 to `Accepted` only if both runs produced comparable,
   recorded evidence.

Recommended: **do not** install Agent Canvas in FORGE-003 — the SDK alone
answers the runtime question at a fraction of the footprint.

---

## Appendix A — bake-off fixture source (reproducible)

`src/pricing.py`

```python
TIERS = (
    (100, 8.00),
    (10, 9.00),
    (1, 10.00),
)


def tier_price(units):
    """Return the per-unit price for ``units`` units.

    Tier boundaries are inclusive lower bounds::

        units >= 100  ->  8.00
        units >=  10  ->  9.00
        units >=   1  -> 10.00

    Raises:
        ValueError: if ``units`` is less than 1.
    """
    for threshold, price in TIERS:
        if units >= threshold:
            return price
    return 10.00          # <-- DEFECT: must raise ValueError for units < 1


def order_total(units):
    """Return the total price for ``units`` units, rounded to 2 decimals."""
    return round(tier_price(units) * units, 2)
```

`tests/test_pricing.py` asserts `tier_price(100) == 8.00`,
`tier_price(10) == 9.00`, `tier_price(1) == 10.00`, `order_total(10) == 90.00`,
and that `tier_price(0)` and `tier_price(-5)` each raise `ValueError`.

Run: `python3 -m unittest discover -s tests -v`
Expected before fix: `FAILED (failures=2)`, exit 1.
Expected after minimal fix: `OK`, exit 0.

## Appendix B — sources consulted

| Source | Used for |
|---|---|
| `github.com/NousResearch/hermes-agent` — README, `pyproject.toml`, `COMPAT_MANIFEST.md`, tree | Hermes capability and risk assessment |
| `github.com/OpenHands/OpenHands` — README, tree | Agent Canvas structure and install paths |
| `github.com/OpenHands/software-agent-sdk` — README, tree, `.python-version` | SDK capability and requirements |
| `github.com/OpenHands/OpenHands-CLI` — `pyproject.toml` | Python pin |
| `registry.npmjs.org/@openhands/agent-canvas` | Install footprint |
| `github.com/f/prompts.chat` — README, `prompts.csv`, `LICENSE-CC0`, `LICENSE-MIT` | Prompt library audit |
| GitHub REST API | Repository metadata, file trees, raw content |

All sources are public and were read on 2026-09-24. No private or client
material was transmitted to any third party during this milestone.
