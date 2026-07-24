"""Built-in question types as :class:`QuestionComponent` classes.

Historically the seven built-in question types (rating / choice / text / likert
/ numeric / matrix / ranking) had their config-validation spread in
``experiments.models._validate_question_config``, their answer-parsing in
``survey.views._read_one`` & friends, and their builder defaults in
``studio.views._BUILTIN_DEFAULT_CONFIG`` — a new or changed type meant editing
several files. Each built-in now lives here as one component class carrying its
``default_config`` / ``validate_config`` / ``read_answer``, exactly like a
plugin. :func:`resolve_component` is the single dispatch that returns the
component for *any* type (built-in or plugin), so the models validator, the
survey parser and the studio builder all go through one door.

Built-ins live in their own ``_BUILTINS`` map (kept out of the plugin
``_REGISTRY``) so the plugin-only semantics of ``is_question_component`` /
``available_question_components`` / the built-in-shadowing guard are unchanged.
Rendering stays in the ``survey/_question.html`` template branches (a view
concern) and per-type analysis stays in ``experiments.analysis`` (an analysis
concern); this module owns a type's *input handling*.
"""
from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from experiments.components import QuestionComponent, get_question_component, is_question_component

# Question.type string values (kept as literals to avoid importing the model at
# module-load time — mirrors experiments.components.BUILTIN_TYPES).
RATING = "rating"
CHOICE = "choice"
TEXT = "text"
LIKERT = "likert"
NUMERIC = "numeric"
MATRIX = "matrix"
RANKING = "ranking"


def _key(question) -> str:
    return f"q_{question.pk}"


class RatingComponent(QuestionComponent):
    type = RATING
    label = "Rating slider"

    def default_config(self) -> dict:
        return {"min": 0, "max": 100, "step": 1}

    def validate_config(self, config: dict) -> None:
        required = {"min", "max", "step"}
        missing = required - config.keys()
        if missing:
            raise ValidationError(
                {"config": f"rating questions require keys {sorted(required)}; "
                           f"missing {sorted(missing)}."}
            )
        try:
            low, high, step = int(config["min"]), int(config["max"]), int(config["step"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"config": "rating min/max/step must be integers."}
            ) from exc
        if step <= 0:
            raise ValidationError({"config": "rating step must be positive."})
        if low >= high:
            raise ValidationError(
                {"config": "rating min must be strictly less than max."}
            )
        for label_key in ("min_label", "max_label"):
            if label_key in config and not isinstance(config[label_key], str):
                raise ValidationError(
                    {"config": f"rating {label_key!r} must be a string if present."}
                )

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        raw = post.get(_key(question))
        if raw is None or raw == "":
            return False, None, None
        try:
            return True, int(raw), None
        except (TypeError, ValueError):
            return True, None, "has an invalid value"


class LikertComponent(QuestionComponent):
    type = LIKERT
    label = "Likert scale"

    def default_config(self) -> dict:
        return {
            "steps": 5,
            "labels": [
                "Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"
            ],
        }

    def validate_config(self, config: dict) -> None:
        steps = config.get("steps")
        if not isinstance(steps, int) or not (2 <= steps <= 11):
            raise ValidationError(
                {"config": "likert questions require an integer 'steps' between 2 and 11."}
            )
        labels = config.get("labels")
        if not isinstance(labels, list) or len(labels) != steps:
            raise ValidationError(
                {"config": f"likert questions require a 'labels' list of exactly {steps} strings."}
            )
        if not all(isinstance(lb, str) and lb for lb in labels):
            raise ValidationError(
                {"config": "every likert label must be a non-empty string."}
            )

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        raw = post.get(_key(question))
        if raw is None or raw == "":
            return False, None, None
        try:
            return True, int(raw), None
        except (TypeError, ValueError):
            return True, None, "has an invalid value"


class TextComponent(QuestionComponent):
    type = TEXT
    label = "Free text"

    def default_config(self) -> dict:
        return {"max_length": 500}

    def validate_config(self, config: dict) -> None:
        max_length = config.get("max_length")
        if not isinstance(max_length, int) or max_length <= 0:
            raise ValidationError(
                {"config": "text questions require a positive integer 'max_length'."}
            )

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        raw = post.get(_key(question))
        if raw is None or raw == "":
            return False, None, None
        return True, str(raw), None


class ChoiceComponent(QuestionComponent):
    type = CHOICE
    label = "Multiple choice"

    def default_config(self) -> dict:
        return {"choices": ["Option 1", "Option 2"], "multi": False}

    def validate_config(self, config: dict) -> None:
        choices = config.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValidationError(
                {"config": "choice questions require a non-empty 'choices' list."}
            )
        if not all(isinstance(c, str) and c for c in choices):
            raise ValidationError(
                {"config": "every choice must be a non-empty string."}
            )

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        if (question.config or {}).get("multi"):
            raw_list = post.getlist(_key(question))
            if not raw_list:
                return False, None, None
            return True, list(raw_list), None
        raw = post.get(_key(question))
        if raw is None or raw == "":
            return False, None, None
        return True, str(raw), None


