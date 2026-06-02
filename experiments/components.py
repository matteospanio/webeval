"""Pluggable question-type *components*.

A **component** lets a developer add a brand-new question widget to webeval
without editing the core: it bundles the four things a question type needs to
work end-to-end —

* ``validate_config`` — check the authored ``config`` dict,
* ``render`` — emit the participant-facing widget HTML,
* ``read_answer`` — parse a submitted answer out of ``request.POST``,
* (a ``label`` for the admin / a future drag-&-drop builder).

This mirrors the pluggable assignment-strategy pattern in
:mod:`experiments.assignment` (a base class + a registry + ``register`` /
``get`` / ``available`` helpers). Components are **additive**: the built-in
types (rating, choice, text, likert, numeric, matrix, ranking) keep their
hard-coded paths; a registered component adds a *new* ``Question.type`` value
alongside them. The survey renderer/parser and the model/admin validators
fall back to the registry for any type they don't recognise.

Third-party apps register components by dropping a ``question_components``
module that calls :func:`register_question_component` (or uses the
``@question_component`` class decorator); :meth:`ExperimentsConfig.ready`
auto-discovers them.
"""
from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join

# Built-in Question.type values keep their hard-coded handling; a component
# may not shadow one of them. Kept as plain strings to avoid importing the
# model at module-import time.
BUILTIN_TYPES = frozenset(
    {"rating", "choice", "text", "likert", "numeric", "matrix", "ranking"}
)

# Question.type is a CharField(max_length=16); a component's key must fit.
MAX_TYPE_LENGTH = 16


class QuestionComponent:
    """Base class for a pluggable question-type widget.

    Subclasses set ``type`` (the stored ``Question.type`` value) and ``label``
    (human-readable) and implement :meth:`render` and :meth:`read_answer`.
    ``validate_config`` is optional (default: accept any dict).
    """

    type: str = ""
    label: str = ""

    def validate_config(self, config: dict) -> None:
        """Raise ``ValidationError`` if the authored ``config`` is malformed."""

    def render(self, question, *, post=None) -> str:
        """Return the inner widget HTML (placed inside the shared fieldset).

        ``post`` is ``request.POST`` on a validation-error re-render (so the
        widget can repopulate the participant's entries), otherwise ``None``.
        Must return a safe string (use :func:`django.utils.html.format_html`).
        """
        raise NotImplementedError

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        """Parse this question's answer from ``post`` (a ``QueryDict``).

        Returns ``(answered, value, error)`` exactly like
        ``survey/views.py::_read_one``: whether any input was given, the
        JSON-serialisable value, and a human-readable problem (or ``None``).
        """
        raise NotImplementedError


_REGISTRY: dict[str, QuestionComponent] = {}


def register_question_component(component: QuestionComponent) -> QuestionComponent:
    """Register a component instance (idempotent by ``type``)."""
    if not component.type:
        raise ValueError("component.type must be a non-empty string")
    if component.type in BUILTIN_TYPES:
        raise ValueError(
            f"'{component.type}' shadows a built-in question type; pick another key"
        )
    if len(component.type) > MAX_TYPE_LENGTH:
        raise ValueError(
            f"component.type must be ≤ {MAX_TYPE_LENGTH} characters "
            f"(got {component.type!r})"
        )
    _REGISTRY[component.type] = component
    return component


def question_component(cls):
    """Class decorator: instantiate a :class:`QuestionComponent` and register it."""
    register_question_component(cls())
    return cls


def get_question_component(type_: str) -> QuestionComponent:
    return _REGISTRY[type_]


def is_question_component(type_: str) -> bool:
    return type_ in _REGISTRY


def available_question_components() -> tuple[QuestionComponent, ...]:
    return tuple(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Shipped example component: constant-sum (distribute N points across items).
# A genuinely new widget the built-in types can't express — proof that the
# plugin surface is complete end-to-end.
# ---------------------------------------------------------------------------


@question_component
class ConstantSumComponent(QuestionComponent):
    """Distribute a fixed number of points across several items.

    config: ``{"items": [str, ...], "total": int}`` (``total`` defaults 100).
    Answer: ``{item_label: points}``; the points must add up to ``total``.
    """

    type = "constant_sum"
    label = "Constant sum (allocate points)"

    def validate_config(self, config: dict) -> None:
        items = config.get("items")
        clean = [i for i in items if isinstance(i, str) and i.strip()] if isinstance(
            items, list
        ) else []
        if len(clean) < 2:
            raise ValidationError(
                {"config": "constant_sum needs an 'items' list of ≥2 non-empty strings."}
            )
        if len(set(items)) != len(items):
            raise ValidationError({"config": "constant_sum items must be distinct."})
        total = config.get("total", 100)
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
            raise ValidationError(
                {"config": "constant_sum 'total' must be a positive integer."}
            )

    def render(self, question, *, post=None) -> str:
        cfg = question.config or {}
        items = cfg.get("items") or []
        total = cfg.get("total", 100)

        def _value(i: int) -> str:
            if post is None:
                return ""
            return post.get(f"q_{question.pk}_i{i}", "")

        rows = format_html_join(
            "",
            '<li class="constant-sum-item"><label><span>{}</span>'
            '<input type="number" name="q_{}_i{}" min="0" max="{}" step="1" '
            'inputmode="numeric" value="{}"></label></li>',
            (
                (item, question.pk, i, total, _value(i))
                for i, item in enumerate(items)
            ),
        )
        return format_html(
            '<ol class="constant-sum-list" data-total="{}">{}</ol>'
            '<small class="constant-sum-hint">Distribute exactly {} points '
            "across the items.</small>",
            total,
            rows,
            total,
        )

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        cfg = question.config or {}
        items = cfg.get("items") or []
        total = cfg.get("total", 100)
        values: dict[str, int] = {}
        answered_any = False
        for i, item in enumerate(items):
            raw = post.get(f"q_{question.pk}_i{i}", "")
            if raw == "" or raw is None:
                continue
            answered_any = True
            try:
                points = int(raw)
            except (TypeError, ValueError):
                return True, None, "must use whole numbers"
            if points < 0:
                return True, None, "cannot be negative"
            values[item] = points
        if not answered_any:
            return False, None, None
        if len(values) != len(items):
            return True, None, "needs a value for every item"
        if sum(values.values()) != total:
            return True, None, f"must add up to {total}"
        return True, values, None
