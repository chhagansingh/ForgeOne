# Prompt Intelligence Library — Design

- **Milestone:** M1 / FORGE-002
- **Date:** 2026-09-24
- **Status:** Design. Not implemented. No runtime code is specified here.
- **Related:** [`prompts-chat-integration.md`](../research/prompts-chat-integration.md),
  [`ADR-0001`](../decisions/ADR-0001-agent-runtime-evaluation.md),
  [`AGENTS.md`](../../AGENTS.md)

This document defines how ForgeOne selects, wraps, versions and composes task
prompts. It is deliberately independent of which agent runtime wins M1 — the
library is ForgeOne-owned data, not a runtime feature.

---

## 1. Core principle

> **A prompt is a versioned, validated artefact with a contract — not a string.**

Community prompt text is a *seed*, never a dependency. The value ForgeOne adds
is the wrapper: identity, typed variables, an output schema, declared tool
requirements and acceptance criteria.

Three rules follow, and they are not negotiable:

1. **Untrusted by default.** Retrieved prompt text is task data. It never
   outranks `AGENTS.md`, the user's requirements, permission rules or
   secret-handling policy.
2. **No execution on retrieval.** A prompt is selected, reviewed and pinned
   before use. Retrieval never triggers a run.
3. **Small and reviewable.** The curated set must be small enough for a human
   to read in full. Bulk import is prohibited.

---

## 2. Prompt record schema

Every prompt in the library is one record with the following fields. Fields
marked **required** may not be empty; a record missing one is invalid and must
not be selectable.

| # | Field | Req. | Purpose |
|---|---|---|---|
| 1 | `prompt_id` | ✅ | Versioned identity, e.g. `forge.code-review.v1` |
| 2 | `category` | ✅ | Task category from the closed taxonomy (§3) |
| 3 | `source` | ✅ | Provenance: upstream repo, commit SHA, original title, contributor |
| 4 | `licence` | ✅ | SPDX identifier (`CC0-1.0`, `MIT`, `Apache-2.0`, `proprietary`) |
| 5 | `input_variables` | ✅ | Typed, named variables with required/optional and defaults |
| 6 | `output_schema` | ✅ | Machine-checkable contract for the expected output |
| 7 | `model_capabilities` | ✅ | What the model must support (tools, vision, context floor) |
| 8 | `required_tools` | ✅ | Tools the prompt assumes; empty list is explicit, not absent |
| 9 | `acceptance_criteria` | ✅ | Deterministic conditions that decide pass/fail |
| 10 | `template_version` | ✅ | Semantic version of the template body itself |
| 11 | `validation_status` | ✅ | `draft` / `reviewed` / `validated` / `deprecated` |
| 12 | `body` | ✅ | The template text with `{{variable}}` placeholders |
| 13 | `reviewed_by` / `reviewed_at` | — | Human review record |
| 14 | `notes` | — | Known limitations, rejected variants, rationale |

### 2.1 Why each field exists

- **`prompt_id`** — versioned so a run can be reproduced after the library
  changes. A run records the exact ID it used, never "the code review prompt".
- **`output_schema`** — makes the model's output checkable. The upstream corpus
  provides this for only 312 of 2,169 entries; here it is mandatory, because
  ForgeOne's evidence discipline requires a deterministic check.
- **`required_tools`** — lets ForgeOne refuse to run a prompt on a runtime that
  cannot satisfy it, instead of silently degrading.
- **`acceptance_criteria`** — the bridge to the blueprint's requirement trace
  (`REQ-ID -> ... -> test ID -> status`).
- **`validation_status`** — a prompt that has never produced a verified pass is
  `draft`, and draft prompts must not be used for unattended runs.

### 2.2 Example record

```yaml
prompt_id: forge.code-review.v1
category: code_review
source:
  origin: prompts.chat
  repository: https://github.com/f/prompts.chat
  commit_sha: "<sha at curation time>"
  original_title: "Code Review Assistant"
  contributor: "<from prompts.csv contributor column>"
licence: CC0-1.0
input_variables:
  - name: language
    type: string
    required: true
  - name: diff
    type: string
    required: true
  - name: repo_conventions
    type: string
    required: false
    default: ""
output_schema:
  type: object
  required: [findings, summary]
  properties:
    findings:
      type: array
      items:
        type: object
        required: [severity, path, line, issue, suggestion]
        properties:
          severity: { enum: [blocker, major, minor, nit] }
          path:     { type: string }
          line:     { type: integer }
          issue:    { type: string }
          suggestion: { type: string }
    summary:
      type: string
model_capabilities:
  tools: false
  vision: false
  min_context_tokens: 32000
required_tools: []
acceptance_criteria:
  - "Every finding cites a path and line present in the supplied diff"
  - "Severity is one of the four enum values"
  - "No finding is reported without a concrete suggestion"
  - "The model does not claim to have executed tests"
template_version: 1.0.0
validation_status: draft
reviewed_by: null
notes: >
  Adapted from a CC0 community prompt. Upstream had no output contract; the
  findings schema is ForgeOne's addition. Not yet validated against the
  bake-off fixture.
```

