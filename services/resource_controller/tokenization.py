"""Token counting for admission control.

The controller must count *actual* tokens using the selected tokenizer **and the
complete chat template** -- not an approximation. During FORGE-003 the agent
estimated token counts from repeated filler text and was wrong by ~2x, because
BPE merges across repetition boundaries make growth sublinear. That mistake
meant a request intended to be 64K tokens actually reached only 32.6K and still
crashed the host.

Counting is injected, never performed by the controller itself, so admission
logic is unit-testable without importing or loading any model.
"""

from __future__ import annotations

import abc
from typing import Any, Mapping, Optional, Sequence


class TokenizerUnavailable(RuntimeError):
    """Raised when tokens cannot be counted. Callers must fail closed."""


class TokenCounter(abc.ABC):
    """Counts tokens for a chat request including the applied chat template."""

    @property
    def available(self) -> bool:
        """False means admission must reject with REJECTED_UNVERIFIED_ESTIMATE."""
        return True

    @abc.abstractmethod
    def count_chat_tokens(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def count_text_tokens(self, text: str) -> int:
        raise NotImplementedError


class MockTokenCounter(TokenCounter):
    """Deterministic counter for tests.

    ``chars_per_token`` approximates a real tokenizer so fixtures stay readable.
    ``available=False`` simulates a missing tokenizer.
    """

    def __init__(self, chars_per_token: float = 4.0, available: bool = True) -> None:
        self._cpt = chars_per_token
        self._available = available
        self.chat_template_overhead = 0

    @property
    def available(self) -> bool:
        return self._available

    def count_text_tokens(self, text: str) -> int:
        if not self._available:
            raise TokenizerUnavailable("mock tokenizer marked unavailable")
        return max(1, int(len(text) / self._cpt))

    def count_chat_tokens(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> int:
        if not self._available:
            raise TokenizerUnavailable("mock tokenizer marked unavailable")
        total = self.chat_template_overhead
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, str):
                total += self.count_text_tokens(content)
            else:
                total += 1
            total += 4  # role + separators
        if tools:
            total += 8 * len(tools)
        return total


class HuggingFaceTokenCounter(TokenCounter):
    """Adapter over a Hugging Face tokenizer that supports ``apply_chat_template``.

    The tokenizer object is **injected**; this class never loads one. It is
    deliberately not exercised against the FORGE-003 checkpoint in this
    milestone -- see the limitations section of the FORGE-003 bake-off report.
    """

    def __init__(self, tokenizer: Any) -> None:
        self._tok = tokenizer

    @property
    def available(self) -> bool:
        return self._tok is not None and hasattr(self._tok, "apply_chat_template")

    def count_chat_tokens(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> int:
        if not self.available:
            raise TokenizerUnavailable("tokenizer does not support apply_chat_template")
        try:
            kwargs: dict[str, Any] = {"add_generation_prompt": True, "tokenize": True}
            if tools:
                kwargs["tools"] = list(tools)
            ids = self._tok.apply_chat_template(list(messages), **kwargs)
        except Exception as exc:
            raise TokenizerUnavailable(f"apply_chat_template failed: {exc}") from exc
        return len(ids)

    def count_text_tokens(self, text: str) -> int:
        if self._tok is None:
            raise TokenizerUnavailable("no tokenizer")
        try:
            return len(self._tok.encode(text))
        except Exception as exc:
            raise TokenizerUnavailable(f"encode failed: {exc}") from exc
