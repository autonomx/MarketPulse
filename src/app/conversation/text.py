"""Small text-formatting utilities shared across features."""
from __future__ import annotations

import re


def remove_emojis(text: str) -> str:
    if not text:
        return text
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\u2702-\u27B0\u24C2-\U0001F251]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub("", text)


def extract_thinking(content: str) -> tuple[str, str]:
    if not content:
        return "", content
    lines = content.split("\n")
    thinking_lines: list[str] = []
    answer_lines: list[str] = []
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if not found and any(marker in stripped for marker in (
            "analyze", "identify the intent", "determine the answer", "formulate", "final output"
        )):
            thinking_lines, answer_lines = lines[:i], lines[i:]
            found = True
            break
    if thinking_lines and answer_lines:
        thinking = "\n".join(thinking_lines).strip()
        answer = "\n".join(answer_lines).strip()
        if len(thinking) > 20:
            return thinking, answer
    return "", content


def format_size(bytes_size: float) -> str:
    value = float(bytes_size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"
