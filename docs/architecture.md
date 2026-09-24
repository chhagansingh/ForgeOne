# ForgeOne — Architecture

**Status:** M0 Foundation. This document describes the *intended* architecture.
Nothing here is implemented yet; the sections below are the contract that later
milestones are built and validated against.

## 1. Component overview

```text
IDE or ForgeOne GUI
     |                  GitHub Issues / PRs / CI
     v                         |
ForgeOne control plane <-------+
     | task classifier / contract / state / audit
     |--- agent adapter: Hermes OR OpenHands (one run owner)
     |--- model adapter: llama.cpp / MLX / approved cloud
     |--- design adapter: official Figma MCP
     |--- project adapters: Web / iOS / Android
     |--- media adapter: FLUX / LTX via compatible runtime
     |--- validation adapter: build / tests / screenshot / API / static checks
     |--- evidence store: manifest, reports, screenshots, diff, log references
     +--- optional ForgeStream: SSD/offload experiments
```

ForgeStream is an **optional research module**. It must never block Studio
delivery; its experiments are independent of the product milestones.

## 2. API ownership — the single most important rule

| Mode | Who owns the agent loop and file tools | What ForgeOne provides |
|---|---|---|
| IDE mode | The IDE agent | A model/router endpoint only. `forge-auto` must **not** repeat file side effects the IDE already performs. |
| Studio mode | ForgeOne control plane | The full agent loop, tools, worktrees and validation. |

Two autonomous tool executors must never run against the same workspace in one
run. This is why the platform ships exactly **one** agent runtime owner (see
[ADR-0001](decisions/ADR-0001-agent-runtime-evaluation.md)) rather than stacking
Hermes + OpenHands + OpenCode + LangGraph as overlapping supervisors.

## 3. Portable API target

Planned, not implemented:

- `GET /v1/models`
- `POST /v1/chat/completions`
- SSE streaming
- structured tool calls where the client expects them
- cancellation and structured errors
- optional async run endpoints

Proposed custom port: **8765**. A Hermes-native API, if selected, may remain on
its own port (8642). Do not present this custom API as implemented until tests
pass.

## 4. Repository and data layout

The project root **is** the Git checkout — there is no nested `repo/` directory.

```text
$FORGEONE_HOME/            # Git root, opened directly in the IDE
  .git/
  README.md
  AGENTS.md
  .gitignore
  .github/{ISSUE_TEMPLATE,workflows}/
  docs/{decisions,requirements,learning-notes,planning,reports}/
  specs/000-foundation/
  apps/studio/
  services/control-plane/
  adapters/{agents,models,design,projects,media}/
  validators/{contracts,code,visual}/
  experiments/forgestream/
  tests/{unit,integration,e2e,fixtures}/
  scripts/
  storage/                 # EXCLUDED FROM GIT
    models/{llm,image,video,vision,adapters}/
    cache/
    runs/
    artifacts/
    worktrees/
    logs/
    benchmarks/
    secrets/               # prefer OS Keychain and env vars
```

`storage/` is ignored in full by `.gitignore`. Model checkpoints are recorded as
manifests with source revision, format, quantization, licence and vision
projector/runtime compatibility — not duplicated into Git.

## 5. Workflow contracts

### Greenfield
requirements + target platform + Figma/API inputs → extract evidence → identify
missing requirements → produce screen graph / API contract → generate code in
workspace → build → run functional and visual checks → repair with bounded
retries → evidence bundle → PR or local deliverable.

### Brownfield
inventory baseline, architecture, frozen files and tests → scoped plan →
isolated worktree → minimal patch → exact baseline comparison → regression and
visual checks → evidence bundle → PR. No undocumented modification of frozen
UI or flow.

### Media
classify generation/editing/video task → construct validated generation spec →
load specialized backend → generate → verify artifact exists, metadata and
visible result → optional review/refine loop → save output. Media proof is kept
separate from software test proof.

## 6. Run state and traceability

**Agent run states:** `queued`, `analyzing`, `waiting_for_input`,
`implementing`, `testing`, `reviewing`, `passed`, `failed`, `blocked`,
`cancelled`. Each run records run ID, repository SHA, request IDs, backend model
version and artifact provenance.

**Requirement trace:**
`REQ-ID -> design node or stated requirement -> source path/commit -> test ID -> screenshot or log -> status`

**Status vocabulary (used consistently in every report):** `PASS`, `FAIL`,
`NOT_RUN`, `NOT_APPLICABLE`, `BLOCKED`.

## 7. Review and test gate

- **Baseline:** current commit SHA, tests, observed known failures, frozen
  constraints.
- **Implementation:** one scoped worktree/branch per issue; no wide refactoring
  without an explicit contract.
- **Automated evidence:** compiler exit code, test summary, failed-test names,
  path-specific diff checks, tests for new behaviour.
- **Review:** fresh-context diff review plus deterministic checks. The same
  model reviewing itself is not independent proof.
- **Visual:** reference/render screenshots at a specified resolution, theme,
  language and device; compare layout and inspect semantic state.
- **Retry:** bounded and failure-driven; restore or roll back if unresolved.
  Mark `NOT_RUN` honestly.
- **Delivery:** PR plus run report and artifact manifest. The default branch is
  protected and requires approval before merge or deployment.

## 8. Security and data policy

- Never embed credentials in prompts, Git commits or screenshots.
- Default API binds to loopback with authentication; never open a public port
  without a network/auth design.
- Do not route confidential customer source to free or unapproved providers.
- Whitelist permitted package sources and install scripts; require approval for
  destructive commands, external publishing, permission escalation or
  deployment.
- Audit and pin dependencies. Record every model checkpoint and its licence.
- Self-hosted CI runners on the owner's production Mac are **not** the default:
  untrusted workflow code can persistently compromise the host.

## 9. Hardware/runtime budget

Baseline: Apple Silicon with 24 GB unified memory and an internal SSD.
Verified actuals are in
[`reports/FORGE-001-M0-validation-report.md`](reports/FORGE-001-M0-validation-report.md).

- Keep IDEs usable; do not assume a large quantized model plus a long KV cache
  and two IDEs all fit comfortably.
- One heavy model resident at a time.
- Capture memory pressure, swap, time-to-first-token, tokens/sec, cache hit rate
  and SSD traffic.
- Keep at least ~100–150 GB disk free as an initial planning guardrail.

## 10. Architecture decision records

| ADR | Topic | Status |
|---|---|---|
| [ADR-0001](decisions/ADR-0001-agent-runtime-evaluation.md) | Agent runtime ownership: Hermes vs OpenHands | Proposed (decided at M1) |
| ADR-0002 | One-endpoint protocol and tool ownership | Not started |
| ADR-0003 | Artifact/evidence schema and gate | Not started |
| ADR-0004 | Figma contract and visual diff strategy | Not started |
| ADR-0005 | Model registry and runtime adapters | Not started |
| ADR-0006 | ForgeStream separation from Studio | Not started |
| ADR-0007 | Cloud vs local execution and credential boundaries | Not started |
