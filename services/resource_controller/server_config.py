"""Protected server configuration adapter for mlx-lm.

Why this module exists
----------------------
FORGE-003 inspection of the installed mlx-lm 0.31.3 established, with source
evidence, that the prompt-cache byte ceiling is **not applied by default**:

* ``server.py:1877-1881``  ``--prompt-cache-bytes`` default is ``None``.
* ``server.py:1871-1876``  ``--prompt-cache-size`` default is ``10`` -- a
  *sequence count*, which is not a memory bound.
* ``server.py:798``        ``trim_to(n_bytes=...)`` is called from exactly one
  site, inside the *batched* generation path, and only when
  ``prompt_cache_bytes is not None``.
* ``server.py:922``        ``_serve_single`` performs **no** cache trimming.
* ``server.py:685-686``    ``_is_batchable`` is ``is_batchable and args.seed is
  None`` -- so passing ``--seed`` routes to the unbounded single path.

This module refuses to build a server command line unless an explicit byte
budget and sequence policy are supplied, and it refuses serving paths where the
budget would not actually be enforced.

**A retained-cache ceiling is not a total memory ceiling.** Active KV cache,
model weights and transient prefill allocations are accounted for *separately*
by :class:`CacheBudget` and are never folded into the cache number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Tuple

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: Executables the controller is willing to launch. Never a shell.
APPROVED_EXECUTABLE_BASENAMES = frozenset({"python", "python3", "python3.12", "uv"})

#: Flags that route mlx-lm to a path where the byte ceiling is NOT enforced.
UNBOUNDED_PATH_FLAGS = ("--seed",)

#: Flags this adapter requires the installed server to support.
REQUIRED_SERVER_FLAGS = ("--prompt-cache-bytes", "--prompt-cache-size", "--host", "--port")


class ServerConfigError(ValueError):
    """Raised when a server configuration is missing, unsafe or unsupported."""


class UnsupportedServingPath(ServerConfigError):
    """Raised when the requested command line would bypass cache enforcement."""


class UnsupportedServerVersion(ServerConfigError):
    """Raised when the installed server does not support a required flag."""


@dataclass(frozen=True)
class CacheBudget:
    """Separated memory accounting. These numbers are NOT interchangeable.

    ``retained_cache_bytes`` bounds the *prompt cache* only. It says nothing
    about the three allocations below, each of which must be budgeted
    independently by the admission controller.
    """

    retained_cache_bytes: int
    max_sequences: int
    active_kv_bytes: int
    weights_bytes: int
    transient_reserve_bytes: int

    def __post_init__(self) -> None:
        for name in (
            "retained_cache_bytes",
            "max_sequences",
            "active_kv_bytes",
            "weights_bytes",
            "transient_reserve_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ServerConfigError(f"CacheBudget.{name} must be an integer")
            if value <= 0:
                raise ServerConfigError(f"CacheBudget.{name} must be > 0, got {value}")

    @property
    def cache_is_bounded(self) -> bool:
        """True only when an explicit byte ceiling and sequence ceiling exist."""
        return self.retained_cache_bytes > 0 and self.max_sequences > 0

    def total_separately_accounted_bytes(self) -> int:
        """Everything EXCEPT the retained cache, which admission tracks apart."""
        return self.active_kv_bytes + self.weights_bytes + self.transient_reserve_bytes


@dataclass(frozen=True)
class ProtectedServerConfig:
    """A validated, non-shell server command line."""

    executable: str
    model: str
    port: int
    cache: CacheBudget
    host: str = "127.0.0.1"
    module: str = "mlx_lm.server"
    extra_args: Tuple[str, ...] = field(default_factory=tuple)
    allow_unbounded_serving_path: bool = False
    #: Optional complete argv. Used by non-MLX backends (e.g. llama-server)
    #: whose command line is not MLX-shaped. When set, ``build_argv`` returns
    #: it verbatim instead of composing the MLX form. Backward compatible:
    #: unset means the previous behaviour, unchanged.
    argv_override: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if not self.executable:
            raise ServerConfigError("executable is required")
        if not self.model:
            raise ServerConfigError("model is required")

        basename = self.executable.rsplit("/", 1)[-1]
        if basename not in APPROVED_EXECUTABLE_BASENAMES:
            raise ServerConfigError(
                f"executable {basename!r} is not in the approved list "
                f"{sorted(APPROVED_EXECUTABLE_BASENAMES)}"
            )

        if self.host not in LOOPBACK_HOSTS:
            raise ServerConfigError(
                f"host {self.host!r} is not loopback; local services must never "
                f"be exposed to the LAN"
            )

        if not isinstance(self.port, int) or not (1024 <= self.port <= 65535):
            raise ServerConfigError(f"port {self.port!r} must be an integer in 1024..65535")

        if self.cache is None:
            raise ServerConfigError("an explicit CacheBudget is required")
        if not self.cache.cache_is_bounded:
            raise ServerConfigError(
                "prompt cache must be explicitly bounded in BOTH bytes and sequences"
            )

        for arg in self.extra_args:
            if not isinstance(arg, str):
                raise ServerConfigError("every argument must be a string")
            if "\x00" in arg:
                raise ServerConfigError("argument contains a NUL byte")
            if any(ch in arg for ch in ("\n", "\r")):
                raise ServerConfigError("argument contains a newline")

        if not self.allow_unbounded_serving_path:
            self._reject_unbounded_path()

    def _reject_unbounded_path(self) -> None:
        for arg in self.extra_args:
            for flag in UNBOUNDED_PATH_FLAGS:
                if arg == flag or arg.startswith(flag + "="):
                    raise UnsupportedServingPath(
                        f"{flag!r} routes mlx-lm to the non-batched path, where the "
                        f"prompt-cache byte ceiling is NOT enforced "
                        f"(server.py:922 _serve_single performs no trim_to). "
                        f"Refusing to build an unbounded command line."
                    )

    # ------------------------------------------------------------------
    def build_argv(self) -> list:
        """Build argv as a list. Never a shell string; no interpolation."""
        if self.argv_override is not None:
            return list(self.argv_override)
        argv = [
            self.executable,
            "-m",
            self.module,
            "--model",
            self.model,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--prompt-cache-bytes",
            str(self.cache.retained_cache_bytes),
            "--prompt-cache-size",
            str(self.cache.max_sequences),
        ]
        argv.extend(self.extra_args)
        return argv

    def as_dict(self) -> dict:
        return {
            "executable": self.executable,
            "model": self.model,
            "host": self.host,
            "port": self.port,
            "prompt_cache_bytes": self.cache.retained_cache_bytes,
            "prompt_cache_size": self.cache.max_sequences,
            "extra_args": list(self.extra_args),
            "unbounded_path_allowed": self.allow_unbounded_serving_path,
        }


# ---------------------------------------------------------------------------
# Installed-version / flag support validation
# ---------------------------------------------------------------------------
_FLAG_RE = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)")


def supported_flags_from_help(help_text: str) -> frozenset:
    """Extract long option names from a server's ``--help`` output."""
    if not isinstance(help_text, str):
        raise ServerConfigError("help text must be a string")
    return frozenset(_FLAG_RE.findall(help_text))


