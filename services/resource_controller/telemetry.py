"""System memory telemetry.

Design note (learned from the FORGE-003 incident):
    ``vm_stat`` "Pages free" is a *misleading* signal on macOS. A healthy host
    routinely reports a few hundred MB free while holding many GB in inactive
    and speculative pages that are instantly reclaimable. The controller must
    therefore use **available = free + inactive + speculative**, never free
    alone. Using free alone would have rejected safe workloads; using nothing
    at all is what caused the incident.
"""

from __future__ import annotations

import abc
import re
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

MACOS_PAGE_SIZE_FALLBACK = 16384


@dataclass(frozen=True)
class MemorySnapshot:
    """One point-in-time view of host memory."""

    total_bytes: int
    free_bytes: int
    inactive_bytes: int
    speculative_bytes: int
    swap_used_bytes: int
    pageouts: int
    timestamp: float

    @property
    def available_bytes(self) -> int:
        """Reclaimable memory: free + inactive + speculative."""
        return self.free_bytes + self.inactive_bytes + self.speculative_bytes

    def pageout_rate(self, previous: Optional["MemorySnapshot"]) -> float:
        """Pages per second since ``previous``. 0.0 when there is no baseline."""
        if previous is None:
            return 0.0
        elapsed = self.timestamp - previous.timestamp
        if elapsed <= 0:
            return 0.0
        delta = max(0, self.pageouts - previous.pageouts)
        return delta / elapsed


class TelemetryUnavailable(RuntimeError):
    """Raised when host memory cannot be read. Callers must fail closed."""


class TelemetrySource(abc.ABC):
    """Abstract memory telemetry. Injected so admission logic is unit-testable."""

    @abc.abstractmethod
    def snapshot(self) -> MemorySnapshot:
        raise NotImplementedError


class MacOSTelemetrySource(TelemetrySource):
    """Real telemetry via ``sysctl`` and ``vm_stat``. No model is ever loaded."""

    def __init__(self, runner=subprocess.run, clock=time.monotonic) -> None:
        self._run = runner
        self._clock = clock

    def _sysctl(self, key: str) -> str:
        out = self._run(["sysctl", "-n", key], capture_output=True, text=True, timeout=10)
        return out.stdout.strip()

    def snapshot(self) -> MemorySnapshot:
        try:
            total = int(self._sysctl("hw.memsize"))
            page_size = int(self._sysctl("hw.pagesize") or MACOS_PAGE_SIZE_FALLBACK)
            vm = self._run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
        except Exception as exc:  # pragma: no cover - depends on host
            raise TelemetryUnavailable(f"cannot read host memory: {exc}") from exc

        def pages(label: str) -> int:
            m = re.search(rf"{label}:\s+(\d+)", vm)
            return int(m.group(1)) if m else 0

        swap_used = 0
        try:
            swap = self._sysctl("vm.swapusage")
            m = re.search(r"used\s*=\s*([\d.]+)M", swap)
            if m:
                swap_used = int(float(m.group(1)) * 1024 * 1024)
        except Exception:  # pragma: no cover - swap is best-effort
            swap_used = 0

        return MemorySnapshot(
            total_bytes=total,
            free_bytes=pages("Pages free") * page_size,
            inactive_bytes=pages("Pages inactive") * page_size,
            speculative_bytes=pages("Pages speculative") * page_size,
            swap_used_bytes=swap_used,
            pageouts=pages("Pageouts"),
            timestamp=self._clock(),
        )


class SyntheticTelemetrySource(TelemetrySource):
    """Scripted telemetry for tests. Yields snapshots in order, repeating the last."""

    def __init__(self, snapshots: List[MemorySnapshot]) -> None:
        if not snapshots:
            raise ValueError("SyntheticTelemetrySource requires at least one snapshot")
        self._snapshots = list(snapshots)
        self._index = 0
        self.calls = 0

    def snapshot(self) -> MemorySnapshot:
        self.calls += 1
        snap = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return snap


class UnavailableTelemetrySource(TelemetrySource):
    """Always fails. Used to prove the controller fails closed."""

    def snapshot(self) -> MemorySnapshot:
        raise TelemetryUnavailable("telemetry deliberately unavailable")


def make_snapshot(
    *,
    total_gb: float = 24.0,
    free_gb: float = 0.25,
    inactive_gb: float = 8.6,
    speculative_gb: float = 0.85,
    swap_used_gb: float = 0.0,
    pageouts: int = 0,
    timestamp: float = 0.0,
) -> MemorySnapshot:
    """Convenience constructor for tests and examples."""
    gb = 1024**3
    return MemorySnapshot(
        total_bytes=int(total_gb * gb),
        free_bytes=int(free_gb * gb),
        inactive_bytes=int(inactive_gb * gb),
        speculative_bytes=int(speculative_gb * gb),
        swap_used_bytes=int(swap_used_gb * gb),
        pageouts=pageouts,
        timestamp=timestamp,
    )
