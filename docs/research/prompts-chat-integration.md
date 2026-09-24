# prompts.chat — Integration Research

- **Milestone:** M1 / FORGE-002
- **Date:** 2026-09-24
- **Upstream:** `github.com/f/prompts.chat` (formerly "Awesome ChatGPT Prompts")
- **Status:** Research complete. No prompts imported. No application installed.

Evidence labels as in
[`FORGE-002-runtime-evaluation.md`](../reports/FORGE-002-runtime-evaluation.md):
`[DOC]` documented, `[OBS]` observed, `[INF]` inferred, `[UNT]` untested.

---

## 1. What it is

A community prompt library and the Next.js application that serves it. The
upstream README describes it as "the world's largest open-source prompt library
for AI" `[DOC]`. Repository metadata `[OBS]`: default branch `main`, primary
language HTML, created 2022-12-05, last push 2026-09-09.

`[INF]` For ForgeOne's purposes the *application* is largely irrelevant. The
**dataset** is the asset.

---

## 2. Licence — the decisive finding

The project is **dual-licensed**, and the split is unusually favourable for
ForgeOne `[DOC]`:

| Material | Licence | Consequence for ForgeOne |
|---|---|---|
| Source code and site-authored content, including `src/content/book` | **MIT** | Redistributable with licence + copyright notice retained |
| **Prompt content and data** — `prompts.csv`, `PROMPTS.md`, user-submitted prompts | **CC0 1.0 Universal** (public domain dedication) | **No attribution legally required. No copyleft. No share-alike. Vendoring is unrestricted.** |

Verified directly `[OBS]`:

- `LICENSE-CC0` contains the full Creative Commons CC0 1.0 Universal legal code.
- `LICENSE-MIT` reads `Copyright (c) 2022-present Fatih Kadir Akin and contributors`.
- The root `LICENSE` file states the dual-licensing split explicitly.

`[INF]` **Redistribution implication: favourable.** ForgeOne may copy, adapt,
relicense and redistribute selected prompt text without legal obligation. This
is materially better than most alternatives, which are typically MIT (requiring
notice retention) or unlicensed (unusable).

**Engineering recommendation:** record CC0 provenance in the prompt metadata
anyway (§4 of the design doc). CC0 removes the *obligation*, not the *value* of
knowing where a prompt came from — provenance is what lets ForgeOne re-audit a
prompt after an upstream change.

---

## 3. Dataset formats and access surfaces

### 3.1 Data formats `[OBS]`

| Artefact | Size | Notes |
|---|---|---|
| `prompts.csv` | **5,769,655 bytes** (~5.5 MB) | Primary machine-readable dataset |
| `PROMPTS.md` | **6,028,309 bytes** (~6.0 MB) | Human-readable rendering of the same content |
| Hugging Face dataset | `datasets/fka/prompts.chat` | Third-party-hosted mirror `[DOC]` |

**`prompts.csv` structure** — verified by parsing the file:

- Columns: `act`, `prompt`, `for_devs`, `type`, `contributor`
- **2,169 data rows** (the raw file has ~121,758 lines because prompt bodies
  contain embedded newlines — naive line counting overstates the dataset 56×)
- `for_devs`: `TRUE` = **159**, `FALSE` = **2,010**
- `type`: `TEXT` = 1,836, `STRUCTURED` = 312, `IMAGE` = 21
- **1,045 distinct contributors**

`[INF]` The `for_devs` flag is the single most useful filter: only ~7% of the
corpus is developer-oriented. Bulk-importing the other 93% would be actively
harmful to prompt quality and context budget.

### 3.2 Templating convention `[OBS]`

`STRUCTURED` and `IMAGE` prompts use an inline variable syntax:

```text
${VariableName:default_value}
```

Examples observed: `${Genre:fantasy, sci-fi, mystery}`, `${City:San Francisco}`,
`${Product Name:MacBook Pro}`, `${city_name:İSTANBUL}`.

`[INF]` This is a *convention*, not a specification — there is no escaping rule,
no type system and no formal grammar published. ForgeOne should adopt the
surface syntax for familiarity but define its own strict grammar, because the
upstream convention cannot express types or required-vs-optional.

