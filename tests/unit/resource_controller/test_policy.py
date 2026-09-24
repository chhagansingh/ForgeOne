"""Policy validation -- fail-closed behaviour."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from services.resource_controller.policy import PolicyError, ResourcePolicy

from .support import GB, make_policy


class PolicyFailClosedTests(unittest.TestCase):
    def test_missing_critical_field_raises(self):
        """Case 12: missing critical policy must fail closed."""
        with self.assertRaises(PolicyError) as ctx:
            ResourcePolicy.from_mapping(
                {
                    "max_context_tokens": 8192,
                    "max_input_tokens": 6144,
                    # reserved_output_tokens deliberately omitted
                    "min_available_memory_bytes": 1 * GB,
                    "max_retained_cache_bytes": 1 * GB,
                    "transient_reserve_bytes": 1 * GB,
                    "request_timeout_s": 60,
                    "cooldown_after_abnormal_exit_s": 60,
                }
            )
        self.assertIn("reserved_output_tokens", str(ctx.exception))

    def test_every_critical_field_is_required(self):
        required = [
            "max_context_tokens",
            "max_input_tokens",
            "reserved_output_tokens",
            "min_available_memory_bytes",
            "max_retained_cache_bytes",
            "transient_reserve_bytes",
            "request_timeout_s",
            "cooldown_after_abnormal_exit_s",
            "telemetry_max_age_s",
        ]
        full = {
            "max_context_tokens": 8192,
            "max_input_tokens": 6144,
            "reserved_output_tokens": 2048,
            "min_available_memory_bytes": 1 * GB,
            "max_retained_cache_bytes": 1 * GB,
            "transient_reserve_bytes": 1 * GB,
            "request_timeout_s": 60,
            "cooldown_after_abnormal_exit_s": 60,
            "telemetry_max_age_s": 5.0,
        }
        for field in required:
            with self.subTest(missing=field):
                partial = {k: v for k, v in full.items() if k != field}
                with self.assertRaises(PolicyError):
                    ResourcePolicy.from_mapping(partial)

    def test_non_positive_value_raises(self):
        with self.assertRaises(PolicyError):
            make_policy(max_context_tokens=0)

    def test_input_plus_output_must_fit_context(self):
        with self.assertRaises(PolicyError) as ctx:
            make_policy(max_context_tokens=4096, max_input_tokens=4096, reserved_output_tokens=1024)
        self.assertIn("exceeds max_context_tokens", str(ctx.exception))

    def test_concurrency_must_be_one(self):
        with self.assertRaises(PolicyError) as ctx:
            make_policy(max_concurrent_requests=4)
        self.assertIn("must be exactly 1", str(ctx.exception))

    def test_automatic_context_escalation_is_prohibited(self):
        with self.assertRaises(PolicyError) as ctx:
            make_policy(allow_context_escalation=True)
        self.assertIn("escalation", str(ctx.exception))

    def test_unknown_field_rejected(self):
        with self.assertRaises(PolicyError) as ctx:
            ResourcePolicy.from_mapping(
                {
                    "max_context_tokens": 8192,
                    "max_input_tokens": 6144,
                    "reserved_output_tokens": 2048,
                    "min_available_memory_bytes": 1 * GB,
                    "max_retained_cache_bytes": 1 * GB,
                    "transient_reserve_bytes": 1 * GB,
                    "request_timeout_s": 60,
                    "cooldown_after_abnormal_exit_s": 60,
                    "surprise_field": 1,
                }
            )
        self.assertIn("unknown policy fields", str(ctx.exception))

    def test_json_file_roundtrip_and_missing_file(self):
        policy = make_policy()
        payload = {
            f.name: getattr(policy, f.name) for f in ResourcePolicy.__dataclass_fields__.values()
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "policy.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            loaded = ResourcePolicy.from_json_file(path)
            self.assertEqual(loaded, policy)

            with self.assertRaises(PolicyError):
                ResourcePolicy.from_json_file(os.path.join(tmp, "nope.json"))

    def test_budget_bytes(self):
        policy = make_policy(min_available_memory_bytes=6 * GB)
        self.assertEqual(policy.budget_bytes(10 * GB), 4 * GB)
        self.assertEqual(policy.budget_bytes(5 * GB), -1 * GB)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
