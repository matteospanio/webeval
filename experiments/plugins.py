"""The unified plugin surface: one decorator, one import, one discovery hook.

PANEL is scriptable by design — new question widgets, assignment
strategies, and pairwise strategies plug into the core through registries.
This module is the single front door to all of them:

* ``@plugin`` — register a plugin class; the *kind* is inferred from its base
  class (or passed explicitly with ``@plugin(kind="strategy")``).
* ``register(instance)`` — the same, for pre-built instances that need
  constructor arguments.
* ``installed_plugins()`` / ``get_plugin()`` — introspection (also exposed by
  ``manage.py plugins``).
* ``temporary_plugin(instance)`` — a context manager for tests.

Every base class a plugin author needs is re-exported here, so a plugin file
needs exactly one import::

    from experiments.plugins import plugin, QuestionComponent

    @plugin
    class StarRating(QuestionComponent):
        type = "star_rating"
        ...

Discovery: :meth:`ExperimentsConfig.ready` calls
``autodiscover_modules("panel_plugins")``, so any installed app that
defines a ``panel_plugins`` module has its registrations picked up at
startup — no core edits. (The older per-kind entry points — a
``question_components`` module, ``register_strategy()`` called from
``AppConfig.ready`` — keep working forever; this module delegates to those
registries rather than replacing them.)

This module deliberately imports only :mod:`experiments.components` and
:mod:`experiments.assignment` — never ``accounts``/``studio``/``survey`` —
to preserve the app dependency direction.
"""
from __future__ import annotations

import difflib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from experiments.assignment import (
    PairSpec,
    PairwiseStrategyBase,
    StrategyBase,
    register_pairwise_strategy,
    register_strategy,
)
from experiments.assignment import _PAIRWISE_REGISTRY, _REGISTRY as _STRATEGY_REGISTRY
from experiments.components import (
    BUILTIN_TYPES,
    MAX_TYPE_LENGTH,
    QuestionComponent,
    register_question_component,
)
from experiments.components import _REGISTRY as _COMPONENT_REGISTRY

__all__ = [
    # authoring surface (one-import convenience re-exports)
    "QuestionComponent",
    "StrategyBase",
    "PairwiseStrategyBase",
    "PairSpec",
    "BUILTIN_TYPES",
    "MAX_TYPE_LENGTH",
    # the unified API
    "plugin",
    "register",
    "get_plugin",
    "installed_plugins",
    "temporary_plugin",
    "PluginError",
    "UnknownPluginError",
    "PluginInfo",
]


class PluginError(ValueError):
    """A plugin registration is invalid. Raised at import time so a broken
    plugin fails loudly at startup instead of misbehaving at runtime."""


class UnknownPluginError(KeyError):
    """Lookup of a plugin kind/key that is not registered."""


# Experiment.assignment_strategy is a CharField(max_length=64).
_MAX_STRATEGY_NAME_LENGTH = 64


@dataclass(frozen=True)
class KindSpec:
    """Everything the unified API needs to know about one plugin kind."""

    kind: str
    base: type
    key_attr: str  # attribute holding the registry key ("type" or "name")
    max_key_length: int
    key_column: str  # DB column that imposes max_key_length (for errors)
    builtin_keys: frozenset[str]
    registry: dict[str, Any]  # the live registry dict (read for introspection)
    register_fn: Callable[[Any], Any]  # legacy writer — stays the enforcement of record


# Ordered: inference walks this list with isinstance(), so PairwiseStrategyBase
# must come before StrategyBase in case a future base ever subclasses another.
_KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        kind="question",
        base=QuestionComponent,
        key_attr="type",
        max_key_length=MAX_TYPE_LENGTH,
        key_column="Question.type",
        builtin_keys=frozenset(BUILTIN_TYPES),
        registry=_COMPONENT_REGISTRY,
        register_fn=register_question_component,
    ),
    KindSpec(
        kind="pairwise_strategy",
        base=PairwiseStrategyBase,
        key_attr="name",
        max_key_length=_MAX_STRATEGY_NAME_LENGTH,
        key_column="Experiment.assignment_strategy",
        builtin_keys=frozenset({"pairwise_balanced"}),
        registry=_PAIRWISE_REGISTRY,
        register_fn=register_pairwise_strategy,
    ),
    KindSpec(
        kind="strategy",
        base=StrategyBase,
        key_attr="name",
        max_key_length=_MAX_STRATEGY_NAME_LENGTH,
        key_column="Experiment.assignment_strategy",
        builtin_keys=frozenset(
            {"balanced_random", "block_random", "counterbalanced", "between_subject"}
        ),
        registry=_STRATEGY_REGISTRY,
        register_fn=register_strategy,
    ),
)

_KINDS_BY_NAME: dict[str, KindSpec] = {spec.kind: spec for spec in _KINDS}


def _spec_for_kind(kind: str) -> KindSpec:
    try:
        return _KINDS_BY_NAME[kind]
    except KeyError:
        valid = ", ".join(sorted(_KINDS_BY_NAME))
        hint = difflib.get_close_matches(kind, _KINDS_BY_NAME, n=1)
        suggestion = f" Did you mean '{hint[0]}'?" if hint else ""
        raise PluginError(
            f"Unknown plugin kind {kind!r}. Valid kinds: {valid}.{suggestion}"
        ) from None


def _spec_for_instance(instance: Any) -> KindSpec:
    for spec in _KINDS:
        if isinstance(instance, spec.base):
            return spec
    bases = ", ".join(spec.base.__name__ for spec in _KINDS)
    raise PluginError(
        f"{type(instance).__name__} does not subclass any plugin base class "
        f"({bases}). Either subclass one, or pass kind=... explicitly."
    )


