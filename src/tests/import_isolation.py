"""Scope historical app import stubs to their own test module and test cases."""
from __future__ import annotations

import sys
from contextlib import contextmanager


def _app_name(name):
    return name == "app" or name.startswith("app.")


class AppImportIsolation:
    def __init__(self):
        self.modules = {}
        self.finders = []

    @contextmanager
    def activate(self):
        before = dict(sys.modules)
        meta_path = list(sys.meta_path)
        packages = {
            name: dict(vars(module)) for name, module in before.items()
            if _app_name(name) and module is not None and "__path__" in vars(module)
        }
        try:
            sys.meta_path[:0] = self.finders
            sys.modules.update(self.modules)
            for name, module in self.modules.items():
                parent, _, child = name.rpartition(".")
                if parent in sys.modules:
                    setattr(sys.modules[parent], child, module)
            yield self
        finally:
            changed = {
                name: module for name, module in list(sys.modules.items())
                if module is not before.get(name) and (
                    _app_name(name) or getattr(module, "__file__", None) == "<stub>"
                )
            }
            self.modules = changed
            self.finders = [finder for finder in sys.meta_path if finder not in meta_path]
            sys.meta_path[:] = meta_path
            for name in changed:
                if name in before:
                    sys.modules[name] = before[name]
                else:
                    sys.modules.pop(name, None)
            for name, module in before.items():
                if _app_name(name) and name not in sys.modules:
                    sys.modules[name] = module
            for name, attributes in packages.items():
                module = before[name]
                # Import loaders bind child modules on their parent package.
                # Restoring sys.modules alone leaves those stubs reachable.
                for child in changed:
                    parent, _, attribute = child.rpartition(".")
                    if parent == name:
                        if attribute in attributes:
                            setattr(module, attribute, attributes[attribute])
                        else:
                            vars(module).pop(attribute, None)