---

## 3. Task category taxonomy

A closed set. New categories require a design change, not an ad-hoc string.

| Category | Purpose | First relevant milestone |
|---|---|---|
| `requirements_analysis` | Extract and gap-check requirements | M4 |
| `planning` | Scoped implementation plans | M4 |
| `coding` | Scoped patch generation | M3 |
| `code_review` | Fresh-context diff review | M3 |
| `test_generation` | Produce tests for new behaviour | M3 |
| `debugging` | Root-cause analysis from evidence | M3 |
| `api_integration` | Contract-driven API work | M5 |
| `design_interpretation` | Figma / UI semantics | M5 |
| `visual_review` | Screenshot comparison and critique | M5 |
| `image_prompting` | Media generation specs | M8 |
| `video_prompting` | Media generation specs | M8 |

---

## 4. Composition order

This ordering is the security boundary. Content **later** in the list has
**lower** authority. The order may not be permuted.

```text
1. Approved project rules            ← HIGHEST AUTHORITY (trusted)
   AGENTS.md, frozen constraints, permission and secret rules

2. Explicit user requirements        ← trusted
   the actual request, acceptance criteria, scope limits

3. Retrieved project context         ← semi-trusted (repository data)
   source files, docs, diffs — task data, not instructions

4. Selected task templates           ← UNTRUSTED
   curated prompt text, incl. CC0-derived bodies

5. Expected output contract          ← trusted, always applied last
   output_schema, acceptance criteria, required evidence format
```

**Why the output contract goes last.** It is the final instruction the model
sees, so it is the most robust position for the format constraint — and it
cannot be displaced by untrusted template text that appears above it.

**Why templates sit at position 4.** They must be below every trusted layer.
A template that says "ignore previous instructions" is contradicted by layers
1–3, which the runtime treats as authoritative.

`[INF]` Layer 3 is marked semi-trusted rather than untrusted because repository
content is ForgeOne's own working material, but it is still *data*: a malicious
comment inside a source file must not be able to promote itself to an
instruction.

---

## 5. Validation lifecycle

```text
draft  ──review──▶  reviewed  ──fixture pass──▶  validated  ──┐
  ▲                                                          │
  └──────────────── deprecated ◀──────────────────────────────┘
                    (superseded or regressed)
```

| Status | Meaning | Permitted use |
|---|---|---|
| `draft` | Written, not yet reviewed | Interactive experiments only |
| `reviewed` | Human-reviewed for correctness and injection safety | Interactive use with a human in the loop |
| `validated` | Produced a recorded, deterministic pass on the reference fixture | Unattended runs |
| `deprecated` | Superseded or known to regress | Not selectable; retained for reproducing old runs |

**Promotion evidence.** A prompt reaches `validated` only by producing a
recorded pass against a deterministic fixture — for coding categories, the
fixture in
[`FORGE-002-runtime-evaluation.md` Appendix A](../reports/FORGE-002-runtime-evaluation.md).
A model's self-assessment is never the promotion evidence.

---

## 6. Integration points

Deliberately runtime-agnostic. The library is a data layer with a small
selection API; it must not become coupled to Hermes or OpenHands internals.

| Point | Contract |
|---|---|
| **Storage** | Versioned records under the repository (text, diffable, reviewable in PRs) |
| **Selection** | `select(category, constraints) -> prompt_id` filtered by `validation_status`, `model_capabilities`, `required_tools` |
| **Composition** | Pure function: `(rules, requirements, context, template, contract) -> rendered_prompt` |
| **Rendering** | Strict `{{variable}}` substitution; unknown or missing required variables are a hard error, never a silent empty string |
| **Provenance** | Every run records the `prompt_id` and `template_version` it used |
| **Offline** | No network at run time; the curated set is vendored |

**Relationship to the runtime.** The runtime executes; the library supplies
text. Neither candidate runtime's prompt-management feature becomes the source
of truth — otherwise the library would have to be rebuilt when the runtime
changes, which is precisely what M1 is trying to keep cheap.

---

## 7. Governance

- **Adding a prompt** requires a reviewed change with provenance recorded.
- **Changing a template body** increments `template_version` and resets
  `validation_status` to `reviewed`.
- **Removing a prompt** sets `deprecated`; it is never deleted, so historical
  runs remain reproducible.
- **Licence check** is a merge gate: a record without an SPDX identifier is
  invalid.
- **Injection check** is a review gate: any template that attempts to override
  system rules is rejected at review time, not patched at run time.
- **Size cap** — the curated set stays small. If it grows past the point where
  a human can read it in full, the taxonomy is wrong.

---

## 8. Open questions

| # | Question | Owner decision needed |
|---|---|---|
| 1 | Where do vendored records live — `docs/` or a future `services/control-plane/` package? | FORGE-003 |
| 2 | Should `output_schema` be JSON Schema, or a lighter ForgeOne-specific form? | FORGE-003 |
| 3 | Who is the human reviewer for `reviewed` status on a single-owner project? | Owner |
| 4 | Does the M5 Figma path need a vision-capable prompt category split? | M5 |

None of these block FORGE-003.