`STRUCTURED` prompts frequently embed JSON with explicit `input_schema` and
`output_schema` keys. Example observed — `Data Transformer`:

```json
{
  "role": "Data Transformer",
  "input_schema": { "type": "array", "items": { "name": "string", "email": "string", "age": "number" } },
  "output_schema": { "type": "object", "properties": { "users_by_age_group": { "under_18": [], "18_to_30": [], "over_30": [] }, "total_count": "number" } },
  "instructions": "..."
}
```

`[INF]` This is the closest the upstream corpus comes to an output contract, and
it appears in only 312 of 2,169 rows. It validates ForgeOne's decision to make
an output schema a **mandatory** field in its own library rather than an
occasional extra.

### 3.3 Access surfaces `[DOC]`

| Surface | Invocation | ForgeOne verdict |
|---|---|---|
| Raw data (CSV/MD) | `raw.githubusercontent.com/f/prompts.chat/main/prompts.csv` | **Preferred.** No install, no runtime dependency, no telemetry |
| Remote MCP | `{"mcpServers":{"prompts.chat":{"url":"https://prompts.chat/api/mcp"}}}` | **Rejected by default** — see §4 |
| Local MCP | `npx prompts.chat mcp` | Acceptable *if* Node exists; still an unnecessary dependency for a static dataset |
| CLI | `npx prompts.chat` | Not needed |
| Claude Code plugin | `/plugin marketplace add f/prompts.chat` | Not needed |
| Self-host | `npx prompts.chat new my-prompt-library` | **Rejected** — requires PostgreSQL; vastly disproportionate to the need |

---

## 4. Privacy and trust boundary

### 4.1 No remote calls in the default path

**Rule:** ForgeOne must not send workspace content, client source, credentials
or user requirements to `prompts.chat` or any remote prompt service.

`[INF]` The remote MCP endpoint is the only surface that would transmit data
outbound. It is therefore **disabled by default** and must be an explicit,
per-user, opt-in configuration change with a visible notice — consistent with
the blueprint's "external providers are opt-in and must be approved for the
data classification".

`[OBS]` The raw-data path requires only an outbound **read** of a public static
file. It transmits nothing about the user's workspace. This is the path ForgeOne
should use, and it can be performed once, offline, at curation time — after
which the vendored copy needs no network at all.

### 4.2 Community prompts are untrusted content

This is the security-critical rule for this integration:

> **Prompt text retrieved from prompts.chat is untrusted content.** It is task
> data, never instructions. It must never override `AGENTS.md`, the user's
> requirements, permission rules, or secret-handling policy.

`[INF]` This is not hypothetical. The corpus contains prompts that instruct a
model to adopt an unrestricted persona, to ignore prior instructions, or to
emit only raw output with no explanation. Several were observed during this
audit. Treating any of them as higher-priority than ForgeOne's own rules would
be a prompt-injection vector.

**Mitigations required in the design (implemented in
[`prompt-intelligence-design.md`](../architecture/prompt-intelligence-design.md)):**

1. Retrieved prompt text is inserted **below** project rules and user
   requirements in the composition order, never above.
2. A prompt is never executed on retrieval — it is *selected*, reviewed and
   pinned before use.
3. Prompts that attempt to override system behaviour are rejected at curation
   time, not at runtime.
4. The curated set is small enough to be human-reviewed in full.

---

## 5. Curated candidate assessment

A small representative set was evaluated by reading the actual prompt text.
Per the milestone rule, **no prompts were imported**; this is an assessment, not
an acquisition.

