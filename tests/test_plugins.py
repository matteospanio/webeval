"""The unified @plugin surface (experiments/plugins.py)."""
from __future__ import annotations

import random

import pytest
from django.core import checks
from django.core.management import call_command

from experiments.plugins import (
    PairwiseStrategyBase,
    PluginError,
    QuestionComponent,
    StrategyBase,
    UnknownPluginError,
    get_plugin,
    installed_plugins,
    plugin,
    register,
    temporary_plugin,
)


class _Widget(QuestionComponent):
    type = "t_widget"
    label = "Test widget"

    def render(self, question, *, post=None):
        return ""

    def read_answer(self, post, question):
        return False, None, None


class _Order(StrategyBase):
    """Test ordering strategy."""

    name = "t_order"

    def select(self, experiment, n, counts, rng=None, participant_index=None):
        return []


class _PairOrder(PairwiseStrategyBase):
    """Test pairwise strategy."""

    name = "t_pairs"

    def select_pairs(self, experiment, n, pair_counts, stimulus_counts=None, rng=None):
        return []


def _cleanup(kind: str, key: str):
    from experiments.plugins import _spec_for_kind

    _spec_for_kind(kind).registry.pop(key, None)


# --- registration & kind inference ------------------------------------------


def test_bare_decorator_infers_each_kind():
    try:
        plugin(_Widget)
        plugin(_Order)
        plugin(_PairOrder)
        assert isinstance(get_plugin("question", "t_widget"), _Widget)
        assert isinstance(get_plugin("strategy", "t_order"), _Order)
        assert isinstance(get_plugin("pairwise_strategy", "t_pairs"), _PairOrder)
    finally:
        _cleanup("question", "t_widget")
        _cleanup("strategy", "t_order")
        _cleanup("pairwise_strategy", "t_pairs")


def test_explicit_kind_and_mismatch():
    try:
        plugin(kind="strategy")(_Order)
        assert get_plugin("strategy", "t_order")
    finally:
        _cleanup("strategy", "t_order")
    with pytest.raises(PluginError, match="must subclass"):
        plugin(kind="question")(_Order)


def test_registered_plugin_reaches_legacy_registry():
    # The unified path delegates to the legacy registries, so every existing
    # consumer (survey dispatch, admin dropdowns, builder palette) sees it.
    from experiments.assignment import get_strategy
    from experiments.components import is_question_component

    try:
        plugin(_Widget)
        plugin(_Order)
        assert is_question_component("t_widget")
        assert get_strategy("t_order")
    finally:
        _cleanup("question", "t_widget")
        _cleanup("strategy", "t_order")


def test_legacy_registrations_visible_to_unified_api():
    # Built-ins registered via the legacy paths appear in installed_plugins().
    rows = {(r.kind, r.key): r for r in installed_plugins()}
    assert rows[("question", "constant_sum")].builtin
    assert rows[("strategy", "balanced_random")].builtin
    assert rows[("pairwise_strategy", "pairwise_balanced")].builtin
    strategies = installed_plugins("strategy")
    assert {r.key for r in strategies} >= {"balanced_random", "counterbalanced"}


# --- error messages ----------------------------------------------------------


def test_unknown_kind_suggests_close_match():
    with pytest.raises(PluginError, match="Did you mean 'strategy'"):
        register(_Order(), kind="stratgy")


def test_non_plugin_class_names_bases():
    class _Loose:
        name = "loose"

    with pytest.raises(PluginError, match="does not subclass"):
        register(_Loose())


def test_missing_key_names_class_and_attribute():
    class _NoKey(StrategyBase):
        name = ""

    with pytest.raises(PluginError, match=r"_NoKey.name must be a non-empty"):
        register(_NoKey())


def test_over_long_key_names_column_and_limit():
    class _LongKey(QuestionComponent):
        type = "x" * 17

    with pytest.raises(PluginError, match=r"Question\.type allows at most 16"):
        register(_LongKey())


def test_builtin_shadowing_rejected():
    class _Shadow(StrategyBase):
        name = "balanced_random"

    with pytest.raises(PluginError, match="shadows a built-in"):
        register(_Shadow())


