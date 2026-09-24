"""Independent watchdog.

Runs as a **separate lightweight process** so that it survives the workload it
supervises. Its job is to notice host degradation and abort the workload before
the machine becomes unusable.

Two honest constraints are designed in, not glossed over:

1. **Best-effort, not a guarantee.** A watchdog cannot prevent an OS-level
   stall caused by an allocation that completes faster than the sampling
   interval. It reduces the window; it does not eliminate it.
2. **Never RSS alone.** Resident size of the workload says nothing about
   system-wide pressure. The verdict combines available memory, swap usage,
   page-out rate, owned-process footprint and wall-clock time.

Telemetry is written **unbuffered and flushed per sample**, so an aborted run
still yields every measurement taken before the abort. The FORGE-003 incident
produced zero data precisely because output was buffered.
"""

from __future__ import annotations

import abc
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .telemetry import MemorySnapshot, TelemetrySource, TelemetryUnavailable


@dataclass(frozen=True)
class WatchdogThresholds:
    min_available_bytes: int
    max_swap_used_bytes: int
    max_pageout_rate: float
    sample_interval_s: float = 0.25
    max_owned_rss_bytes: Optional[int] = None
    max_wall_clock_s: Optional[float] = None

    def __post_init__(self) -> None:
        if self.min_available_bytes <= 0:
            raise ValueError("min_available_bytes must be > 0")
        if self.max_swap_used_bytes < 0:
            raise ValueError("max_swap_used_bytes must be >= 0")
        if self.max_pageout_rate < 0:
            raise ValueError("max_pageout_rate must be >= 0")
        if self.sample_interval_s <= 0:
            raise ValueError("sample_interval_s must be > 0")


@dataclass(frozen=True)
class WatchdogVerdict:
    abort: bool
    reasons: tuple

    def as_dict(self) -> dict:
        return {"abort": self.abort, "reasons": list(self.reasons)}


def evaluate(
    snapshot: MemorySnapshot,
    previous: Optional[MemorySnapshot],
    thresholds: WatchdogThresholds,
    *,
    elapsed_s: float = 0.0,
    owned_rss_bytes: Optional[int] = None,
) -> WatchdogVerdict:
    """Pure decision function. No I/O, fully unit-testable."""
    reasons: List[str] = []

    if snapshot.available_bytes < thresholds.min_available_bytes:
        reasons.append(
            f"available memory {snapshot.available_bytes} below floor "
            f"{thresholds.min_available_bytes}"
        )

    if snapshot.swap_used_bytes > thresholds.max_swap_used_bytes:
        reasons.append(
            f"swap used {snapshot.swap_used_bytes} exceeds limit "
            f"{thresholds.max_swap_used_bytes}"
        )

    rate = snapshot.pageout_rate(previous)
    if rate > thresholds.max_pageout_rate:
        reasons.append(f"pageout rate {rate:.1f}/s exceeds limit {thresholds.max_pageout_rate}")

    if (
        thresholds.max_owned_rss_bytes is not None
        and owned_rss_bytes is not None
        and owned_rss_bytes > thresholds.max_owned_rss_bytes
    ):
        reasons.append(
            f"owned process RSS {owned_rss_bytes} exceeds limit "
            f"{thresholds.max_owned_rss_bytes}"
        )

    if thresholds.max_wall_clock_s is not None and elapsed_s > thresholds.max_wall_clock_s:
        reasons.append(f"wall clock {elapsed_s:.1f}s exceeds limit {thresholds.max_wall_clock_s}")

    return WatchdogVerdict(abort=bool(reasons), reasons=tuple(reasons))


class TelemetryWriter(abc.ABC):
    """Persists telemetry records."""

    @abc.abstractmethod
    def write(self, record: dict) -> None: ...


