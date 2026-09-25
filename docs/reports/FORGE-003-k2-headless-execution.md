# FORGE-003 — K2 Headless Execution Guide

How to run the first protected K2-Horizon inference from a **plain macOS
Terminal**, after you have freed memory by quitting heavy applications.

**Nothing here was executed during the implementation task.** The launcher was
created and validated model-free only.

---

## Why this exists

The protected K2 load was attempted from inside the IDE and correctly
**BLOCKED**: available memory was ~5.5 GiB against an **11 GiB** requirement
(7 GiB provisional cold-start charge + 4 GiB reserve). The IDE and its helper
processes are a large part of that memory pressure, so the experiment must run
with them closed.

The launcher is deliberately standalone: it does not depend on Devin, VS Code or
any IDE staying open. The watchdog runs as its own process.

---

## Commands (copy-paste)

### Step 1 — Save your work

Commit or stash anything unsaved. This closes your IDE.

### Step 2 — Quit memory-heavy applications

Quit **Devin**, **Brave**, Microsoft Teams, and any other large application.
Nothing will be closed for you — the launcher never terminates unrelated
processes.

### Step 3 — Open macOS Terminal

Terminal.app, not an IDE terminal.

### Step 4 — Preflight (safe: never loads weights)

```bash
# From the repository root (this repo is public, so the owner-local absolute
# path is deliberately not hardcoded here):
cd "$(git rev-parse --show-toplevel)"   # if you are already inside the repo
./scripts/run_k2_headless_smoke.sh --check
```

This samples memory for **30 seconds**, checks swap and page-out behaviour,
verifies no conflicting model process is running and that ports 8082/8084 are
free, then prints `PASS` or `BLOCKED`.

Exit codes: `0` admitted · `2` blocked.

### Step 5 — One-shot protected experiment

**Only if Step 4 printed PASS.**

```bash
./scripts/run_k2_headless_smoke.sh --execute
```

This runs **exactly one** bounded session:

| Limit | Value |
|---|---|
| Context | **1024 tokens** |
| Output | **32 tokens** |
| Concurrent requests | **1** |
| Server slots | **1** |
| Inference requests | **exactly 1** |

Sequence: admission gate → independent watchdog → supervised `llama-server` on
`127.0.0.1:8082` → health check → `ProtectedGateway` on `127.0.0.1:8084` → one
inference → clean shutdown.

There is **no automatic retry**. If it fails, it stops.

Exit codes: `0` ok · `2` blocked · `3` aborted · `4` failed.

### Step 6 — Read the summary

```bash
cat storage/runs/k2-headless-summary.json
```

Contains the admission decision, request outcome, token counts, timing and
cleanup verification.

Raw telemetry (Git-ignored):

```bash
cat storage/runs/k2-headless-telemetry.jsonl   # watchdog samples
cat storage/runs/k2-headless-admission.jsonl   # controller decisions
```

Backend log: `storage/logs/` — check it for Metal device lines.

### Step 7 — Reopen your applications

Nothing is left running: the launcher stops only ForgeOne-owned processes and
releases every reservation on all exit paths.

---

## If the preflight says BLOCKED

It will print the observed and required GiB. **Free more memory and re-run
Step 4.** The threshold is not adjustable — it exists because an earlier
unprotected experiment caused a Metal OOM.

Do not lower it to make the experiment pass.

---

## What the launcher enforces

| Control | Behaviour |
|---|---|
| Storage containment | every path is realpath-checked against `$FORGEONE_HOME/storage/`; an escaping path is refused before anything starts |
| Admission gate | 30 s sustained sampling; ≥11 GiB effective; stable swap and page-outs |
| Watchdog | separate process, **ready before** the backend starts |
| Protected path | the backend is started by the Resource Controller; the raw endpoint is never agent-accessible |
| Budgets | 1024 ctx / 32 output / 1 slot / 1 request, `--no-prompt-cache`, `--parallel 1` |
| Cleanup | `finally` block stops gateway, backend and watchdog on **every** exit path, including Ctrl+C |
| No retry | a single attempt; no loop, no schedule |

## What it does NOT do

- It does **not** close your applications.
- It does **not** fall back to CPU or to another model.
- It does **not** claim tool calling works — one plain-text response proves
  nothing about tool calling.
- It does **not** establish a safe maximum context. That remains **UNKNOWN**.

## Remaining blockers before this can succeed

1. **Memory headroom** — needs ≥11 GiB available; last observed 5.4 GiB.
2. K2 checkpoint load unvalidated.
3. Metal execution for the K2-specific ops unproven.
4. Exact K2 token accounting unvalidated.
5. No inference validated.

**`RUNTIME_BUILT` is true.** `CHECKPOINT_LOAD_VALIDATED`,
`METAL_EXECUTION_VALIDATED`, `TOKEN_ACCOUNTING_VALIDATED`,
`PROTECTED_INFERENCE_VALIDATED` and `TOOL_CALLING_VALIDATED` are all still
**false**.
