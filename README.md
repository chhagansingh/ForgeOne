# ForgeOne

ForgeOne Studio — a design-to-delivery agentic engineering platform.

> **Status: M0 Foundation.** This repository currently contains only the
> foundation scaffold and documentation. ForgeOne is **not** an operational
> product: there is no control plane, no `forge-auto` endpoint, no agent
> runtime, no model adapter and no GUI yet. Every milestone below is delivered
> only when its exit gate is met with recorded evidence.

## What it will do

Take user chat, Figma URLs/selections, requirements, API contracts, existing
repositories and screenshots, and produce reproducible code changes or new
projects, media artifacts where requested, and an **evidence bundle** that
documents the validation actually performed.

Explicit non-goals for the first release:

- general-purpose foundation-model training;
- inventing missing business or API requirements;
- fully automatic production deployment;
- any claim of pixel-perfect or defect-free output;
- a custom dense-weight router;
- implementing every supported platform simultaneously.

## Principles

- The human owns requirements, repository permissions and merge/deploy approvals.
- **One tool executor per run** — an IDE agent and a server-side agent never both
  execute the same model tool call.
- Verify with deterministic evidence, not model self-assertion.
- Untrusted content (repo files, Figma text, web pages) is task data, never
  higher-priority instructions.
- Model weights, caches, credentials and client assets stay outside this source
  repository.
- Local-first; external providers are opt-in and must be approved for the data
  classification.
- Observed facts, model hypotheses, implementation status and untested
  assumptions are reported separately.

## Repository layout

```text
README.md                  This file
AGENTS.md                  Working agreement for AI coding agents
LICENSE                    MIT
.gitignore                 Storage/secret/artifact exclusion rules
.github/ISSUE_TEMPLATE/    Issue templates
docs/
  architecture.md          Architecture and workflow contracts
  decisions/               Architecture Decision Records (ADRs)
  planning/                Sanitized planning documents
  reports/                 Milestone validation reports
specs/
  000-foundation/          Foundation specification
storage/                   EXCLUDED FROM GIT — models, cache, runs, artifacts
```

## Milestones

| Milestone | Deliverable | Exit gate |
|---|---|---|
| **M0 Foundation** | Read-only inspection, then safe scoped repo bootstrap | Environment recorded, docs validated, sanitized commit pushed or `PUSH BLOCKED` |
| M1 Agent bake-off | Hermes vs OpenHands on an identical test repo | One runtime chosen using recorded evidence |
| M2 Local model gateway | Model registry, single virtual model, health endpoints | One IDE can chat/stream and tool events have one owner |
| M3 Engineering vertical slice | Request → worktree → patch → tests → review | Demonstrable pass/fail/blocked run, no silent false pass |
| M4 Project contract layer | Requirements, screens, API/navigation contract | Traceability and missing-information reporting |
| M5 Web/Figma slice | One small app or screen plus reference snapshots | Browser build/flow/screenshot evidence |
| M6 Native iOS adapter | Simulator/device workflow | Xcode build and XCTest evidence |
| M7 Android adapter | Gradle/device automation | Actual Android build/functional evidence |
| M8 Media integration | FLUX + LTX adapters | Sequential load/unload and retrievable outputs |
| M9 UX & public release | Unified GUI, run dashboard, install docs | External clean install and documented limitations |
| FS0 ForgeStream baseline | SSD/cache/latency profiler | Same-checkpoint baseline with repeatable tests |
| FS1 ForgeStream experiment | One cache/prefetch improvement | Measured benefit with fidelity check; revert if not |

## Documentation

- [`AGENTS.md`](AGENTS.md) — rules for AI agents working here.
- [`docs/architecture.md`](docs/architecture.md) — architecture and workflow contracts.
- [`docs/architecture/prompt-intelligence-design.md`](docs/architecture/prompt-intelligence-design.md) — prompt record schema and composition order.
- [`docs/decisions/`](docs/decisions/) — ADRs, starting with the agent-runtime evaluation.
- [`docs/research/prompts-chat-integration.md`](docs/research/prompts-chat-integration.md) — prompt library audit and reuse strategy.
- [`specs/000-foundation/spec.md`](specs/000-foundation/spec.md) — foundation requirements.
- [`specs/001-agent-runtime/spec.md`](specs/001-agent-runtime/spec.md) — agent runtime selection requirements.
- [`docs/reports/`](docs/reports/) — milestone validation reports.

## Development environment

Not yet provisioned. M0 intentionally installs nothing, and M1 installs nothing
without explicit owner approval. The verified local baselines are recorded in
[`docs/reports/FORGE-001-M0-validation-report.md`](docs/reports/FORGE-001-M0-validation-report.md)
and [`docs/reports/FORGE-002-runtime-evaluation.md`](docs/reports/FORGE-002-runtime-evaluation.md).

## License

MIT — see [LICENSE](LICENSE).
