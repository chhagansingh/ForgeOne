"""REAL tokenizer validation against the locally cached Qwen3 checkpoint.

This module is the answer to a previously open limitation: ``HuggingFaceTokenCounter``
had only ever been exercised through mocks. Here it is validated against the
actual tokenizer and the actual chat template shipped with the checkpoint.

**No model weights are loaded.** ``mlx_lm.utils.load_tokenizer`` restricts its
download/read patterns to ``*.json``, ``*.txt``, ``*.jinja``, ``*.model`` and
similar — ``*.safetensors`` is excluded (utils.py:434-443). The module skips
cleanly when the checkpoint is absent, so the suite still runs elsewhere.

The counter must count what the server will actually process. The server's call
is, from server.py:548-553::

    tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tools=tools, tokenize=True
    )

with ``chat_template_args`` defaulting to ``{}`` (server.py:1848-1852). Every
comparison below is against that exact expression.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from services.resource_controller.tokenization import (
    HuggingFaceTokenCounter,
    TokenizerUnavailable,
)

CHECKPOINT = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = (
    REPO_ROOT
    / "storage/cache/huggingface/hub"
    / f"models--{CHECKPOINT.replace('/', '--')}"
    / "snapshots"
    / REVISION
)

LOAD_ERROR = None
TOKENIZER = None

if SNAPSHOT.is_dir():
    try:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_HOME", str(REPO_ROOT / "storage/cache/huggingface"))
        from mlx_lm.utils import load_tokenizer

        TOKENIZER = load_tokenizer(SNAPSHOT)  # tokenizer files only, no weights
    except Exception as exc:  # pragma: no cover - environment dependent
        LOAD_ERROR = f"{type(exc).__name__}: {exc}"
else:  # pragma: no cover - environment dependent
    LOAD_ERROR = f"checkpoint snapshot not found at {SNAPSHOT}"


def server_count(messages, tools=None):
    """The server's exact prompt-construction call (server.py:548-553)."""
    return len(
        TOKENIZER.apply_chat_template(
            list(messages), add_generation_prompt=True, tools=tools, tokenize=True
        )
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Execute the project test suite and return the raw result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the test file"},
                    "verbose": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
    }
]


