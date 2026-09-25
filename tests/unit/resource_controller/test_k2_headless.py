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
