"""Process supervision for ForgeOne-owned workloads.

Guarantees:

* **Only ForgeOne-owned processes are ever signalled.** Ownership is recorded at
  spawn time and re-verified immediately before any signal.
* **PID reuse cannot cause a stray kill.** An identity is
  ``(pid, start_marker, cmdline_fingerprint)``; if the live process does not
  match the recorded identity, the supervisor refuses to signal it.
* **Duplicate service instances are refused.** Starting a tag that is already
  running raises, rather than silently stacking a second model server.
* **Local services bind to loopback.** A non-loopback ``--host``/``--bind`` in
  the command line is rejected before spawn.
* **Shutdown is bounded.** SIGTERM to the process group, then SIGKILL after a
  grace period. No unbounded wait.
"""

from __future__ import annotations

import abc
import hashlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .outcomes import Outcome

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_HOST_FLAGS = ("--host", "--bind", "--listen", "--hostname")


class SupervisorError(RuntimeError):
    pass


class DuplicateServiceError(SupervisorError):
    pass


class OwnershipMismatchError(SupervisorError):
    """Raised when a PID no longer matches its recorded identity (PID reuse)."""


class BindPolicyError(SupervisorError):
    """Raised when a command line would bind a local service to a non-loopback host."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    start_marker: str
    fingerprint: str
    tag: str


def fingerprint_cmdline(cmdline: str) -> str:
    return hashlib.sha256(cmdline.encode("utf-8", "replace")).hexdigest()[:16]


class ProcessAdapter(abc.ABC):
    """Abstract process operations, injected so supervision is unit-testable."""

    @abc.abstractmethod
    def spawn(self, argv: Sequence[str], env: Optional[dict] = None, cwd: Optional[str] = None):
        """Return ``(pid, pgid)``."""

    @abc.abstractmethod
    def describe(self, pid: int) -> Optional[tuple]:
        """Return ``(pgid, start_marker, cmdline)`` or None if the PID is gone."""

    @abc.abstractmethod
    def signal(self, pid: int, sig: int, group: bool = False) -> None: ...

    @abc.abstractmethod
    def poll(self, pid: int) -> Optional[int]:
        """Return exit code, or None if still running."""

    @abc.abstractmethod
    def wait(self, pid: int, timeout: float) -> Optional[int]:
        """Block up to ``timeout``. Return exit code or None if still running."""


class SubprocessAdapter(ProcessAdapter):
    """Real adapter. Spawns into its own process group."""

    def __init__(self) -> None:
        self._procs: Dict[int, subprocess.Popen] = {}

    def spawn(self, argv: Sequence[str], env: Optional[dict] = None, cwd: Optional[str] = None):
        proc = subprocess.Popen(
            list(argv),
            env=env,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # own process group -> bounded group shutdown
        )
        self._procs[proc.pid] = proc
        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = proc.pid
        return proc.pid, pgid

    def describe(self, pid: int) -> Optional[tuple]:
        try:
            out = subprocess.run(
                ["ps", "-o", "pgid=,lstart=,command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except Exception:
            return None
        if not out:
            return None
        parts = out.split(None, 6)
        if len(parts) < 7:
            return None
        try:
            pgid = int(parts[0])
        except ValueError:
            return None
        start_marker = " ".join(parts[1:6])
        cmdline = parts[6]
        return pgid, start_marker, cmdline

    def signal(self, pid: int, sig: int, group: bool = False) -> None:
        if group:
            try:
                os.killpg(os.getpgid(pid), sig)
                return
            except OSError:
                pass
        os.kill(pid, sig)

    def poll(self, pid: int) -> Optional[int]:
        proc = self._procs.get(pid)
        if proc is None:
            return None
        return proc.poll()

    def wait(self, pid: int, timeout: float) -> Optional[int]:
        proc = self._procs.get(pid)
        if proc is None:
            return None
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


class FakeProcessAdapter(ProcessAdapter):
    """Deterministic adapter for tests. Spawns nothing real."""

    def __init__(self) -> None:
        self.processes: Dict[int, dict] = {}
        self.signals: List[tuple] = []
        self._next_pid = 40000

    def spawn(self, argv: Sequence[str], env: Optional[dict] = None, cwd: Optional[str] = None):
        pid = self._next_pid
        self._next_pid += 1
        cmdline = " ".join(argv)
        self.processes[pid] = {
            "pgid": pid,
            "start_marker": f"fake-start-{pid}",
            "cmdline": cmdline,
            "alive": True,
            "exit_code": None,
            "exit_after_signal": True,
        }
        return pid, pid

    def describe(self, pid: int) -> Optional[tuple]:
        p = self.processes.get(pid)
        if p is None or not p["alive"]:
            return None
        return p["pgid"], p["start_marker"], p["cmdline"]

    def signal(self, pid: int, sig: int, group: bool = False) -> None:
        self.signals.append((pid, sig, group))
        p = self.processes.get(pid)
        if p is not None and p.get("exit_after_signal", True):
            p["alive"] = False
            p["exit_code"] = 0 if sig == signal.SIGTERM else -int(sig)

    def poll(self, pid: int) -> Optional[int]:
        p = self.processes.get(pid)
        if p is None:
            return None
        return p["exit_code"] if not p["alive"] else None

    def wait(self, pid: int, timeout: float) -> Optional[int]:
        return self.poll(pid)

    # -- test helpers ----------------------------------------------------
    def simulate_exit(self, pid: int, code: int) -> None:
        p = self.processes[pid]
        p["alive"] = False
        p["exit_code"] = code

    def simulate_pid_reuse(self, pid: int, new_cmdline: str = "some-other-process") -> None:
        """Model the OS handing this PID to an unrelated process."""
        p = self.processes[pid]
        p["start_marker"] = f"reused-{pid}"
        p["cmdline"] = new_cmdline
        p["alive"] = True


class ProcessSupervisor:
    """Owns ForgeOne child processes. Never touches anything it did not start."""

    def __init__(
        self,
        adapter: ProcessAdapter,
        *,
        graceful_timeout_s: float = 5.0,
        clock=time.monotonic,
    ) -> None:
        self._adapter = adapter
        self._graceful = graceful_timeout_s
        self._clock = clock
        self._owned: Dict[str, ProcessIdentity] = {}

    # -- ownership -------------------------------------------------------
    def owned(self) -> Dict[str, ProcessIdentity]:
        return dict(self._owned)

    def owned_pids(self) -> List[int]:
        return [i.pid for i in self._owned.values()]

    def is_owned(self, pid: int) -> bool:
        return any(i.pid == pid for i in self._owned.values())

    def _verify_ownership(self, identity: ProcessIdentity) -> None:
        live = self._adapter.describe(identity.pid)
        if live is None:
            raise OwnershipMismatchError(
                f"pid {identity.pid} is no longer running; refusing to signal"
            )
        pgid, start_marker, cmdline = live
        if start_marker != identity.start_marker or fingerprint_cmdline(cmdline) != identity.fingerprint:
            raise OwnershipMismatchError(
                f"pid {identity.pid} no longer matches its recorded identity "
                f"(possible PID reuse); refusing to signal"
            )

    # -- bind policy -----------------------------------------------------
    @staticmethod
    def check_bind_policy(argv: Sequence[str]) -> None:
        for idx, token in enumerate(argv):
            for flag in _HOST_FLAGS:
                if token == flag and idx + 1 < len(argv):
                    host = argv[idx + 1]
                    if host not in LOOPBACK_HOSTS:
                        raise BindPolicyError(
                            f"refusing to start local service bound to {host!r}; "
                            f"loopback ({', '.join(sorted(LOOPBACK_HOSTS))}) is required"
                        )
                elif token.startswith(flag + "="):
                    host = token.split("=", 1)[1]
                    if host not in LOOPBACK_HOSTS:
                        raise BindPolicyError(
                            f"refusing to start local service bound to {host!r}; "
                            f"loopback is required"
                        )

    # -- lifecycle -------------------------------------------------------
    def start(
        self,
        tag: str,
        argv: Sequence[str],
        *,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
    ) -> ProcessIdentity:
        if tag in self._owned:
            existing = self._owned[tag]
            if self._adapter.describe(existing.pid) is not None:
                raise DuplicateServiceError(
                    f"service {tag!r} is already running as pid {existing.pid}; "
                    f"refusing to start a duplicate instance"
                )
            del self._owned[tag]

        self.check_bind_policy(argv)

        pid, pgid = self._adapter.spawn(argv, env=env, cwd=cwd)
        described = self._adapter.describe(pid)
        if described is None:
            raise SupervisorError(f"spawned pid {pid} could not be described")
        _, start_marker, cmdline = described

        identity = ProcessIdentity(
            pid=pid,
            pgid=pgid,
            start_marker=start_marker,
            fingerprint=fingerprint_cmdline(cmdline),
            tag=tag,
        )
        self._owned[tag] = identity
        return identity

    def stop(self, identity: ProcessIdentity, *, outcome: Outcome = Outcome.CANCELLED) -> Outcome:
        """Bounded shutdown: SIGTERM the group, then SIGKILL after the grace period."""
        try:
            self._verify_ownership(identity)
        except OwnershipMismatchError:
            self._owned.pop(identity.tag, None)
            return Outcome.PROCESS_FAILED

        self._adapter.signal(identity.pid, signal.SIGTERM, group=True)
        deadline = self._clock() + self._graceful
        while self._clock() < deadline:
            if self._adapter.poll(identity.pid) is not None:
                self._owned.pop(identity.tag, None)
                return outcome

        # Still alive -> bounded escalation, but only after re-verifying ownership.
        try:
            self._verify_ownership(identity)
        except OwnershipMismatchError:
            self._owned.pop(identity.tag, None)
            return Outcome.PROCESS_FAILED

        self._adapter.signal(identity.pid, signal.SIGKILL, group=True)
        self._owned.pop(identity.tag, None)
        return outcome

    def cancel(self, tag: str) -> Outcome:
        identity = self._owned.get(tag)
        if identity is None:
            return Outcome.PROCESS_FAILED
        return self.stop(identity, outcome=Outcome.CANCELLED)

    def cleanup_all(self) -> Dict[str, Outcome]:
        """Terminate every owned process. Never touches unowned PIDs."""
        results: Dict[str, Outcome] = {}
        for tag, identity in list(self._owned.items()):
            results[tag] = self.stop(identity, outcome=Outcome.CANCELLED)
        self._owned.clear()
        return results