def test_collision_names_owner_and_replace_escape_hatch():
    class _First(StrategyBase):
        name = "t_clash"

    class _Second(StrategyBase):
        name = "t_clash"

    try:
        register(_First())
        with pytest.raises(PluginError, match=r"_First.*replace=True"):
            register(_Second())
        register(_Second(), replace=True)
        assert isinstance(get_plugin("strategy", "t_clash"), _Second)
    finally:
        _cleanup("strategy", "t_clash")


def test_same_class_reregistration_is_idempotent():
    try:
        register(_Order())
        register(_Order())  # module re-import (autoreload/tests) — no error
        assert isinstance(get_plugin("strategy", "t_order"), _Order)
    finally:
        _cleanup("strategy", "t_order")


def test_get_plugin_unknown_key_lists_installed_and_suggests():
    with pytest.raises(UnknownPluginError, match="balanced_random"):
        get_plugin("strategy", "balanced_randm")


def test_decorating_an_instance_is_an_error():
    with pytest.raises(PluginError, match="decorates a class"):
        plugin(_Order())


# --- temporary_plugin (test helper) ------------------------------------------


def test_temporary_plugin_registers_and_restores():
    assert "t_order" not in {r.key for r in installed_plugins("strategy")}
    with temporary_plugin(_Order()):
        assert get_plugin("strategy", "t_order")
    with pytest.raises(UnknownPluginError):
        get_plugin("strategy", "t_order")


def test_temporary_plugin_restores_previous_registration():
    class _Replacement(StrategyBase):
        name = "t_order"

    try:
        register(_Order())
        with temporary_plugin(_Replacement()):
            assert isinstance(get_plugin("strategy", "t_order"), _Replacement)
        assert isinstance(get_plugin("strategy", "t_order"), _Order)
    finally:
        _cleanup("strategy", "t_order")


# --- strategies registered via @plugin actually run --------------------------


@pytest.mark.django_db
def test_plugin_strategy_selects_stimuli():
    from experiments.tests.factories import ConditionFactory, ExperimentFactory, TextStimulusFactory

    class _FirstN(StrategyBase):
        name = "t_first_n"

        def select(self, experiment, n, counts, rng=None, participant_index=None):
            from experiments.models import Stimulus

            pool = list(
                Stimulus.objects.filter(
                    condition__experiment=experiment, is_active=True
                ).order_by("id")
            )
            return pool[: n or len(pool)]

    exp = ExperimentFactory()
    cond = ConditionFactory(experiment=exp)
    stims = [TextStimulusFactory(condition=cond) for _ in range(3)]
    with temporary_plugin(_FirstN()):
        chosen = get_plugin("strategy", "t_first_n").select(
            exp, 2, {}, rng=random.Random(0)
        )
    assert [s.id for s in chosen] == [stims[0].id, stims[1].id]


# --- management command ------------------------------------------------------


def test_plugins_command_lists_installed(capsys):
    call_command("plugins")
    out = capsys.readouterr().out
    assert "constant_sum" in out
    assert "balanced_random" in out
    assert "built-in" in out


def test_plugins_command_kind_filter(capsys):
    call_command("plugins", "--kind", "question")
    out = capsys.readouterr().out
    assert "constant_sum" in out
    assert "balanced_random" not in out


# --- system check: orphaned question types ------------------------------------


@pytest.mark.django_db
def test_orphaned_type_system_check_warns():
    from experiments.checks import check_orphaned_question_types
    from experiments.models import Question
    from experiments.tests.factories import ExperimentFactory

    exp = ExperimentFactory()
    Question.objects.create(
        experiment=exp,
        section=Question.Section.STIMULUS,
        type="gone_plugin",  # was authored by a plugin app since removed
        prompt="Orphaned",
        config={},
    )
    results = check_orphaned_question_types(None, databases=["default"])
    assert len(results) == 1
    assert isinstance(results[0], checks.Warning)
    assert "gone_plugin" in results[0].msg
    assert results[0].id == "experiments.W001"


@pytest.mark.django_db
def test_orphaned_type_system_check_quiet_when_clean():
    from experiments.checks import check_orphaned_question_types

    assert check_orphaned_question_types(None, databases=["default"]) == []
    # And without a database in play the check is skipped entirely.
    assert check_orphaned_question_types(None, databases=None) == []
