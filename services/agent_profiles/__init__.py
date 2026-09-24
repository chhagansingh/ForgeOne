"""ForgeOne agent integration profiles.

A *profile* is a ForgeOne-owned configuration for an agent SDK: which tools it
gets, what system prompt it uses, and which safeguards must hold. Profiles are
identified explicitly so that a measurement is never mistaken for stock
upstream behaviour.

**Profiles do not modify the upstream package.** They use the SDK's supported
customization mechanisms — ``Agent(system_prompt=...)`` for the prompt and
``Agent(tools=[...])`` for the tool set.

See :mod:`services.agent_profiles.compact_v1` for ``FORGEONE_COMPACT_V1``.
"""

from __future__ import annotations

from .compact_v1 import (
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

__all__ = [
    "FORGEONE_COMPACT_V1",
    "FORGEONE_LLM_KWARGS",
    "SAFEGUARDS",
    "SAFEGUARD_MARKERS",
    "AgentProfile",
    "LLMConfigError",
    "ProfileError",
    "get_profile",
    "list_profiles",
    "validate_llm_kwargs",
]
