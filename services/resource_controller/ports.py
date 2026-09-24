"""Port availability probing.

Why ``lsof`` alone is not enough
--------------------------------
During the FORGE-003 incident, port 8081 appeared **free** to ``lsof`` but a
``bind()`` failed with ``EADDRINUSE``. ``netstat`` showed ``tcp46 *.8081 LISTEN``
— a socket owned by a user ``lsof`` could not enumerate without root. The
controller had reported "port available" on the strength of a false negative.

The probe below therefore requires **both** an independent ``netstat`` look and
a real ``bind()`` attempt, and treats the port as occupied if either disagrees.
"""

from __future__ import annotations

import abc
import socket
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class PortStatus:
    host: str
    port: int
    free: bool
    netstat_listening: bool
    bind_ok: bool
    detail: str

    @property
    def occupied(self) -> bool:
        return not self.free


class PortProbe(abc.ABC):
    """Abstract port probe so protected startup is unit-testable."""

    @abc.abstractmethod
    def check(self, host: str, port: int) -> PortStatus:
        raise NotImplementedError


class RealPortProbe(PortProbe):
    """netstat + bind(). Conservative: occupied if either signal says so."""

    def __init__(self, runner=subprocess.run) -> None:
        self._run = runner

    def _netstat_listening(self, port: int) -> Optional[bool]:
        try:
            out = self._run(
                ["netstat", "-an", "-p", "tcp"], capture_output=True, text=True, timeout=10
            ).stdout
        except Exception:
            return None  # unknown -> handled by caller as "cannot verify"
        needle = f".{port} "
        for line in out.splitlines():
            if needle in line and "LISTEN" in line.upper():
                return True
        return False

    def _bind_ok(self, host: str, port: int) -> bool:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen(1)
            return True
        except OSError:
            return False
        finally:
            s.close()

    def check(self, host: str, port: int) -> PortStatus:
        listening = self._netstat_listening(port)
        bind_ok = self._bind_ok(host, port)

        if listening is None:
            # Cannot verify independently -> fail closed rather than assume free.
            return PortStatus(
                host, port, False, False, bind_ok,
                "netstat unavailable; cannot verify port is free (fail closed)",
            )

        free = (not listening) and bind_ok
        if free:
            detail = "netstat shows no listener and bind() succeeded"
        elif listening and not bind_ok:
            detail = "port is in use (netstat LISTEN and bind() failed)"
        elif listening:
            detail = "netstat reports a LISTEN socket (possibly another user's)"
        else:
            detail = "bind() failed though netstat showed no listener"
        return PortStatus(host, port, free, listening, bind_ok, detail)


class StaticPortProbe(PortProbe):
    """Scripted probe for tests."""

    def __init__(self, free: bool = True, detail: str = "static probe") -> None:
        self._free = free
        self._detail = detail
        self.calls: list = []

    def check(self, host: str, port: int) -> PortStatus:
        self.calls.append((host, port))
        return PortStatus(host, port, self._free, not self._free, self._free, self._detail)


class PortUnavailable(RuntimeError):
    """Raised when a requested port is not usable."""


def require_free_port(probe: PortProbe, host: str, port: int) -> PortStatus:
    """Fail closed unless the port is demonstrably free."""
    status = probe.check(host, port)
    if status.occupied:
        raise PortUnavailable(
            f"port {port} on {host} is not available: {status.detail}"
        )
    return status