def verify_server_support(
    help_text: str, required: Iterable = REQUIRED_SERVER_FLAGS
) -> None:
    """Fail closed when the installed server lacks a required flag.

    Version-agnostic on purpose: it inspects the actual ``--help`` output of the
    installed server rather than trusting a version number.
    """
    present = supported_flags_from_help(help_text)
    missing = sorted(set(required) - present)
    if missing:
        raise UnsupportedServerVersion(
            "installed mlx-lm server does not support required flag(s): "
            + ", ".join(missing)
        )


def serving_path_is_bounded(argv: Sequence) -> Tuple[bool, str]:
    """Report whether a command line keeps the prompt-cache byte ceiling active.

    Mirrors the installed source logic (``server.py:685-686``): the byte ceiling
    is only applied on the batched path, and ``--seed`` forces the single path.
    """
    if "--seed" in argv or any(str(a).startswith("--seed=") for a in argv):
        return False, "--seed selects the non-batched path, which does not trim the cache"

    if "--prompt-cache-bytes" not in argv:
        return False, "--prompt-cache-bytes is absent, so no byte ceiling is applied"

    try:
        idx = list(argv).index("--prompt-cache-bytes")
        value = argv[idx + 1]
        if int(value) <= 0:
            return False, "--prompt-cache-bytes must be a positive integer"
    except (ValueError, IndexError):
        return False, "--prompt-cache-bytes has no usable value"

    return True, "byte ceiling active on the batched path"
