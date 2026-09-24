"""Public API of the ForgeOne Resource Controller.

Ties together policy, admission control, process supervision, the watchdog and
telemetry persistence. Every dependency is injected, so the whole controller is
exercisable in tests without loading a model or spawning a heavy process.

The controller **never** silently reduces context, swaps models, or reports a
rejected task as successful. A decision is exactly one of the
:class:`~services.resource_controller.outcomes.Outcome` values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from .admission import AdmissionController, AdmissionDecision, AdmissionRequest
from .estimator import ModelMetadata
from .outcomes import Outcome
from .policy import ResourcePolicy
from .supervisor import ProcessIdentity, ProcessSupervisor
from .telemetry import TelemetrySource
from .tokenization import TokenCounter
from .watchdog import (
    TelemetryWriter,
    Watchdog,
    WatchdogThresholds,
    WatchdogVerdict,
)


@dataclass(frozen=True)
class SupervisedStart:
    """Result of asking the controller to start a workload."""

    outcome: Outcome
    reason: str
    identity: Optional[ProcessIdentity] = None


class CooldownActive(RuntimeError):
    """Raised when a start is attempted during the post-abort cooldown."""


class ResourceController:
    def __init__(
        self,
        policy: Optional[ResourcePolicy],
        token_counter: Optional[TokenCounter],
        metadata: Optional[ModelMetadata],
        telemetry: Optional[TelemetrySource],
        *,
        supervisor: Optional[ProcessSupervisor] = None,
        telemetry_writer: Optional[TelemetryWriter] = None,
        watchdog_thresholds: Optional[WatchdogThresholds] = None,
        clock=time.monotonic,
    ) -> None:
        self._policy = policy
        self._telemetry = telemetry
        self._supervisor = supervisor
        self._writer = telemetry_writer
        self._thresholds = watchdog_thresholds
        self._clock = clock
        self._admission = AdmissionController(policy, token_counter, metadata, telemetry)
        self._cooldown_until = 0.0

    # -- admission -------------------------------------------------------
    def admit(self, request: AdmissionRequest) -> AdmissionDecision:
        return self._admission.admit(request)

    def admit_and_record(self, request: AdmissionRequest) -> AdmissionDecision:
        decision = self.admit(request)
        if self._writer is not None:
            record = decision.as_dict()
            record["event"] = "admission"
            record["label"] = request.label
            self._writer.write(record)
        return decision

    # -- cooldown --------------------------------------------------------
    def record_abnormal_exit(self) -> None:
        """Enter cooldown. Called after any non-graceful termination."""
        if self._policy is None:
            return
        self._cooldown_until = self._clock() + self._policy.cooldown_after_abnormal_exit_s

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - self._clock())

    def in_cooldown(self) -> bool:
        return self.cooldown_remaining() > 0.0

    # -- supervision -----------------------------------------------------
    def start_supervised(
        self,
        tag: str,
        argv: Sequence[str],
        *,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
    ) -> SupervisedStart:
        if self._supervisor is None:
            return SupervisedStart(
                outcome=Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                reason="no supervisor configured; refusing to start unsupervised",
            )
        if self.in_cooldown():
            return SupervisedStart(
                outcome=Outcome.REJECTED_MEMORY,
                reason=f"cooldown active for another {self.cooldown_remaining():.1f}s",
            )
        try:
            identity = self._supervisor.start(tag, argv, env=env, cwd=cwd)
        except Exception as exc:
            return SupervisedStart(
                outcome=Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                reason=f"start refused: {type(exc).__name__}: {exc}",
            )
        if self._writer is not None:
            self._writer.write(
                {"event": "started", "tag": tag, "pid": identity.pid, "argv": list(argv)}
            )
        return SupervisedStart(outcome=Outcome.ADMITTED, reason="started", identity=identity)

    def stop_supervised(self, tag: str, *, outcome: Outcome = Outcome.CANCELLED) -> Outcome:
        if self._supervisor is None:
            return Outcome.PROCESS_FAILED
        identity = self._supervisor.owned().get(tag)
        if identity is None:
            return Outcome.PROCESS_FAILED
        result = self._supervisor.stop(identity, outcome=outcome)
        if result in (Outcome.ABORTED_MEMORY_PRESSURE, Outcome.PROCESS_FAILED, Outcome.TIMED_OUT):
            self.record_abnormal_exit()
        if self._writer is not None:
            self._writer.write({"event": "stopped", "tag": tag, "outcome": result.value})
        return result

    def cancel(self, tag: str) -> Outcome:
        return self.stop_supervised(tag, outcome=Outcome.CANCELLED)

    def cleanup(self) -> Dict[str, Outcome]:
        if self._supervisor is None:
            return {}
        return self._supervisor.cleanup_all()

    def owned_pids(self):
        return self._supervisor.owned_pids() if self._supervisor else []

    # -- watchdog --------------------------------------------------------
    def make_watchdog(self, writer: Optional[TelemetryWriter] = None) -> Watchdog:
        if self._telemetry is None:
            raise ValueError("no telemetry source configured")
        if self._thresholds is None:
            raise ValueError("no watchdog thresholds configured")
        return Watchdog(
            self._telemetry,
            self._thresholds,
            writer or self._writer or _NullWriter(),
        )

    def run_watchdog(self, *, max_samples: Optional[int] = None) -> WatchdogVerdict:
        verdict = self.make_watchdog().run(max_samples=max_samples)
        if verdict.abort:
            self.record_abnormal_exit()
        return verdict


class _NullWriter(TelemetryWriter):
    def write(self, record: dict) -> None:  # pragma: no cover - trivial
        pass
