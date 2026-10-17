"""Prompt/template models for shared rendering metadata."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


ProviderPayloadFormat = Literal["chat_messages", "completion_text", "image_prompt", "json_instruction"]


class PromptTemplate(BaseModel):
    id: str
    version: str
    module: str
    text: str
    variables: list[str] = Field(default_factory=list)
    provider_payload_format: ProviderPayloadFormat = "chat_messages"
    metadata: dict[str, Any] = Field(default_factory=dict)
    safety_metadata: dict[str, Any] = Field(default_factory=dict)
    grounding_metadata: dict[str, Any] = Field(default_factory=dict)

    def format(self, *args: Any, **kwargs: Any) -> str:
        """The text with ``str.format`` placeholders filled."""
        return self.text.format(*args, **kwargs)


_TEMPLATE_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def prompt_template(template_id: str, version: str, text: str) -> PromptTemplate:
    """Declare one versioned prompt; its module is the id's first segment.

    Features declare each model-facing prompt once, as a module-level
    constant beside the code that sends it, and use ``TEMPLATE.text`` or
    ``TEMPLATE.format(...)``. ``app.prompts.registry`` lists those modules
    for the golden test that pins every template's version and text.
    """
    if not _TEMPLATE_ID.match(template_id):
        raise ValueError(f"prompt id must be dotted lowercase, like 'chat.greeting': {template_id!r}")
    if not version.strip() or not text.strip():
        raise ValueError(f"prompt {template_id} needs a version and text")
    return PromptTemplate(id=template_id, version=version, module=template_id.split(".", 1)[0], text=text)


class PromptRenderRequest(BaseModel):
    template: PromptTemplate
    variables: dict[str, Any] = Field(default_factory=dict)


class RenderedPrompt(BaseModel):
    template_id: str
    version: str
    module: str
    rendered_text: str
    variables: dict[str, Any]
    provider_payload_format: ProviderPayloadFormat
    rendering_metadata: dict[str, Any] = Field(default_factory=dict)
    safety_metadata: dict[str, Any] = Field(default_factory=dict)
    grounding_metadata: dict[str, Any] = Field(default_factory=dict)
    replay_metadata: dict[str, Any] = Field(default_factory=dict)
