# ADR-0001 — Agent runtime ownership: Hermes vs OpenHands

- **Status:** Proposed (decision deliberately deferred to milestone M1)
- **Date:** 2026-09-24
- **Deciders:** Repository owner
- **Related:** [architecture.md §2](../architecture.md), milestone M1

## Context

ForgeOne needs exactly **one** owner of the agent task loop. The blueprint's
first principle is that an IDE agent and a server-side agent must never both
execute the same model tool call, and that stacking multiple overlapping
supervisors (for example Hermes + OpenHands + OpenCode + LangGraph) is
explicitly out of scope for v0.1.

Two candidate runtimes are on the table:

| Candidate | Source | Why it is a candidate |
|---|---|---|
| **Hermes** | `github.com/NousResearch/hermes-agent` | Compact agent with a native API surface; plausible fit for the local-first, single-machine model gateway planned for M2. |
| **OpenHands** | `github.com/OpenHands/OpenHands` | Mature agent framework with an existing GUI, worktree/sandbox isolation story and a broader tool ecosystem. |

**No comparative evidence has been gathered yet.** M0 is a foundation
milestone: it installs nothing and runs no bake-off. Any statement of preference
at this point would be an untested assumption, so this ADR records the
*evaluation plan* rather than a decision.

## Decision

**Deferred.** ForgeOne will not adopt, install or integrate either runtime until
the M1 bake-off produces recorded evidence. Until then, agent work in this
repository is performed by an existing IDE agent acting as the single tool
executor, and ForgeOne makes no claim about which runtime it will ship.

## M1 bake-off protocol

Both candidates are evaluated on the **same** small sample coding task, on the
same machine, against the same test repository and the same baseline commit SHA.

### Recorded measurements

| Dimension | What is recorded |
|---|---|
| Setup effort | Wall-clock time and number of manual steps from clean machine to first successful run; number of global/system changes required |
| Tool-call accuracy | Correct tool calls / total tool calls; malformed or repeated calls; recovery after a failed call |
| Human-visible GUI progress | Whether the operator can see plan, progress and diffs without reading raw logs |
| Session resume | Whether an interrupted run can be resumed and how much context survives |
| Cancellation | Whether a running task can be stopped cleanly and whether it leaves the workspace consistent |
| Worktree isolation | Whether work happens in an isolated worktree/branch and whether the main checkout stays untouched |
| Source diffs | Quality and minimality of the produced patch; presence of unrelated edits |
| API extensibility | Whether a portable `GET /v1/models` + `POST /v1/chat/completions` + SSE surface can be exposed without forking the project |
| Resource use | Peak memory, swap, disk footprint on the 24 GB unified-memory baseline |
| Licence / dependency risk | Licence terms, dependency count, pinning story |

### Exit criteria

1. Exactly one runtime is selected as ForgeOne's task-loop owner.
2. The decision is supported by recorded measurements, not preference.
3. The losing candidate is documented as *rejected with reasons* and is not
   installed as a shadow supervisor.
4. A follow-up ADR (or an accepted revision of this one) records the outcome
   and supersedes this `Proposed` status.

## Consequences

- **Positive:** avoids premature lock-in and prevents the double-executor
  failure mode that the blueprint forbids.
- **Positive:** M1 gets an explicit, falsifiable scorecard instead of an
  aesthetic comparison.
- **Negative:** M2/M3 work that depends on the runtime is blocked until M1
  completes. This is accepted — the foundation must not be built on an
  unverified assumption.
- **Neutral:** the existing IDE agent remains the authoring tool for ForgeOne
  regardless of which framework eventually becomes ForgeOne's runtime.

## Open questions for M1

- Does the selected runtime need Docker, and is that acceptable on this host
  (Docker is not currently installed)?
- Can the runtime run without global toolchain changes (no Homebrew, no `sudo`)?
- What is the smallest patch to expose the portable API surface without forking?
- How is the "one run owner" rule enforced technically, not just by convention?
