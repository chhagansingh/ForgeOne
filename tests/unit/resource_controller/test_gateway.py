"""Protected gateway adapter tests.

Fake transport, synthetic telemetry, no model, no inference. The HTTP listener
is exercised on loopback against a recording forwarder only.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request

from services.resource_controller.controller import ResourceController
from services.resource_controller.gateway import (
    GatewayError,
    GatewayHTTPServer,
    ProtectedGateway,
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

from .support import (
    GB,
    FixedTokenCounter,
    healthy_snapshot,
    make_metadata,
    make_policy,
    message,
)

PY = "/usr/bin/python3"


def free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def gateway_policy(**kw):
    """Mirrors the approved smoke-test policy: 2048 context / 512 in / 128 out."""
    base = dict(
        max_context_tokens=2048,
        max_input_tokens=512,
        reserved_output_tokens=128,
        min_available_memory_bytes=int(3.5 * GB),
    )
    base.update(kw)
    return make_policy(**base)


def make_gateway(**kw):
    policy = kw.get("policy", gateway_policy())
    controller = ResourceController(
        policy,
        kw.get("counter", FixedTokenCounter(200)),
        kw.get("metadata", make_metadata()),
        kw.get("telemetry", SyntheticTelemetrySource([healthy_snapshot()])),
        supervisor=ProcessSupervisor(FakeProcessAdapter(), graceful_timeout_s=1.0),
        telemetry_writer=kw.get("writer"),
        watchdog_thresholds=WatchdogThresholds(
            min_available_bytes=1 * GB, max_swap_used_bytes=8 * GB,
            max_pageout_rate=1e9, sample_interval_s=0.001,
        ),
    )
    forwarder = kw.get("forwarder", RecordingForwarder(
        response={"choices": [{"message": {"role": "assistant", "content": "done"},
                               "finish_reason": "stop"}]}
    ))
    server = ProtectedServer(
        controller,
        ProtectedServerConfig(
            executable=PY, model="test-model", port=8082,
            cache=CacheBudget(retained_cache_bytes=2 * GB, max_sequences=8,
                              active_kv_bytes=int(2.5 * GB),
                              weights_bytes=int(2.2 * GB),
                              transient_reserve_bytes=int(2.0 * GB)),
        ),
        port_probe=StaticPortProbe(free=True),
        watchdog_readiness=StaticWatchdogReadiness(True),
        forwarder=forwarder,
    )
    server.start()
    return ProtectedGateway(server), server, forwarder


def payload(**kw):
    base = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 64}
    base.update(kw)
    return base


class AdapterTests(unittest.TestCase):
    def test_valid_request_is_forwarded_and_translated(self):
        gw, _, fwd = make_gateway()
        status, body = gw.handle_chat_completion(payload())
        self.assertEqual(status, 200)
        self.assertEqual(len(fwd.calls), 1)
        self.assertEqual(body["choices"][0]["message"]["content"], "done")
        self.assertEqual(body["forgeone"]["outcome"], "ADMITTED")

    def test_streaming_is_refused(self):
        gw, _, fwd = make_gateway()
        with self.assertRaises(GatewayError) as ctx:
            gw.handle_chat_completion(payload(stream=True))
        self.assertIn("stream=True is not supported", str(ctx.exception))
        self.assertEqual(fwd.calls, [])

    def test_max_tokens_above_reservation_is_refused_not_clamped(self):
        gw, _, fwd = make_gateway()
        with self.assertRaises(GatewayError) as ctx:
            gw.handle_chat_completion(payload(max_tokens=999))
        self.assertIn("exceeds the reserved output budget", str(ctx.exception))
        self.assertEqual(fwd.calls, [], "must refuse, never silently clamp")

    def test_missing_messages_refused(self):
        gw, _, fwd = make_gateway()
        with self.assertRaises(GatewayError):
            gw.handle_chat_completion({"model": "x"})
        self.assertEqual(fwd.calls, [])

    def test_non_positive_max_tokens_refused(self):
        gw, _, _ = make_gateway()
        with self.assertRaises(GatewayError):
            gw.handle_chat_completion(payload(max_tokens=0))

    def test_rejected_admission_never_reaches_the_model(self):
        gw, _, fwd = make_gateway(counter=FixedTokenCounter(13000))
        status, body = gw.handle_chat_completion(payload())
        self.assertEqual(status, 400)  # REJECTED_CONTEXT
        self.assertEqual(fwd.calls, [])
        self.assertEqual(body["error"]["code"], "REJECTED_CONTEXT")

    def test_concurrency_rejection_maps_to_429(self):
        gw, server, fwd = make_gateway()
        held = server._controller.reservations.acquire("manual")  # noqa: SLF001
        try:
            status, body = gw.handle_chat_completion(payload())
            self.assertEqual(status, 429)
            self.assertEqual(body["error"]["code"], "REJECTED_CONCURRENCY")
            self.assertEqual(fwd.calls, [])
        finally:
            server._controller.reservations.release(held)

    def test_memory_rejection_maps_to_503(self):
        from services.resource_controller.telemetry import SyntheticTelemetrySource
        from .support import low_memory_snapshot

        # Healthy for startup, then pressure arrives before the request.
        gw, _, fwd = make_gateway(
            telemetry=SyntheticTelemetrySource([healthy_snapshot(), low_memory_snapshot()])
        )
        status, body = gw.handle_chat_completion(payload())
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "REJECTED_MEMORY")
        self.assertEqual(fwd.calls, [])

    def test_no_policy_refuses_to_serve(self):
        gw, _, _ = make_gateway()
        gw.server._controller._policy = None  # noqa: SLF001
        with self.assertRaises(GatewayError) as ctx:
            gw.handle_chat_completion(payload())
        self.assertIn("no ResourcePolicy", str(ctx.exception))


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.gw, self.server, self.fwd = make_gateway()
        self.http = GatewayHTTPServer(self.gw, self.port)
        self.http.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.http.stop()

    def test_non_loopback_bind_refused(self):
        with self.assertRaises(GatewayError):
            GatewayHTTPServer(self.gw, free_port(), host="0.0.0.0")

    def test_models_endpoint(self):
        with urllib.request.urlopen(self.base + "/v1/models", timeout=10) as r:
            body = json.loads(r.read())
        self.assertEqual(body["object"], "list")

    def test_chat_completion_endpoint(self):
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(payload()).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
        self.assertEqual(r.status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "done")
        self.assertEqual(len(self.fwd.calls), 1)

    def test_streaming_refused_over_http(self):
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(payload(stream=True)).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.fwd.calls, [])

    def test_bad_json_refused(self):
        req = urllib.request.Request(
            self.base + "/v1/chat/completions", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/nope", timeout=10)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
