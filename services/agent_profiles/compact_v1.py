"""FORGEONE_COMPACT_V1 — a bounded ForgeOne integration profile.

Why this exists
---------------
The stock OpenHands system prompt alone measured **2,318 tokens** against an
approved total context of **2,048** — before any tool schema. That is a property
of the *budget*, not a defect in OpenHands.

This profile reduces the request footprint so a **limited** coding workflow can
fit a smaller endpoint. It reduces cost in exactly two ways, both explicit:

1. **A narrower tool set.** Only the tools the fixture task actually needs.
2. **A shorter system prompt**, supplied through the SDK's supported
   ``Agent(system_prompt=...)`` mechanism.

What it does **NOT** do
-----------------------
It does **not** achieve reduction by removing safeguards. Every mandatory
safeguard is restated in the compact prompt and asserted by tests. The upstream
package is not patched, forked or monkey-patched.

Scope
-----
This is a **limited ForgeOne integration profile**, not a benchmark of stock
OpenHands. A measurement taken with it must never be reported as a stock
OpenHands result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple  # noqa: F401

PROFILE_ID = "FORGEONE_COMPACT_V1"


class ProfileError(ValueError):
    """Raised when a profile is unknown or malformed."""


#: Safeguards that MUST hold in every ForgeOne profile. Tests assert each one
#: appears in the rendered prompt, so a token-saving edit cannot quietly drop it.
SAFEGUARDS: Tuple[str, ...] = (
    "workspace_confinement",
    "no_credential_inspection",
    "no_private_repository_access",
    "no_unapproved_network_calls",
    "no_destructive_operations",
    "explicit_permissions",
    "real_test_execution",
    "honest_failure_reporting",
    "resource_controller_enforcement",
    "no_direct_model_endpoint_access",
)

#: Phrases that must appear verbatim in the compact prompt for each safeguard.
SAFEGUARD_MARKERS: Mapping[str, str] = {
    "workspace_confinement": "Only work inside the workspace directory",
    "no_credential_inspection": "Never read, print or search for credentials",
    "no_private_repository_access": "Do not access any other repository",
    "no_unapproved_network_calls": "Make no network calls",
    "no_destructive_operations": "No destructive commands",
    "explicit_permissions": "Ask before anything outside these permissions",
    "real_test_execution": "Run the real test command",
    "honest_failure_reporting": "Report failures exactly as observed",
    "resource_controller_enforcement": "All model access goes through the ForgeOne gateway",
    "no_direct_model_endpoint_access": "Never call a model endpoint directly",
}

COMPACT_SYSTEM_PROMPT = """You are a coding agent working inside a confined workspace.

Capabilities
- terminal: run shell commands in the workspace.
- file_editor: view, create and edit files in the workspace.

Task loop
1. Read the relevant files.
2. Identify the defect.
3. Apply the smallest correct fix.
4. Run the real test command.
5. Inspect the resulting diff.
6. Report what actually happened.

Hard limits
- Only work inside the workspace directory. Never touch paths outside it.
- Never read, print or search for credentials, keys, tokens or .env files.
- Do not access any other repository, and never a customer or private one.
- Make no network calls and install nothing.
- No destructive commands: no rm -rf, no force-push, no history rewrite.
- Ask before anything outside these permissions; if unsure, stop and report.
- All model access goes through the ForgeOne gateway. Never call a model endpoint directly.

Evidence
- Run the real test command and quote its actual output.
- Report failures exactly as observed. Never claim a test passed unless you
  ran it and saw it pass. If something was not run, say NOT_RUN.
