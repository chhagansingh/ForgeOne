"""Real HTTP transport for the protected request path.

``RequestForwarder`` was designed as an injected transport
(``protected.py:75``) with only a test double shipped. This module supplies the
real implementation so the protected path can carry an actual request without
anything bypassing the controller.

Deliberate constraints:

* **Loopback only.** A non-loopback base URL is refused at construction, so the
  transport cannot be pointed at a remote host by accident or by a config typo.
* **Standard library only** (``urllib``). No new dependency is introduced.
* **No retries.** A failed forward raises; the controller records
  ``PROCESS_FAILED``. Retrying after an abnormal termination is forbidden.
* The response must contain ``choices``; a bare HTTP 200 is not treated as a
  successful completion.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional, Sequence

from .protected import RequestForwarder

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class TransportError(RuntimeError):
    """Raised when a forward fails. Never retried automatically."""


class NonLoopbackEndpoint(ValueError):
    """Raised when the endpoint is not loopback."""


class HttpRequestForwarder(RequestForwarder):
    """POSTs a chat-completion request to a local OpenAI-compatible server."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        opener: Optional[Any] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.hostname not in LOOPBACK_HOSTS:
            raise NonLoopbackEndpoint(
                f"endpoint host {parsed.hostname!r} is not loopback; "
                f"only {sorted(LOOPBACK_HOSTS)} are permitted"
            )
        if parsed.scheme not in ("http", "https"):
            raise NonLoopbackEndpoint(f"unsupported scheme {parsed.scheme!r}")

        self.base_url = base_url.rstrip("/")
        self.model = model
        self._opener = opener or urllib.request.build_opener()
        self._extra_body = dict(extra_body or {})

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: Mapping[str, Any], timeout_s: float) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", "replace")
                status = getattr(resp, "status", None) or resp.getcode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise TransportError(f"HTTP {exc.code} from server: {detail}") from exc
        except Exception as exc:  # connection refused, timeout, ...
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TransportError(f"server returned non-JSON (HTTP {status}): {body[:200]}") from exc

        if not isinstance(parsed, dict) or "choices" not in parsed:
            raise TransportError(
                f"server response lacks 'choices' (HTTP {status}); "
                f"a 200 alone is not a completion"
            )
        return parsed

    def get_models(self, timeout_s: float = 10.0) -> dict:
        """Readiness probe. Used to derive resident state from real server state."""
        req = urllib.request.Request(self.base_url + "/v1/models", method="GET")
        with self._opener.open(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    # ------------------------------------------------------------------
    def forward(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]],
        max_tokens: int,
        timeout_s: float,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": int(max_tokens),
            "temperature": 0.0,
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        payload.update(self._extra_body)

        parsed = self._post("/v1/chat/completions", payload, timeout_s)
        if not parsed.get("choices"):
            raise TransportError("server returned an empty choices array")
        return parsed
