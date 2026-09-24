# ADR-0001 — Agent runtime ownership: Hermes vs OpenHands

- **Status:** Proposed — **still undecided.** Agent coding is blocked by the
  endpoint's approved **2,048-token context**, not by either runtime: the
  complete OpenHands initial request measures **5,600 tokens**, and a ForgeOne
  compact profile still needs **2,446**. Gateway protocol compatibility is
  **resolved (PASS)**. See finding 4 below.
- **Date:** 2026-09-24 (revised after the payload-compatibility capture —
  [report](../reports/FORGE-003-agent-payload-compatibility.md))
- **Deciders:** Repository owner
- **Related:** [architecture.md](../architecture.md) §2,
  [FORGE-002 runtime evaluation](../reports/FORGE-002-runtime-evaluation.md),
  [specs/001-agent-runtime](../../specs/001-agent-runtime/spec.md)

## Context

ForgeOne needs exactly **one** owner of the agent task loop. The blueprint's
first principle is that an IDE agent and a server-side agent must never both
execute the same model tool call, and that stacking multiple overlapping
supervisors (Hermes + OpenHands + OpenCode + LangGraph) is out of scope for
v0.1.

Two candidates are on the table: **Hermes Agent**
(`github.com/NousResearch/hermes-agent`, MIT, Python) and **OpenHands**
(`github.com/OpenHands/OpenHands`, MIT, now TypeScript + a separate Python SDK).

## Decision

**Deferred. No runtime is selected.** FORGE-002 completed a static evaluation
but could not execute the practical bake-off, and selecting on static evidence
alone would be precisely the "preference dressed as evaluation" the blueprint
prohibits.

**Why the practical bake-off could not run** — this is a factual constraint,
not a judgement about either candidate:

| Blocker | Detail |
|---|---|
| Python version | Both need Python ≥3.11. This host has **only 3.9.6**. |
| Node.js | Agent Canvas needs Node ≥24. **Not installed.** |
| Docker | The sandboxed OpenHands path needs Docker. **Not installed.** |
| Model access | **No LLM credential and no local model endpoint exist.** Without a model, no agent can be driven at all. |
| Install method | Hermes' official installer is a remote `curl \| bash` script — prohibited without explicit owner approval. |

What **was** executed is recorded in
[FORGE-002 §5](../reports/FORGE-002-runtime-evaluation.md): a disposable
deterministic fixture whose baseline was run for real — 6 tests, 2 failures,
exit 1. That fixture is the reference harness for the eventual decision.

## Evidence gathered (summary)

Full detail in the [FORGE-002 report](../reports/FORGE-002-runtime-evaluation.md).
The three findings that most affect this decision:

1. **OpenHands has been restructured.** `OpenHands/OpenHands` is now *Agent
   Canvas* — a TypeScript/Electron control centre marked **beta**. The agent
   runtime moved to `OpenHands/software-agent-sdk` (Python, MIT), alongside
   `typescript-client`, `automation` and `OpenHands-CLI`. Any integration plan
   written against "the OpenHands repo" is out of date. `[OBS]`
2. **Both projects converged on ACP** (Agent Client Protocol) as the IDE seam.
   Hermes ships a `hermes-acp` entry point; Agent Canvas advertises running any
   ACP-compatible agent. This is the lowest-coupling integration path and is a
   stronger signal than either project's own GUI. `[DOC]` `[OBS]`
3. **Hermes' internal APIs are explicitly unstable.** Its `COMPAT_MANIFEST.md`
   states that internal import paths are *not* a stable API following a
   September 2026 decomposition, with a temporary shim already past its removal
   date. Any ForgeOne adapter must target CLI/ACP/MCP surfaces only. `[OBS]`

4. **The local endpoint's approved context is too small for OpenHands.**
   Measured with the real tokenizer, before loading weights: the OpenHands
   system prompt alone is **2,318 tokens** (11,017 chars), and the **complete**
   initial request — system + task + three tool schemas — is **5,600 tokens**
   against an approved total context of **2,048**. `[OBS]`
   `REAL_TOKENIZER_VERIFIED`

   Tool schemas are the dominant cost (**3,221 tokens** for three tools, more
   than the entire system prompt). A ForgeOne compact profile
   (`FORGEONE_COMPACT_V1`) reduces the request to **2,212 tokens** by narrowing
   the tool set and shortening the prompt **without removing any safeguard** —
   still `BLOCKED_CONTEXT`. The minimum measured context for that limited
   workflow is **2,446 tokens** including output reservation.

   **This is a property of the endpoint's budget, not a defect in OpenHands.**
   No agent runtime was initialised, so this is *not* evidence that OpenHands
   would fail — only that it cannot run within 2,048 tokens.

   **Gateway compatibility is separately resolved: PASS.** The SDK defaults to
   `stream=False` and `requires_streaming` is subscription-only, so no streaming
   adapter is required. `num_retries` defaults to **5**, which conflicts with
   the no-auto-retry rule and must be forced to `0`.

   **This is a property of the endpoint, not a defect in OpenHands.** The
   runtime was never initialised, so this is *not* evidence that OpenHands
   would fail — only that it cannot run within the currently approved budget.

