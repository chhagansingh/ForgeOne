---
name: Bug report
about: Report a defect with reproducible evidence
title: "[BUG] "
labels: ["bug", "triage"]
assignees: []
---

## Summary

<!-- One or two sentences describing the defect. -->

## Environment

- ForgeOne commit SHA:
- macOS / OS version:
- Runtime and version (IDE agent, control plane, adapter, model backend):
- Hardware (chip, unified memory):

## Reproduction

1.
2.
3.

**Reproduction rate:** <!-- always / intermittent (n/m) / once -->

## Expected behaviour

## Observed behaviour

## Evidence

<!-- Attach or link: exit codes, test output, failed-test names, logs,
     screenshots. Redact credentials, tokens, client data and local paths. -->

```text
<paste raw output here>
```

## Validation status

<!-- Choose one, honestly. -->

- [ ] `FAIL` — reproduced with the evidence above
- [ ] `NOT_RUN` — could not be reproduced yet
- [ ] `BLOCKED` — blocked by an external dependency (state which)

## Impact

<!-- Who or which workflow is affected, and whether a workaround exists. -->

## Checklist

- [ ] I removed secrets, tokens and client data from all attached evidence.
- [ ] I recorded the exact commit SHA the defect was observed on.
- [ ] I did not report an unexecuted check as passing.
