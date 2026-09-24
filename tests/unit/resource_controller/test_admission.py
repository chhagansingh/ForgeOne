"""Admission control -- the gate that would have prevented the P0 incident.

All tests use synthetic telemetry and fixed token counters. No model is
imported, loaded or run.
"""

from __future__ import annotations

import unittest

from services.resource_controller.admission import AdmissionController, AdmissionRequest
from services.resource_controller.outcomes import Outcome
from services.resource_controller.telemetry import (
    SyntheticTelemetrySource,
    UnavailableTelemetrySource,
)
from services.resource_controller.tokenization import MockTokenCounter

from .support import (
    GB,
    FixedTokenCounter,
    healthy_snapshot,
    low_memory_snapshot,
    make_metadata,
    make_policy,
    message,
)


def controller(**kw):
    return AdmissionController(
        kw.get("policy", make_policy()),
        kw.get("counter", FixedTokenCounter(2000)),
        kw.get("metadata", make_metadata()),
        kw.get("telemetry", SyntheticTelemetrySource([healthy_snapshot()])),
    )


def request(**kw):
    base = dict(messages=message(), requested_output_tokens=500)
    base.update(kw)
    return AdmissionRequest(**base)


class AdmissionTests(unittest.TestCase):
    def test_safe_request_admitted(self):
        """Case 1: a request that fits every limit is admitted."""
        decision = controller().admit(request())
        self.assertEqual(decision.outcome, Outcome.ADMITTED)
        self.assertTrue(decision.admitted)
        self.assertIsNotNone(decision.estimate)
        self.assertLessEqual(decision.estimate.total_bytes, decision.budget_bytes)

    def test_input_limit_exceeded(self):
        """Case 2: input beyond max_input_tokens is REJECTED_CONTEXT."""
        decision = controller(counter=FixedTokenCounter(13000)).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_CONTEXT)
        self.assertIn("max_input_tokens", decision.reason)

    def test_output_reservation_exceeded(self):
        """Case 3: output beyond the reservation is REJECTED_CONTEXT."""
        decision = controller().admit(request(requested_output_tokens=5000))
        self.assertEqual(decision.outcome, Outcome.REJECTED_CONTEXT)
        self.assertIn("reserved_output_tokens", decision.reason)

    def test_total_context_check_is_defence_in_depth(self):
        """input + output > max_context is unreachable under a valid policy.

        The policy invariant (max_input + reserved_output <= max_context) makes
        it impossible, so the controller's total-context check is deliberate
        defence in depth: it exists so that a future policy source which bypasses
        validation still cannot overflow the context window. What is asserted
        here is the invariant itself, plus the boundary case behaving sanely.
        """
        policy = make_policy()
        self.assertLessEqual(
            policy.max_input_tokens + policy.reserved_output_tokens,
            policy.max_context_tokens,
        )
        # Exactly at the boundary: the context checks pass (memory may still bind).
        decision = controller(counter=FixedTokenCounter(policy.max_input_tokens)).admit(
            request(requested_output_tokens=policy.reserved_output_tokens)
        )
        self.assertNotEqual(decision.outcome, Outcome.REJECTED_CONTEXT)

    def test_kv_estimate_exceeds_budget(self):
        """Case 4: within all token limits, but the KV estimate does not fit."""
        decision = controller(counter=FixedTokenCounter(12000)).admit(
            request(requested_output_tokens=4000)
        )
        self.assertEqual(decision.outcome, Outcome.REJECTED_MEMORY)
        self.assertIn("exceeds headroom", decision.reason)
        self.assertIsNotNone(decision.estimate)

    def test_retained_cache_exceeds_budget(self):
        """Case 5: retained prompt cache beyond the cache budget."""
        decision = controller().admit(request(retained_cache_bytes=3 * GB))
        self.assertEqual(decision.outcome, Outcome.REJECTED_MEMORY)
        self.assertIn("retained cache", decision.reason)

    def test_insufficient_available_memory(self):
        """Case 6: host below the available-memory floor."""
        decision = controller(
            telemetry=SyntheticTelemetrySource([low_memory_snapshot()])
        ).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_MEMORY)
        self.assertIn("below the floor", decision.reason)

    def test_concurrent_request_rejected(self):
        """Case 9: local inference is limited to one concurrent request."""
        decision = controller().admit(request(active_requests=1))
        self.assertEqual(decision.outcome, Outcome.REJECTED_CONCURRENCY)

    def test_missing_tokenizer(self):
        """Case 10: no tokenizer means no verified count means no admission."""
        decision = controller(counter=None).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("tokenizer", decision.reason)

    def test_unavailable_tokenizer(self):
        decision = controller(counter=MockTokenCounter(available=False)).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)

    def test_missing_model_metadata(self):
        """Case 11: no metadata means no KV estimate means no admission."""
        decision = controller(metadata=None).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("metadata", decision.reason)

    def test_missing_critical_policy(self):
        """Case 12: no policy means no budget means no admission."""
        decision = controller(policy=None).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("policy", decision.reason)

    def test_unavailable_telemetry_fails_closed(self):
        decision = controller(telemetry=UnavailableTelemetrySource()).admit(request())
        self.assertEqual(decision.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)
        self.assertIn("telemetry unavailable", decision.reason)

    def test_non_positive_output_tokens_rejected(self):
        decision = controller().admit(request(requested_output_tokens=0))
        self.assertEqual(decision.outcome, Outcome.REJECTED_UNVERIFIED_ESTIMATE)

    def test_rejection_never_silently_reduces_context(self):
        """The controller must reject, never quietly shrink the request."""
        req = request(requested_output_tokens=5000)
        decision = controller().admit(req)
        self.assertEqual(decision.outcome, Outcome.REJECTED_CONTEXT)
        # The request object is untouched and no substitute was produced.
        self.assertEqual(req.requested_output_tokens, 5000)
        self.assertFalse(decision.admitted)

    def test_decision_is_json_serialisable(self):
        decision = controller().admit(request())
        payload = decision.as_dict()
        self.assertEqual(payload["outcome"], "ADMITTED")
        self.assertIn("total_bytes", payload["estimate"])

    def test_non_resident_model_can_be_rejected_where_resident_is_admitted(self):
        """Starting a server (non-resident) costs more than serving a request."""
        ctrl = controller()
        resident = ctrl.admit(request(model_resident=True))
        cold = ctrl.admit(request(model_resident=False))
        self.assertEqual(resident.outcome, Outcome.ADMITTED)
        self.assertEqual(cold.outcome, Outcome.REJECTED_MEMORY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
