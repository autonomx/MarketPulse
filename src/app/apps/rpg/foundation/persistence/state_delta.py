"""The per-turn state change as a JSON Patch (RFC 6902), and its application (WP-5.6).

A turn row stores the patch from its state before to its state after; a full
snapshot is written only every ``OMNIX_RPG_SNAPSHOT_EVERY`` revisions, so a
state is a snapshot plus the patches of the turns after it.

``state_delta`` emits ``add``, ``remove`` and ``replace``. It recurses into
objects that changed; a list that grew at its end gets one ``add`` per new
item at ``/-``, and any other changed value (a list edited in place, a changed
scalar, a type change) is replaced whole. Equal subtrees are skipped by
comparison, so the cost follows the size of the change, not of the state.
"""
from __future__ import annotations

import copy
from typing import Any

JsonPatch = list[dict[str, Any]]


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def state_delta(before: Any, after: Any) -> JsonPatch:
    """The patch that turns ``before`` into ``after``; empty when they are equal."""
    operations: JsonPatch = []
    _diff(before, after, "", operations)
    return operations


def _diff(before: Any, after: Any, path: str, operations: JsonPatch) -> None:
    if before == after and type(before) is type(after):
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before:
            if key not in after:
                operations.append({"op": "remove", "path": f"{path}/{_escape(str(key))}"})
        for key, value in after.items():
            child = f"{path}/{_escape(str(key))}"
            if key not in before:
                operations.append({"op": "add", "path": child, "value": value})
            else:
                _diff(before[key], value, child, operations)
        return
    if (
        isinstance(before, list)
        and isinstance(after, list)
        and len(after) > len(before)
        and after[: len(before)] == before
    ):
        for value in after[len(before):]:
            operations.append({"op": "add", "path": f"{path}/-", "value": value})
        return
    operations.append({"op": "replace", "path": path, "value": after})


def apply_state_delta(state: Any, operations: JsonPatch) -> Any:
    """``state`` with ``operations`` applied; ``state`` itself is left unchanged."""
    result = copy.deepcopy(state)
    for operation in operations:
        path = str(operation["path"])
        value = copy.deepcopy(operation.get("value"))
        if path == "":
            if operation["op"] == "remove":
                raise ValueError("a patch cannot remove the whole state")
            result = value
            continue
        *parents, last = [_unescape(token) for token in path.split("/")[1:]]
        target = result
        for token in parents:
            target = target[int(token)] if isinstance(target, list) else target[token]
        if isinstance(target, list):
            if operation["op"] == "add":
                if last == "-":
                    target.append(value)
                else:
                    target.insert(int(last), value)
            elif operation["op"] == "remove":
                del target[int(last)]
            else:
                target[int(last)] = value
        elif operation["op"] == "remove":
            del target[last]
        else:
            target[last] = value
    return result


__all__ = ["JsonPatch", "apply_state_delta", "state_delta"]
