"""Protected execution boundary: startup and request paths that cannot bypass admission.

The gap this closes
-------------------
``ResourceController.admit_and_record()`` was a *standalone* method. Nothing
stopped a caller from launching a server or forwarding a request without ever
calling it (``controller.py:95-125`` launched processes with no admission step
at all). A gate that can be walked around is not a gate.

:class:`ProtectedServer` is the only supported way to start a managed inference
server and to send it a request. Both paths are gated:

**Startup** (in order, first failure wins):
    1. explicit, validated policy present
    2. model metadata present
    3. fresh system telemetry
    4. watchdog ready **before** the heavy process launches
    5. port demonstrably free (netstat + bind probe)
    6. cold-start residency admitted (weights + overhead + transient reserve)

**Request** (in order):
    1. atomic reservation acquired (closes the check-then-act race)
    2. admission evaluated with the reservation already held
    3. rejection => the request is **not** forwarded
    4. reservation released on every exit path, including exceptions

What is enforced in code vs best-effort
---------------------------------------
*Enforced in code:* admission ordering, atomic single-request reservation,
cold-start charging, loopback-only binding, approved-executable check,
unbounded-serving-path rejection, port probing, reservation release on all
paths, ownership-verified signalling.

*Best-effort, OS-level:* the watchdog's ability to abort before a stall;
macOS memory telemetry accuracy; that ``netstat``+``bind`` catches every
conceivable listener. These reduce risk; they do not eliminate it.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .admission import AdmissionDecision, AdmissionRequest
from .controller import ResourceController
from .estimator import Estimate
from .outcomes import Outcome
from .ports import PortProbe, PortStatus, PortUnavailable, require_free_port
from .server_config import ProtectedServerConfig
from .supervisor import ProcessIdentity
from .watchdog import (
    ProcessWatchdogReadiness,
    StaticWatchdogReadiness,
    TelemetryWriter,
    WatchdogReadiness,
)

__all__ = [
    "ProcessWatchdogReadiness",
    "ProtectedResult",
    "ProtectedServer",
    "ProtectedStartup",
    "RecordingForwarder",
    "RequestForwarder",
    "StaticWatchdogReadiness",
    "WatchdogReadiness",
]


class RequestForwarder(abc.ABC):
    """Transport to the inference server. Injected so tests never touch HTTP."""

    @abc.abstractmethod
    def forward(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]],
        max_tokens: int,
        timeout_s: float,
    ) -> Any:
        raise NotImplementedError


class RecordingForwarder(RequestForwarder):
    """Test double. Records calls so bypasses are detectable."""

    def __init__(self, response: Any = None, raises: Optional[Exception] = None) -> None:
        self.calls: list = []
        self._response = response if response is not None else {"ok": True}
        self._raises = raises

    def forward(self, *, messages, tools, max_tokens, timeout_s):
        self.calls.append(
            {"messages": list(messages), "tools": tools, "max_tokens": max_tokens,
             "timeout_s": timeout_s}
        )
        if self._raises is not None:
            raise self._raises
        return self._response


@dataclass(frozen=True)
class ProtectedStartup:
    outcome: Outcome
    reason: str
    identity: Optional[ProcessIdentity] = None
    cold_start_estimate: Optional[Estimate] = None
    port_status: Optional[PortStatus] = None

    @property
    def started(self) -> bool:
        return self.outcome is Outcome.ADMITTED and self.identity is not None


@dataclass(frozen=True)
class ProtectedResult:
    outcome: Outcome
    reason: str
    decision: Optional[AdmissionDecision] = None
    response: Any = None
    executed: bool = False

    @property
    def admitted(self) -> bool:
        return self.outcome is Outcome.ADMITTED


class ProtectedServer:
    """The only supported path to a managed server and its requests."""

    def __init__(
        self,
        controller: ResourceController,
        config: ProtectedServerConfig,
        *,
        port_probe: PortProbe,
        watchdog_readiness: WatchdogReadiness,
        forwarder: RequestForwarder,
        writer: Optional[TelemetryWriter] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._controller = controller
        self._config = config
        self._port_probe = port_probe
        self._watchdog = watchdog_readiness
        self._forwarder = forwarder
        self._writer = writer
        self._clock = clock
        self._identity: Optional[ProcessIdentity] = None

    # -- introspection ---------------------------------------------------
    @property
    def config(self) -> ProtectedServerConfig:
        return self._config

    @property
    def running(self) -> bool:
        return self._identity is not None

    @property
    def forwarder(self) -> RequestForwarder:
        """Exposed for diagnostics only. Using it directly bypasses admission."""
        return self._forwarder

    def _record(self, event: str, **fields: Any) -> None:
        if self._writer is not None:
            payload = {"event": event}
            payload.update(fields)
            self._writer.write(payload)

    # -- A. protected startup --------------------------------------------
    def start(self) -> ProtectedStartup:
        # 1/2/3/6. policy, metadata, fresh telemetry and cold-start residency
        decision = self._controller.admit_startup()
        if not decision.admitted:
            self._record("startup_refused", outcome=decision.outcome.value,
                         reason=decision.reason)
            return ProtectedStartup(
                outcome=decision.outcome,
                reason=decision.reason,
                cold_start_estimate=decision.estimate,
            )

        # 4. watchdog must be live BEFORE the heavy process launches
        if not self._watchdog.is_ready():
            reason = "watchdog is not ready; refusing to launch an unsupervised process"
            self._record("startup_refused", outcome=Outcome.REJECTED_UNVERIFIED_ESTIMATE.value,
                         reason=reason)
            return ProtectedStartup(
                outcome=Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                reason=reason,
                cold_start_estimate=decision.estimate,
            )

        # 5. port must be demonstrably free
        try:
            port_status = require_free_port(
                self._port_probe, self._config.host, self._config.port
            )
        except PortUnavailable as exc:
            reason = str(exc)
            self._record("startup_refused", outcome=Outcome.REJECTED_UNVERIFIED_ESTIMATE.value,
                         reason=reason)
            return ProtectedStartup(
                outcome=Outcome.REJECTED_UNVERIFIED_ESTIMATE,
                reason=reason,
                cold_start_estimate=decision.estimate,
            )

        # Launch through the supervisor (ownership, PID reuse, bind policy).
        argv = self._config.build_argv()
        started = self._controller.start_supervised(
            tag=f"model-server:{self._config.port}", argv=argv
        )
        if started.outcome is not Outcome.ADMITTED:
            self._record("startup_refused", outcome=started.outcome.value,
                         reason=started.reason)
            return ProtectedStartup(
                outcome=started.outcome,
                reason=started.reason,
                cold_start_estimate=decision.estimate,
                port_status=port_status,
            )

        self._identity = started.identity
        self._record(
            "startup_ok",
            pid=started.identity.pid if started.identity else None,
            port=self._config.port,
            argv=argv,
            cold_start_bytes=decision.estimate.total_bytes if decision.estimate else None,
        )
        return ProtectedStartup(
            outcome=Outcome.ADMITTED,
            reason="protected startup complete",
            identity=started.identity,
            cold_start_estimate=decision.estimate,
            port_status=port_status,
        )

    def stop(self, *, outcome: Outcome = Outcome.CANCELLED) -> Outcome:
        if self._identity is None:
            return Outcome.PROCESS_FAILED
        tag = f"model-server:{self._config.port}"
        result = self._controller.stop_supervised(tag, outcome=outcome)
        self._identity = None
        return result

    # -- B. protected requests -------------------------------------------
    def request(
        self,
        messages: Sequence[Mapping[str, Any]],
        requested_output_tokens: int,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        label: str = "request",
    ) -> ProtectedResult:
        if self._identity is None:
            return ProtectedResult(
                outcome=Outcome.PROCESS_FAILED,
                reason="no managed server is running; refusing to forward",
                executed=False,
            )

        # 1. atomic reservation FIRST, so check-and-act cannot race.
        reservation, outcome, reason = self._controller.reservations.try_acquire(label)
        if reservation is None:
            self._record("request_refused", outcome=outcome.value, reason=reason)
            return ProtectedResult(outcome=outcome, reason=reason, executed=False)

        try:
            # 2. admission, with concurrency derived from the held reservation.
            request = AdmissionRequest(
                messages=messages,
                requested_output_tokens=requested_output_tokens,
                tools=tools,
                # Conservative: assume the retained cache could be at its ceiling.
                retained_cache_bytes=self._config.cache.retained_cache_bytes,
                # Exclude our own reservation: this request already holds the
                # only slot, so counting it would refuse every first request.
                active_requests=self._controller.reservations.active_count_excluding(
                    reservation
                ),
                label=label,
                model_resident=True,  # the server is already running
            )
            decision = self._controller.admit_and_record(request)

            # 3. rejection means NOT executed.
            if not decision.admitted:
                self._record("request_refused", outcome=decision.outcome.value,
                             reason=decision.reason)
                return ProtectedResult(
                    outcome=decision.outcome,
                    reason=decision.reason,
                    decision=decision,
                    executed=False,
                )

            # 4. forward
            try:
                response = self._forwarder.forward(
                    messages=messages,
                    tools=tools,
                    max_tokens=requested_output_tokens,
                    timeout_s=self._controller.policy.request_timeout_s,
                )
            except Exception as exc:  # transport failure is not a rejection
                reason = f"forward failed: {type(exc).__name__}: {exc}"
                self._record("request_failed", reason=reason)
                return ProtectedResult(
                    outcome=Outcome.PROCESS_FAILED,
                    reason=reason,
                    decision=decision,
                    executed=True,
                )

            self._record("request_ok", outcome=Outcome.ADMITTED.value, label=label)
            return ProtectedResult(
                outcome=Outcome.ADMITTED,
                reason=decision.reason,
                decision=decision,
                response=response,
                executed=True,
            )
        finally:
            # 5. release on every exit path, including exceptions.
            self._controller.reservations.release(reservation)