Additional risk signals: Agent Canvas's npm manifest declares
`engines.node >= 24` while its README says `22.12.x`; `OpenHands-CLI` pins
`requires-python == 3.12.*`; Hermes presents a very broad surface (TUI + web +
desktop + messaging gateway + cron).

## M1 bake-off protocol

Unchanged in intent, now with a concrete harness. Both candidates are evaluated
on the **same** fixture, same machine, same model, same baseline commit — never
concurrently.

### Recorded measurements

| Dimension | What is recorded |
|---|---|
| Setup effort | Wall-clock time and manual steps from clean machine to first successful run; global/system changes required |
| Tool-call accuracy | Correct / total tool calls; malformed or repeated calls; recovery after a failed call |
| Human-visible progress | Whether the operator sees plan, progress and diffs without reading raw logs |
| Session resume | Whether an interrupted run resumes, and how much context survives |
| Cancellation | Whether a running task stops cleanly and leaves the workspace consistent |
| Worktree isolation | Whether work happens in an isolated worktree/branch and whether the primary checkout stays untouched |
| Source diff | Quality and minimality of the produced patch; presence of unrelated edits |
| API extensibility | Whether a portable `GET /v1/models` + `POST /v1/chat/completions` + SSE surface is reachable without forking |
| Resource use | Peak memory, swap, disk footprint on the 24 GB baseline |
| Licence / dependency risk | Licence terms, dependency count, pinning story |

### Exit criteria

1. Exactly one runtime is selected as ForgeOne's task-loop owner.
2. The decision is supported by recorded measurements, not preference.
3. The rejected candidate is documented as *rejected with reasons* and is not
   installed as a shadow supervisor.
4. This ADR moves to `Accepted` with the evidence attached.

## What is required to decide

**A context budget large enough to run an agent runtime at all.** Finding 4 is
now the binding constraint: no agent runtime can be evaluated while the
approved context is 2,048 tokens. The local endpoint itself is proven sound
(protected smoke test PASS, real tool calling, watchdog clean) — it is simply
configured too small for an agent SDK's fixed prompt overhead.

Required, in order:

1. **A larger approved context**, established by a *guarded* measurement under
   the Resource Controller — not by assuming 8K/16K/32K works. The prior
   incident occurred during a 32,611-token prefill, so the safe ceiling is
   genuinely unknown and must be measured incrementally with the watchdog
   active.
2. **Then** the runtime bake-off, with the same endpoint, model, fixture and
   acceptance criteria for both candidates.

Separately, both items below need one owner approval:

1. **A scoped toolchain install** — `uv` plus Python 3.11 and/or 3.13. This
   alone makes Hermes and the OpenHands SDK reachable. Node ≥24 is needed
   *only* if Agent Canvas itself is wanted, and the recommendation is **not** to
   install it for FORGE-003: the SDK answers the runtime question at a fraction
   of the footprint (68.6 MB / 11,007 files avoided).
2. **One model endpoint** for the bake-off — either a local OpenAI-compatible
   server (M2 work) or a single approved provider key.

## Consequences

- **Positive:** the foundation is not built on an unverified assumption.
- **Positive:** the fixture and its failing baseline now exist and are
  reproducible, so the eventual comparison starts from measured ground.
- **Positive:** targeting ACP/MCP decouples ForgeOne from both runtimes'
  internal churn — the risk identified in finding 3 is designed out rather than
  accepted.
- **Negative:** M2/M3 work that depends on the runtime remains blocked until
  the bake-off runs. This is accepted.
- **Neutral:** the existing IDE agent remains the authoring tool for ForgeOne
  regardless of which framework becomes the runtime.

## Open questions for the bake-off

- Does the selected runtime require Docker, and is that acceptable on this host?
- Can either runtime run without global toolchain changes (no Homebrew, no
  `sudo`)?
- What is the smallest patch to expose the portable API surface without forking?
- How is the "one run owner" rule enforced technically, not just by convention?
- Does the Agent Server API carry a version/compatibility guarantee?
