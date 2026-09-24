"""Explicit resource policy for local inference workloads.

Fail-closed contract: if a critical field is missing, malformed or violates an
invariant, constructing a policy raises :class:`PolicyError`. There is no
"default to something safe" path -- an unknown budget is treated as an unsafe
budget, because that is exactly how the FORGE-003 incident happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class PolicyError(ValueError):
    """Raised when a policy is missing, malformed or self-inconsistent."""


_CRITICAL_FIELDS = (
    "max_context_tokens",
    "max_input_tokens",
    "reserved_output_tokens",
    "min_available_memory_bytes",
    "max_retained_cache_bytes",
    "transient_reserve_bytes",
    "request_timeout_s",
    "cooldown_after_abnormal_exit_s",
)


def _require_positive_int(data: Mapping[str, Any], key: str) -> int:
    if key not in data:
        raise PolicyError(f"missing critical policy field: {key!r}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"policy field {key!r} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise PolicyError(f"policy field {key!r} must be > 0, got {value}")
    return value


def _require_positive_number(data: Mapping[str, Any], key: str) -> float:
    if key not in data:
        raise PolicyError(f"missing critical policy field: {key!r}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"policy field {key!r} must be a number, got {type(value).__name__}")
    if value <= 0:
        raise PolicyError(f"policy field {key!r} must be > 0, got {value}")
    return float(value)


@dataclass(frozen=True)
class ResourcePolicy:
    """Immutable, validated resource budget for one host."""

    # --- context budget -------------------------------------------------
    max_context_tokens: int
    max_input_tokens: int
    reserved_output_tokens: int

    # --- memory budget --------------------------------------------------
    min_available_memory_bytes: int
    max_retained_cache_bytes: int
    transient_reserve_bytes: int

    # --- timing / lifecycle --------------------------------------------
    request_timeout_s: float
    cooldown_after_abnormal_exit_s: float

    # --- invariants enforced below -------------------------------------
    max_concurrent_requests: int = 1
    allow_context_escalation: bool = False

    def __post_init__(self) -> None:
        for name in _CRITICAL_FIELDS:
            value = getattr(self, name)
            if value is None:
                raise PolicyError(f"missing critical policy field: {name!r}")
            if value <= 0:
                raise PolicyError(f"policy field {name!r} must be > 0, got {value}")

        if self.max_input_tokens + self.reserved_output_tokens > self.max_context_tokens:
            raise PolicyError(
                "max_input_tokens + reserved_output_tokens "
                f"({self.max_input_tokens} + {self.reserved_output_tokens}) "
                f"exceeds max_context_tokens ({self.max_context_tokens})"
            )

        if self.max_concurrent_requests != 1:
            raise PolicyError(
                "max_concurrent_requests must be exactly 1 for local inference; "
                f"got {self.max_concurrent_requests}"
            )

        if self.allow_context_escalation:
            raise PolicyError(
                "allow_context_escalation must be False: automatic context escalation "
                "is prohibited (see the FORGE-003 incident report)"
            )

    # ------------------------------------------------------------------
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ResourcePolicy":
        if not isinstance(data, Mapping):
            raise PolicyError(f"policy must be a mapping, got {type(data).__name__}")

        unknown = set(data) - {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        if unknown:
            raise PolicyError(f"unknown policy fields: {sorted(unknown)}")

        return cls(
            max_context_tokens=_require_positive_int(data, "max_context_tokens"),
            max_input_tokens=_require_positive_int(data, "max_input_tokens"),
            reserved_output_tokens=_require_positive_int(data, "reserved_output_tokens"),
            min_available_memory_bytes=_require_positive_int(data, "min_available_memory_bytes"),
            max_retained_cache_bytes=_require_positive_int(data, "max_retained_cache_bytes"),
            transient_reserve_bytes=_require_positive_int(data, "transient_reserve_bytes"),
            request_timeout_s=_require_positive_number(data, "request_timeout_s"),
            cooldown_after_abnormal_exit_s=_require_positive_number(
                data, "cooldown_after_abnormal_exit_s"
            ),
            max_concurrent_requests=int(data.get("max_concurrent_requests", 1)),
            allow_context_escalation=bool(data.get("allow_context_escalation", False)),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ResourcePolicy":
        p = Path(path)
        if not p.is_file():
            raise PolicyError(f"policy file not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PolicyError(f"policy file is not valid JSON: {exc}") from exc
        return cls.from_mapping(data)

    # ------------------------------------------------------------------
    def budget_bytes(self, available_bytes: int) -> int:
        """Headroom above the reserved floor. Negative means 'do not run'."""
        return available_bytes - self.min_available_memory_bytes
