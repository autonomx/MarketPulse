from __future__ import annotations

from typing import Any, Dict, List
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.ai_conversation_prompt_builder.build_npc_conversation_line_prompt', "1", 'Write exactly one NPC conversation line as JSON.\nSchema: {{"speaker": "...", "text": "...", "kind": "statement|question|challenge|warning|agreement|interruption"}}\nConversation kind: {v0}\nTopic type: {v1}\nTopic summary: {v2}\nSpeaker: {v3}\nRecent lines:\n{v4}\nConstraints: one short line only, no narration, no markdown.')


def build_npc_conversation_line_prompt(
    conversation: Dict[str, Any],
    speaker_id: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    recent_lines: List[Dict[str, Any]],
) -> str:
    conversation = _safe_dict(conversation)
    topic = _safe_dict(conversation.get("topic"))
    lines_text = "\n".join(
        f'- { _safe_str(line.get("speaker")) }: {_safe_str(line.get("text"))}'
        for line in (recent_lines or [])[-4:]
    )
    return (
        _PROMPT_1.format(v0=(_safe_str(conversation.get('kind'))), v1=(_safe_str(topic.get('type'))), v2=(_safe_str(topic.get('summary'))), v3=(_safe_str(speaker_id)), v4=(lines_text))
    )