"""


#: LLM settings a ForgeOne profile MUST apply, derived from SDK source
#: inspection (``openhands/sdk/llm/llm.py``) rather than assumption.
#:
#: ==========================  ==============  ===============================
#: SDK field                   SDK default     ForgeOne requirement
#: ==========================  ==============  ===============================
#: ``api_mode``                ``"auto"``      ``"chat"`` -- force Chat
#:                                             Completions; "auto" can resolve
#:                                             to the Responses API, which the
#:                                             gateway does not implement.
#: ``stream``                  ``False``       ``False`` -- already compatible;
#:                                             no streaming adapter needed.
#: ``num_retries``             ``5``           ``0`` -- the milestone forbids
#:                                             automatic retry after a resource
#:                                             rejection. 5 would silently
#:                                             retry five times.
#: ``timeout``                 ``300``         policy ``request_timeout_s``
#: ``max_output_tokens``       ``None``        ``<= reserved_output_tokens``
#: ==========================  ==============  ===============================
FORGEONE_LLM_KWARGS = {
    "api_mode": "chat",
    "stream": False,
    "num_retries": 0,
}


class LLMConfigError(ValueError):
    """Raised when an LLM configuration would violate a ForgeOne requirement."""


def validate_llm_kwargs(kwargs: Mapping[str, object]) -> None:
    """Fail closed on any LLM setting that breaks a ForgeOne requirement.

    Called before an agent is constructed, so an unsafe default cannot slip
    through: ``num_retries`` in particular defaults to 5 upstream.
    """
    if kwargs.get("stream") is True:
        raise LLMConfigError(
            "stream=True is not supported by the protected gateway; "
            "the SDK already defaults to stream=False"
        )
    if kwargs.get("api_mode", "chat") != "chat":
        raise LLMConfigError(
            f"api_mode must be 'chat' (got {kwargs.get('api_mode')!r}); "
            "'auto' can resolve to the Responses API, which the gateway "
            "does not implement"
        )
    retries = kwargs.get("num_retries", 5)
    if retries != 0:
        raise LLMConfigError(
            f"num_retries must be 0 (got {retries!r}); automatic retry after a "
            "resource rejection is forbidden"
        )
    timeout = kwargs.get("timeout")
    if timeout is None or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise LLMConfigError("timeout must be a positive number of seconds")
    out = kwargs.get("max_output_tokens")
    if out is None or not isinstance(out, int) or out <= 0:
        raise LLMConfigError("max_output_tokens must be a positive integer")


@dataclass(frozen=True)
class AgentProfile:
    """A ForgeOne-owned agent configuration."""

    profile_id: str
    version: str
    tool_names: Tuple[str, ...]
    system_prompt: str
    safeguards: Tuple[str, ...]
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ProfileError("profile_id is required")
        if not self.tool_names:
            raise ProfileError("at least one tool is required")
        if not self.system_prompt.strip():
            raise ProfileError("system_prompt must not be empty")

        missing = [s for s in SAFEGUARDS if s not in self.safeguards]
        if missing:
            raise ProfileError(f"profile is missing mandatory safeguards: {missing}")

        absent = [
            s for s in self.safeguards
            if SAFEGUARD_MARKERS.get(s, "") and SAFEGUARD_MARKERS[s] not in self.system_prompt
        ]
        if absent:
            raise ProfileError(
                f"system prompt does not restate safeguard(s): {absent}"
            )

    def as_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "tool_names": list(self.tool_names),
            "system_prompt_chars": len(self.system_prompt),
            "safeguards": list(self.safeguards),
            "notes": self.notes,
        }


FORGEONE_COMPACT_V1 = AgentProfile(
    profile_id=PROFILE_ID,
    version="1.0.0",
    # Only the tools this fixture task genuinely needs. task_tracker is the
    # single largest schema (6,324 chars) and is not required to read, edit,
    # test, diff and report.
    tool_names=("terminal", "file_editor"),
    system_prompt=COMPACT_SYSTEM_PROMPT,
    safeguards=SAFEGUARDS,
    notes=(
        "Limited ForgeOne integration profile. Narrower tool set and a shorter "
        "prompt; no safeguard removed. Not a benchmark of stock OpenHands."
    ),
)

_REGISTRY = {FORGEONE_COMPACT_V1.profile_id: FORGEONE_COMPACT_V1}


def get_profile(profile_id: str) -> AgentProfile:
    try:
        return _REGISTRY[profile_id]
    except KeyError:
        raise ProfileError(
            f"unknown profile {profile_id!r}; known: {sorted(_REGISTRY)}"
        ) from None


def list_profiles() -> Tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
