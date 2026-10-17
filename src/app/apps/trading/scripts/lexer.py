"""Tokens of a script, with Pine's indentation rules.

Blocks are indented by 4 spaces (or a tab). A line indented by a number of spaces that isn't a multiple of 4
continues the previous line, and so does everything inside brackets. Comments start with ``//``; the
``//@version=N`` annotation is recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ScriptSyntaxError

KEYWORDS = {
    "if", "else", "for", "to", "by", "in", "while", "var", "varip", "and", "or", "not", "true", "false",
    "switch", "break", "continue", "import", "export", "type", "method", "enum",
}

# Longest first, so ":=" wins over ":".
OPERATORS = (
    ":=", "=>", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=",
    "+", "-", "*", "/", "%", "<", ">", "=", "?", ":", ",", "(", ")", "[", "]", ".",
)

_NUMBER = re.compile(r"(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COLOR = re.compile(r"#([0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})(?![0-9A-Za-z_])")


@dataclass(frozen=True)
class Token:
    kind: str  # NUMBER, STRING, COLOR, NAME, KEYWORD, OP, NEWLINE, INDENT, DEDENT, EOF
    value: object
    line: int
    column: int


@dataclass(frozen=True)
class Lexed:
    tokens: list[Token]
    version: int | None


def tokenize(source: str) -> Lexed:
    tokens: list[Token] = []
    version: int | None = None
    indents = [0]
    depth = 0  # open brackets: newlines inside them don't end the statement
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("//@version="):
            try:
                version = int(stripped.split("=", 1)[1].strip())
            except ValueError:
                raise ScriptSyntaxError("unreadable //@version annotation", number, 1) from None
        if not stripped or stripped.startswith("//"):
            continue
        expanded = raw.replace("\t", "    ")
        indent = len(expanded) - len(expanded.lstrip(" "))
        # Inside brackets, or indented off the 4-space grid past the block: the previous line goes on.
        continuation = depth > 0 or (tokens and indent > indents[-1] and indent % 4 != 0)
        if not continuation:
            if tokens and tokens[-1].kind != "NEWLINE":
                tokens.append(Token("NEWLINE", None, number - 1, 0))
            if indent > indents[-1]:
                if indent != indents[-1] + 4:
                    raise ScriptSyntaxError("a block is indented by 4 spaces or one tab", number, 1)
                indents.append(indent)
                tokens.append(Token("INDENT", None, number, 1))
            while indent < indents[-1]:
                indents.pop()
                tokens.append(Token("DEDENT", None, number, 1))
            if indent != indents[-1]:
                raise ScriptSyntaxError("this line's indentation matches no open block", number, 1)
        depth = _line_tokens(expanded, number, indent, tokens, depth)
    if tokens and tokens[-1].kind != "NEWLINE":
        tokens.append(Token("NEWLINE", None, len(lines), 0))
    while len(indents) > 1:
        indents.pop()
        tokens.append(Token("DEDENT", None, len(lines), 0))
    tokens.append(Token("EOF", None, len(lines), 0))
    return Lexed(tokens, version)


def _line_tokens(text: str, line: int, start: int, tokens: list[Token], depth: int) -> int:
    i = start
    n = len(text)
    while i < n:
        char = text[i]
        column = i + 1
        if char in " \t":
            i += 1
            continue
        if text.startswith("//", i):
            break
        if char.isdigit() or (char == "." and i + 1 < n and text[i + 1].isdigit()):
            match = _NUMBER.match(text, i)
            assert match is not None
            literal = match.group(0)
            value: object = int(literal) if match.group(1).isdigit() and not match.group(2) else float(literal)
            tokens.append(Token("NUMBER", value, line, column))
            i = match.end()
            continue
        if char in "\"'":
            i, value = _string(text, i, line)
            tokens.append(Token("STRING", value, line, column))
            continue
        if char == "#":
            match = _COLOR.match(text, i)
            if match is None:
                raise ScriptSyntaxError("a color is written #RRGGBB or #RRGGBBAA", line, column)
            tokens.append(Token("COLOR", "#" + match.group(1).upper(), line, column))
            i = match.end()
            continue
        match = _NAME.match(text, i)
        if match:
            word = match.group(0)
            tokens.append(Token("KEYWORD" if word in KEYWORDS else "NAME", word, line, column))
            i = match.end()
            continue
        for operator in OPERATORS:
            if text.startswith(operator, i):
                if operator in "([":
                    depth += 1
                elif operator in ")]":
                    depth = max(0, depth - 1)
                tokens.append(Token("OP", operator, line, column))
                i += len(operator)
                break
        else:
            raise ScriptSyntaxError(f"unexpected character {char!r}", line, column)
    return depth


_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


def _string(text: str, start: int, line: int) -> tuple[int, str]:
    quote = text[start]
    i = start + 1
    parts: list[str] = []
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            parts.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if char == quote:
            return i + 1, "".join(parts)
        parts.append(char)
        i += 1
    raise ScriptSyntaxError("unterminated string", line, start + 1)
