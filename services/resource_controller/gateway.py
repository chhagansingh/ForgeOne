"""Protected OpenAI-compatible gateway.

Why this exists
---------------
An agent SDK must never be pointed straight at an unprotected model server.
Doing so would bypass admission, the reservation, the tokenizer-based budget
check and the watchdog gate -- i.e. exactly the controls whose absence caused
the FORGE-003 Metal OOM incident.

This gateway is the **only** supported endpoint an agent runtime may be
configured with. It accepts an OpenAI-shaped ``/v1/chat/completions`` payload,
routes it through :class:`~services.resource_controller.protected.ProtectedServer`,
and returns an OpenAI-shaped response.

Contract enforced here:

* The endpoint binds **loopback only** -- never ``0.0.0.0``.
* ``max_tokens`` in the payload may not exceed the policy's reserved output
  budget. A larger value is **rejected**, never silently clamped.
* ``stream=True`` is refused: streaming is not implemented on the protected
  path, and pretending otherwise would be worse than saying no.
* The request is served **single-threaded**, matching
  ``max_concurrent_requests = 1``.
* A rejected request returns a structured error and is **not** forwarded.

Tested with a fake forwarder and synthetic telemetry; no model is required to
exercise the adapter.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional

from .outcomes import Outcome
from .protected import ProtectedResult, ProtectedServer

LOOPBACK = "127.0.0.1"
MAX_BODY_BYTES = 4 * 1024 * 1024


class GatewayError(ValueError):
    """Raised for a malformed or policy-violating gateway request."""


def _openai_error(message: str, status: int, code: str) -> dict:
    return {
        "error": {"message": message, "type": code, "code": code, "status": status}
    }


class ProtectedGateway:
    """Translates OpenAI-shaped requests onto the protected execution path."""

    def __init__(self, server: ProtectedServer) -> None:
        self._server = server

    @property
    def server(self) -> ProtectedServer:
        return self._server

    # ------------------------------------------------------------------
    def handle_chat_completion(self, payload: Mapping[str, Any]) -> tuple:
        """Return ``(status_code, body_dict)``. Never forwards a rejected request."""
        if not isinstance(payload, Mapping):
            raise GatewayError("payload must be a JSON object")

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise GatewayError("'messages' must be a non-empty list")

        if payload.get("stream"):
            raise GatewayError(
                "stream=True is not supported on the protected path; "
                "requesting it would imply an unverified streaming contract"
            )

        policy = self._server._controller.policy  # noqa: SLF001 - same package
        if policy is None:
            raise GatewayError("no ResourcePolicy configured; refusing to serve")

        requested = payload.get("max_tokens", policy.reserved_output_tokens)
        if not isinstance(requested, int) or requested <= 0:
            raise GatewayError("'max_tokens' must be a positive integer")
        if requested > policy.reserved_output_tokens:
            raise GatewayError(
                f"max_tokens {requested} exceeds the reserved output budget "
                f"{policy.reserved_output_tokens}; refusing rather than clamping"
            )

        tools = payload.get("tools")

        result: ProtectedResult = self._server.request(
            messages, requested, tools=tools, label="gateway"
        )

        if not result.admitted:
            return self._status_for(result), _openai_error(
                result.reason, self._status_for(result), result.outcome.value
            )

        choice = (result.response or {}).get("choices", [{}])[0]
        return 200, {
            "id": f"forgeone-{result.outcome.value.lower()}",
            "object": "chat.completion",
            "model": payload.get("model", "forgeone-protected"),
            "choices": [
                {
                    "index": 0,
                    "message": choice.get("message", {"role": "assistant", "content": ""}),
                    "finish_reason": choice.get("finish_reason", "stop"),
                }
            ],
            "forgeone": {
                "outcome": result.outcome.value,
                "input_tokens": result.decision.input_tokens if result.decision else None,
                "reason": result.reason,
            },
        }

    @staticmethod
    def _status_for(result: ProtectedResult) -> int:
        return {
            Outcome.REJECTED_CONTEXT: 400,
            Outcome.REJECTED_MEMORY: 503,
            Outcome.REJECTED_CONCURRENCY: 429,
            Outcome.REJECTED_UNVERIFIED_ESTIMATE: 503,
            Outcome.PROCESS_FAILED: 502,
        }.get(result.outcome, 500)


# ---------------------------------------------------------------------------
# Minimal loopback HTTP wrapper
# ---------------------------------------------------------------------------
def make_handler(gateway: ProtectedGateway):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ForgeOneProtectedGateway/1.0"

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._send(200, {"object": "list", "data": [
                    {"id": "forgeone-protected", "object": "model", "owned_by": "forgeone"}
                ]})
            elif self.path.rstrip("/") in ("/health", ""):
                self._send(200, {"status": "ok", "protected": True})
            else:
                self._send(404, _openai_error("not found", 404, "not_found"))

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._send(404, _openai_error("not found", 404, "not_found"))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, _openai_error("bad Content-Length", 400, "bad_request"))
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send(413, _openai_error("body missing or too large", 413, "bad_request"))
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:
                self._send(400, _openai_error(f"invalid JSON: {exc}", 400, "bad_request"))
                return
            try:
                status, body = gateway.handle_chat_completion(payload)
            except GatewayError as exc:
                self._send(400, _openai_error(str(exc), 400, "bad_request"))
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._send(500, _openai_error(f"{type(exc).__name__}: {exc}", 500, "internal"))
                return
            self._send(status, body)

        def log_message(self, *args):  # silence default stderr logging
            return

    return Handler


class GatewayHTTPServer:
    """Loopback-only, single-threaded HTTP listener in front of the gateway."""

    def __init__(self, gateway: ProtectedGateway, port: int, host: str = LOOPBACK) -> None:
        if host != LOOPBACK:
            raise GatewayError(
                f"gateway must bind {LOOPBACK}; refusing to bind {host!r}"
            )
        self._gateway = gateway
        self._httpd: Optional[ThreadingHTTPServer] = None
        self.port = port
        self.host = host
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self._httpd is not None:
            raise GatewayError("gateway already running")
        # ThreadingHTTPServer is used only so a slow client cannot block accept();
        # the protected path itself still admits exactly one request at a time.
        self._httpd = ThreadingHTTPServer((self.host, self.port), make_handler(self._gateway))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
