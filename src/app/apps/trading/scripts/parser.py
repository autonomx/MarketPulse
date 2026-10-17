"""Parser: tokens to a ``Script`` syntax tree (Pine v5/v6 syntax, the subset in OMNIX_SCRIPTS_SPEC.md)."""

from __future__ import annotations

from .errors import ScriptSyntaxError, ScriptUnsupportedError
from .lexer import Token, tokenize
from .syntax import (
    Assign,
    Binary,
    Block,
    Break,
    Call,
    ColorLiteral,
    Continue,
    Declare,
    For,
    ForIn,
    FunctionDef,
    History,
    If,
    Literal,
    MethodCall,
    Name,
    Node,
    Param,
    Script,
    Switch,
    Ternary,
    TupleDeclare,
    TupleExpr,
    Unary,
    While,
)

TYPE_QUALIFIERS = {"series", "simple", "const"}
TYPE_NAMES = {"int", "float", "bool", "string", "color", "line", "label", "box", "table", "linefill", "polyline", "array", "matrix", "map"}
ASSIGN_OPS = {":=", "+=", "-=", "*=", "/=", "%="}
_UNSUPPORTED_KEYWORDS = {
    "import": "libraries (import)",
    "export": "libraries (export)",
    "type": "user-defined types",
    "method": "user-defined methods",
    "enum": "enums",
}

# Expressions nested deeper than this (brackets, unary operators) or chains of more operators are refused before
# they can exhaust the interpreter's stack.
MAX_NESTING = 64
MAX_CHAIN = 256

# Binary operators by precedence, loosest first (Pine's table; ?: is below them all).
_LEVELS: list[set[str]] = [{"or"}, {"and"}, {"==", "!="}, {"<", ">", "<=", ">="}, {"+", "-"}, {"*", "/", "%"}]


