"""Strict prompt renderer used by feature-specific prompt wrappers."""
from __future__ import annotations

import hashlib
import string
from typing import Any

from .models import PromptRenderRequest, PromptTemplate, RenderedPrompt


class PromptRenderError(ValueError):
    """Raised when a template cannot be rendered safely."""


class _StrictFormatMap(dict[str, Any]):
    def __missing__(self, key: str) -> Any:
        raise PromptRenderError(f"Missing prompt variable: {key}")


def _template_variables(text: str) -> list[str]:
    formatter = string.Formatter()
    variables: list[str] = []
    for _, field_name, _, _ in formatter.parse(text):
        if not field_name:
            continue
        root_name = field_name.split(".", 1)[0].split("[", 1)[0]
        if root_name and root_name not in variables:
            variables.append(root_name)
    return variables


def _render_hash(rendered_text: str) -> str:
    return hashlib.sha256(rendered_text.encode("utf-8")).hexdigest()[:16]


class PromptTemplateRenderer:
    """Render prompt templates without owning feature-specific semantics."""

    def render(self, request: PromptRenderRequest) -> RenderedPrompt:
        template = request.template
        expected = template.variables or _template_variables(template.text)
        missing = [name for name in expected if name not in request.variables]
        if missing:
            raise PromptRenderError(f"Missing prompt variables: {', '.join(missing)}")

        rendered = template.text.format_map(_StrictFormatMap(request.variables))
        return RenderedPrompt(
            template_id=template.id,
            version=template.version,
            module=template.module,
            rendered_text=rendered,
            variables={name: request.variables.get(name) for name in expected},
            provider_payload_format=template.provider_payload_format,
            rendering_metadata={
                "renderer": "PromptTemplateRenderer",
                "variable_names": expected,
                "rendered_hash": _render_hash(rendered),
            },
            safety_metadata=template.safety_metadata,
            grounding_metadata=template.grounding_metadata,
            replay_metadata={
                "template_id": template.id,
                "template_version": template.version,
                "rendered_hash": _render_hash(rendered),
            },
        )

    def render_template(self, template: PromptTemplate, variables: dict[str, Any]) -> RenderedPrompt:
        return self.render(PromptRenderRequest(template=template, variables=variables))