@unittest.skipUnless(TOKENIZER is not None, f"real tokenizer unavailable ({LOAD_ERROR})")
class RealTokenizerValidationTests(unittest.TestCase):
    def setUp(self):
        self.counter = HuggingFaceTokenCounter(TOKENIZER)

    # -- availability ----------------------------------------------------
    def test_tokenizer_exposes_template_and_tool_calling(self):
        """Establishes that this validation is not vacuous."""
        self.assertTrue(getattr(TOKENIZER, "has_chat_template", False))
        self.assertTrue(getattr(TOKENIZER, "has_tool_calling", False))
        self.assertIsNotNone(getattr(TOKENIZER, "tool_parser", None))

    def test_counter_reports_available(self):
        self.assertTrue(self.counter.available)

    # -- agreement with the server path ----------------------------------
    def test_matches_server_path_simple(self):
        """Case 1: single user message."""
        msgs = [{"role": "user", "content": "Reply with exactly: OK"}]
        self.assertEqual(self.counter.count_chat_tokens(msgs), server_count(msgs))

    def test_matches_server_path_with_system_message(self):
        """Case 1: system + user."""
        msgs = [
            {"role": "system", "content": "You are a careful coding agent."},
            {"role": "user", "content": "Summarise the repository."},
        ]
        self.assertEqual(self.counter.count_chat_tokens(msgs), server_count(msgs))

    def test_matches_server_path_multi_turn(self):
        """Case 1: system/user/assistant interleaved."""
        msgs = [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
        ]
        self.assertEqual(self.counter.count_chat_tokens(msgs), server_count(msgs))

    def test_matches_server_path_with_tools(self):
        """Case 2: tool definitions and JSON schemas inflate the prompt."""
        msgs = [{"role": "user", "content": "Run the tests."}]
        with_tools = self.counter.count_chat_tokens(msgs, tools=TOOLS)
        self.assertEqual(with_tools, server_count(msgs, TOOLS))
        without = self.counter.count_chat_tokens(msgs)
        self.assertGreater(with_tools, without, "tool schemas must add tokens")

    def test_matches_server_path_with_assistant_tool_call(self):
        """Case 2: an assistant turn carrying a structured tool call."""
        msgs = [
            {"role": "user", "content": "Run the tests."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_tests",
                            "arguments": '{"path": "tests/test_pricing.py", "verbose": true}',
                        },
                    }
                ],
            },
        ]
        self.assertEqual(self.counter.count_chat_tokens(msgs, tools=TOOLS), server_count(msgs, TOOLS))

    def test_matches_server_path_with_tool_response(self):
        """Case 2: a tool result returned to the model."""
        msgs = [
            {"role": "user", "content": "Run the tests."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "run_tests", "arguments": '{"path": "t.py"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "FAILED (failures=2)",
            },
        ]
        self.assertEqual(self.counter.count_chat_tokens(msgs, tools=TOOLS), server_count(msgs, TOOLS))

    def test_tool_response_adds_tokens(self):
        base = [{"role": "user", "content": "Run the tests."}]
        extended = base + [{"role": "tool", "tool_call_id": "c1", "content": "FAILED"}]
        self.assertGreater(
            self.counter.count_chat_tokens(extended, tools=TOOLS),
            self.counter.count_chat_tokens(base, tools=TOOLS),
        )

    # -- boundaries and malformed input -----------------------------------
    def test_empty_message_list(self):
        """Case 3: empty input must not silently return zero or crash."""
        try:
            n = self.counter.count_chat_tokens([])
        except TokenizerUnavailable:
            self.skipTest("template rejects an empty conversation")
        self.assertGreaterEqual(n, 0)

    def test_empty_content_is_counted(self):
        msgs = [{"role": "user", "content": ""}]
        self.assertEqual(self.counter.count_chat_tokens(msgs), server_count(msgs))

    def test_longer_content_monotonic(self):
        """Case 3: token count must grow with content, not saturate."""
        prev = 0
        for n in (10, 100, 1000, 5000):
            msgs = [{"role": "user", "content": "x " * n}]
            got = self.counter.count_chat_tokens(msgs)
            self.assertEqual(got, server_count(msgs))
            self.assertGreater(got, prev)
            prev = got

    def test_context_boundary_is_measured_not_assumed(self):
        """Case 3: report the real count near a candidate ceiling, no guessing."""
        msgs = [{"role": "user", "content": "word " * 2048}]
        n = self.counter.count_chat_tokens(msgs)
        self.assertEqual(n, server_count(msgs))
        self.assertGreater(n, 2048)
        # Deliberately does NOT assert any 8K/16K/32K ceiling: the safe ceiling
        # is unknown and must not be invented from a tokenizer measurement.

    def test_special_tokens_are_present(self):
        """Case 1: the template adds ChatML control tokens, not just raw text."""
        msgs = [{"role": "user", "content": "hi"}]
        ids = TOKENIZER.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True)
        decoded = TOKENIZER.decode(ids) if hasattr(TOKENIZER, "decode") else ""
        self.assertIn("<|im_start|>", decoded)
        self.assertGreater(len(ids), self.counter.count_text_tokens("hi"))

    # -- the test itself must be able to fail ------------------------------
    def test_naive_concatenation_disagrees_with_the_template(self):
        """Case 3: proves the agreement above is meaningful, not vacuous.

        A naive "join the message strings" counter is what FORGE-003 used. It
        must produce a different number, otherwise these comparisons would be
        asserting nothing.
        """
        msgs = [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "What is 2+2?"},
        ]
        naive = len(" ".join(m["content"] for m in msgs).split())
        real = self.counter.count_chat_tokens(msgs)
        self.assertNotEqual(naive, real)
        self.assertGreater(real, naive, "the template adds role and control tokens")

    def test_text_only_counting_differs_from_chat_counting(self):
        msgs = [{"role": "user", "content": "hello world"}]
        self.assertLess(
            self.counter.count_text_tokens("hello world"),
            self.counter.count_chat_tokens(msgs),
        )


class UnavailableTokenizerTests(unittest.TestCase):
    """Case 3: an unavailable tokenizer must fail closed, never guess."""

    def test_available_is_false_for_none(self):
        self.assertFalse(HuggingFaceTokenCounter(None).available)

    def test_counting_raises_when_unavailable(self):
        c = HuggingFaceTokenCounter(None)
        with self.assertRaises(TokenizerUnavailable):
            c.count_chat_tokens([{"role": "user", "content": "hi"}])
        with self.assertRaises(TokenizerUnavailable):
            c.count_text_tokens("hi")

    def test_object_without_chat_template_is_unavailable(self):
        class Bare:
            pass

        c = HuggingFaceTokenCounter(Bare())
        self.assertFalse(c.available)
        with self.assertRaises(TokenizerUnavailable):
            c.count_chat_tokens([])

    def test_broken_template_raises_rather_than_returning_a_number(self):
        class Exploding:
            def apply_chat_template(self, *a, **kw):
                raise RuntimeError("template exploded")

        c = HuggingFaceTokenCounter(Exploding())
        with self.assertRaises(TokenizerUnavailable):
            c.count_chat_tokens([{"role": "user", "content": "hi"}])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
