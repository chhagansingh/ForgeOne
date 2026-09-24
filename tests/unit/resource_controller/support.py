"""Shared fixtures for Resource Controller unit tests.

Everything here is synthetic. No model is imported, loaded or run; no
high-memory workload is ever started. Tests use fake adapters, scripted
telemetry and at most a trivial short-lived OS process.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from services.resource_controller.estimator import ModelMetadata
from services.resource_controller.policy import ResourcePolicy
from services.resource_controller.telemetry import make_snapshot
from services.resource_controller.tokenization import TokenCounter

GB = 1024**3
MB = 1024**2

# KV geometry observed on the FORGE-003 checkpoint (config.json).
LAYERS = 36
KV_HEADS = 8
HEAD_DIM = 128
BYTES_PER_ELEMENT = 2
KV_BYTES_PER_TOKEN = 2 * LAYERS * KV_HEADS * HEAD_DIM * BYTES_PER_ELEMENT  # 147456


class FixedTokenCounter(TokenCounter):
    """Returns an exact token count, so admission boundaries are testable."""

    def __init__(self, tokens: int, available: bool = True) -> None:
        self._tokens = tokens
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def count_chat_tokens(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> int:
        return self._tokens

    def count_text_tokens(self, text: str) -> int:
        return self._tokens


def make_policy(**overrides) -> ResourcePolicy:
    base = dict(
        max_context_tokens=16384,
        max_input_tokens=12288,
        reserved_output_tokens=4096,
        min_available_memory_bytes=6 * GB,
        max_retained_cache_bytes=2 * GB,
        transient_reserve_bytes=2 * GB,
        request_timeout_s=300.0,
        cooldown_after_abnormal_exit_s=120.0,
    )
    base.update(overrides)
    return ResourcePolicy(**base)


def make_metadata(**overrides) -> ModelMetadata:
    base = dict(
        num_hidden_layers=LAYERS,
        num_key_value_heads=KV_HEADS,
        head_dim=HEAD_DIM,
        weight_bytes=int(2.2 * GB),
        runtime_overhead_bytes=int(1.5 * GB),
        bytes_per_element=BYTES_PER_ELEMENT,
        source_revision="test-revision",
    )
    base.update(overrides)
    return ModelMetadata(**base)


def healthy_snapshot():
    """Matches the real host shape: little 'free', plenty reclaimable."""
    return make_snapshot(
        total_gb=24.0, free_gb=0.25, inactive_gb=8.6, speculative_gb=0.85,
        swap_used_gb=0.0, pageouts=1000, timestamp=0.0,
    )


def low_memory_snapshot():
    return make_snapshot(
        total_gb=24.0, free_gb=0.10, inactive_gb=1.2, speculative_gb=0.1,
        swap_used_gb=0.5, pageouts=1000, timestamp=0.0,
    )


def message(text: str = "hello"):
    return [{"role": "user", "content": text}]
