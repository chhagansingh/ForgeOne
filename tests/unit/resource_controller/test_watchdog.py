"""Watchdog thresholds, abort behaviour and telemetry durability."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from services.resource_controller.telemetry import (
    SyntheticTelemetrySource,
    UnavailableTelemetrySource,
    make_snapshot,
)
from services.resource_controller.watchdog import (
    JsonlTelemetryWriter,
    MemoryTelemetryWriter,
    Watchdog,
    WatchdogThresholds,
    evaluate,
)

from .support import GB, healthy_snapshot, low_memory_snapshot


def thresholds(**kw):
    base = dict(
        min_available_bytes=6 * GB,
        max_swap_used_bytes=2 * GB,
        max_pageout_rate=5000.0,
        sample_interval_s=0.25,
    )
    base.update(kw)
    return WatchdogThresholds(**base)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class EvaluateTests(unittest.TestCase):
    def test_healthy_telemetry_does_not_abort(self):
        verdict = evaluate(healthy_snapshot(), None, thresholds())
        self.assertFalse(verdict.abort)
        self.assertEqual(verdict.reasons, ())

    def test_critical_memory_pressure_aborts(self):
        """Case 7: available memory below the floor."""
        verdict = evaluate(low_memory_snapshot(), None, thresholds())
        self.assertTrue(verdict.abort)
        self.assertTrue(any("below floor" in r for r in verdict.reasons))

    def test_swap_threshold_aborts(self):
        """Case 8: swap above the limit."""
        snap = make_snapshot(swap_used_gb=5.0, pageouts=1000, timestamp=0.0)
        verdict = evaluate(snap, None, thresholds())
        self.assertTrue(verdict.abort)
        self.assertTrue(any("swap used" in r for r in verdict.reasons))

    def test_rapid_swap_growth_via_pageout_rate_aborts(self):
        """Case 8/15: page-out rate above the limit."""
        prev = make_snapshot(pageouts=1000, timestamp=0.0)
        now = make_snapshot(pageouts=20000, timestamp=1.0)
        self.assertAlmostEqual(now.pageout_rate(prev), 19000.0)
        verdict = evaluate(now, prev, thresholds())
        self.assertTrue(verdict.abort)
        self.assertTrue(any("pageout rate" in r for r in verdict.reasons))

    def test_pageout_rate_zero_without_baseline(self):
        snap = make_snapshot(pageouts=999999, timestamp=5.0)
        self.assertEqual(snap.pageout_rate(None), 0.0)

    def test_owned_rss_threshold_aborts(self):
        """Case 15: owned-process footprint above its own ceiling."""
        verdict = evaluate(
            healthy_snapshot(), None, thresholds(max_owned_rss_bytes=2 * GB),
            owned_rss_bytes=9 * GB,
        )
        self.assertTrue(verdict.abort)
        self.assertTrue(any("owned process RSS" in r for r in verdict.reasons))

    def test_rss_is_not_the_only_signal(self):
        """A small owned RSS must still abort when the host is under pressure."""
        verdict = evaluate(
            low_memory_snapshot(), None, thresholds(max_owned_rss_bytes=99 * GB),
            owned_rss_bytes=1024,
        )
        self.assertTrue(verdict.abort)

    def test_wall_clock_timeout_aborts(self):
        """Case 13/15: wall-clock ceiling."""
        verdict = evaluate(
            healthy_snapshot(), None, thresholds(max_wall_clock_s=10.0), elapsed_s=99.0
        )
        self.assertTrue(verdict.abort)
        self.assertTrue(any("wall clock" in r for r in verdict.reasons))


class WatchdogRunTests(unittest.TestCase):
    def test_aborts_and_persists_telemetry(self):
        """Case 20: telemetry survives a simulated failure."""
        clock = FakeClock()
        writer = MemoryTelemetryWriter()
        source = SyntheticTelemetrySource(
            [healthy_snapshot(), healthy_snapshot(), low_memory_snapshot()]
        )
        wd = Watchdog(source, thresholds(), writer, clock=clock, sleeper=clock.sleep)
        verdict = wd.run(max_samples=10)

        self.assertTrue(verdict.abort)
        samples = [r for r in writer.records if r["event"] == "sample"]
        self.assertEqual(len(samples), 3)
        self.assertEqual([r["abort"] for r in samples], [False, False, True])
        self.assertEqual(writer.records[-1]["event"], "abort")

    def test_telemetry_unavailable_aborts(self):
        clock = FakeClock()
        writer = MemoryTelemetryWriter()
        wd = Watchdog(
            UnavailableTelemetrySource(), thresholds(), writer, clock=clock, sleeper=clock.sleep
        )
        verdict = wd.run(max_samples=5)
        self.assertTrue(verdict.abort)
        self.assertIn("telemetry unavailable", verdict.reasons[0])

    def test_stops_after_max_samples_when_healthy(self):
        clock = FakeClock()
        writer = MemoryTelemetryWriter()
        source = SyntheticTelemetrySource([healthy_snapshot()])
        wd = Watchdog(source, thresholds(), writer, clock=clock, sleeper=clock.sleep)
        verdict = wd.run(max_samples=4)
        self.assertFalse(verdict.abort)
        self.assertEqual(len([r for r in writer.records if r["event"] == "sample"]), 4)

    def test_jsonl_writer_flushes_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "telemetry.jsonl")
            writer = JsonlTelemetryWriter(path)
            writer.write({"event": "sample", "n": 1})
            writer.write({"event": "abort", "reasons": ["test"]})
            # Read before close(): proves each record was flushed, not buffered.
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(x) for x in fh if x.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[1]["event"], "abort")
            writer.close()

    def test_thresholds_validation(self):
        with self.assertRaises(ValueError):
            WatchdogThresholds(min_available_bytes=0, max_swap_used_bytes=1, max_pageout_rate=1)
        with self.assertRaises(ValueError):
            WatchdogThresholds(
                min_available_bytes=1, max_swap_used_bytes=1, max_pageout_rate=1,
                sample_interval_s=0,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
