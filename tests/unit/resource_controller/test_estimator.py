"""Estimator behaviour -- geometry, residency accounting and fail-closed metadata."""

from __future__ import annotations

import unittest

from services.resource_controller.estimator import (
    MetadataUnavailable,
    ModelMetadata,
    estimate,
)

from .support import GB, KV_BYTES_PER_TOKEN, make_metadata


class EstimatorTests(unittest.TestCase):
    def test_missing_metadata_raises(self):
        """Case 11: missing model metadata must fail closed."""
        with self.assertRaises(MetadataUnavailable):
            estimate(context_tokens=1000, metadata=None)

    def test_kv_bytes_per_token_matches_geometry(self):
        meta = make_metadata()
        self.assertEqual(meta.kv_bytes_per_token(), KV_BYTES_PER_TOKEN)
        self.assertEqual(meta.kv_bytes_per_token(), 147456)

    def test_kv_bytes_scales_linearly(self):
        meta = make_metadata()
        self.assertEqual(meta.kv_bytes(0), 0)
        self.assertEqual(meta.kv_bytes(16384), 147456 * 16384)

    def test_from_hf_config_reads_explicit_head_dim(self):
        meta = ModelMetadata.from_hf_config(
            {
                "num_hidden_layers": 36,
                "num_key_value_heads": 8,
                "head_dim": 128,
                "hidden_size": 2560,
                "num_attention_heads": 32,
            },
            weight_bytes=1 * GB,
            runtime_overhead_bytes=1 * GB,
        )
        self.assertEqual(meta.head_dim, 128)
        self.assertEqual(meta.kv_bytes_per_token(), KV_BYTES_PER_TOKEN)

    def test_from_hf_config_derives_head_dim_when_absent(self):
        meta = ModelMetadata.from_hf_config(
            {
                "num_hidden_layers": 36,
                "num_key_value_heads": 8,
                "hidden_size": 4096,
                "num_attention_heads": 32,
            },
            weight_bytes=1 * GB,
            runtime_overhead_bytes=1 * GB,
        )
        self.assertEqual(meta.head_dim, 128)

    def test_from_hf_config_missing_field_raises(self):
        """Case 11: incomplete metadata must raise, never guess."""
        with self.assertRaises(MetadataUnavailable) as ctx:
            ModelMetadata.from_hf_config(
                {"num_hidden_layers": 36},  # no kv heads
                weight_bytes=1 * GB,
                runtime_overhead_bytes=1 * GB,
            )
        self.assertIn("num_key_value_heads", str(ctx.exception))

    def test_from_hf_config_rejects_non_divisible_hidden_size(self):
        with self.assertRaises(MetadataUnavailable):
            ModelMetadata.from_hf_config(
                {
                    "num_hidden_layers": 36,
                    "num_key_value_heads": 8,
                    "hidden_size": 4095,
                    "num_attention_heads": 32,
                },
                weight_bytes=1 * GB,
                runtime_overhead_bytes=1 * GB,
            )

    def test_resident_model_excludes_weights_from_total(self):
        meta = make_metadata()
        est = estimate(
            context_tokens=1000, metadata=meta, transient_reserve_bytes=1 * GB, model_resident=True
        )
        self.assertEqual(
            est.total_bytes, meta.kv_bytes(1000) + 1 * GB
        )
        self.assertGreater(est.weights_bytes, 0)  # still reported for transparency

    def test_non_resident_model_charges_weights(self):
        meta = make_metadata()
        est = estimate(
            context_tokens=1000, metadata=meta, transient_reserve_bytes=1 * GB, model_resident=False
        )
        self.assertEqual(
            est.total_bytes,
            meta.kv_bytes(1000) + 1 * GB + meta.weight_bytes + meta.runtime_overhead_bytes,
        )

    def test_retained_cache_is_charged(self):
        meta = make_metadata()
        est = estimate(
            context_tokens=0, metadata=meta, retained_cache_bytes=512 * 1024 * 1024,
            transient_reserve_bytes=0,
        )
        self.assertEqual(est.total_bytes, 512 * 1024 * 1024)

    def test_negative_inputs_rejected(self):
        with self.assertRaises(ValueError):
            estimate(context_tokens=100, metadata=make_metadata(), retained_cache_bytes=-1)
        with self.assertRaises(ValueError):
            estimate(context_tokens=100, metadata=make_metadata(), transient_reserve_bytes=-1)

    def test_metadata_rejects_non_positive_geometry(self):
        with self.assertRaises(MetadataUnavailable):
            make_metadata(num_key_value_heads=0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