def _impl_path(instance: Any) -> str:
    cls = type(instance)
    return f"{cls.__module__}.{cls.__qualname__}"


def register(instance: Any, *, kind: str | None = None, replace: bool = False) -> Any:
    """Register a plugin *instance* in the registry for its kind.

    The kind is inferred from the instance's base class unless given. Raises
    :class:`PluginError` on a missing/over-long key, a key that shadows a
    built-in, or a collision with a different already-registered class
    (pass ``replace=True`` to overwrite deliberately). Re-registering the
    *same* class under the same key is a silent no-op, so module re-imports
    (dev autoreload, tests) stay harmless.
    """
    spec = _spec_for_kind(kind) if kind is not None else _spec_for_instance(instance)
    if not isinstance(instance, spec.base):
        raise PluginError(
            f"kind='{spec.kind}' plugins must subclass {spec.base.__name__}; "
            f"{type(instance).__name__} does not."
        )

    key = getattr(instance, spec.key_attr, "")
    if not key or not isinstance(key, str):
        raise PluginError(
            f"{type(instance).__name__}.{spec.key_attr} must be a non-empty "
            f"string — it is the key the plugin is looked up by."
        )
    if len(key) > spec.max_key_length:
        raise PluginError(
            f"{type(instance).__name__}.{spec.key_attr} = {key!r} is "
            f"{len(key)} characters; {spec.key_column} allows at most "
            f"{spec.max_key_length}."
        )
    if key in spec.builtin_keys:
        raise PluginError(
            f"{key!r} shadows a built-in {spec.kind} — pick another "
            f"{spec.key_attr}."
        )

    existing = spec.registry.get(key)
    if existing is not None and not replace:
        if _impl_path(existing) == _impl_path(instance):
            return instance  # same class re-registered — idempotent no-op
        raise PluginError(
            f"A {spec.kind} plugin with {spec.key_attr}={key!r} is already "
            f"registered by {_impl_path(existing)}. Pass replace=True to "
            f"overwrite it."
        )

    # Delegate the actual write to the legacy register function so its checks
    # (and any project code that patched it) remain the enforcement of record.
    spec.register_fn(instance)
    return instance


def plugin(cls: type | None = None, *, kind: str | None = None):
    """Class decorator: instantiate the class (no args) and register it.

    Usable bare — the kind is inferred from the base class::

        @plugin
        class MyWidget(QuestionComponent): ...

    or with an explicit kind::

        @plugin(kind="strategy")
        class MyOrder(StrategyBase): ...

    Classes whose ``__init__`` needs arguments should build the instance
    themselves and call :func:`register`.
    """

    def _decorate(klass: type):
        if not isinstance(klass, type):
            raise PluginError(
                "@plugin decorates a class; got "
                f"{type(klass).__name__} ({klass!r}). To register a pre-built "
                "instance, call register(instance) instead."
            )
        register(klass(), kind=kind)
        return klass

    return _decorate(cls) if cls is not None else _decorate


@dataclass(frozen=True)
class PluginInfo:
    """One row of the installed-plugin table."""

    kind: str
    key: str
    label: str
    impl: str  # dotted path of the implementing class
    builtin: bool  # ships with PANEL (vs a third-party registration)


def _label_for(instance: Any) -> str:
    label = getattr(instance, "label", "")
    if label:
        return label
    doc = (type(instance).__doc__ or "").strip()
    return doc.splitlines()[0] if doc else ""


def installed_plugins(kind: str | None = None) -> tuple[PluginInfo, ...]:
    """Every registered plugin (built-ins included), optionally one kind."""
    specs = (_spec_for_kind(kind),) if kind is not None else _KINDS
    rows: list[PluginInfo] = []
    for spec in specs:
        for key, instance in spec.registry.items():
            impl = _impl_path(instance)
            rows.append(
                PluginInfo(
                    kind=spec.kind,
                    key=key,
                    label=_label_for(instance),
                    impl=impl,
                    builtin=impl.startswith("experiments."),
                )
            )
    return tuple(rows)


def get_plugin(kind: str, key: str) -> Any:
    """Return the registered instance, or raise :class:`UnknownPluginError`
    with the installed keys (and a did-you-mean when one is close)."""
    spec = _spec_for_kind(kind)
    try:
        return spec.registry[key]
    except KeyError:
        installed = ", ".join(sorted(spec.registry)) or "(none)"
        hint = difflib.get_close_matches(key, spec.registry, n=1)
        suggestion = f" Did you mean {hint[0]!r}?" if hint else ""
        raise UnknownPluginError(
            f"No {kind} plugin with key {key!r}. Installed: {installed}."
            f"{suggestion}"
        ) from None


@contextmanager
def temporary_plugin(instance: Any, *, kind: str | None = None) -> Iterator[Any]:
    """Register ``instance`` for the duration of a ``with`` block (tests).

    Restores the previous registry entry (or removes the key) on exit. Note
    that DB rows referencing the temporary key must be created *and used*
    inside the block — afterwards they become orphaned types.
    """
    spec = _spec_for_kind(kind) if kind is not None else _spec_for_instance(instance)
    key = getattr(instance, spec.key_attr, "")
    previous = spec.registry.get(key)
    register(instance, kind=spec.kind, replace=True)
    try:
        yield instance
    finally:
        if previous is not None:
            spec.registry[key] = previous
        else:
            spec.registry.pop(key, None)