class JsonlTelemetryWriter(TelemetryWriter):
    """Append-only JSONL with an explicit flush on every record."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # pragma: no cover
            pass


class MemoryTelemetryWriter(TelemetryWriter):
    """In-memory writer for tests. Proves records survive a simulated abort."""

    def __init__(self) -> None:
        self.records: List[dict] = []

    def write(self, record: dict) -> None:
        self.records.append(dict(record))


class Watchdog:
    """Samples telemetry, persists it, and returns an abort verdict."""

    def __init__(
        self,
        telemetry: TelemetrySource,
        thresholds: WatchdogThresholds,
        writer: TelemetryWriter,
        *,
        owned_rss_probe: Optional[Callable[[], Optional[int]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._telemetry = telemetry
        self._thresholds = thresholds
        self._writer = writer
        self._rss_probe = owned_rss_probe
        self._clock = clock
        self._sleep = sleeper
        self.last_verdict: Optional[WatchdogVerdict] = None

    def run(
        self,
        *,
        max_samples: Optional[int] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> WatchdogVerdict:
        started = self._clock()
        previous: Optional[MemorySnapshot] = None
        samples = 0
        verdict = WatchdogVerdict(abort=False, reasons=())

        while True:
            try:
                snap = self._telemetry.snapshot()
            except TelemetryUnavailable as exc:
                # Telemetry loss is itself an abort condition: never fly blind.
                verdict = WatchdogVerdict(abort=True, reasons=(f"telemetry unavailable: {exc}",))
                self._writer.write({"event": "telemetry_unavailable", "error": str(exc)})
                break

            rss = self._rss_probe() if self._rss_probe else None
            elapsed = self._clock() - started
            verdict = evaluate(
                snap, previous, self._thresholds, elapsed_s=elapsed, owned_rss_bytes=rss
            )

            self._writer.write(
                {
                    "event": "sample",
                    "n": samples,
                    "elapsed_s": round(elapsed, 3),
                    "available_bytes": snap.available_bytes,
                    "free_bytes": snap.free_bytes,
                    "swap_used_bytes": snap.swap_used_bytes,
                    "pageouts": snap.pageouts,
                    "pageout_rate": round(snap.pageout_rate(previous), 3),
                    "owned_rss_bytes": rss,
                    "abort": verdict.abort,
                    "reasons": list(verdict.reasons),
                }
            )

            previous = snap
            samples += 1

            if verdict.abort:
                self._writer.write({"event": "abort", "reasons": list(verdict.reasons)})
                break
            if max_samples is not None and samples >= max_samples:
                break
            if should_stop is not None and should_stop():
                break

            self._sleep(self._thresholds.sample_interval_s)

        self.last_verdict = verdict
        return verdict


# ---------------------------------------------------------------------------
# Standalone entry point: run the watchdog as its own process.
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    from .telemetry import MacOSTelemetrySource

    parser = argparse.ArgumentParser(
        prog="python -m services.resource_controller.watchdog",
        description="ForgeOne Resource Controller watchdog (standalone process).",
    )
    parser.add_argument("--telemetry", required=True, help="JSONL output path")
    parser.add_argument("--min-available-gb", type=float, required=True)
    parser.add_argument("--max-swap-gb", type=float, required=True)
    parser.add_argument("--max-pageout-rate", type=float, default=5000.0)
    parser.add_argument("--max-wall-clock-s", type=float, default=None)
    parser.add_argument("--sample-interval-s", type=float, default=0.25)
    parser.add_argument("--owned-pid", type=int, default=None)
    parser.add_argument(
        "--abort-command",
        default=None,
        help="optional shell-free argv prefix to run on abort (e.g. kill signals)",
    )
    args = parser.parse_args(argv)

    gb = 1024**3

    def rss_probe() -> Optional[int]:
        if args.owned_pid is None:
            return None
        try:
            import subprocess

            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(args.owned_pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return int(out) * 1024 if out else None
        except Exception:
            return None

    thresholds = WatchdogThresholds(
        min_available_bytes=int(args.min_available_gb * gb),
        max_swap_used_bytes=int(args.max_swap_gb * gb),
        max_pageout_rate=args.max_pageout_rate,
        sample_interval_s=args.sample_interval_s,
        max_wall_clock_s=args.max_wall_clock_s,
    )

    writer = JsonlTelemetryWriter(args.telemetry)
    watchdog = Watchdog(
        MacOSTelemetrySource(), thresholds, writer, owned_rss_probe=rss_probe
    )
    try:
        verdict = watchdog.run()
    finally:
        writer.close()

    if verdict.abort:
        print(f"ABORTED_MEMORY_PRESSURE: {'; '.join(verdict.reasons)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
