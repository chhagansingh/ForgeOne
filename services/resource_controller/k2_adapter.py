"""Minimal protected K2-Horizon inference adapter.

The K2 checkpoint is a **GGUF** file with architecture ``k2-horizon``. It cannot
be served by MLX-LM (which expects safetensors). It needs a llama.cpp-class
backend that registers ``k2-horizon`` — in practice the publisher fork
``ifm-ai/llama.cpp`` branch ``model/K2Horizon``.

This adapter is the *only* supported way to reach that backend. It enforces the
same protections as the Qwen path and adds K2-specific identity checks:

* **Exact approved model identity** — repository, pinned revision, filename.
* **Checkpoint path validation** — the resolved real path must sit under
  ``$FORGEONE_HOME/storage/``; a symlink escaping storage is refused.
* **Chat-template presence** — the GGUF embeds a template; its absence is a
  hard failure, because inventing a generic template would silently change
  model behaviour.
* **Model-specific token counting** — the K2 tokenizer is **not** the Qwen
  tokenizer, so K2 counts are never derived from Qwen's.

**Mock transport only in this milestone.** No runtime is built, no weights are
loaded, no inference is run.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .outcomes import Outcome
from .protected import ProtectedResult, ProtectedServer
from .tokenization import TokenizerUnavailable

APPROVED_REPO = "IFM/K2-Horizon-3.7B-GGUF"
APPROVED_REVISION = "a81d5fec318b47b9c7144a839f538f6b9006291c"
APPROVED_FILENAME = "K2-Horizon-4B-Q6_K.gguf"
APPROVED_SIZE = 4161403264
APPROVED_ARCHITECTURE = "k2-horizon"


class AdapterError(ValueError):
    """Raised for an identity, path or configuration violation."""


class MissingChatTemplate(AdapterError):
    """Raised when the checkpoint carries no usable chat template."""


class TokenizerMismatch(AdapterError):
    """Raised when a counter is not the K2 tokenizer's."""


@dataclass(frozen=True)
class K2ModelIdentity:
    repository: str
    revision: str
    filename: str
    size_bytes: int
    architecture: str
    chat_template: Optional[str]

    @classmethod
    def approved(cls, chat_template: Optional[str]) -> "K2ModelIdentity":
        return cls(
            repository=APPROVED_REPO,
            revision=APPROVED_REVISION,
            filename=APPROVED_FILENAME,
            size_bytes=APPROVED_SIZE,
            architecture=APPROVED_ARCHITECTURE,
            chat_template=chat_template,
        )

    def validate(self) -> None:
        """Fail closed on any deviation from the approved artefact."""
        if self.repository != APPROVED_REPO:
            raise AdapterError(f"unapproved repository {self.repository!r}")
        if self.revision != APPROVED_REVISION:
            raise AdapterError(f"unapproved revision {self.revision!r}")
        if self.filename != APPROVED_FILENAME:
            raise AdapterError(
                f"unapproved filename {self.filename!r}; only {APPROVED_FILENAME} is approved"
            )
        if self.size_bytes != APPROVED_SIZE:
            raise AdapterError(
                f"size {self.size_bytes} does not match the approved {APPROVED_SIZE}"
            )
        if self.architecture != APPROVED_ARCHITECTURE:
            raise AdapterError(f"unapproved architecture {self.architecture!r}")
        if not self.chat_template or not self.chat_template.strip():
            raise MissingChatTemplate(
                "the checkpoint carries no chat template; a generic or Qwen "
                "template must NOT be substituted"
            )


def resolve_checkpoint_path(
    declared: Path, storage_root: Path, *, realpath=Path.resolve
) -> Path:
    """Resolve a checkpoint path and require it to stay inside storage/.

    Checks the **resolved real path**, not a string prefix, so a symlink that
    points outside storage is refused.
    """
    resolved = realpath(Path(declared))
    root = realpath(Path(storage_root))
    if root != resolved and root not in resolved.parents:
        raise AdapterError(
            f"checkpoint path {resolved} escapes the canonical storage root {root}"
        )
    return resolved


