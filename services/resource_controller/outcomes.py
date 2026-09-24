"""Machine-readable outcomes for every Resource Controller decision.

Outcomes are explicit and terminal. The controller never silently downgrades a
request: it either admits it as specified, or rejects it with a named reason.
"""

from __future__ import annotations

from enum import Enum


class Outcome(str, Enum):
    """Every terminal state a controller decision can reach."""

    ADMITTED = "ADMITTED"

    REJECTED_CONTEXT = "REJECTED_CONTEXT"
    REJECTED_MEMORY = "REJECTED_MEMORY"
    REJECTED_CONCURRENCY = "REJECTED_CONCURRENCY"
    REJECTED_UNVERIFIED_ESTIMATE = "REJECTED_UNVERIFIED_ESTIMATE"

    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED_MEMORY_PRESSURE = "ABORTED_MEMORY_PRESSURE"
    PROCESS_FAILED = "PROCESS_FAILED"


REJECTION_OUTCOMES = frozenset(
    {
        Outcome.REJECTED_CONTEXT,
        Outcome.REJECTED_MEMORY,
        Outcome.REJECTED_CONCURRENCY,
        Outcome.REJECTED_UNVERIFIED_ESTIMATE,
    }
)

TERMINATION_OUTCOMES = frozenset(
    {
        Outcome.CANCELLED,
        Outcome.TIMED_OUT,
        Outcome.ABORTED_MEMORY_PRESSURE,
        Outcome.PROCESS_FAILED,
    }
)


def is_rejection(outcome: Outcome) -> bool:
    return outcome in REJECTION_OUTCOMES


def is_success(outcome: Outcome) -> bool:
    """Only ADMITTED counts as success. Nothing else may be reported as such."""
    return outcome is Outcome.ADMITTED
