"""Protected execution boundary -- startup gating and request gating.

Everything is faked: no model, no server, no HTTP, no real inference. The
forwarder is a recording double, so a bypass would be visible as an unexpected
call.
"""

from __future__ import annotations

import unittest

from services.resource_controller.controller import ResourceController
from services.resource_controller.outcomes import Outcome
from services.resource_controller.ports import StaticPortProbe
from services.resource_controller.protected import (
    ProtectedServer,
    RecordingForwarder,
    StaticWatchdogReadiness,
)
from services.resource_controller.server_config import CacheBudget, ProtectedServerConfig
from services.resource_controller.supervisor import FakeProcessAdapter, ProcessSupervisor
from services.resource_controller.telemetry import SyntheticTelemetrySource
from services.resource_controller.watchdog import MemoryTelemetryWriter, WatchdogThresholds

from .support import (
    GB,
    FixedTokenCounter,
    healthy_snapshot,
    low_memory_snapshot,
    make_metadata,
    make_policy,
    message,
    stale_snapshot,
)

PY = "/usr/bin/python3"


def protected_policy(**kw):
    """A policy whose headroom actually fits a cold start on this fixture."""
    base = dict(min_available_memory_bytes=int(3.5 * GB))
    base.update(kw)
    return make_policy(**base)


def make_config(**kw):
    base = dict(
        executable=PY,
        model="mlx-community/Qwen3-4B-Instruct-2507-4bit",
        port=8082,
        cache=CacheBudget(
            retained_cache_bytes=2 * GB,
            max_sequences=8,
            active_kv_bytes=int(2.5 * GB),
            weights_bytes=int(2.2 * GB),
            transient_reserve_bytes=int(2.0 * GB),
        ),
    )
    base.update(kw)
    return ProtectedServerConfig(**base)


def make_protected(**kw):
    policy = kw.get("policy", protected_policy())
    controller = ResourceController(
        policy,
        kw.get("counter", FixedTokenCounter(2000)),
        kw.get("metadata", make_metadata()),
        kw.get("telemetry", SyntheticTelemetrySource([healthy_snapshot()])),
        supervisor=kw.get(
            "supervisor", ProcessSupervisor(FakeProcessAdapter(), graceful_timeout_s=1.0)
        ),
        telemetry_writer=kw.get("writer"),
        watchdog_thresholds=WatchdogThresholds(
            min_available_bytes=1 * GB, max_swap_used_bytes=8 * GB,
            max_pageout_rate=1e9, sample_interval_s=0.001,
        ),
    )
    forwarder = kw.get("forwarder", RecordingForwarder())
    server = ProtectedServer(
        controller,
        kw.get("config", make_config()),
        port_probe=kw.get("port_probe", StaticPortProbe(free=True)),
        watchdog_readiness=kw.get("readiness", StaticWatchdogReadiness(True)),
        forwarder=forwarder,
        writer=kw.get("writer"),
    )
    return server, controller, forwarder


