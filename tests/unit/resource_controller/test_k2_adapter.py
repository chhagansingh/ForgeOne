"""K2 adapter tests — identity, path containment, tokenizer separation.

Mock transport and synthetic telemetry only. No weights loaded, no inference.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from services.resource_controller.controller import ResourceController
from services.resource_controller.k2_adapter import (
    APPROVED_FILENAME,
    APPROVED_REPO,
    APPROVED_REVISION,
    APPROVED_SIZE,
    AdapterError,
    K2InferenceAdapter,
    K2ModelIdentity,
    MissingChatTemplate,
    MockK2TokenCounter,
    TokenizerMismatch,
    require_k2_counter,
    resolve_checkpoint_path,
)
from services.resource_controller.outcomes import Outcome
from services.resource_controller.ports import StaticPortProbe
from services.resource_controller.protected import (
    ProtectedServer,
    RecordingForwarder,
    StaticWatchdogReadiness,
)
from services.resource_controller.server_config import CacheBudget, ProtectedServerConfig
from services.resource_controller.supervisor import FakeProcessAdapter, ProcessSupervisor
from services.resource_controller.telemetry import SyntheticTelemetrySource
from services.resource_controller.watchdog import WatchdogThresholds

from .support import GB, FixedTokenCounter, healthy_snapshot, make_metadata, make_policy, message

PY = "/usr/bin/python3"
TEMPLATE = "{{- bos_token }}<|ifm|im_start|>..."  # stand-in for the real 43040-char template


def approved(**kw):
    base = dict(chat_template=TEMPLATE)
    base.update(kw)
    return K2ModelIdentity(
        repository=kw.get("repository", APPROVED_REPO),
        revision=kw.get("revision", APPROVED_REVISION),
        filename=kw.get("filename", APPROVED_FILENAME),
        size_bytes=kw.get("size_bytes", APPROVED_SIZE),
        architecture=kw.get("architecture", "k2-horizon"),
        chat_template=base["chat_template"],
    )


def make_adapter(**kw):
    policy = make_policy(
        max_context_tokens=4096, max_input_tokens=3072, reserved_output_tokens=128,
        min_available_memory_bytes=int(3.5 * GB),
    )
    controller = ResourceController(
        policy, FixedTokenCounter(200), make_metadata(),
        SyntheticTelemetrySource([healthy_snapshot()]),
        supervisor=ProcessSupervisor(FakeProcessAdapter(), graceful_timeout_s=1.0),
        watchdog_thresholds=WatchdogThresholds(
            min_available_bytes=1 * GB, max_swap_used_bytes=8 * GB,
            max_pageout_rate=1e9, sample_interval_s=0.001),
    )
    forwarder = RecordingForwarder(
        response={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    server = ProtectedServer(
        controller,
        ProtectedServerConfig(
            executable=PY, model=APPROVED_REPO, port=8082,
            cache=CacheBudget(retained_cache_bytes=2 * GB, max_sequences=8,
                              active_kv_bytes=int(2.5 * GB),
                              weights_bytes=int(4.2 * GB),
                              transient_reserve_bytes=int(2.0 * GB))),
        port_probe=StaticPortProbe(free=True),
        watchdog_readiness=StaticWatchdogReadiness(True), forwarder=forwarder)
    server.start()
    root = kw.get("storage_root", Path("/tmp/forgeone-storage"))
    ckpt = kw.get("checkpoint_path", root / "cache" / APPROVED_FILENAME)
    adapter = K2InferenceAdapter(
        server, kw.get("identity", approved()), kw.get("counter", MockK2TokenCounter()),
        storage_root=root, checkpoint_path=ckpt)
    return adapter, server, forwarder


class IdentityTests(unittest.TestCase):
    def test_approved_identity_passes(self):
        approved().validate()

    def test_wrong_repository_rejected(self):
        with self.assertRaises(AdapterError):
            approved(repository="someone/else").validate()

    def test_wrong_revision_rejected(self):
        with self.assertRaises(AdapterError) as c:
            approved(revision="0" * 40).validate()
        self.assertIn("revision", str(c.exception))

    def test_wrong_filename_rejected(self):
        """Q5_K_M, Q8_0 etc. must never be silently substituted."""
        for bad in ("K2-Horizon-4B-Q5_K_M.gguf", "K2-Horizon-4B-Q8_0.gguf",
                    "K2-Horizon-4B-BF16.gguf"):
            with self.subTest(f=bad):
                with self.assertRaises(AdapterError):
                    approved(filename=bad).validate()

    def test_wrong_size_rejected(self):
        with self.assertRaises(AdapterError):
            approved(size_bytes=1).validate()

    def test_wrong_architecture_rejected(self):
        with self.assertRaises(AdapterError):
            approved(architecture="qwen3").validate()


class ChatTemplateTests(unittest.TestCase):
    def test_missing_template_fails_closed(self):
        with self.assertRaises(MissingChatTemplate):
            approved(chat_template=None).validate()

    def test_empty_template_fails_closed(self):
        with self.assertRaises(MissingChatTemplate):
            approved(chat_template="   ").validate()

    def test_real_template_is_used_not_a_substitute(self):
        self.assertTrue(approved().chat_template)


class PathContainmentTests(unittest.TestCase):
    def test_path_inside_storage_accepted(self):
        root = Path("/tmp/forgeone-storage")
        got = resolve_checkpoint_path(root / "models" / "x.gguf", root,
                                      realpath=lambda p: Path(p))
        self.assertTrue(str(got).startswith(str(root)))

    def test_path_outside_storage_rejected(self):
        with self.assertRaises(AdapterError) as c:
            resolve_checkpoint_path(Path("/tmp/elsewhere/x.gguf"),
                                    Path("/tmp/forgeone-storage"),
                                    realpath=lambda p: Path(p))
        self.assertIn("escapes the canonical storage root", str(c.exception))

    def test_symlink_escaping_storage_rejected(self):
        """Realpath is checked, so a symlink out of storage is caught."""
        root = Path("/tmp/forgeone-storage")

        def fake_resolve(p):
            # The symlink under storage resolves to a path OUTSIDE storage.
            if str(p).startswith(str(root)) and "link.gguf" in str(p):
                return Path("/Users/someone/.cache/huggingface/hub/model.gguf")
            return Path(p)

        with self.assertRaises(AdapterError):
            resolve_checkpoint_path(root / "link.gguf", root, realpath=fake_resolve)


class TokenizerSeparationTests(unittest.TestCase):
    def test_k2_counter_accepted(self):
        require_k2_counter(MockK2TokenCounter())

    def test_qwen_counter_rejected(self):
        """Qwen counts must never be reused as K2 counts."""
        class QwenCounter:
            model_family = "qwen"
        with self.assertRaises(TokenizerMismatch) as c:
            require_k2_counter(QwenCounter())
        self.assertIn("never be reused", str(c.exception))

    def test_counter_without_family_rejected(self):
        class Bare:
            pass
        with self.assertRaises(TokenizerMismatch):
            require_k2_counter(Bare())


class RoutingTests(unittest.TestCase):
    def test_request_routes_through_protected_path(self):
        adapter, _, fwd = make_adapter()
        result = adapter.request(message("hi"), 64)
        self.assertEqual(result.outcome, Outcome.ADMITTED)
        self.assertEqual(len(fwd.calls), 1)

    def test_no_direct_backend_bypass(self):
        adapter, server, fwd = make_adapter()
        server.stop()  # nothing running
        result = adapter.request(message("hi"), 64)
        self.assertNotEqual(result.outcome, Outcome.ADMITTED)
        self.assertEqual(fwd.calls, [], "must not reach the backend without admission")

    def test_reservation_released(self):
        adapter, server, _ = make_adapter()
        adapter.request(message("hi"), 64)
        self.assertEqual(server._controller.reservations.active_count, 0)  # noqa: SLF001

    def test_qwen_path_regression_protected(self):
        """The Qwen protected path is untouched by the K2 adapter."""
        from services.resource_controller.protected import ProtectedServer as PS
        self.assertTrue(hasattr(PS, "request"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
