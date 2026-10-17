"""Minimum external authority compiler for coding-specific providers.

Natural-language meaning comes from SemanticTask. Omnix deterministically maps
that untrusted semantic description to authority bounded by the coding profile.
Text matching remains only as a compatibility fallback for legacy/internal
callers that do not have SemanticTask v2 output.
"""
from __future__ import annotations

import re
from typing import Iterable

from app.capabilities import browser_capability_ids
from app.capabilities.mcp_policy import infer_mcp_capabilities_for_task

_BROWSER_EXPLICIT = re.compile(
    r"\b(?:agent[- ]browser|browser\.(?:assert_[a-z_]+|(?:open|snapshot|screenshot|get_text|get_attribute))|"
    r"browser\s+(?:test|testing|automation|validation|verify|verification)|"
    r"e2e|end[- ]to[- ]end|playwright|visual\s+(?:test|testing|validation|regression)|"
    r"click\s+(?:through|the)|interact\s+with\s+(?:the\s+)?(?:page|ui|app))\b",
    re.I,
)
_UI_SURFACE = re.compile(
    r"\b(?:frontend|front[- ]end|ui|ux|web(?:\s+(?:app|page|screen))?|html|css|react|vue|typescript|tsx?|jsx?|"
    r"button|icon|element|component|layout|form|modal|dialog|dropdown|drop\s+down|menu|tab|side\s*bar|sidebar|"
    r"tool\s*bar|toolbar|header|footer|input|textarea|tooltip|badge|chip|theme|light\s+mode|dark\s+mode)\b",
    re.I,
)
_BROWSER_FORBIDDEN = re.compile(
    r"\b(?:do not|don't|never|without)\s+(?:use|open|run|launch)?\s*(?:the\s+)?"
    r"(?:browser|agent[- ]browser|playwright)\b",
    re.I,
)


def task_requires_browser_authority(
    task: str,
    *,
    semantic_workspace_surfaces: Iterable[str] | None = None,
    allow_text_semantic_fallback: bool = True,
) -> bool:
    """Return whether deterministic policy should issue governed browser authority.

    ``semantic_workspace_surfaces`` is untrusted semantic meaning, not authority.
    The caller still compiles the resulting browser capabilities against the
    coding profile ceiling. Raw-text classification is retained only for callers
    that explicitly opt into legacy compatibility behavior.
    """

    text = str(task or "")
    # Explicit user prohibition is a deny-only floor and may always narrow
    # authority regardless of semantic model output.
    if _BROWSER_FORBIDDEN.search(text):
        return False
    surfaces = {str(value).strip().casefold() for value in (semantic_workspace_surfaces or [])}
    if "web_ui" in surfaces:
        return True
    if not allow_text_semantic_fallback:
        return False
    return bool(_BROWSER_EXPLICIT.search(text) or _UI_SURFACE.search(text))


def coding_external_capabilities_for_task(
    task: str,
    *,
    semantic_workspace_surfaces: Iterable[str] | None = None,
    allow_text_semantic_fallback: bool = True,
) -> tuple[str, ...]:
    external: list[str] = []
    if task_requires_browser_authority(
        task,
        semantic_workspace_surfaces=semantic_workspace_surfaces,
        allow_text_semantic_fallback=allow_text_semantic_fallback,
    ):
        external.extend(browser_capability_ids())
    external.extend(infer_mcp_capabilities_for_task(task))
    return tuple(dict.fromkeys(external))