class NumericComponent(QuestionComponent):
    type = NUMERIC
    label = "Numeric input"

    def default_config(self) -> dict:
        return {}

    def validate_config(self, config: dict) -> None:
        for key in ("min", "max"):
            if key in config:
                val = config[key]
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValidationError({"config": f"numeric {key!r} must be a number."})
        low, high = config.get("min"), config.get("max")
        if low is not None and high is not None and low >= high:
            raise ValidationError({"config": "numeric 'min' must be less than 'max'."})
        if "integer" in config and not isinstance(config["integer"], bool):
            raise ValidationError({"config": "numeric 'integer' must be true or false."})
        if "unit" in config and not isinstance(config["unit"], str):
            raise ValidationError({"config": "numeric 'unit' must be a string."})

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        raw = post.get(_key(question))
        if raw is None or raw == "":
            return False, None, None
        cfg = question.config or {}
        try:
            value: float | int = float(raw)
        except (TypeError, ValueError):
            return True, None, "must be a number"
        if cfg.get("integer"):
            if value != int(value):
                return True, None, "must be a whole number"
            value = int(value)
        low, high = cfg.get("min"), cfg.get("max")
        if low is not None and value < low:
            return True, None, f"must be at least {low}"
        if high is not None and value > high:
            return True, None, f"must be at most {high}"
        return True, value, None


class MatrixComponent(QuestionComponent):
    type = MATRIX
    label = "Matrix (grid)"

    def default_config(self) -> dict:
        return {"rows": ["Row 1"], "columns": ["Column 1", "Column 2"]}

    def validate_config(self, config: dict) -> None:
        rows = config.get("rows")
        columns = config.get("columns")
        if not isinstance(rows, list) or not rows or not all(
            isinstance(r, str) and r for r in rows
        ):
            raise ValidationError(
                {"config": "matrix questions require a non-empty 'rows' list of strings."}
            )
        if len(set(rows)) != len(rows):
            raise ValidationError({"config": "matrix 'rows' must be distinct."})
        if not isinstance(columns, list) or not columns or not all(
            isinstance(c, str) and c for c in columns
        ):
            raise ValidationError(
                {"config": "matrix questions require a non-empty 'columns' list of strings."}
            )

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        cfg = question.config or {}
        rows = cfg.get("rows") or []
        columns = set(cfg.get("columns") or [])
        answer: dict[str, str] = {}
        answered_any = False
        for i, row in enumerate(rows):
            val = post.get(f"q_{question.pk}_r{i}")
            if val:
                answered_any = True
                if val not in columns:
                    return True, None, "has an invalid value"
                answer[row] = val
        if not answered_any:
            return False, None, None
        if question.required and len(answer) != len(rows):
            return True, None, "needs an answer in every row"
        return True, answer, None


class RankingComponent(QuestionComponent):
    type = RANKING
    label = "Ranking / ordering"

    def default_config(self) -> dict:
        return {"items": ["Item 1", "Item 2"]}

    def validate_config(self, config: dict) -> None:
        items = config.get("items")
        if not isinstance(items, list) or len(items) < 2 or not all(
            isinstance(i, str) and i for i in items
        ):
            raise ValidationError(
                {"config": "ranking questions require an 'items' list of at least two strings."}
            )
        if len(set(items)) != len(items):
            raise ValidationError({"config": "ranking 'items' must be distinct."})

    def read_answer(self, post, question) -> tuple[bool, Any, str | None]:
        cfg = question.config or {}
        items = cfg.get("items") or []
        n = len(items)
        ranks: dict[int, int] = {}
        answered_any = False
        for i in range(n):
            val = post.get(f"q_{question.pk}_i{i}")
            if val:
                answered_any = True
                try:
                    ranks[i] = int(val)
                except (TypeError, ValueError):
                    return True, None, "has an invalid rank"
        if not answered_any:
            return False, None, None
        if len(ranks) != n or sorted(ranks.values()) != list(range(1, n + 1)):
            return True, None, "needs a unique rank for every item"
        ordered = [items[i] for i, _ in sorted(ranks.items(), key=lambda kv: kv[1])]
        return True, ordered, None


_BUILTINS: dict[str, QuestionComponent] = {
    c.type: c
    for c in (
        RatingComponent(),
        ChoiceComponent(),
        TextComponent(),
        LikertComponent(),
        NumericComponent(),
        MatrixComponent(),
        RankingComponent(),
    )
}


def resolve_component(question_type: str) -> QuestionComponent | None:
    """Return the component that handles ``question_type`` — a built-in or a
    registered plugin — or ``None`` for an unknown type. Single dispatch for
    config validation, answer parsing and builder defaults."""
    builtin = _BUILTINS.get(question_type)
    if builtin is not None:
        return builtin
    if is_question_component(question_type):
        return get_question_component(question_type)
    return None


def builtin_default_config() -> dict[str, dict]:
    """``{type: default_config}`` for every built-in — the builder palette's
    seed configs."""
    return {t: c.default_config() for t, c in _BUILTINS.items()}
