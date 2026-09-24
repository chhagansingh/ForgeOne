---
name: Feature or milestone task
about: Propose a scoped change with an explicit acceptance gate
title: "[FEAT] "
labels: ["enhancement", "triage"]
assignees: []
---

## Goal

<!-- What outcome is wanted, in one or two sentences. -->

## Milestone

<!-- M1..M9 / FS0..FS1, or "unscheduled". -->

## Why now

<!-- What depends on this, and what breaks if it is deferred. -->

## Scope

**In scope**

-

**Explicitly out of scope**

-

## Acceptance gate

<!-- The deterministic evidence that will prove this is done. A milestone is
     never "done" because the model says so. -->

- [ ] Command / test:
- [ ] Expected evidence:
- [ ] `PASS` requires:

## Contract and traceability

| Field | Value |
|---|---|
| Requirement IDs | <!-- REQ-xxx-yy --> |
| Affected paths | |
| Baseline commit SHA | |
| Frozen files / constraints | |
| Owner approval needed for | |

## Risk

<!-- Data classification, credentials, destructive operations, licence or
     dependency implications, hardware budget. -->

## Alternatives considered

<!-- Including doing nothing. -->

## Checklist

- [ ] One scoped branch/worktree per issue.
- [ ] No secrets, model weights or client data will be committed.
- [ ] The result will be reported as `PASS` / `FAIL` / `NOT_RUN` / `BLOCKED` honestly.
