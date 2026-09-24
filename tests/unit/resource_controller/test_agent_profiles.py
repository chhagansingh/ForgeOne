"""FORGEONE_COMPACT_V1 profile tests.

Pure configuration validation — no model, no SDK, no inference. These run in any
interpreter, including the system Python.

The central guarantee under test: the compact profile reduces token cost by
*narrowing* the prompt and tool set, and **never** by dropping a safeguard.
"""

from __future__ import annotations

import unittest

from services.agent_profiles import (
    FORGEONE_COMPACT_V1,
    FORGEONE_LLM_KWARGS,
    SAFEGUARDS,
    SAFEGUARD_MARKERS,
    AgentProfile,
    LLMConfigError,
    ProfileError,
    get_profile,
    list_profiles,
    validate_llm_kwargs,
)


class ProfileSelectionTests(unittest.TestCase):
    def test_profile_is_registered_and_selectable(self):
        """Case 4: compact-profile selection."""
        self.assertIn("FORGEONE_COMPACT_V1", list_profiles())
        self.assertIs(get_profile("FORGEONE_COMPACT_V1"), FORGEONE_COMPACT_V1)

    def test_unknown_profile_raises(self):
        with self.assertRaises(ProfileError):
            get_profile("NOPE")

    def test_profile_is_explicitly_identified(self):
        self.assertEqual(FORGEONE_COMPACT_V1.profile_id, "FORGEONE_COMPACT_V1")
        self.assertTrue(FORGEONE_COMPACT_V1.version)
        self.assertIn("not a benchmark", FORGEONE_COMPACT_V1.notes.lower())

    def test_tool_set_is_narrowed_but_real(self):
        """Case 4: the tool set must still permit read/edit/test."""
        self.assertEqual(set(FORGEONE_COMPACT_V1.tool_names), {"terminal", "file_editor"})
        self.assertNotIn("task_tracker", FORGEONE_COMPACT_V1.tool_names)


class SafeguardPreservationTests(unittest.TestCase):
    def test_every_safeguard_is_declared(self):
        """Case 5: mandatory safety instructions preserved."""
        self.assertEqual(set(FORGEONE_COMPACT_V1.safeguards), set(SAFEGUARDS))

    def test_every_safeguard_appears_in_the_prompt(self):
        """Case 5: a token-saving edit must not silently drop a control."""
        for safeguard, marker in SAFEGUARD_MARKERS.items():
            with self.subTest(safeguard=safeguard):
                self.assertIn(marker, FORGEONE_COMPACT_V1.system_prompt)

    def test_constructing_a_profile_without_a_safeguard_fails(self):
        with self.assertRaises(ProfileError) as ctx:
            AgentProfile(
                profile_id="BROKEN", version="1.0.0",
                tool_names=("terminal",),
                system_prompt=FORGEONE_COMPACT_V1.system_prompt,
                safeguards=tuple(s for s in SAFEGUARDS if s != "no_destructive_operations"),
            )
        self.assertIn("missing mandatory safeguards", str(ctx.exception))

    def test_constructing_a_profile_with_a_dropped_prompt_marker_fails(self):
        """The prompt itself must restate each safeguard, not just the list."""
        with self.assertRaises(ProfileError) as ctx:
            AgentProfile(
                profile_id="BROKEN", version="1.0.0", tool_names=("terminal",),
                system_prompt="Do the task.", safeguards=SAFEGUARDS,
            )
        self.assertIn("does not restate safeguard", str(ctx.exception))

    def test_prompt_requires_evidence_and_honest_failure(self):
        p = FORGEONE_COMPACT_V1.system_prompt
        self.assertIn("Run the real test command", p)
        self.assertIn("Report failures exactly as observed", p)
        self.assertIn("NOT_RUN", p)

    def test_empty_tool_set_rejected(self):
        with self.assertRaises(ProfileError):
            AgentProfile(profile_id="X", version="1", tool_names=(),
                         system_prompt="x", safeguards=SAFEGUARDS)

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ProfileError):
            AgentProfile(profile_id="X", version="1", tool_names=("terminal",),
                         system_prompt="   ", safeguards=SAFEGUARDS)


class LLMConfigTests(unittest.TestCase):
    def safe(self, **kw):
        base = {"api_mode": "chat", "stream": False, "num_retries": 0,
                "timeout": 120, "max_output_tokens": 128}
        base.update(kw)
        return base

    def test_documented_defaults_are_the_safe_values(self):
        """Case 10: streaming/non-streaming compatibility with the gateway."""
        self.assertEqual(FORGEONE_LLM_KWARGS["stream"], False)
        self.assertEqual(FORGEONE_LLM_KWARGS["api_mode"], "chat")
        self.assertEqual(FORGEONE_LLM_KWARGS["num_retries"], 0)

    def test_safe_config_passes(self):
        validate_llm_kwargs(self.safe())

    def test_streaming_rejected(self):
        """Case 10: the gateway refuses stream=True; the config must not ask."""
        with self.assertRaises(LLMConfigError) as ctx:
            validate_llm_kwargs(self.safe(stream=True))
        self.assertIn("stream=True is not supported", str(ctx.exception))

    def test_api_mode_auto_rejected(self):
        with self.assertRaises(LLMConfigError) as ctx:
            validate_llm_kwargs(self.safe(api_mode="auto"))
        self.assertIn("api_mode must be 'chat'", str(ctx.exception))

    def test_api_mode_responses_rejected(self):
        with self.assertRaises(LLMConfigError):
            validate_llm_kwargs(self.safe(api_mode="responses"))

    def test_upstream_default_retries_rejected(self):
        """Case 10: the SDK defaults num_retries=5; that must not pass."""
        with self.assertRaises(LLMConfigError) as ctx:
            validate_llm_kwargs(self.safe(num_retries=5))
        self.assertIn("num_retries must be 0", str(ctx.exception))

    def test_missing_retries_defaults_to_unsafe_and_is_rejected(self):
        kw = self.safe()
        del kw["num_retries"]
        with self.assertRaises(LLMConfigError):
            validate_llm_kwargs(kw)

    def test_missing_timeout_rejected(self):
        kw = self.safe()
        del kw["timeout"]
        with self.assertRaises(LLMConfigError):
            validate_llm_kwargs(kw)

    def test_missing_max_output_tokens_rejected(self):
        """Case 6: complete input + output reservation must be explicit."""
        kw = self.safe()
        del kw["max_output_tokens"]
        with self.assertRaises(LLMConfigError):
            validate_llm_kwargs(kw)

    def test_negative_output_tokens_rejected(self):
        with self.assertRaises(LLMConfigError):
            validate_llm_kwargs(self.safe(max_output_tokens=-1))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