def parse_script(source: str) -> Script:
    lexed = tokenize(source)
    parser = _Parser(lexed.tokens)
    body = parser.statements(until={"EOF"})
    return Script(line=1, body=body, version=lexed.version)


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    def nest(self) -> None:
        self.depth += 1
        if self.depth > MAX_NESTING:
            self.fail("the expression is nested too deeply")

    # Token helpers

    @property
    def token(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def at(self, kind: str, value: object = None) -> bool:
        token = self.token
        return token.kind == kind and (value is None or token.value == value)

    def at_op(self, *values: str) -> bool:
        return self.token.kind == "OP" and self.token.value in values

    def at_keyword(self, *values: str) -> bool:
        return self.token.kind == "KEYWORD" and self.token.value in values

    def expect_op(self, value: str) -> Token:
        if not self.at_op(value):
            self.fail(f"expected {value!r}")
        return self.advance()

    def expect_name(self) -> str:
        if self.token.kind != "NAME":
            self.fail("expected a name")
        return str(self.advance().value)

    def fail(self, message: str) -> None:
        token = self.token
        found = "end of script" if token.kind == "EOF" else "end of line" if token.kind == "NEWLINE" else repr(token.value)
        raise ScriptSyntaxError(f"{message}, found {found}", token.line, token.column)

    def end_statement(self) -> None:
        # A statement ends at a newline; one that ended with an indented block already consumed it.
        if self.at("NEWLINE"):
            self.advance()
        elif self.pos > 0 and self.tokens[self.pos - 1].kind == "DEDENT":
            return
        elif not (self.at("EOF") or self.at("DEDENT")):
            self.fail("expected the end of the line")

    # Statements

    def statements(self, until: set[str]) -> list[Node]:
        body: list[Node] = []
        while self.token.kind not in until:
            if self.at("NEWLINE"):
                self.advance()
                continue
            body.append(self.statement())
        return body

    def block(self) -> Block:
        line = self.token.line
        if not self.at("NEWLINE"):
            self.fail("expected a new line and an indented block")
        self.advance()
        if not self.at("INDENT"):
            self.fail("expected an indented block")
        self.advance()
        body = self.statements(until={"DEDENT", "EOF"})
        if self.at("DEDENT"):
            self.advance()
        return Block(line=line, body=body)

    def statement(self) -> Node:
        token = self.token
        node: Node
        if token.kind == "KEYWORD":
            word = token.value
            if word in _UNSUPPORTED_KEYWORDS:
                raise ScriptUnsupportedError(f"{_UNSUPPORTED_KEYWORDS[word]} are not supported", token.line, token.column)
            if word in ("var", "varip"):
                self.advance()
                node = self.declaration(mode=str(word))
                self.end_statement()
                return node
            if word == "for":
                return self.for_loop()
            if word == "while":
                self.advance()
                condition = self.expression()
                return While(line=token.line, condition=condition, body=self.block())
            if word in ("break", "continue"):
                self.advance()
                self.end_statement()
                return Break(line=token.line) if word == "break" else Continue(line=token.line)
        if token.kind == "NAME" and self.at_function_def():
            return self.function_def()
        if token.kind == "OP" and token.value == "[" and self.at_tuple_declare():
            return self.tuple_declare()
        if token.kind == "NAME" and self.at_typed_declaration():
            node = self.declaration(mode="")
            self.end_statement()
            return node
        if token.kind == "NAME" and self.peek().kind == "OP" and self.peek().value == "=":
            node = self.declaration(mode="")
            self.end_statement()
            return node
        if token.kind == "NAME" and self.peek().kind == "OP" and self.peek().value in ASSIGN_OPS:
            name = str(self.advance().value)
            op = str(self.advance().value)
            node = Assign(line=token.line, name=name, op=op, value=self.expression())
            self.end_statement()
            return node
        node = self.expression()
        self.end_statement()
        return node

    def at_function_def(self) -> bool:
        # name ( ... ) =>
        if not (self.peek().kind == "OP" and self.peek().value == "("):
            return False
        depth = 0
        i = self.pos + 1
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind == "OP" and str(token.value) in "([":
                depth += 1
            elif token.kind == "OP" and str(token.value) in ")]":
                depth -= 1
                if depth == 0:
                    following = self.tokens[i + 1] if i + 1 < len(self.tokens) else token
                    return following.kind == "OP" and following.value == "=>"
            elif token.kind in ("NEWLINE", "EOF"):
                return False
            i += 1
        return False

    def at_tuple_declare(self) -> bool:
        i = self.pos + 1
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind == "NAME" or (token.kind == "OP" and token.value == ","):
                i += 1
                continue
            following = self.tokens[i + 1] if i + 1 < len(self.tokens) else token
            return token.kind == "OP" and token.value == "]" and following.kind == "OP" and following.value in ("=", ":=")
        return False

    def at_typed_declaration(self) -> bool:
        # [series|simple|const] type[<...>][[]] name =
        i = self.pos
        while self.tokens[i].kind == "NAME" and self.tokens[i].value in TYPE_QUALIFIERS:
            i += 1
        if self.tokens[i].kind != "NAME":
            return False
        i += 1
        if self.tokens[i].kind == "OP" and self.tokens[i].value == "<":
            depth = 0
            while i < len(self.tokens):
                token = self.tokens[i]
                if token.kind == "OP" and token.value == "<":
                    depth += 1
                elif token.kind == "OP" and token.value == ">":
                    depth -= 1
                    if depth == 0:
                        break
                elif token.kind in ("NEWLINE", "EOF"):
                    return False
                i += 1
            i += 1
        elif self.tokens[i].kind == "OP" and self.tokens[i].value == ".":
            # chart.point and other dotted type names
            i += 2
        if self.tokens[i].kind == "OP" and self.tokens[i].value == "[" and self.tokens[i + 1].kind == "OP" and self.tokens[i + 1].value == "]":
            i += 2
        return self.tokens[i].kind == "NAME" and self.tokens[i + 1].kind == "OP" and self.tokens[i + 1].value == "="

    def type_name(self) -> str:
        parts: list[str] = []
        while self.at("NAME") and self.token.value in TYPE_QUALIFIERS:
            self.advance()
        parts.append(self.expect_name())
        while self.at_op("."):
            self.advance()
            parts.append(self.expect_name())
        name = ".".join(parts)
        if self.at_op("<"):
            self.advance()
            inner = [self.type_name()]
            while self.at_op(","):
                self.advance()
                inner.append(self.type_name())
            self.expect_op(">")
            name += "<" + ",".join(inner) + ">"
        if self.at_op("[") and self.peek().kind == "OP" and self.peek().value == "]":
            self.advance()
            self.advance()
            name += "[]"
        return name

    def declaration(self, mode: str) -> Declare:
        line = self.token.line
        type_name = None
        if self.at_typed_declaration():
            type_name = self.type_name()
        name = self.expect_name()
        self.expect_op("=")
        return Declare(line=line, name=name, value=self.expression(), mode=mode, type_name=type_name)

    def tuple_declare(self) -> TupleDeclare:
        line = self.token.line
        self.expect_op("[")
        names = [self.expect_name()]
        while self.at_op(","):
            self.advance()
            names.append(self.expect_name())
        self.expect_op("]")
        if self.at_op(":="):
            raise ScriptUnsupportedError("tuple reassignment (:=) is not supported", line)
        self.expect_op("=")
        node = TupleDeclare(line=line, names=names, value=self.expression())
        self.end_statement()
        return node

    def function_def(self) -> FunctionDef:
        line = self.token.line
        name = self.expect_name()
        self.expect_op("(")
        params: list[Param] = []
        while not self.at_op(")"):
            type_name = None
            if self.at("NAME") and self.peek().kind == "NAME" or (
                self.at("NAME") and self.token.value in TYPE_QUALIFIERS
            ):
                type_name = self.type_name()
            param = self.expect_name()
            default = None
            if self.at_op("="):
                self.advance()
                default = self.expression()
            params.append(Param(param, default, type_name))
            if not self.at_op(")"):
                self.expect_op(",")
        self.expect_op(")")
        self.expect_op("=>")
        if self.at("NEWLINE"):
            body = self.block()
        else:
            expression = self.expression()
            self.end_statement()
            body = Block(line=line, body=[expression])
        return FunctionDef(line=line, name=name, params=params, body=body)

    def for_loop(self) -> Node:
        line = self.advance().line
        if self.at_op("["):
            self.advance()
            index_var = self.expect_name()
            self.expect_op(",")
            item_var = self.expect_name()
            self.expect_op("]")
            if not self.at_keyword("in"):
                self.fail("expected 'in'")
            self.advance()
            iterable = self.expression()
            return ForIn(line=line, index_var=index_var, item_var=item_var, iterable=iterable, body=self.block())
        var = self.expect_name()
        if self.at_keyword("in"):
            self.advance()
            iterable = self.expression()
            return ForIn(line=line, index_var=None, item_var=var, iterable=iterable, body=self.block())
        self.expect_op("=")
        start = self.expression()
        if not self.at_keyword("to"):
            self.fail("expected 'to'")
        self.advance()
        end = self.expression()
        step = None
        if self.at_keyword("by"):
            self.advance()
            step = self.expression()
        return For(line=line, var=var, start=start, end=end, step=step, body=self.block())

    def if_statement(self) -> If:
        line = self.advance().line
        condition = self.expression()
        then = self.block()
        otherwise = None
        if self.at_keyword("else"):
            else_line = self.advance().line
            if self.at_keyword("if"):
                otherwise = Block(line=else_line, body=[self.if_statement()])
            else:
                otherwise = self.block()
        return If(line=line, condition=condition, then=then, otherwise=otherwise)

    def switch_statement(self) -> Switch:
        line = self.advance().line
        subject = None if self.at("NEWLINE") else self.expression()
        if not self.at("NEWLINE"):
            self.fail("expected a new line after switch")
        self.advance()
        if not self.at("INDENT"):
            self.fail("expected the indented cases of the switch")
        self.advance()
        cases: list[tuple[Node | None, Block]] = []
        while not (self.at("DEDENT") or self.at("EOF")):
            if self.at("NEWLINE"):
                self.advance()
                continue
            case_line = self.token.line
            match = None
            if not self.at_op("=>"):
                match = self.expression()
            self.expect_op("=>")
            if self.at("NEWLINE"):
                body = self.block()
            else:
                body = Block(line=case_line, body=[self.statement()])
            cases.append((match, body))
        if self.at("DEDENT"):
            self.advance()
        return Switch(line=line, subject=subject, cases=cases)

    # Expressions

    def expression(self) -> Node:
        self.nest()
        try:
            if self.at_keyword("if"):
                return self.if_statement()
            if self.at_keyword("switch"):
                return self.switch_statement()
            return self.ternary()
        finally:
            self.depth -= 1

    def ternary(self) -> Node:
        condition = self.binary(0)
        if self.at_op("?"):
            line = self.advance().line
            then = self.ternary()
            self.expect_op(":")
            otherwise = self.ternary()
            return Ternary(line=line, condition=condition, then=then, otherwise=otherwise)
        return condition

    def binary(self, level: int) -> Node:
        if level == len(_LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        operators = _LEVELS[level]
        terms = 0
        while (self.token.kind in ("OP", "KEYWORD")) and self.token.value in operators:
            token = self.advance()
            right = self.binary(level + 1)
            left = Binary(line=token.line, op=str(token.value), left=left, right=right)
            terms += 1
            # The compiled expression nests one closure per operator.
            if terms > MAX_CHAIN:
                self.fail("the expression is too long")
        return left

    def unary(self) -> Node:
        if self.at_op("-", "+") or self.at_keyword("not"):
            token = self.advance()
            self.nest()
            try:
                operand = self.unary()
            finally:
                self.depth -= 1
            return Unary(line=token.line, op=str(token.value), operand=operand)
        return self.postfix(self.primary())

    def postfix(self, node: Node) -> Node:
        while True:
            if self.at_op("("):
                node = self.call(node)
            elif self.at_op("["):
                line = self.advance().line
                offset = self.expression()
                self.expect_op("]")
                node = History(line=line, target=node, offset=offset)
            elif self.at_op("."):
                self.advance()
                member = self.expect_name()
                if isinstance(node, Name):
                    node = Name(line=node.line, name=f"{node.name}.{member}")
                elif self.at_op("("):
                    args, kwargs = self.arguments()
                    node = MethodCall(line=node.line, target=node, method=member, args=args, kwargs=kwargs)
                else:
                    raise ScriptUnsupportedError("fields of values (user-defined types) are not supported", node.line)
            elif self.at_op("<") and isinstance(node, Name) and node.name.split(".")[-1] in ("new", "from") and self.generic_ahead():
                self.advance()
                type_args = [self.type_name()]
                while self.at_op(","):
                    self.advance()
                    type_args.append(self.type_name())
                self.expect_op(">")
                call = self.call(node)
                assert isinstance(call, Call)
                call.type_args = type_args
                node = call
            else:
                return node

    def generic_ahead(self) -> bool:
        # array.new<float>( : a type list closed by > and followed by (
        i = self.pos + 1
        depth = 1
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind == "OP" and token.value == "<":
                depth += 1
            elif token.kind == "OP" and token.value == ">":
                depth -= 1
                if depth == 0:
                    following = self.tokens[i + 1]
                    return following.kind == "OP" and following.value == "("
            elif not (token.kind == "NAME" or (token.kind == "OP" and token.value in (",", "."))):
                return False
            i += 1
        return False

    def call(self, node: Node) -> Node:
        line = self.token.line
        args, kwargs = self.arguments()
        if isinstance(node, Name):
            return Call(line=node.line, callee=node.name, args=args, kwargs=kwargs)
        raise ScriptSyntaxError("only named functions can be called", line)

    def arguments(self) -> tuple[list[Node], dict[str, Node]]:
        self.expect_op("(")
        args: list[Node] = []
        kwargs: dict[str, Node] = {}
        while not self.at_op(")"):
            if self.at("NAME") and self.peek().kind == "OP" and self.peek().value == "=":
                key = str(self.advance().value)
                self.advance()
                kwargs[key] = self.expression()
            else:
                if kwargs:
                    self.fail("positional arguments come before named ones")
                args.append(self.expression())
            if not self.at_op(")"):
                self.expect_op(",")
        self.expect_op(")")
        return args, kwargs

    def primary(self) -> Node:
        token = self.token
        if token.kind == "NUMBER":
            self.advance()
            return Literal(line=token.line, value=token.value)
        if token.kind == "STRING":
            self.advance()
            return Literal(line=token.line, value=token.value)
        if token.kind == "COLOR":
            self.advance()
            return ColorLiteral(line=token.line, value=str(token.value))
        if token.kind == "KEYWORD" and token.value in ("true", "false"):
            self.advance()
            return Literal(line=token.line, value=token.value == "true")
        if token.kind == "NAME":
            self.advance()
            return Name(line=token.line, name=str(token.value))
        if token.kind == "OP" and token.value == "(":
            self.advance()
            node = self.expression()
            self.expect_op(")")
            return node
        if token.kind == "OP" and token.value == "[":
            self.advance()
            items: list[Node] = []
            while not self.at_op("]"):
                items.append(self.expression())
                if not self.at_op("]"):
                    self.expect_op(",")
            self.expect_op("]")
            return TupleExpr(line=token.line, items=items)
        if token.kind == "KEYWORD" and token.value in _UNSUPPORTED_KEYWORDS:
            raise ScriptUnsupportedError(f"{_UNSUPPORTED_KEYWORDS[str(token.value)]} are not supported", token.line, token.column)
        self.fail("expected a value")
        raise AssertionError("unreachable")