class ProtectedStartupTests(unittest.TestCase):
    def test_successful_protected_startup(self):
        server, _, _ = make_protected()
        result = server.start()
        self.assertEqual(result.outcome, Outcome.ADMITTED, result.reason)
        self.assertTrue(result.started)
        self.assertIsNotNone(result.identity)
        self.assertTrue(server.running)

    def test_missing_policy_blocks_startup(self):
        """Case 7."""
        server, _, _ = make_protected(policy=None)
        result = server.start()
        self.assertEqual(result.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("policy", result.reason)
        self.assertFalse(server.running)

    def test_missing_metadata_blocks_startup(self):
        server, _, _ = make_protected(metadata=None)
        result = server.start()
        self.assertEqual(result.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertFalse(server.running)

    def test_watchdog_not_ready_blocks_startup(self):
        """Case 8: never launch a heavy process unsupervised."""
        server, _, _ = make_protected(readiness=StaticWatchdogReadiness(False))
        result = server.start()
        self.assertEqual(result.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("watchdog is not ready", result.reason)
        self.assertFalse(server.running)

    def test_port_occupied_blocks_startup(self):
        """Case 12."""
        server, _, _ = make_protected(
            port_probe=StaticPortProbe(free=False, detail="held by another process")
        )
        result = server.start()
        self.assertEqual(result.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("not available", result.reason)
        self.assertFalse(server.running)

    def test_memory_pressure_blocks_startup(self):
        """Case 15."""
        server, _, _ = make_protected(
            telemetry=SyntheticTelemetrySource([low_memory_snapshot()])
        )
        result = server.start()
        self.assertEqual(result.outcome, Outcome.REJECTED_MEMORY)
        self.assertFalse(server.running)

    def test_stale_telemetry_blocks_startup(self):
        """Case 14: a stale snapshot describes a host that no longer exists."""
        server, _, _ = make_protected(
            telemetry=SyntheticTelemetrySource([stale_snapshot(600.0)])
        )
        result = server.start()
        self.assertEqual(result.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("stale", result.reason)
        self.assertFalse(server.running)

    def test_cold_start_charges_residency(self):
        """Case 9: weights and overhead belong to the cold start."""
        server, _, _ = make_protected()
        result = server.start()
        est = result.cold_start_estimate
        self.assertIsNotNone(est)
        meta = make_metadata()
        self.assertFalse(est.model_resident)
        self.assertEqual(
            est.total_bytes,
            meta.weight_bytes + meta.runtime_overhead_bytes + protected_policy().transient_reserve_bytes,
        )
        self.assertGreaterEqual(est.total_bytes, meta.weight_bytes)

    def test_refusals_are_recorded(self):
        """Case 21: telemetry survives a refusal."""
        writer = MemoryTelemetryWriter()
        server, _, _ = make_protected(readiness=StaticWatchdogReadiness(False), writer=writer)
        server.start()
        events = [r["event"] for r in writer.records]
        self.assertIn("startup_refused", events)
        self.assertTrue(any("watchdog" in r.get("reason", "") for r in writer.records))

    def test_startup_is_recorded_on_success(self):
        writer = MemoryTelemetryWriter()
        server, _, _ = make_protected(writer=writer)
        server.start()
        events = [r["event"] for r in writer.records]
        self.assertIn("startup_ok", events)
        ok = next(r for r in writer.records if r["event"] == "startup_ok")
        self.assertEqual(ok["port"], 8082)
        self.assertIn("--prompt-cache-bytes", ok["argv"])


class ProtectedRequestTests(unittest.TestCase):
    def test_request_is_admitted_and_forwarded_once(self):
        server, _, forwarder = make_protected()
        server.start()
        result = server.request(message(), requested_output_tokens=500)
        self.assertEqual(result.outcome, Outcome.ADMITTED, result.reason)
        self.assertTrue(result.executed)
        self.assertEqual(len(forwarder.calls), 1)
        self.assertEqual(forwarder.calls[0]["max_tokens"], 500)

    def test_rejected_request_is_never_forwarded(self):
        """Case 20: the execution adapter cannot bypass admission."""
        server, _, forwarder = make_protected(counter=FixedTokenCounter(13000))
        server.start()
        result = server.request(message(), requested_output_tokens=500)
        self.assertFalse(result.admitted)
        self.assertFalse(result.executed)
        self.assertEqual(forwarder.calls, [], "a rejected request must not reach the server")

    def test_request_before_start_is_refused(self):
        server, _, forwarder = make_protected()
        result = server.request(message(), requested_output_tokens=500)
        self.assertEqual(result.outcome, Outcome.PROCESS_FAILED)
        self.assertEqual(forwarder.calls, [])

    def test_resident_model_is_not_double_counted(self):
        """Case 10: the request estimate excludes weights the host already holds."""
        server, _, _ = make_protected()
        server.start()
        result = server.request(message(), requested_output_tokens=500)
        est = result.decision.estimate
        meta = make_metadata()
        self.assertTrue(est.model_resident)
        # A resident request charges KV + retained cache + transient only.
        self.assertEqual(
            est.total_bytes,
            est.kv_bytes + est.retained_cache_bytes + est.transient_reserve_bytes,
        )
        # Weights are reported but NOT charged -- the host already holds them.
        self.assertGreater(est.weights_bytes, 0)
        self.assertNotIn(meta.weight_bytes, (est.total_bytes,))
        self.assertLess(est.total_bytes, est.total_bytes + meta.weight_bytes)
        # A cold start over the same context would cost strictly more.
        from services.resource_controller.estimator import estimate as _est

        cold = _est(
            context_tokens=est.context_tokens, metadata=meta,
            retained_cache_bytes=est.retained_cache_bytes,
            transient_reserve_bytes=est.transient_reserve_bytes,
            model_resident=False,
        )
        self.assertEqual(cold.total_bytes - est.total_bytes, meta.weight_bytes + meta.runtime_overhead_bytes)

    def test_reservation_released_after_success(self):
        """Case 16."""
        server, controller, _ = make_protected()
        server.start()
        server.request(message(), requested_output_tokens=500)
        self.assertEqual(controller.reservations.active_count, 0)

    def test_reservation_released_after_rejection(self):
        server, controller, _ = make_protected(counter=FixedTokenCounter(13000))
        server.start()
        server.request(message(), requested_output_tokens=500)
        self.assertEqual(controller.reservations.active_count, 0)

    def test_reservation_released_after_forward_failure(self):
        """Case 16: an exception must not leak the slot."""
        forwarder = RecordingForwarder(raises=ConnectionError("transport down"))
        server, controller, _ = make_protected(forwarder=forwarder)
        server.start()
        result = server.request(message(), requested_output_tokens=500)
        self.assertEqual(result.outcome, Outcome.PROCESS_FAILED)
        self.assertEqual(controller.reservations.active_count, 0)

    def test_second_concurrent_request_is_rejected(self):
        """Case 11: a held reservation blocks a second request."""
        server, controller, forwarder = make_protected()
        server.start()
        held = controller.reservations.acquire("manual")
        try:
            result = server.request(message(), requested_output_tokens=500)
            self.assertEqual(result.outcome, Outcome.REJECTED_CONCURRENCY)
            self.assertEqual(forwarder.calls, [])
        finally:
            controller.reservations.release(held)

    def test_unexpected_child_exit_is_detected(self):
        """Case 17."""
        adapter = FakeProcessAdapter()
        supervisor = ProcessSupervisor(adapter, graceful_timeout_s=1.0)
        server, controller, forwarder = make_protected(supervisor=supervisor)
        started = server.start()
        pid = started.identity.pid
        adapter.simulate_exit(pid, 137)  # e.g. killed by the OS

        # The exit code is observable, and the supervisor must not signal a
        # process that is no longer the one it started.
        self.assertEqual(adapter.poll(pid), 137)
        outcome = server.stop()
        self.assertEqual(outcome, Outcome.PROCESS_FAILED)
        self.assertFalse(server.running)
        self.assertEqual(forwarder.calls, [])

    def test_stop_is_safe_when_not_running(self):
        server, _, _ = make_protected()
        self.assertEqual(server.stop(), Outcome.PROCESS_FAILED)

    def test_unrelated_process_is_not_touched(self):
        """Case 19: only owned PIDs are ever signalled."""
        adapter = FakeProcessAdapter()
        supervisor = ProcessSupervisor(adapter, graceful_timeout_s=1.0)
        server, _, _ = make_protected(supervisor=supervisor)
        started = server.start()
        server.stop()

        signalled = {pid for pid, _, _ in adapter.signals}
        self.assertEqual(signalled, {started.identity.pid})
        # An unrelated PID that was never registered is untouched.
        self.assertNotIn(99999, signalled)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
