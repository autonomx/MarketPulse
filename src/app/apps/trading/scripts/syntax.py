"""The syntax tree of a script. Every node keeps its line for error messages, and a unique ``id`` that the
runtime uses to give each call site its own series state."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

_ids = itertools.count(1)


def _next_id() -> int:
    return next(_ids)


@dataclass
class Node:
    line: int
    id: int = field(default_factory=_next_id, init=False, compare=False)


# Expressions


@dataclass
class Literal(Node):
    value: object  # int, float, bool, str, or None for na


@dataclass
class ColorLiteral(Node):
    value: str  # "#RRGGBB" or "#RRGGBBAA"


@dataclass
class Name(Node):
    name: str  # dotted for namespaced names: "ta.tr", "barstate.islast", "color.red"


@dataclass
class Call(Node):
    callee: str  # dotted name
    args: list[Node]
    kwargs: dict[str, Node]
    type_args: list[str] = field(default_factory=list)  # array.new<float>(...)


@dataclass
class MethodCall(Node):
    target: Node
    method: str
    args: list[Node]
    kwargs: dict[str, Node]


@dataclass
class History(Node):
    target: Node
    offset: Node


@dataclass
class Unary(Node):
    op: str  # "-", "+", "not"
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Ternary(Node):
    condition: Node
    then: Node
    otherwise: Node


@dataclass
class TupleExpr(Node):
    items: list[Node]


# Statements (some are also expressions: if and switch return their branch's last value)


@dataclass
class Block(Node):
    body: list[Node]


@dataclass
class If(Node):
    condition: Node
    then: Block
    otherwise: Block | None


@dataclass
class Switch(Node):
    subject: Node | None
    cases: list[tuple[Node | None, Block]]  # None: the default case


@dataclass
class For(Node):
    var: str
    start: Node
    end: Node
    step: Node | None
    body: Block


@dataclass
class ForIn(Node):
    index_var: str | None
    item_var: str
    iterable: Node
    body: Block


@dataclass
class While(Node):
    condition: Node
    body: Block


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Declare(Node):
    name: str
    value: Node
    mode: str = ""  # "", "var", "varip"
    type_name: str | None = None


@dataclass
class TupleDeclare(Node):
    names: list[str]
    value: Node


@dataclass
class Assign(Node):
    name: str
    op: str  # ":=", "+=", ...
    value: Node


@dataclass
class Param:
    name: str
    default: Node | None
    type_name: str | None = None


@dataclass
class FunctionDef(Node):
    name: str
    params: list[Param]
    body: Block


@dataclass
class Script(Node):
    body: list[Node]
    version: int | None
