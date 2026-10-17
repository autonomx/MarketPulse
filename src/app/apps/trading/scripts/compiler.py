"""Compiling a parsed script into closures over the run's series (moved out of runtime.py)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from .errors import ScriptRuntimeError, ScriptSyntaxError, ScriptUnsupportedError
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
    Script,
    Switch,
    Ternary,
    TupleDeclare,
    TupleExpr,
    Unary,
    While,
)
from .runtime import (
    Closure,
    Context,
    INT_LIMIT,
    Program,
    SERIES_NAMES,
    ScriptInput,
    Slot,
    _ARITHMETIC,
    _Binding,
    _Break,
    _Continue,
    _Function,
    _Scope,
    _checked_int,
    _compare,
    _na,
    truthy,
)

class _Compiler:
    def __init__(self, script: Script) -> None:
        from . import builtins

        self.builtins = builtins
        self.script = script
        self.scope = _Scope(None, in_function=False)
        self.declaration: dict[str, Any] = {}
        self.inputs: list[ScriptInput] = []
        self.plot_specs: list[tuple[str, str, dict[str, Any]]] = []
        self.uses_drawings = False
        self.uses_last_bar = False
        self.securities: list[str] = []
        # Top-level variables holding an input's value (`tf = input.timeframe("D")`), by name: the input's title and the
        # variable's binding (a local of the same name is another variable). One that is reassigned is dropped; one a
        # request.security() context uses can't be reassigned (security_bindings).
        self.input_variables: dict[str, tuple[str, _Binding]] = {}
        self.security_bindings: set[int] = set()
        self.function_depth = 0
        self.loop_depth = 0
        self.version = script.version or 6

    def program(self) -> Program:
        body: list[Closure] = []
        lines: list[int] = []
        for statement in self.script.body:
            closure = self.statement(statement)
            if closure is not None:
                body.append(closure)
                lines.append(statement.line)
        if not self.declaration:
            raise ScriptSyntaxError("a script declares itself with indicator(...)", 1)
        return Program(
            self.script, body, self.declaration, self.inputs, self.plot_specs, self.uses_drawings, lines, self.uses_last_bar,
            list(self.securities),
        )

    def fail(self, node: Node, message: str, unsupported: bool = False) -> None:
        error = ScriptUnsupportedError if unsupported else ScriptSyntaxError
        raise error(message, node.line)

    # Statements

    def statement(self, node: Node) -> Closure | None:
        if isinstance(node, FunctionDef):
            self.function_def(node)
            return None
        if isinstance(node, Declare):
            return self.declare(node)
        if isinstance(node, TupleDeclare):
            return self.tuple_declare(node)
        if isinstance(node, Assign):
            return self.assign(node)
        if isinstance(node, For):
            return self.for_loop(node)
        if isinstance(node, ForIn):
            return self.for_in(node)
        if isinstance(node, While):
            return self.while_loop(node)
        if isinstance(node, (Break, Continue)) and self.loop_depth == 0:
            self.fail(node, "break and continue belong in a loop")
        if isinstance(node, Break):
            def do_break(ctx: Context) -> Any:
                raise _Break

            return do_break
        if isinstance(node, Continue):
            def do_continue(ctx: Context) -> Any:
                raise _Continue

            return do_continue
        return self.expression(node)

    def block(self, block: Block) -> Closure:
        outer = self.scope
        self.scope = _Scope(outer, outer.in_function)
        try:
            closures = [closure for statement in block.body if (closure := self.statement(statement)) is not None]
        finally:
            self.scope = outer
        if not closures:
            return lambda ctx: None
        if len(closures) == 1:
            return closures[0]

        def run_block(ctx: Context) -> Any:
            value = None
            for closure in closures:
                value = closure(ctx)
            return value

        return run_block

    def bind(self, node: Node, name: str, persistent: bool) -> _Binding:
        if name in self.builtins.RESERVED_NAMES:
            self.fail(node, f"{name!r} is a built-in name and can't be declared")
        if name in self.scope.names:
            self.fail(node, f"{name!r} is already declared: assign it with :=")
        binding = _Binding("slot", key=node.id * 1000 + len(self.scope.names), persistent=persistent, is_global=not self.scope.in_function)
        self.scope.names[name] = binding
        return binding

    def slot_writer(self, binding: _Binding) -> Callable[[Context], Slot]:
        key, persistent = binding.key, binding.persistent
        if binding.is_global:
            return lambda ctx: ctx.root.slot(key, persistent)
        return lambda ctx: ctx.slot(key, persistent)

    def declare(self, node: Declare) -> Closure:
        inputs_before = len(self.inputs)
        value = self.expression(node.value)
        if node.name in ("indicator", "strategy"):
            self.fail(node, f"{node.name!r} can't be used as a variable name")
        binding = self.bind(node, node.name, persistent=node.mode in ("var", "varip"))
        if (
            not node.mode and self.scope.parent is None and not self.function_depth and isinstance(node.value, Call)
            and node.value.callee.startswith("input.") and len(self.inputs) == inputs_before + 1
        ):
            self.input_variables[node.name] = (self.inputs[-1].title, binding)
        get_slot = self.slot_writer(binding)
        if node.mode:
            def declare_var(ctx: Context) -> Any:
                slot = get_slot(ctx)
                if not slot.initialized:
                    slot.current = value(ctx)
                    slot.initialized = True
                return slot.current

            return declare_var

        def declare_value(ctx: Context) -> Any:
            result = value(ctx)
            get_slot(ctx).current = result
            return result

        return declare_value

    def tuple_declare(self, node: TupleDeclare) -> Closure:
        value = self.expression(node.value)
        writers = [self.slot_writer(self.bind(node, name, persistent=False)) for name in node.names]
        count = len(writers)

        def declare_tuple(ctx: Context) -> Any:
            result = value(ctx)
            if not isinstance(result, tuple) or len(result) != count:
                raise ScriptRuntimeError(f"expected {count} values, got {result!r}", node.line)
            for writer, item in zip(writers, result, strict=True):
                writer(ctx).current = item
            return result

        return declare_tuple

    def assign(self, node: Assign) -> Closure:
        binding = self.scope.lookup(node.name)
        if binding is None or binding.kind != "slot":
            self.fail(node, f"{node.name!r} is assigned with {node.op} before it is declared")
        assert binding is not None
        if id(binding) in self.security_bindings:
            self.fail(node, f"{node.name!r} sets a request.security() context and can't be reassigned", unsupported=True)
        recorded = self.input_variables.get(node.name)
        if recorded is not None and recorded[1] is binding:
            del self.input_variables[node.name]  # no longer the input's value on every bar
        value = self.expression(node.value)
        get_slot = self.slot_writer(binding)
        if node.op == ":=":
            def reassign(ctx: Context) -> Any:
                result = value(ctx)
                get_slot(ctx).current = result
                return result

            return reassign
        operation = _ARITHMETIC[node.op[0]]

        def update(ctx: Context) -> Any:
            slot = get_slot(ctx)
            slot.current = operation(slot.current, value(ctx))
            return slot.current

        return update

    def loop_body(self, body: Block, bindings: list[tuple[str, _Binding]]) -> Closure:
        outer = self.scope
        self.scope = _Scope(outer, outer.in_function)
        for name, binding in bindings:
            self.scope.names[name] = binding
        self.loop_depth += 1
        try:
            return self.block(body)
        finally:
            self.loop_depth -= 1
            self.scope = outer

    def for_loop(self, node: For) -> Closure:
        start, end = self.expression(node.start), self.expression(node.end)
        step = self.expression(node.step) if node.step is not None else None
        binding = _Binding("slot", key=node.id * 1000, is_global=not self.scope.in_function)
        body = self.loop_body(node.body, [(node.var, binding)])
        get_slot = self.slot_writer(binding)

        # v6 checks the `to` bound again before each iteration; v5 reads it once.
        dynamic = self.version >= 6

        def run_for(ctx: Context) -> Any:
            first, last = start(ctx), end(ctx)
            if _na(first) or _na(last):
                return None
            increment = step(ctx) if step is not None else (1 if last >= first else -1)
            if _na(increment) or increment == 0:
                return None
            increment = abs(increment) if last >= first else -abs(increment)
            counter = get_slot(ctx)
            value = None
            index = first
            run = ctx.run
            while (index <= last) if increment > 0 else (index >= last):
                run.count_loop()
                counter.current = index
                try:
                    value = body(ctx)
                except _Continue:
                    pass
                except _Break:
                    break
                index += increment
                if dynamic:
                    last = end(ctx)
                    if _na(last):
                        break
            return value

        return run_for

    def for_in(self, node: ForIn) -> Closure:
        iterable = self.expression(node.iterable)
        item_binding = _Binding("slot", key=node.id * 1000, is_global=not self.scope.in_function)
        bindings = [(node.item_var, item_binding)]
        index_binding = None
        if node.index_var is not None:
            index_binding = _Binding("slot", key=node.id * 1000 + 1, is_global=not self.scope.in_function)
            bindings.append((node.index_var, index_binding))
        body = self.loop_body(node.body, bindings)
        item_slot = self.slot_writer(item_binding)
        index_slot = self.slot_writer(index_binding) if index_binding else None

        def run_for_in(ctx: Context) -> Any:
            items = iterable(ctx)
            if items is None:
                return None
            values = list(cast(Any, getattr(items, "items", items)))
            value = None
            for index, item in enumerate(values):
                ctx.run.count_loop()
                item_slot(ctx).current = item
                if index_slot is not None:
                    index_slot(ctx).current = index
                try:
                    value = body(ctx)
                except _Continue:
                    pass
                except _Break:
                    break
            return value

        return run_for_in

    def while_loop(self, node: While) -> Closure:
        condition = self.expression(node.condition)
        self.loop_depth += 1
        try:
            body = self.block(node.body)
        finally:
            self.loop_depth -= 1

        def run_while(ctx: Context) -> Any:
            value = None
            while truthy(condition(ctx)):
                ctx.run.count_loop()
                try:
                    value = body(ctx)
                except _Continue:
                    continue
                except _Break:
                    break
            return value

        return run_while

    def function_def(self, node: FunctionDef) -> None:
        if self.scope.in_function or self.scope.parent is not None:
            self.fail(node, "functions are declared at the top level of a script")
        function = _Function(node.name, [])
        outer = self.scope
        self.scope = _Scope(outer, in_function=True)
        try:
            for param in node.params:
                default = self.expression(param.default) if param.default is not None else None
                binding = _Binding("slot", key=node.id * 1000 + len(function.params) + 1, is_global=False)
                self.scope.names[param.name] = binding
                function.params.append((param.name, binding.key, default))
            self.function_depth += 1
            function.body = self.block(node.body)
        finally:
            self.function_depth -= 1
            self.scope = outer
        # Bound after its body is compiled: a function can't call itself (Pine has no recursion).
        self.scope.names[node.name] = _Binding("function", function=function)

    # Expressions

    def expression(self, node: Node) -> Closure:
        if isinstance(node, Literal):
            value = node.value
            return lambda ctx: value
        if isinstance(node, ColorLiteral):
            color = node.value
            return lambda ctx: color
        if isinstance(node, Name):
            return self.name(node)
        if isinstance(node, Binary):
            return self.binary(node)
        if isinstance(node, Unary):
            return self.unary(node)
        if isinstance(node, Ternary):
            condition, then, otherwise = self.expression(node.condition), self.expression(node.then), self.expression(node.otherwise)
            return lambda ctx: then(ctx) if truthy(condition(ctx)) else otherwise(ctx)
        if isinstance(node, History):
            return self.history(node)
        if isinstance(node, Call):
            return self.call(node)
        if isinstance(node, MethodCall):
            return self.method_call(node, self.expression(node.target), node.method, node.args, node.kwargs)
        if isinstance(node, TupleExpr):
            items = [self.expression(item) for item in node.items]
            return lambda ctx: tuple(item(ctx) for item in items)
        if isinstance(node, If):
            return self.if_expression(node)
        if isinstance(node, Switch):
            return self.switch(node)
        self.fail(node, f"{type(node).__name__} can't be used as a value")
        raise AssertionError("unreachable")

    def name(self, node: Name) -> Closure:
        binding = self.scope.lookup(node.name)
        if binding is not None and binding.kind == "slot":
            key = binding.key
            if binding.is_global:
                def read_global(ctx: Context) -> Any:
                    slot = ctx.root.slots.get(key)
                    return None if slot is None else slot.current

                return read_global

            def read_local(ctx: Context) -> Any:
                slot = ctx.slots.get(key)
                return None if slot is None else slot.current

            return read_local
        if binding is not None:
            self.fail(node, f"{node.name!r} is a function: call it with ()")
        builtin = self.builtins.builtin_variable(self, node)
        if builtin is None:
            self.fail(node, f"undeclared identifier {node.name!r}", unsupported=self.builtins.is_unsupported(node.name))
        assert builtin is not None
        return builtin

    def binary(self, node: Binary) -> Closure:
        left, right = self.expression(node.left), self.expression(node.right)
        if node.op in ("and", "or"):
            if self.version >= 6:
                # v6 evaluates and/or lazily.
                if node.op == "and":
                    return lambda ctx: truthy(left(ctx)) and truthy(right(ctx))
                return lambda ctx: truthy(left(ctx)) or truthy(right(ctx))
            # v5 evaluates both sides on every bar (a ta.* call on the right still advances).
            if node.op == "and":
                return lambda ctx: (lambda a, b: truthy(a) and truthy(b))(left(ctx), right(ctx))
            return lambda ctx: (lambda a, b: truthy(a) or truthy(b))(left(ctx), right(ctx))
        if node.op in _ARITHMETIC:
            operation = _ARITHMETIC[node.op]
            if node.op == "/" and self.version <= 5:
                # v5 truncates only an int constant divided by an int constant (7 / 2 == 3).
                a_constant, b_constant = _int_constant(node.left), _int_constant(node.right)
                if a_constant is not None and b_constant is not None and b_constant != 0:
                    truncated = int(a_constant / b_constant)
                    return lambda ctx: truncated
            if node.op in ("+", "-", "*"):
                # The hot path: two numbers.
                symbol = node.op

                def arithmetic(ctx: Context) -> Any:
                    a = left(ctx)
                    b = right(ctx)
                    if a is None or b is None:
                        return None
                    kind_a, kind_b = type(a), type(b)
                    if (kind_a is float or kind_a is int) and (kind_b is float or kind_b is int):
                        result = a + b if symbol == "+" else a - b if symbol == "-" else a * b
                        if type(result) is int and not -INT_LIMIT <= result <= INT_LIMIT:
                            return _checked_int(result)
                        return result
                    return operation(a, b)

                return arithmetic
            return lambda ctx: operation(left(ctx), right(ctx))
        compare = _compare(node.op)
        return lambda ctx: compare(left(ctx), right(ctx))

    def unary(self, node: Unary) -> Closure:
        operand = self.expression(node.operand)
        if node.op == "not":
            return lambda ctx: not truthy(operand(ctx))
        if node.op == "-":
            def negate(ctx: Context) -> Any:
                value = operand(ctx)
                return None if value is None else -value

            return negate
        return operand

    def history(self, node: History) -> Closure:
        offset = self.expression(node.offset)
        target = node.target
        if isinstance(target, Name):
            binding = self.scope.lookup(target.name)
            if binding is not None and binding.kind == "slot":
                key = binding.key
                is_global = binding.is_global

                def read_history(ctx: Context) -> Any:
                    n = offset(ctx)
                    if _na(n) or n < 0:
                        return None
                    slot = (ctx.root if is_global else ctx).slots.get(key)
                    return None if slot is None else slot.at(int(n))

                return read_history
            if binding is None and target.name in SERIES_NAMES:
                series = target.name

                def read_series(ctx: Context) -> Any:
                    n = offset(ctx)
                    if _na(n) or n < 0:
                        return None
                    run = ctx.run
                    return run.series(series, run.t - int(n))

                return read_series
        # Any other expression: its own history buffer, filled on the bars it runs.
        value = self.expression(target)
        key = node.id * 1000 + 999

        def read_expression_history(ctx: Context) -> Any:
            slot = ctx.slot(key, False)
            slot.current = value(ctx)
            n = offset(ctx)
            if _na(n) or n < 0:
                return None
            return slot.at(int(n))

        return read_expression_history

    def if_expression(self, node: If) -> Closure:
        condition = self.expression(node.condition)
        then = self.block(node.then)
        otherwise = self.block(node.otherwise) if node.otherwise is not None else None

        def run_if(ctx: Context) -> Any:
            if truthy(condition(ctx)):
                return then(ctx)
            return otherwise(ctx) if otherwise is not None else None

        return run_if

    def switch(self, node: Switch) -> Closure:
        subject = self.expression(node.subject) if node.subject is not None else None
        cases = [(self.expression(match) if match is not None else None, self.block(body)) for match, body in node.cases]

        def run_switch(ctx: Context) -> Any:
            value = subject(ctx) if subject is not None else None
            for match, body in cases:
                if match is None:
                    return body(ctx)
                candidate = match(ctx)
                # na equals nothing, so an na subject takes the default case.
                if (subject is not None and not _na(value) and not _na(candidate) and candidate == value) or (subject is None and truthy(candidate)):
                    return body(ctx)
            return None

        return run_switch

    def call(self, node: Call) -> Closure:
        binding = self.scope.lookup(node.callee)
        if binding is not None and binding.kind == "function":
            assert binding.function is not None
            return self.user_call(node, binding.function)
        head, _, method = node.callee.partition(".")
        if method and "." not in method:
            target = self.scope.lookup(head)
            if target is not None and target.kind == "slot":
                return self.method_call(node, self.name(Name(line=node.line, name=head)), method, node.args, node.kwargs)
        factory = self.builtins.builtin_function(node.callee)
        if factory is None:
            if self.builtins.is_unsupported(node.callee):
                self.fail(node, f"{node.callee}() is not supported yet", unsupported=True)
            self.fail(node, f"unknown function {node.callee}()")
        assert factory is not None
        return factory(self, node)

    def method_call(self, node: Node, target: Closure, method: str, args: list[Node], kwargs: dict[str, Node]) -> Closure:
        # obj.method(...) is <type>.method(obj, ...), with the type known at run time.
        arguments = [self.expression(arg) for arg in args]
        named = {key: self.expression(value) for key, value in kwargs.items()}
        builtins = self.builtins

        def call_method(ctx: Context) -> Any:
            obj = target(ctx)
            kind = builtins.kind_of(obj)
            function = builtins.method(kind, method)
            if function is None:
                raise ScriptRuntimeError(f"{kind or 'na'} has no method {method}()", node.line)
            return function(ctx, obj, *[a(ctx) for a in arguments], **{key: value(ctx) for key, value in named.items()})

        return call_method

    def user_call(self, node: Call, function: _Function) -> Closure:
        if len(node.args) > len(function.params):
            self.fail(node, f"{function.name}() takes {len(function.params)} arguments")
        names = [name for name, _, _ in function.params]
        values: list[Closure | None] = [None] * len(names)
        for index, arg in enumerate(node.args):
            values[index] = self.expression(arg)
        for key, arg in node.kwargs.items():
            if key not in names:
                self.fail(node, f"{function.name}() has no argument {key!r}")
            values[names.index(key)] = self.expression(arg)
        params: list[tuple[int, Closure]] = []
        for (name, slot, default), value in zip(function.params, values, strict=True):
            chosen = value or default
            if chosen is None:
                self.fail(node, f"{function.name}() needs a value for {name!r}")
            assert chosen is not None
            params.append((slot, chosen))
        site = node.id
        body = function

        def call_user(ctx: Context) -> Any:
            arguments = [(key, value(ctx)) for key, value in params]
            child = ctx.child(site)
            # The previous call's values become the series' history (x[1] in a function is its last call's x).
            if child.slots or child.calls:
                child.commit_call()
            for key, argument in arguments:
                child.slot(key, False).current = argument
            assert body.body is not None
            return body.body(child)

        return call_user


def _int_constant(node: Node) -> int | None:
    """An int literal, or a negated one."""
    if isinstance(node, Literal) and type(node.value) is int:
        return node.value
    if isinstance(node, Unary) and node.op == "-" and isinstance(node.operand, Literal) and type(node.operand.value) is int:
        return -node.operand.value
    return None