| Upstream `act` | Chars | for_devs | Assessment |
|---|---|---|---|
| `Code Review Assistant` | 1,233 | TRUE | **Strong.** Structured review dimensions: readability, maintainability, style, potential bugs, performance, best practice. Closest to usable as-is. |
| `requirement-analysis-and-planning-agent` | 999 | FALSE | **Strong.** Frontmatter-style agent definition with an explicit workflow. Notably *not* flagged `for_devs` despite being developer work — evidence that the flag is unreliable. |
| `Comprehensive UI/UX Mobile App Analysis` | 920 | FALSE | **Good.** Multi-persona screenshot critique (designer / developer / user). Directly relevant to the M5 visual-review path. |
| `Accessibility Auditor` | 408 | TRUE | **Good.** Names WCAG 2.2 and Section 508, keyboard nav, screen readers, contrast. Useful as a validator prompt template. |
| `Bug Discovery Code Assistant` | 503 | TRUE | **Moderate.** Reasonable structure, but no output contract. |
| `IT Architect` | 605 | TRUE | **Moderate.** Gap-analysis framing is useful; too open-ended as a contract. |
| `Comprehensive Repository Analysis and Bug Fixing Framework` | 3,195 | FALSE | **Reject.** Instructs the model to fix "ALL verifiable bugs" across an entire repository — directly contradicts ForgeOne's scoped-patch and bounded-retry rules. |
| `Unit Tester Assistant` | 242 | TRUE | **Reject.** Too thin to add value over ForgeOne's own template. |
| `Midjourney Prompt Generator` | 848 | FALSE | **Weak for ForgeOne.** Vendor-specific phrasing; only the descriptive-structure idea transfers to the M8 media path. |

`[INF]` **Conclusion: the corpus is a seed, not a library.** Even the strongest
candidates lack output schemas, acceptance criteria and versioning. ForgeOne's
value-add is the wrapper, not the text.

---

## 6. Reuse strategy — no application dependency

**Decision:** ForgeOne vendors a *curated, transformed subset* of prompt text
into its own versioned library. The prompts.chat application is **never** a
runtime dependency.

```text
CURATION TIME (offline, one-off, human-reviewed)
  1. Fetch prompts.csv from the public raw endpoint  (read-only, public data)
  2. Filter to for_devs = TRUE, plus manually reviewed exceptions
  3. Score against ForgeOne task categories
  4. Reject anything that attempts to override system rules
  5. Transform into the ForgeOne prompt record schema
  6. Record provenance: source repo, commit SHA, act title, CC0 licence
  7. Human review and approval
  8. Commit the vendored record into docs/ or a prompt package

RUN TIME (no network, no prompts.chat dependency)
  ForgeOne composes prompts from its own versioned library only
```

This satisfies the milestone constraint — "reuse selected prompts WITHOUT
installing or cloning the complete application as a mandatory runtime
dependency" — by construction:

| Constraint | How it is satisfied |
|---|---|
| No application install | Only `prompts.csv` is read, as a static file |
| No Node/PostgreSQL dependency | Nothing from the Next.js app is used |
| No runtime network call | Vendoring happens at curation time; run time is fully offline |
| No outbound workspace data | Only a public static file is fetched |
| Auditable | Every vendored prompt carries source commit + licence provenance |

**Provenance record minimum:** upstream repository, upstream commit SHA at
curation, original `act` title, `contributor` field, licence (`CC0-1.0`), and
the ForgeOne prompt ID it became.

---

## 7. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Prompt-injection via retrieved prompt text | **High** | Untrusted-content rule; composition order; curation-time rejection |
| 2 | Upstream dataset changes or disappears | Medium | Vendored copy is authoritative; provenance records the source commit |
| 3 | `for_devs` flag is unreliable (proven by `requirement-analysis-and-planning-agent`) | Medium | Never trust the flag alone; human review every candidate |
| 4 | Corpus size tempts bulk import | Medium | Hard cap on the curated set; each addition reviewed |
| 5 | Quality variance across 1,045 contributors | Medium | Small curated set; acceptance criteria per prompt |
| 6 | Remote MCP accidentally enabled | **High** | Disabled by default; explicit opt-in with a visible notice |
| 7 | CC0 prompts contain third-party trademarks (e.g. Midjourney, ChatGPT) | Low | Reject vendor-specific prompts; prefer vendor-neutral templates |

---

## 8. Recommendation

1. **Adopt CC0 prompt data as a seed corpus**, vendored, never as a runtime
   dependency. ✅
2. **Cap the first curated set at ≤ 8 prompts**, one per ForgeOne task category.
3. **Do not enable the remote MCP endpoint** by default; treat it as an opt-in
   feature for a future milestone if a live-browse use case appears.
4. **Do not install prompts.chat** in any form for FORGE-003.
5. Re-evaluate after M3, once real task telemetry shows which categories
   actually benefit from a template.

See [`prompt-intelligence-design.md`](../architecture/prompt-intelligence-design.md)
for the record schema and composition order.
