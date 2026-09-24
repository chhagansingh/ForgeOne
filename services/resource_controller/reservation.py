"""Atomic single-request reservation.

Why this exists
---------------
Admission previously read ``AdmissionRequest.active_requests``, a value supplied
by the caller (``admission.py:33``). Two concurrent callers could both observe
``active_requests=0`` and both be admitted — a time-of-check/time-of-use race.
``max_concurrent_requests`` is fixed at 1, so the race would admit two requests
where the policy permits one.

The manager below makes check-and-reserve a single atomic operation under a
lock, and guarantees release on every exit path (including exceptions) via the
:class:`Reservation` context manager.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .outcomes import Outcome


class ConcurrencyExceeded(RuntimeError):
    """Raised when a reservation is requested while one is already held."""


@dataclass
class Reservation:
    """A held slot.

    Releasing twice is safe and reports the first result. The context-manager
    exit releases the **manager slot**, not merely the local flag -- releasing
    only the flag would leak the slot and permanently block further requests.
    """

    token: str
    label: str
    _manager: Optional["ReservationManager"] = field(default=None, repr=False)
    _released: bool = field(default=False, repr=False)

    @property
    def released(self) -> bool:
        return self._released

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._manager is not None:
            self._manager.release(self)
        else:
            self.release()
        return False

    def release(self) -> bool:
        """Mark released. Returns True only the first time."""
        if self._released:
            return False
        self._released = True
        return True


class ReservationManager:
    """Holds at most ``max_concurrent`` reservations, atomically."""

    def __init__(self, max_concurrent: int = 1) -> None:
        if max_concurrent != 1:
            raise ValueError(
                "local inference supports exactly one concurrent request; "
                f"got max_concurrent={max_concurrent}"
            )
        self._max = max_concurrent
        self._lock = threading.Lock()
        self._held: dict = {}

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._held)

    @property
    def active_labels(self) -> list:
        with self._lock:
            return [r.label for r in self._held.values()]

    def acquire(self, label: str = "request") -> Reservation:
        """Atomically claim the single slot, or raise."""
        with self._lock:
            if len(self._held) >= self._max:
                held = ", ".join(r.label for r in self._held.values())
                raise ConcurrencyExceeded(
                    f"{len(self._held)} reservation(s) already held ({held}); "
                    f"max_concurrent={self._max}"
                )
            res = Reservation(token=uuid.uuid4().hex[:12], label=label, _manager=self)
            self._held[res.token] = res
            return res

    def active_count_excluding(self, reservation: Optional[Reservation]) -> int:
        """Concurrency as seen by a caller that already holds ``reservation``.

        A request that holds the slot must not count *itself* against the
        concurrency limit, otherwise the first request would always be refused.
        """
        with self._lock:
            if reservation is None:
                return len(self._held)
            return sum(1 for t in self._held if t != reservation.token)

    def try_acquire(self, label: str = "request") -> tuple:
        """Non-raising variant: ``(reservation | None, outcome, reason)``."""
        try:
            return self.acquire(label), Outcome.ADMITTED, "reserved"
        except ConcurrencyExceeded as exc:
            return None, Outcome.REJECTED_CONCURRENCY, str(exc)

    def release(self, reservation: Optional[Reservation]) -> bool:
        """Release a reservation. Idempotent; safe with None."""
        if reservation is None:
            return False
        with self._lock:
            removed = self._held.pop(reservation.token, None)
        first = reservation.release()
        return removed is not None and first

    def release_all(self) -> int:
        """Emergency release of every slot. Returns how many were held."""
        with self._lock:
            held = list(self._held.values())
            self._held.clear()
        for r in held:
            r.release()
        return len(held)
