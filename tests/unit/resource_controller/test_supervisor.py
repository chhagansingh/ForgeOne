"""Process supervision -- ownership, PID reuse, bounded shutdown, cleanup.

Most tests use FakeProcessAdapter. One test uses a trivial `sleep` process to
prove the real adapter works; nothing heavier is ever started.
"""

from __future__ import annotations

import signal
import sys
import time
import unittest

from services.resource_controller.outcomes import Outcome
from services.resource_controller.supervisor import (
    BindPolicyError,
    DuplicateServiceError,
    FakeProcessAdapter,
    OwnershipMismatchError,
    ProcessSupervisor,
    SubprocessAdapter,
    fingerprint_cmdline,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_supervisor(graceful=5.0):
    adapter = FakeProcessAdapter()
    clock = FakeClock()
    return ProcessSupervisor(adapter, graceful_timeout_s=graceful, clock=clock), adapter, clock


class OwnershipTests(unittest.TestCase):
    def test_start_records_identity(self):
        sup, adapter, _ = make_supervisor()
        ident = sup.start("model-server", ["python", "-m", "mlx_lm.server"])
        self.assertEqual(ident.tag, "model-server")
        self.assertEqual(sup.owned_pids(), [ident.pid])
        self.assertTrue(sup.is_owned(ident.pid))

    def test_duplicate_service_rejected(self):
        """A second instance of a running service must be refused."""
        sup, _, _ = make_supervisor()
        sup.start("model-server", ["python", "-m", "mlx_lm.server"])
        with self.assertRaises(DuplicateServiceError):
            sup.start("model-server", ["python", "-m", "mlx_lm.server"])

    def test_pid_reuse_protection(self):
        """Case 19: refuse to signal a PID that no longer matches its identity."""
        sup, adapter, _ = make_supervisor()
        ident = sup.start("model-server", ["python", "-m", "mlx_lm.server"])

        # The OS hands this PID to an unrelated process.
        adapter.simulate_pid_reuse(ident.pid, "some-other-process --dangerous")

        with self.assertRaises(OwnershipMismatchError):
            sup._verify_ownership(ident)

        before = len(adapter.signals)
        result = sup.stop(ident)
        self.assertEqual(result, Outcome.PROCESS_FAILED)
        # Critically: no signal was delivered to the reused PID.
        self.assertEqual(len(adapter.signals), before)

    def test_dead_process_is_not_signalled(self):
        sup, adapter, _ = make_supervisor()
        ident = sup.start("svc", ["python", "-c", "pass"])
        adapter.simulate_exit(ident.pid, 0)
        with self.assertRaises(OwnershipMismatchError):
            sup._verify_ownership(ident)

    def test_fingerprint_is_stable(self):
        self.assertEqual(fingerprint_cmdline("abc"), fingerprint_cmdline("abc"))
        self.assertNotEqual(fingerprint_cmdline("abc"), fingerprint_cmdline("abd"))


class BindPolicyTests(unittest.TestCase):
    def test_loopback_bind_allowed(self):
        ProcessSupervisor.check_bind_policy(["--host", "127.0.0.1", "--port", "8082"])

    def test_non_loopback_bind_rejected(self):
        """Local services must never be exposed to the LAN."""
        for argv in (
            ["--host", "0.0.0.0"],
            ["--host=0.0.0.0"],
            ["--bind", "192.168.1.10"],
            ["--listen=::"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(BindPolicyError):
                    ProcessSupervisor.check_bind_policy(argv)

    def test_start_refuses_non_loopback(self):
        sup, adapter, _ = make_supervisor()
        with self.assertRaises(BindPolicyError):
            sup.start("svc", ["python", "-m", "http.server", "--bind", "0.0.0.0"])
        self.assertEqual(sup.owned_pids(), [])


class LifecycleTests(unittest.TestCase):
    def test_cancel_owned_process(self):
        """Case 14: cancellation terminates and releases ownership."""
        sup, adapter, _ = make_supervisor()
        ident = sup.start("svc", ["python", "-c", "pass"])
        result = sup.cancel("svc")
        self.assertEqual(result, Outcome.CANCELLED)
        self.assertEqual(sup.owned_pids(), [])
        self.assertTrue(any(pid == ident.pid and sig == signal.SIGTERM
                            for pid, sig, _ in adapter.signals))

    def test_timeout_escalates_to_sigkill(self):
        """Case 13: a process that ignores SIGTERM is killed after the grace period."""
        sup, adapter, clock = make_supervisor(graceful=5.0)
        ident = sup.start("stubborn", ["python", "-c", "pass"])
        adapter.processes[ident.pid]["exit_after_signal"] = False  # ignores SIGTERM

        def advance(seconds):
            clock.t += seconds

        # Drive the grace period forward by patching the supervisor clock loop.
        original = sup._clock
        ticks = iter([0.0, 1.0, 2.0, 6.0, 6.0, 6.0, 6.0, 6.0])
        sup._clock = lambda: next(ticks, 99.0)
        try:
            result = sup.stop(ident)
        finally:
            sup._clock = original

        self.assertEqual(result, Outcome.CANCELLED)
        sigs = [s for _, s, _ in adapter.signals]
        self.assertIn(signal.SIGTERM, sigs)
        self.assertIn(signal.SIGKILL, sigs)

    def test_unexpected_process_exit(self):
        """Case 16: an unexpected exit is observable and releases ownership."""
        sup, adapter, _ = make_supervisor()
        ident = sup.start("svc", ["python", "-c", "pass"])
        adapter.simulate_exit(ident.pid, 137)  # e.g. SIGKILL from the OS
        self.assertEqual(adapter.poll(ident.pid), 137)
        result = sup.stop(ident)
        self.assertEqual(result, Outcome.PROCESS_FAILED)
        self.assertEqual(sup.owned_pids(), [])

    def test_owned_process_cleanup(self):
        """Case 18: cleanup terminates every owned process and nothing else."""
        sup, adapter, _ = make_supervisor()
        a = sup.start("svc-a", ["python", "-c", "a"])
        b = sup.start("svc-b", ["python", "-c", "b"])
        unowned_pid = 99999

        results = sup.cleanup_all()
        self.assertEqual(set(results), {"svc-a", "svc-b"})
        self.assertEqual(sup.owned_pids(), [])
        signalled = {pid for pid, _, _ in adapter.signals}
        self.assertEqual(signalled, {a.pid, b.pid})
        self.assertNotIn(unowned_pid, signalled)

    def test_cancel_unknown_tag_is_process_failed(self):
        sup, _, _ = make_supervisor()
        self.assertEqual(sup.cancel("never-started"), Outcome.PROCESS_FAILED)

    def test_only_owned_pids_are_ever_signalled(self):
        sup, adapter, _ = make_supervisor()
        sup.start("svc", ["python", "-c", "pass"])
        sup.cleanup_all()
        sup.cleanup_all()  # idempotent
        self.assertEqual(len({p for p, _, _ in adapter.signals}), 1)


@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "POSIX only")
class RealProcessTests(unittest.TestCase):
    """One deliberately trivial real process. `sleep` costs ~1 MB and 0% CPU."""

    def test_real_short_process_is_started_and_cancelled(self):
        sup = ProcessSupervisor(SubprocessAdapter(), graceful_timeout_s=3.0)
        ident = sup.start("tiny-sleeper", ["sleep", "60"])
        try:
            self.assertGreater(ident.pid, 0)
            self.assertIn(ident.pid, sup.owned_pids())
            result = sup.stop(ident)
            self.assertEqual(result, Outcome.CANCELLED)
            self.assertEqual(sup.owned_pids(), [])
        finally:
            sup.cleanup_all()

    def test_real_bind_policy_enforced_before_spawn(self):
        sup = ProcessSupervisor(SubprocessAdapter(), graceful_timeout_s=1.0)
        with self.assertRaises(BindPolicyError):
            sup.start("bad", ["sleep", "60", "--host", "0.0.0.0"])
        self.assertEqual(sup.owned_pids(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
