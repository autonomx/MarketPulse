"""Composable feature repository registry for PostgreSQL units of work."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Hashable, Mapping, cast

RepositoryFactory = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    type: Hashable
    factory: RepositoryFactory
    alias: str | None = None


_LOCK = RLock()
_SPECS_BY_TYPE: Mapping[Hashable, RepositorySpec] = MappingProxyType({})
_SPECS_BY_ALIAS: Mapping[str, RepositorySpec] = MappingProxyType({})
MAX_REPOSITORY_SPECS = 512


def install_repository_specs(specs: tuple[RepositorySpec, ...]) -> None:
    global _SPECS_BY_TYPE, _SPECS_BY_ALIAS
    with _LOCK:
        by_type = dict(_SPECS_BY_TYPE)
        by_alias = dict(_SPECS_BY_ALIAS)
        for spec in specs:
            existing = by_type.get(spec.type)
            if existing is not None and existing != spec:
                raise ValueError(f"duplicate repository type: {spec.type!r}")
            if spec.alias:
                alias_existing = by_alias.get(spec.alias)
                if alias_existing is not None and alias_existing != spec:
                    raise ValueError(f"duplicate repository alias: {spec.alias}")
                by_alias[spec.alias] = spec
            by_type[spec.type] = spec
        if len(by_type) > MAX_REPOSITORY_SPECS:
            raise ValueError("repository spec capacity exceeded")
        _SPECS_BY_TYPE = MappingProxyType(by_type)
        _SPECS_BY_ALIAS = MappingProxyType(by_alias)


def register_repository_specs(specs: tuple[RepositorySpec, ...]) -> None:
    """Register feature-owned repository providers during composition."""
    install_repository_specs(specs)


def repository_spec(repo_type: Hashable) -> RepositorySpec | None:
    with _LOCK:
        return _SPECS_BY_TYPE.get(repo_type)


def repository_spec_by_alias(alias: str) -> RepositorySpec | None:
    with _LOCK:
        return _SPECS_BY_ALIAS.get(alias)


def reset_repository_specs() -> None:
    global _SPECS_BY_TYPE, _SPECS_BY_ALIAS
    with _LOCK:
        _SPECS_BY_TYPE = MappingProxyType({})
        _SPECS_BY_ALIAS = MappingProxyType({})


def registered_repository_aliases() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_SPECS_BY_ALIAS))


def clear_repository_specs_for_tests() -> None:
    reset_repository_specs()


def register_feature_repositories(feature_id: str) -> None:
    """Register one feature's repository specs outside gateway composition."""
    from app.runtime.feature_catalog import load_feature

    # FeatureModule types repositories structurally; the catalog holds this registry's specs.
    register_repository_specs(cast(tuple[RepositorySpec, ...], tuple(load_feature(feature_id).repositories)))