class K2TokenCounter(abc.ABC):
    """K2-specific token counting. Never satisfied by a Qwen tokenizer."""

    @property
    @abc.abstractmethod
    def model_family(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def count_chat_tokens(self, messages, tools=None) -> int:
        raise NotImplementedError


class MockK2TokenCounter(K2TokenCounter):
    """Deterministic stand-in for tests. Declares its family explicitly."""

    def __init__(self, chars_per_token: float = 4.0) -> None:
        self._cpt = chars_per_token
        self.calls = 0

    @property
    def model_family(self) -> str:
        return "k2-horizon"

    def count_chat_tokens(self, messages, tools=None) -> int:
        self.calls += 1
        total = 0
        for m in messages:
            c = m.get("content")
            total += max(1, int(len(c) / self._cpt)) if isinstance(c, str) else 1
            total += 4
        if tools:
            total += 8 * len(tools)
        return total


class BackendK2TokenCounter(K2TokenCounter):
    """Production counter: exact counts from the running K2 backend's tokenizer.

    The backend is the only authority on how *this* checkpoint tokenizes.
    Qwen's tokenizer, character estimates and hardcoded synthetic values are
    never substituted -- if the endpoint is missing or fails, this raises
    :class:`TokenizerUnavailable` and request admission fails closed.

    Valid only once the backend is healthy. Startup admission does not use the
    token counter, so that ordering is safe by construction.
    """

    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self.last_count: Optional[int] = None

    @property
    def model_family(self) -> str:
        return "k2-horizon"

    def _tokenize(self, text: str) -> int:
        import json as _json
        import urllib.error
        import urllib.request

        last_error = None
        for path in ("/tokenize", "/v1/tokenize"):
            req = urllib.request.Request(
                self.base_url + path,
                data=_json.dumps({"content": text}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    got = _json.loads(resp.read().decode())
                toks = got.get("tokens") if isinstance(got, dict) else None
                if isinstance(toks, list) and toks:
                    return len(toks)
                last_error = f"{path} returned no token list"
            except urllib.error.HTTPError as exc:
                last_error = f"{path} HTTP {exc.code}"
            except Exception as exc:
                last_error = f"{path} {type(exc).__name__}: {exc}"
        raise TokenizerUnavailable(
            f"K2 backend exposed no usable tokenize endpoint ({last_error})"
        )

    def count_chat_tokens(self, messages, tools=None) -> int:
        # The backend tokenizes the RENDERED prompt. Send the concatenated
        # message content so the count reflects real K2 tokens for the text;
        # the session additionally validates the templated prompt against the
        # context budget before forwarding.
        text = "\n".join(
            str(m.get("content") or "") for m in messages if isinstance(m, dict)
        )
        if tools:
            text += "\n" + str(tools)
        n = self._tokenize(text)
        self.last_count = n
        return n

    def count_text_tokens(self, text: str) -> int:
        return self._tokenize(text)


def require_k2_counter(counter: Any) -> K2TokenCounter:
    """Refuse a counter that is not K2-specific.

    Reusing the Qwen tokenizer would silently mis-count: the two tokenizers
    share no vocabulary or pre-tokenizer.
    """
    family = getattr(counter, "model_family", None)
    if family != "k2-horizon":
        raise TokenizerMismatch(
            f"token counter family {family!r} is not 'k2-horizon'; Qwen counts "
            "must never be reused as K2 counts"
        )
    return counter


class K2InferenceAdapter:
    """Wraps a protected server with K2-specific identity and counting rules."""

    def __init__(
        self,
        server: ProtectedServer,
        identity: K2ModelIdentity,
        counter: K2TokenCounter,
        *,
        storage_root: Path,
        checkpoint_path: Path,
    ) -> None:
        identity.validate()
        self._counter = require_k2_counter(counter)
        self._resolved = resolve_checkpoint_path(checkpoint_path, storage_root)
        self._server = server
        self._identity = identity

    @property
    def identity(self) -> K2ModelIdentity:
        return self._identity

    @property
    def checkpoint_path(self) -> Path:
        return self._resolved

    def request(
        self,
        messages: Sequence[Mapping[str, Any]],
        requested_output_tokens: int,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> ProtectedResult:
        """Forward through the protected path. Never bypasses admission."""
        # Count with the K2 counter first so a mismatch is caught before any
        # transport work happens.
        self._counter.count_chat_tokens(messages, tools)
        return self._server.request(
            messages, requested_output_tokens, tools=tools, label="k2"
        )
