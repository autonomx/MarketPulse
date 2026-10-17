"""Allowlisted Pi methodology resources used by Omnix.

Skill discovery remains disabled. Coding/reviewer runs receive only these explicit
repository-owned skill paths; Pi's native skill loader supplies the methodology
without turning it into Omnix execution authority. The skills never grant tools,
capabilities, resource scopes, approvals, or completion authority.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


_SKILL_ROOT = Path(__file__).with_name("pi_skills")
_PROFILE_SKILLS = {
    "coding": ("engineering/SKILL.md",),
    "coding-reviewer": ("review/SKILL.md",),
}


def trusted_skill_paths(*, profile: str) -> tuple[Path, ...]:
    """Return the fixed allowlist of native Pi skills for one profile."""

    return tuple((_SKILL_ROOT / relative).resolve() for relative in _PROFILE_SKILLS.get(profile, ()))


def compile_coding_skills(*, profile: str) -> tuple[str, str]:
    """Return observable trusted-skill text/digest without making it authority.

    Pi receives these files through explicit ``--skill`` flags. The compiled text
    is retained for diagnostics/digests and reviewer provenance, not reinjected as
    a second bespoke agent workflow.
    """

    paths = trusted_skill_paths(profile=profile)
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            # Missing trusted resources should be visible in the digest/prompt
            # metadata; the runtime argv tests also fail if a configured path is
            # absent in the source tree.
            chunks.append(f"[missing trusted skill: {path}]")
    text = "\n\n".join(chunks)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def skill_ids() -> tuple[str, ...]:
    return tuple(path.parent.name for path in trusted_skill_paths(profile="coding"))
