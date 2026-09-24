"""Controller orchestration -- cooldown, cleanup, telemetry, refusal paths."""

from __future__ import annotations

import unittest

from services.resource_controller.admission import AdmissionRequest
from services.resource_controller.controller import ResourceController
from services.resource_controller.outcomes import Outcome
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
)


def make_thresholds(**kw):
    base = dict(
        min_available_bytes=6 * GB,
        max_swap_used_bytes=2 * GB,
        max_pageout_rate=5000.0,
        sample_interval_s=0.001,
    )
    base.update(kw)
    return WatchdogThresholds(**base)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_controller(**kw):
    clock = kw.get("clock", FakeClock())
    policy = kw.get("policy", make_policy())
    supervisor = kw.get("supervisor")
    if supervisor is None and kw.get("with_supervisor", True):
        supervisor = ProcessSupervisor(FakeProcessAdapter(), graceful_timeout_s=1.0, clock=clock)
    return ResourceController(
        policy,
        kw.get("counter", FixedTokenCounter(2000)),
        kw.get("metadata", make_metadata()),
        kw.get("telemetry", SyntheticTelemetrySource([healthy_snapshot()])),
        supervisor=supervisor,
        telemetry_writer=kw.get("writer"),
        watchdog_thresholds=kw.get("thresholds", make_thresholds()),
        clock=clock,
    )


def request(**kw):
    base = dict(messages=message(), requested_output_tokens=500)
    base.update(kw)
    return AdmissionRequest(**base)


class CooldownTests(unittest.TestCase):
    def test_cooldown_after_abnormal_exit(self):
        """Case 17: no new start during the post-abort cooldown."""
        clock = FakeClock()
        ctrl = make_controller(clock=clock)
        self.assertFalse(ctrl.in_cooldown())

        ctrl.record_abnormal_exit()
        self.assertTrue(ctrl.in_cooldown())
        self.assertAlmostEqual(ctrl.cooldown_remaining(), 120.0)

        blocked = ctrl.start_supervised("svc", ["sleep", "1"])
        self.assertEqual(blocked.outcome, Outcome.REJECTED_MEMORY)
        self.assertIn("cooldown active", blocked.reason)

        clock.t += 121.0
        self.assertFalse(ctrl.in_cooldown())
        allowed = ctrl.start_supervised("svc", ["sleep", "1"])
        self.assertEqual(allowed.outcome, Outcome.ADMITTED)

    def test_abort_triggers_cooldown(self):
        clock = FakeClock()
        ctrl = make_controller(
            clock=clock,
            telemetry=SyntheticTelemetrySource([healthy_snapshot(), low_memory_snapshot()]),
        )
        verdict = ctrl.run_watchdog(max_samples=5)
        self.assertTrue(verdict.abort)
        self.assertTrue(ctrl.in_cooldown())


class SupervisionTests(unittest.TestCase):
    def test_start_and_cancel_supervised(self):
        ctrl = make_controller()
        started = ctrl.start_supervised("model-server", ["sleep", "60", "--host", "127.0.0.1"])
        self.assertEqual(started.outcome, Outcome.ADMITTED)
        self.assertIsNotNone(started.identity)
        self.assertEqual(len(ctrl.owned_pids()), 1)

        outcome = ctrl.cancel("model-server")
        self.assertEqual(outcome, Outcome.CANCELLED)
        self.assertEqual(ctrl.owned_pids(), [])

    def test_start_refused_without_supervisor(self):
        ctrl = make_controller(supervisor=None, with_supervisor=False)
        started = ctrl.start_supervised("svc", ["sleep", "1"])
        self.assertEqual(started.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("no supervisor", started.reason)

    def test_bind_violation_is_refused_not_started(self):
        ctrl = make_controller()
        started = ctrl.start_supervised("svc", ["sleep", "1", "--host", "0.0.0.0"])
        self.assertEqual(started.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("BindPolicyError", started.reason)
        self.assertEqual(ctrl.owned_pids(), [])

    def test_cleanup_terminates_all_owned(self):
        """Case 18: controller-level cleanup."""
        ctrl = make_controller()
        ctrl.start_supervised("a", ["sleep", "60"])
        ctrl.start_supervised("b", ["sleep", "60"])
        results = ctrl.cleanup()
        self.assertEqual(set(results), {"a", "b"})
        self.assertEqual(ctrl.owned_pids(), [])

    def test_cancel_unknown_tag(self):
        ctrl = make_controller()
        self.assertEqual(ctrl.cancel("nope"), Outcome.PROCESS_FAILED)


class TelemetryRecordingTests(unittest.TestCase):
    def test_admission_is_recorded(self):
        writer = MemoryTelemetryWriter()
        ctrl = make_controller(writer=writer)
        decision = ctrl.admit_and_record(request())
        self.assertEqual(decision.outcome, Outcome.ADMITTED)
        self.assertEqual(len(writer.records), 1)
        self.assertEqual(writer.records[0]["event"], "admission")
        self.assertEqual(writer.records[0]["outcome"], "ADMITTED")

    def test_rejection_is_recorded_with_reason(self):
        writer = MemoryTelemetryWriter()
        ctrl = make_controller(writer=writer)
        decision = ctrl.admit_and_record(request(retained_cache_bytes=8 * 1024**3))
        self.assertEqual(decision.outcome, Outcome.REJECTED_MEMORY)
        self.assertEqual(writer.records[0]["outcome"], "REJECTED_MEMORY")
        self.assertTrue(writer.records[0]["reason"])

    def test_lifecycle_events_recorded(self):
        writer = MemoryTelemetryWriter()
        ctrl = make_controller(writer=writer)
        ctrl.start_supervised("svc", ["sleep", "60"])
        ctrl.cancel("svc")
        events = [r["event"] for r in writer.records]
        self.assertEqual(events, ["started", "stopped"])


class EndToEndGateTests(unittest.TestCase):
    def test_the_incident_scenario_is_now_refused(self):
        """A 32,611-token request on a host with 9.7 GB available must be rejected.

        This mirrors the real FORGE-003 failure: the request that crashed the
        Metal backend would not have been admitted.
        """
        ctrl = make_controller(counter=FixedTokenCounter(32611))
        decision = ctrl.admit_and_record(request(requested_output_tokens=4096))
        self.assertFalse(decision.admitted)
        self.assertIn(
            decision.outcome,
            (Outcome.REJECTED_CONTEXT, Outcome.REJECTED_MEMORY),
        )

    def test_small_request_still_admitted(self):
        """The gate must not be so strict that normal work is impossible."""
        ctrl = make_controller(counter=FixedTokenCounter(1500))
        decision = ctrl.admit(request(requested_output_tokens=500))
        self.assertEqual(decision.outcome, Outcome.ADMITTED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
