"""KV-cache and total-memory estimation from verified model metadata.

Estimation is deliberately explicit about its uncertainty. The FORGE-003
incident showed that measured memory exceeded the theoretical KV figure, because
the theory accounts only for the *steady-state* cache and not for transient
prefill working buffers. That gap is represented here by
``transient_reserve_bytes`` -- a conservative, configurable allowance that the
operator must set, rather than a silently assumed multiplier.

``transient_reserve_bytes`` is a **conservative reserve, not a measurement**.
The true transient prefill peak for this hardware remains unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


class MetadataUnavailable(RuntimeError):
    """Raised when model metadata is missing or incomplete. Fail closed."""


@dataclass(frozen=True)
class ModelMetadata:
    """Verified structural facts about a checkpoint, needed to size a KV cache."""

    num_hidden_layers: int
    num_key_value_heads: int
    head_dim: int
    weight_bytes: int
    runtime_overhead_bytes: int
    bytes_per_element: int = 2
    source_revision: str = "unknown"

    def __post_init__(self) -> None:
        for name in (
            "num_hidden_layers",
            "num_key_value_heads",
            "head_dim",
            "weight_bytes",
            "runtime_overhead_bytes",
            "bytes_per_element",
        ):
            value = getattr(self, name)
            if value is None or value <= 0:
                raise MetadataUnavailable(f"model metadata field {name!r} must be > 0, got {value}")

    def kv_bytes_per_token(self) -> int:
        """K and V, per layer, per KV head, per element, for one token."""
        return (
            2
            * self.num_hidden_layers
            * self.num_key_value_heads
            * self.head_dim
            * self.bytes_per_element
        )

    def kv_bytes(self, context_tokens: int) -> int:
        if context_tokens < 0:
            raise ValueError("context_tokens must be >= 0")
        return self.kv_bytes_per_token() * context_tokens

    @classmethod
    def from_hf_config(
        cls,
        config: Mapping[str, Any],
        *,
        weight_bytes: int,
        runtime_overhead_bytes: int,
        bytes_per_element: int = 2,
        source_revision: str = "unknown",
    ) -> "ModelMetadata":
        """Build metadata from a Hugging Face ``config.json`` mapping.

        Raises :class:`MetadataUnavailable` when a required structural field is
        absent -- never guesses a default.
        """
        if not isinstance(config, Mapping):
            raise MetadataUnavailable(f"config must be a mapping, got {type(config).__name__}")

        def need(key: str) -> int:
            if key not in config or config[key] is None:
                raise MetadataUnavailable(f"config.json is missing required field {key!r}")
            try:
                return int(config[key])
            except (TypeError, ValueError) as exc:
                raise MetadataUnavailable(f"config field {key!r} is not an integer") from exc

        layers = need("num_hidden_layers")
        kv_heads = need("num_key_value_heads")

        if "head_dim" in config and config["head_dim"]:
            head_dim = int(config["head_dim"])
        else:
            hidden = need("hidden_size")
            heads = need("num_attention_heads")
            if heads <= 0 or hidden % heads != 0:
                raise MetadataUnavailable(
                    "cannot derive head_dim: hidden_size is not divisible by num_attention_heads"
                )
            head_dim = hidden // heads

        return cls(
            num_hidden_layers=layers,
            num_key_value_heads=kv_heads,
            head_dim=head_dim,
            weight_bytes=weight_bytes,
            runtime_overhead_bytes=runtime_overhead_bytes,
            bytes_per_element=bytes_per_element,
            source_revision=source_revision,
        )


@dataclass(frozen=True)
class Estimate:
    """Itemised peak-memory estimate for one request.

    ``model_resident`` controls whether weights and runtime overhead are charged
    to this request:

    * ``model_resident=False`` -- the request will cause the model to be loaded,
      so its full footprint is part of the *incremental* cost and is charged.
    * ``model_resident=True`` -- the model is already loaded and therefore
      already reflected in the host's ``available_bytes``. Charging it again
      would double-count and cause spurious rejections.

    ``weights_bytes`` and ``overhead_bytes`` are always reported for
    transparency; they are simply excluded from :attr:`total_bytes` when the
    model is resident.
    """

    context_tokens: int
    kv_bytes: int
    retained_cache_bytes: int
    weights_bytes: int
    overhead_bytes: int
    transient_reserve_bytes: int
    model_resident: bool = True

    @property
    def total_bytes(self) -> int:
        residency = 0 if self.model_resident else (self.weights_bytes + self.overhead_bytes)
        return (
            self.kv_bytes
            + self.retained_cache_bytes
            + self.transient_reserve_bytes
            + residency
        )

    def breakdown(self) -> dict:
        return {
            "context_tokens": self.context_tokens,
            "kv_bytes": self.kv_bytes,
            "retained_cache_bytes": self.retained_cache_bytes,
            "weights_bytes": self.weights_bytes,
            "overhead_bytes": self.overhead_bytes,
            "transient_reserve_bytes": self.transient_reserve_bytes,
            "model_resident": self.model_resident,
            "total_bytes": self.total_bytes,
        }


def estimate(
    *,
    context_tokens: int,
    metadata: Optional[ModelMetadata],
    retained_cache_bytes: int = 0,
    transient_reserve_bytes: int = 0,
    model_resident: bool = True,
) -> Estimate:
    """Estimate incremental peak memory. Missing metadata raises -- never assumes safety."""
    if metadata is None:
        raise MetadataUnavailable("model metadata unavailable; cannot estimate")
    if retained_cache_bytes < 0:
        raise ValueError("retained_cache_bytes must be >= 0")
    if transient_reserve_bytes < 0:
        raise ValueError("transient_reserve_bytes must be >= 0")

    return Estimate(
        context_tokens=context_tokens,
        kv_bytes=metadata.kv_bytes(context_tokens),
        retained_cache_bytes=retained_cache_bytes,
        weights_bytes=metadata.weight_bytes,
        overhead_bytes=metadata.runtime_overhead_bytes,
        transient_reserve_bytes=transient_reserve_bytes,
        model_resident=model_resident,
    )
