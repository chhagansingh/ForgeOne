"""Atomic single-request reservation -- closing the check-then-act race."""

from __future__ import annotations

import threading
import unittest

from services.resource_controller.outcomes import Outcome
from services.resource_controller.reservation import (
    ConcurrencyExceeded,
    ReservationManager,
)


class ReservationTests(unittest.TestCase):
    def test_acquire_and_release(self):
        m = ReservationManager()
        r = m.acquire("first")
        self.assertEqual(m.active_count, 1)
        self.assertTrue(m.release(r))
        self.assertEqual(m.active_count, 0)

    def test_second_acquire_is_refused(self):
        """Case 11: only one concurrent request is permitted."""
        m = ReservationManager()
        m.acquire("first")
        with self.assertRaises(ConcurrencyExceeded):
            m.acquire("second")
        self.assertEqual(m.active_count, 1)

    def test_try_acquire_reports_rejected_concurrency(self):
        m = ReservationManager()
        m.acquire("first")
        res, outcome, reason = m.try_acquire("second")
        self.assertIsNone(res)
        self.assertEqual(outcome, Outcome.REJECTED_CONCURRENCY)
        self.assertIn("reservation", reason)

    def test_release_is_idempotent(self):
        m = ReservationManager()
        r = m.acquire()
        self.assertTrue(m.release(r))
        self.assertFalse(m.release(r))  # second release is a no-op
        self.assertEqual(m.active_count, 0)

    def test_release_none_is_safe(self):
        self.assertFalse(ReservationManager().release(None))

    def test_context_manager_releases_on_exception(self):
        """Case 16: the slot is released even when the body raises."""
        m = ReservationManager()
        with self.assertRaises(RuntimeError):
            with m.acquire("boom"):
                self.assertEqual(m.active_count, 1)
                raise RuntimeError("simulated failure")
        self.assertEqual(m.active_count, 0)

    def test_release_all(self):
        m = ReservationManager()
        m.acquire("a")
        self.assertEqual(m.release_all(), 1)
        self.assertEqual(m.active_count, 0)
        m.acquire("b")  # usable afterwards

    def test_races_are_atomic(self):
        """Case 11: two racing threads -- exactly one may win."""
        m = ReservationManager()
        barrier = threading.Barrier(8)
        wins = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            try:
                m.acquire("racer")
                with lock:
                    wins.append(1)
            except ConcurrencyExceeded:
                pass

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(wins), 1, "exactly one racer may hold the slot")
        self.assertEqual(m.active_count, 1)

    def test_max_concurrent_must_be_one(self):
        for bad in (0, 2, 4):
            with self.subTest(max_concurrent=bad):
                with self.assertRaises(ValueError):
                    ReservationManager(max_concurrent=bad)

    def test_active_labels(self):
        m = ReservationManager()
        m.acquire("alpha")
        self.assertEqual(m.active_labels, ["alpha"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
