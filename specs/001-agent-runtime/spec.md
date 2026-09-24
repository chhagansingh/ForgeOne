# SPEC 001 — Agent Runtime Selection and Prompt Intelligence

- **Milestone:** M1 — Agent bake-off
- **Issue:** `FORGE-002` (research) → `FORGE-003` (execution)
- **Date:** 2026-09-24
- **Status:** Partially implemented. Static evaluation complete; practical
  bake-off `BLOCKED`.
- **Related:** [ADR-0001](../../docs/decisions/ADR-0001-agent-runtime-evaluation.md),
  [FORGE-002 report](../../docs/reports/FORGE-002-runtime-evaluation.md),
  [prompt intelligence design](../../docs/architecture/prompt-intelligence-design.md)

## 1. Objective

Select exactly one agent runtime to own ForgeOne's task loop, on recorded
evidence rather than preference — or, if evidence cannot yet be produced, say so
precisely and keep the decision open.

M1 is explicitly **not** an installation milestone. Installing a runtime
requires a separate, scoped, owner-approved task.

## 2. Requirements

### 2.1 Evaluation requirements

| ID | Requirement | Status |
|---|---|---|
| REQ-001-01 | Evaluate Hermes Agent and OpenHands against current official sources, not marketing material or popularity metrics. | **PASS** |
| REQ-001-02 | Distinguish documented capability, observed behaviour, inference and untested assumption in every claim. | **PASS** |
| REQ-001-03 | Cover all fourteen evaluation dimensions defined in the milestone brief. | **PASS** |
| REQ-001-04 | Record the exact install requirements (packages, download size, filesystem impact, privileges) **before** any substantial installation. | **PASS** |
| REQ-001-05 | Build a small, disposable, independently owned test project — never a client repository. | **PASS** |
| REQ-001-06 | Execute the deterministic part of the bake-off and record the real result. | **PASS** (execution and honest recording); the suite itself **FAILED** — 6 tests, 2 failures, exit 1 |
| REQ-001-07 | Execute the agent-driven part of the bake-off on both runtimes with the same model and fixture. | **BLOCKED** |
| REQ-001-08 | Never fabricate a benchmark or report an unexecuted test as passing. | **PASS** |
| REQ-001-09 | Never run Hermes and OpenHands concurrently against the same workspace. | **PASS** (neither installed) |
| REQ-001-10 | Update ADR-0001 with the evidence; keep it `Proposed` if the decision is not supported. | **PASS** |

### 2.2 Prompt intelligence requirements

| ID | Requirement | Status |
|---|---|---|
| REQ-001-11 | Audit `f/prompts.chat`: repository, dataset formats, licence, MCP/API, toolkit. | **PASS** |
| REQ-001-12 | Establish a reuse path that does **not** require installing or cloning the prompts.chat application as a runtime dependency. | **PASS** (design) |
| REQ-001-13 | Define the prompt record schema with all required fields. | **PASS** (design) |
| REQ-001-14 | Define the prompt-composition order with project rules and user requirements above all retrieved content. | **PASS** (design) |
| REQ-001-15 | Treat community prompts as untrusted content that can never override `AGENTS.md`, user requirements, permissions or secret-handling rules. | **PASS** (design) |
| REQ-001-16 | Prohibit sending private workspace content, client source or credentials to the remote prompts.chat MCP/API; remote integration must be explicit opt-in. | **PASS** (design) |
| REQ-001-17 | Curate a small representative set only — never bulk-import the corpus. | **PASS** (≤8 candidates assessed, none imported) |
| REQ-001-18 | Cover the required example categories: requirements analysis, planning, coding, code review, test generation, debugging, API integration, Figma/UI interpretation, visual review, image prompting, video prompting. | **PASS** (taxonomy defined) |

### 2.3 Governance requirements

| ID | Requirement | Status |
|---|---|---|
| REQ-001-19 | Never force-push, never merge to `main` automatically, never rewrite history. | **PASS** |
| REQ-001-20 | Scan staged files for secrets, personal absolute paths and confidential data before committing. | **PASS** |
| REQ-001-21 | Verify source and licence attribution for any reused third-party material. | **PASS** |
| REQ-001-22 | Report every check as `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED` or `NOT_APPLICABLE` with evidence. | **PASS** |

## 3. Approval gates for FORGE-003

Both are **owner decisions**. No installation may begin without them.

| Gate | Scope | Why |
|---|---|---|
| **G1 — Toolchain** | Install `uv` plus Python 3.11 and/or 3.13, project-scoped or user-scoped. Record exact versions and checksums before installing. | Both runtimes require Python ≥3.11; this host has only 3.9.6. |
| **G2 — Model endpoint** | Provide one OpenAI-compatible endpoint — local server or a single approved provider key. | Without a model, no agent runtime can be driven at all. |

**Explicitly not required:** Docker, Homebrew, `sudo`, Node ≥24, or the Agent
Canvas package (68.6 MB / 11,007 files). The recommendation is to answer the
runtime question with the OpenHands **SDK** and the Hermes **CLI**, not with
either project's GUI.

## 4. Bake-off protocol

1. Obtain G1 and G2.
2. Install **one** candidate first. Recommended: OpenHands `software-agent-sdk`
   via `uv` on Python 3.13.
3. Record: model, revision, quantisation, context window.
4. Execute against the fixture in
   [FORGE-002 Appendix A](../../docs/reports/FORGE-002-runtime-evaluation.md):
   read the broken function → identify the cause → minimal fix → run the failing
   test → repair → rerun → inspect `git diff` → produce an evidence report.
5. Record measurements per ADR-0001's table.
6. Uninstall or isolate; then repeat identically for the other candidate.
7. **Never run both concurrently.**

### Acceptance gate

The milestone closes only when:

- both runs produced comparable, recorded evidence; **or**
- the owner explicitly accepts a decision on partial evidence, recorded as such.

A run counts as `PASS` only if the post-fix test suite reports `OK` with exit
code 0 **and** the `git diff` is minimal and relevant. A model's own claim of
success is never the evidence.

## 5. Explicit non-goals

- Building an agent platform from scratch. Reuse existing technology.
- Installing Docker, modifying global Python, changing system security
  settings, downloading multi-GB checkpoints, creating paid cloud resources, or
  running remote installation scripts without explicit owner approval.
- Adopting either candidate's GUI as ForgeOne's product GUI.
- Importing the prompts.chat corpus, or installing prompts.chat in any form.
- Enabling the remote prompts.chat MCP endpoint by default.
- Any ForgeStream work. ForgeStream stays independent of this decision.

## 6. Requirement trace

Per [`docs/architecture.md`](../../docs/architecture.md) §6:

```text
REQ-ID -> stated requirement -> source path/commit -> test ID -> screenshot or log -> status
```

At M1, coding-category requirements trace to the deterministic fixture in the
FORGE-002 report. Runtime-comparison requirements remain `BLOCKED` until G1 and
G2 are granted, and are recorded as such rather than as passing.

## 7. Next milestone

`FORGE-003: Runtime bake-off execution on approved toolchain` — see §3 and
§4. Recommended scope and rationale are in
[FORGE-002 §8](../../docs/reports/FORGE-002-runtime-evaluation.md).
