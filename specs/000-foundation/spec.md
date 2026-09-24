# SPEC 000 — Foundation (M0 / FORGE-001)

- **Issue:** `FORGE-001: Foundation inspection and safe bootstrap`
- **Milestone:** M0 Foundation
- **Date:** 2026-09-24
- **Status:** Implemented and validated — see
  [`docs/reports/FORGE-001-M0-validation-report.md`](../../docs/reports/FORGE-001-M0-validation-report.md)

## 1. Objective

Establish a trustworthy, non-destructive foundation for ForgeOne: verify the
real environment, place a Git checkout at the actual project root, publish a
minimal scaffold and documentation, and prove — with recorded evidence — what
was and was not done.

M0 is explicitly **not** a full-stack install.

## 2. Requirements

| ID | Requirement | Status |
|---|---|---|
| REQ-000-01 | The canonical project root is discovered and verified (Unicode, trailing whitespace, `pwd -P`, symlink resolution) before any write. No duplicate or lookalike directory is created. | PASS |
| REQ-000-02 | The Git checkout lives at the project root itself. No nested `repo/` directory and no second checkout. | PASS |
| REQ-000-03 | The remote repository is inspected non-destructively: visibility, default branch, existing content, existing history. Existing remote content is never overwritten and history is never rewritten. | PASS |
| REQ-000-04 | Pre-existing local seed files are preserved and never overwritten or deleted. | PASS |
| REQ-000-05 | `storage/` exists for models, caches, run outputs and worktrees, and is excluded from Git in full. | PASS |
| REQ-000-06 | Credentials, `.env` files, checkpoints, caches, virtualenvs, build outputs and generated artifacts are excluded from Git. | PASS |
| REQ-000-07 | Only minimal approved scaffold is created: `.gitignore`, `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/decisions/ADR-0001-agent-runtime-evaluation.md`, `specs/000-foundation/`, `.github/ISSUE_TEMPLATE/`, a sanitized M0 validation report. | PASS |
| REQ-000-08 | The agent-runtime decision (Hermes vs OpenHands) is recorded as a provisional ADR with explicit bake-off criteria; **neither runtime is installed in M0**. | PASS |
| REQ-000-09 | The full staged diff is reviewed for sensitive content before committing. | PASS |
| REQ-000-10 | Delivery happens on a scoped branch (`feat/forge-001-foundation`) pushed for review. No direct push to the default branch, no force-push, no auto-merge. | PASS |
| REQ-000-11 | Every check is reported honestly as `PASS`, `FAIL`, `NOT_RUN`, `NOT_APPLICABLE` or `BLOCKED`. No invented test execution. | PASS |

## 3. Explicit non-goals for M0

- Downloading model weights or running any model.
- Installing Hermes, OpenHands, OpenCode, LangGraph, Docker or any agent runtime.
- Homebrew, global Python, `sudo` or other system-wide changes.
- Exposing any network port publicly.
- Configuring CI secrets.
- Any claim that ForgeOne is operational.

## 4. Acceptance gate

The M0 exit gate from the blueprint is: *actual environment recorded, docs
validated, sanitized commit pushed or `PUSH BLOCKED`.*

This gate is satisfied only when the validation report records, for every
requirement above, the command executed and the observed result — including any
item that is `NOT_RUN` or `BLOCKED`.

## 5. Requirement trace

Per [`docs/architecture.md`](../../docs/architecture.md) §6:

```text
REQ-ID -> stated requirement -> source path/commit -> test ID -> screenshot or log -> status
```

At M0 there is no automated test engine yet (none is installed). Verification is
therefore command-and-observation based, recorded in the validation report with
the exact command, its output and the resulting status. Automated tests arrive
with the M2/M3 vertical slice.

## 6. Next milestone

`FORGE-002: Hermes vs OpenHands bake-off` (M1) — see
[ADR-0001](../../docs/decisions/ADR-0001-agent-runtime-evaluation.md).
M1 installation work begins only under a separate scoped instruction.
