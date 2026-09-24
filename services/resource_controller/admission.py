"""Admission control -- the gate that would have prevented the FORGE-003 incident.

Every dependency is injected (policy, token counter, model metadata, telemetry),
so the whole decision path is unit-testable without importing, loading or
running any model.

Fail-closed contract:
    If the tokenizer, the model metadata or the telemetry cannot be obtained,
    the request is **rejected**, not admitted on an assumption. An unverified
    estimate is treated as an unsafe estimate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .estimator import Estimate, MetadataUnavailable, ModelMetadata, estimate
from .outcomes import Outcome
from .policy import ResourcePolicy
from .telemetry import MemorySnapshot, TelemetrySource, TelemetryUnavailable
from .tokenization import TokenCounter, TokenizerUnavailable


@dataclass(frozen=True)
class AdmissionRequest:
    """A single request asking permission to run."""

    messages: Sequence[Mapping[str, Any]]
    requested_output_tokens: int
    tools: Optional[Sequence[Mapping[str, Any]]] = None
    retained_cache_bytes: int = 0
    active_requests: int = 0
    label: str = "request"
    model_resident: bool = True
    """False when this request will cause the model to be loaded, so that the
    weights and runtime overhead are charged to it. Default True because a
    serving endpoint normally already holds the model."""


@dataclass(frozen=True)
class AdmissionDecision:
    """The verdict, always with a machine-readable outcome and a human reason."""

    outcome: Outcome
    reason: str
    input_tokens: Optional[int] = None
    estimate: Optional[Estimate] = None
    available_bytes: Optional[int] = None
    budget_bytes: Optional[int] = None

    @property
    def admitted(self) -> bool:
        return self.outcome is Outcome.ADMITTED

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "input_tokens": self.input_tokens,
            "available_bytes": self.available_bytes,
            "budget_bytes": self.budget_bytes,
            "estimate": self.estimate.breakdown() if self.estimate else None,
        }


class AdmissionController:
    """Evaluates admission in a fixed, documented order.

    Evaluation order (first failure wins, so rejections are deterministic):

        1. dependencies present          -> REJECTED_UNVERIFIED_ESTIMATE
        2. telemetry obtainable          -> REJECTED_UNVERIFIED_ESTIMATE
        3. telemetry fresh               -> REJECTED_UNVERIFIED_ESTIMATE
        4. concurrency limit             -> REJECTED_CONCURRENCY
        5. tokens countable              -> REJECTED_UNVERIFIED_ESTIMATE
        6. input token limit             -> REJECTED_CONTEXT
        7. output reservation            -> REJECTED_CONTEXT
        8. total context limit           -> REJECTED_CONTEXT
        9. retained-cache budget         -> REJECTED_MEMORY
       10. available-memory floor        -> REJECTED_MEMORY
       11. estimate vs headroom          -> REJECTED_MEMORY
       12. otherwise                     -> ADMITTED
    """

    def __init__(
        self,
        policy: Optional[ResourcePolicy],
        token_counter: Optional[TokenCounter],
        metadata: Optional[ModelMetadata],
        telemetry: Optional[TelemetrySource],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._counter = token_counter
        self._metadata = metadata
        self._telemetry = telemetry
        self._clock = clock

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _missing(name: str, value: Any) -> Optional[str]:
        if value is None:
            return f"{name} unavailable"
        if name == "tokenizer" and not getattr(value, "available", True):
            return "tokenizer reported itself unavailable"
        return None

    def _reject(self, outcome: Outcome, reason: str, **kw: Any) -> AdmissionDecision:
        return AdmissionDecision(outcome=outcome, reason=reason, **kw)

    # -- the decision ----------------------------------------------------
    def admit(self, request: AdmissionRequest) -> AdmissionDecision:
        # 1. dependencies -------------------------------------------------
        for name, value in (
            ("policy", self._policy),
            ("tokenizer", self._counter),
            ("model metadata", self._metadata),
            ("telemetry", self._telemetry),
        ):
            problem = self._missing(name, value)
            if problem:
                return self._reject(
                    Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                    f"cannot verify request safety: {problem}",
                )

        policy = self._policy
        assert policy is not None  # narrowing for type checkers

        # 2. telemetry ----------------------------------------------------
        try:
            snapshot: MemorySnapshot = self._telemetry.snapshot()  # type: ignore[union-attr]
        except TelemetryUnavailable as exc:
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify request safety: telemetry unavailable ({exc})",
            )

        # 3. telemetry freshness -------------------------------------------
        now = self._clock()
        if not snapshot.is_fresh(now, policy.telemetry_max_age_s):
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify request safety: telemetry is stale "
                f"({snapshot.age(now):.1f}s old, limit {policy.telemetry_max_age_s}s)",
            )

        available = snapshot.available_bytes
        budget = policy.budget_bytes(available)

        # 4. concurrency --------------------------------------------------
        if request.active_requests >= policy.max_concurrent_requests:
            return self._reject(
                Outcome.REJECTED_CONCURRENCY,
                f"{request.active_requests} active request(s); "
                f"max_concurrent_requests={policy.max_concurrent_requests}",
                available_bytes=available,
                budget_bytes=budget,
            )

        # 5. token counting -----------------------------------------------
        try:
            input_tokens = self._counter.count_chat_tokens(  # type: ignore[union-attr]
                request.messages, tools=request.tools
            )
        except TokenizerUnavailable as exc:
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify request safety: token counting failed ({exc})",
                available_bytes=available,
                budget_bytes=budget,
            )

        out_tokens = request.requested_output_tokens
        if out_tokens is None or out_tokens <= 0:
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                "requested_output_tokens must be a positive integer",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 6. input limit ---------------------------------------------------
        if input_tokens > policy.max_input_tokens:
            return self._reject(
                Outcome.REJECTED_CONTEXT,
                f"input tokens {input_tokens} exceed max_input_tokens {policy.max_input_tokens}",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 7. output reservation --------------------------------------------
        if out_tokens > policy.reserved_output_tokens:
            return self._reject(
                Outcome.REJECTED_CONTEXT,
                f"requested output {out_tokens} exceeds reserved_output_tokens "
                f"{policy.reserved_output_tokens}",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 8. total context --------------------------------------------------
        total_context = input_tokens + out_tokens
        if total_context > policy.max_context_tokens:
            return self._reject(
                Outcome.REJECTED_CONTEXT,
                f"input {input_tokens} + output {out_tokens} = {total_context} exceeds "
                f"max_context_tokens {policy.max_context_tokens}",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 9. retained cache --------------------------------------------------
        if request.retained_cache_bytes > policy.max_retained_cache_bytes:
            return self._reject(
                Outcome.REJECTED_MEMORY,
                f"retained cache {request.retained_cache_bytes} bytes exceeds "
                f"max_retained_cache_bytes {policy.max_retained_cache_bytes}",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 10. available-memory floor -------------------------------------------
        if available < policy.min_available_memory_bytes:
            return self._reject(
                Outcome.REJECTED_MEMORY,
                f"available memory {available} bytes is below the floor "
                f"{policy.min_available_memory_bytes}",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 11. estimate vs headroom ---------------------------------------------
        try:
            est = estimate(
                context_tokens=total_context,
                metadata=self._metadata,
                retained_cache_bytes=request.retained_cache_bytes,
                transient_reserve_bytes=policy.transient_reserve_bytes,
                model_resident=request.model_resident,
            )
        except MetadataUnavailable as exc:
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify request safety: {exc}",
                input_tokens=input_tokens,
                available_bytes=available,
                budget_bytes=budget,
            )

        if est.total_bytes > budget:
            return self._reject(
                Outcome.REJECTED_MEMORY,
                f"estimated peak {est.total_bytes} bytes exceeds headroom {budget} bytes "
                f"(available {available} - floor {policy.min_available_memory_bytes})",
                input_tokens=input_tokens,
                estimate=est,
                available_bytes=available,
                budget_bytes=budget,
            )

        # 12. admitted -----------------------------------------------------------
        return AdmissionDecision(
            outcome=Outcome.ADMITTED,
            reason=(
                f"estimated peak {est.total_bytes} bytes within headroom {budget} bytes "
                f"at context {total_context}"
            ),
            input_tokens=input_tokens,
            estimate=est,
            available_bytes=available,
            budget_bytes=budget,
        )

    # ------------------------------------------------------------------
    def admit_startup(self) -> AdmissionDecision:
        """Admit a **cold model start**, charging residency rather than a prompt.

        A cold start loads the weights and the runtime, so those costs belong to
        this decision and not to the first request. There is no prompt yet, so
        no KV cache is charged here — the first request is admitted separately
        with ``model_resident=True`` and must not be double-charged.

        Fails closed on any missing dependency or stale telemetry, exactly as a
        request does.
        """
        for name, value in (
            ("policy", self._policy),
            ("model metadata", self._metadata),
            ("telemetry", self._telemetry),
        ):
            problem = self._missing(name, value)
            if problem:
                return self._reject(
                    Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                    f"cannot verify startup safety: {problem}",
                )

        policy = self._policy
        assert policy is not None

        try:
            snapshot = self._telemetry.snapshot()  # type: ignore[union-attr]
        except TelemetryUnavailable as exc:
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify startup safety: telemetry unavailable ({exc})",
            )

        now = self._clock()
        if not snapshot.is_fresh(now, policy.telemetry_max_age_s):
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify startup safety: telemetry is stale "
                f"({snapshot.age(now):.1f}s old, limit {policy.telemetry_max_age_s}s)",
            )

        available = snapshot.available_bytes
        budget = policy.budget_bytes(available)

        if available < policy.min_available_memory_bytes:
            return self._reject(
                Outcome.REJECTED_MEMORY,
                f"available memory {available} bytes is below the floor "
                f"{policy.min_available_memory_bytes}",
                available_bytes=available,
                budget_bytes=budget,
            )

        try:
            est = estimate(
                context_tokens=0,
                metadata=self._metadata,
                retained_cache_bytes=0,
                transient_reserve_bytes=policy.transient_reserve_bytes,
                model_resident=False,  # cold start: charge weights + overhead
            )
        except MetadataUnavailable as exc:
            return self._reject(
                Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                f"cannot verify startup safety: {exc}",
                available_bytes=available,
                budget_bytes=budget,
            )

        if est.total_bytes > budget:
            return self._reject(
                Outcome.REJECTED_MEMORY,
                f"cold-start estimate {est.total_bytes} bytes exceeds headroom "
                f"{budget} bytes (available {available} - floor "
                f"{policy.min_available_memory_bytes})",
                estimate=est,
                available_bytes=available,
                budget_bytes=budget,
            )

        return AdmissionDecision(
            outcome=Outcome.ADMITTED,
            reason=(
                f"cold-start estimate {est.total_bytes} bytes within headroom {budget} bytes"
            ),
            estimate=est,
            available_bytes=available,
            budget_bytes=budget,
        )
