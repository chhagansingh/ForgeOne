"""Headless K2 launcher tests.

Deterministic, model-free. No weights loaded, no inference, no backend started.
Verifies the launcher's *contract*: --check never loads weights, --execute
rejects insufficient memory, and cleanup releases owned resources.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from services.resource_controller.k2_adapter import (
    APPROVED_REPO,
    AdapterError,
    resolve_checkpoint_path,
)

REPO = Path(__file__).resolve().parents[3]
LAUNCHER = REPO / "scripts/run_k2_headless_smoke.sh"
SESSION = REPO / "scripts/k2_headless_session.py"
DOC = REPO / "docs/reports/FORGE-003-k2-headless-execution.md"


class LauncherFilesTests(unittest.TestCase):
    def test_launcher_exists_and_is_executable(self):
        self.assertTrue(LAUNCHER.is_file(), "launcher missing")
        self.assertTrue(LAUNCHER.stat().st_mode & 0o111, "launcher not executable")

    def test_session_exists(self):
        self.assertTrue(SESSION.is_file(), "session script missing")

    def test_launcher_rejects_unknown_mode(self):
        r = subprocess.run(["bash", str(LAUNCHER), "--nonsense"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 64)
        self.assertIn("usage:", r.stderr)

    def test_launcher_requires_a_mode(self):
        r = subprocess.run(["bash", str(LAUNCHER)], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 64)

    def test_launcher_sources_the_storage_policy(self):
        text = LAUNCHER.read_text()
        self.assertIn("forgeone-env.sh", text)
        self.assertIn("escapes", text, "must contain a storage-containment guard")

    def test_no_unprotected_backend_launch(self):
        """The launcher must never exec a raw llama-server itself."""
        text = LAUNCHER.read_text()
        self.assertNotIn("llama-server\" ", text.split("check_inside")[0])
        self.assertIn("exec", text)


class SessionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SESSION.read_text()

    def test_check_never_loads_weights(self):
        """--check must not reach the model-loading path."""
        check_block = self.text.split("if args.check:")[1].split("# --execute")[0]
        for forbidden in ("ProtectedServer(", "llama-server", "load_tokenizer"):
            self.assertNotIn(forbidden, check_block,
                             f"--check must not contain {forbidden}")

    def test_execute_requires_admission_before_loading(self):
        """--execute must gate BEFORE any weight load."""
        tail = self.text.split("# --execute")[1]
        self.assertLess(tail.index("preflight()"), tail.index("execute()"))

    def test_watchdog_starts_before_backend(self):
        body = self.text.split("def execute()")[1]
        self.assertLess(body.index("launch_watchdog_process"),
                        body.index("backend.start()"),
                        "watchdog must be ready before the backend starts")

    def test_cleanup_in_finally_block(self):
        self.assertIn("finally:", self.text)
        body = self.text.split("finally:")[1]
        for required in ("http.stop()", "backend.stop()", "wd_proc.terminate()"):
            self.assertIn(required, body, f"cleanup must include {required}")

    def test_approved_limits_present(self):
        self.assertIn("CTX = 1024", self.text)
        self.assertIn("OUTPUT = 32", self.text)
        self.assertIn("SLOTS = 1", self.text)

    def test_no_automatic_retry(self):
        for bad in ("while True", "retry(", "attempt += 1"):
            self.assertNotIn(bad, self.text, f"must not retry: {bad}")

    def test_provisional_charge_is_labelled_not_measured(self):
        self.assertIn("PROVISIONAL ADMISSION BUDGET, not a measured peak", self.text)

    def test_never_uses_qwen_token_counts(self):
        self.assertNotIn("HuggingFaceTokenCounter()", self.text.split("def execute")[1]
                         .split("MockK2TokenCounter")[0])


class AdapterIntegrationTests(unittest.TestCase):
    def test_approved_identity_constants_match(self):
        self.assertEqual(APPROVED_REPO, "IFM/K2-Horizon-3.7B-GGUF")

    def test_checkpoint_path_outside_storage_refused(self):
        with self.assertRaises(AdapterError):
            resolve_checkpoint_path(Path("/tmp/elsewhere/x.gguf"),
                                    REPO / "storage", realpath=lambda p: Path(p))

    def test_real_checkpoint_is_inside_storage(self):
        ck = (REPO / "storage/cache/huggingface/hub"
              / "models--IFM--K2-Horizon-3.7B-GGUF/snapshots"
              / "a81d5fec318b47b9c7144a839f538f6b9006291c/K2-Horizon-4B-Q6_K.gguf")
        if ck.is_file():
            got = resolve_checkpoint_path(ck, REPO / "storage")
            self.assertIn("storage", str(got))


class DocumentationTests(unittest.TestCase):
    def test_execution_doc_exists(self):
        self.assertTrue(DOC.is_file(), "execution doc missing")

    def test_doc_documents_both_modes(self):
        text = DOC.read_text()
        self.assertIn("--check", text)
        self.assertIn("--execute", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class MetadataWiringTests(unittest.TestCase):
    """DEFECT A regression: metadata must be real, not None."""

    @classmethod
    def setUpClass(cls):
        cls.text = SESSION.read_text()

    def test_production_no_longer_passes_none_metadata(self):
        self.assertNotIn("MockK2TokenCounter(), None", self.text)

    def test_production_builds_real_metadata(self):
        self.assertIn("metadata = ModelMetadata(", self.text)
        self.assertIn("num_hidden_layers=36", self.text)
        self.assertIn("num_key_value_heads=8", self.text)
        self.assertIn("head_dim=128", self.text)
        self.assertIn("weight_bytes=CHECKPOINT_SIZE", self.text)

    def test_overhead_is_labelled_provisional_not_measured(self):
        self.assertIn("PROVISIONAL_OVERHEAD", self.text)
        self.assertIn("NOT a measurement", self.text)

    def test_charge_apportionment_sums_to_the_approved_seven_gib(self):
        """weights + provisional overhead + transient must equal the 7 GiB charge."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("k2s", SESSION)
        m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(m)
        except SystemExit:
            pass
        total = m.CHECKPOINT_SIZE + m.PROVISIONAL_OVERHEAD + m.TRANSIENT_RESERVE
        self.assertEqual(total, m.PROVISIONAL_CHARGE)

    def test_effective_requirement_is_eleven_gib(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("k2s", SESSION)
        m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(m)
        except SystemExit:
            pass
        self.assertEqual(m.EFFECTIVE_REQUIRED, 11 * 1024**3)

    def test_preflight_uses_the_same_terms_as_admission(self):
        """Preflight and startup admission must not disagree."""
        self.assertIn("required = EFFECTIVE_REQUIRED", self.text)


class RealMetadataAdmissionTests(unittest.TestCase):
    """The previously unreachable path: verified metadata is ACCEPTED."""

    def _metadata(self):
        from services.resource_controller.estimator import ModelMetadata
        return ModelMetadata(
            num_hidden_layers=36, num_key_value_heads=8, head_dim=128,
            weight_bytes=4161403264, runtime_overhead_bytes=3288334336,
            bytes_per_element=2, source_revision="a81d5fec" * 5,
        )

    def _controller(self, available_gb, metadata):
        from services.resource_controller.admission import AdmissionController
        from services.resource_controller.telemetry import SyntheticTelemetrySource

        from services.resource_controller.telemetry import make_snapshot

        from .support import FixedTokenCounter, make_policy

        GB = 1024**3
        policy = make_policy(
            max_context_tokens=1024, max_input_tokens=992, reserved_output_tokens=32,
            min_available_memory_bytes=4 * GB, transient_reserve_bytes=64 * 1024**2)
        # free + inactive + speculative must reach `available_gb`
        inactive = max(0.0, available_gb - 0.25 - 0.85)
        snap = make_snapshot(total_gb=24.0, free_gb=0.25, inactive_gb=inactive,
                             speculative_gb=0.85, swap_used_gb=2.0,
                             pageouts=100)
        return AdmissionController(
            policy, FixedTokenCounter(200), metadata,
            SyntheticTelemetrySource([snap]))

    def test_real_metadata_is_accepted_not_rejected_as_unverified(self):
        decision = self._controller(20.0, self._metadata()).admit_startup()
        self.assertNotEqual(
            decision.outcome.name, "REJECTED_UNVERIFIED_ESTIMATE",
            "verified metadata must not be rejected as unverified")
        self.assertTrue(decision.admitted, f"expected admission, got {decision.outcome}")

    def test_missing_metadata_is_still_rejected(self):
        """REJECTED_UNVERIFIED_ESTIMATE must not be weakened."""
        decision = self._controller(20.0, None).admit_startup()
        self.assertEqual(decision.outcome.name, "REJECTED_UNVERIFIED_ESTIMATE")

    def test_insufficient_memory_blocks(self):
        decision = self._controller(5.0, self._metadata()).admit_startup()
        self.assertFalse(decision.admitted)


class ProductionTokenizerWiringTests(unittest.TestCase):
    """DEFECT B regression: no mock counter in production."""

    @classmethod
    def setUpClass(cls):
        cls.text = SESSION.read_text()

    def test_no_mock_counter_in_the_execute_path(self):
        body = self.text.split("def execute()")[1]
        self.assertNotIn("MockK2TokenCounter()", body)

    def test_production_uses_the_backend_counter(self):
        self.assertIn("BackendK2TokenCounter(", self.text)

    def test_backend_counter_fails_closed_without_an_endpoint(self):
        from services.resource_controller.k2_adapter import BackendK2TokenCounter
        from services.resource_controller.tokenization import TokenizerUnavailable

        counter = BackendK2TokenCounter("http://127.0.0.1:1", timeout_s=0.3)
        with self.assertRaises(TokenizerUnavailable):
            counter.count_chat_tokens([{"role": "user", "content": "hi"}])

    def test_backend_counter_declares_k2_family(self):
        from services.resource_controller.k2_adapter import BackendK2TokenCounter
        self.assertEqual(BackendK2TokenCounter("http://x").model_family, "k2-horizon")

    def test_backend_counter_passes_the_family_guard(self):
        from services.resource_controller.k2_adapter import (
            BackendK2TokenCounter, require_k2_counter)
        require_k2_counter(BackendK2TokenCounter("http://x"))

    def test_token_accounting_blocks_before_inference(self):
        body = self.text.split("def execute()")[1]
        self.assertLess(body.index("TOKEN_ACCOUNTING_BLOCKED"),
                        body.index("chat/completions"),
                        "token accounting must gate before the completion request")
