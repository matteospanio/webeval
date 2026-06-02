"""Unit tests for the advanced assignment strategies."""
from __future__ import annotations

import random

import pytest

from experiments.assignment import available_strategies, get_strategy
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    StimulusFactory,
)

pytestmark = pytest.mark.django_db


def _exp_with_conditions(n_conditions=2, per_condition=3):
    exp = ExperimentFactory()
    conds = [
        ConditionFactory(experiment=exp, name=f"c{i}") for i in range(n_conditions)
    ]
    for c in conds:
        for j in range(per_condition):
            StimulusFactory(condition=c, title=f"{c.name}-{j}")
    return exp, conds


def _rng():
    return random.Random(42)


def test_new_strategies_are_registered():
    for name in ("balanced_random", "block_random", "counterbalanced", "between_subject"):
        assert name in available_strategies()
        assert get_strategy(name).name == name


def test_block_random_first_block_is_one_per_condition():
    exp, _ = _exp_with_conditions(2, 3)
    result = get_strategy("block_random").select(exp, None, {}, rng=_rng())
    assert len(result) == 6
    assert {s.condition_id for s in result[:2]} == {
        s.condition_id for s in result
    }  # both conditions appear in the first block


def test_counterbalanced_rotates_first_condition_by_participant():
    exp, _ = _exp_with_conditions(2, 2)
    strat = get_strategy("counterbalanced")
    r0 = strat.select(exp, None, {}, rng=_rng(), participant_index=0)
    r1 = strat.select(exp, None, {}, rng=_rng(), participant_index=1)
    assert r0[0].condition_id != r1[0].condition_id


def test_between_subject_assigns_one_condition_round_robin():
    exp, _ = _exp_with_conditions(2, 3)
    strat = get_strategy("between_subject")
    r0 = strat.select(exp, None, {}, rng=_rng(), participant_index=0)
    r1 = strat.select(exp, None, {}, rng=_rng(), participant_index=1)
    r2 = strat.select(exp, None, {}, rng=_rng(), participant_index=2)

    assert len({s.condition_id for s in r0}) == 1
    assert len({s.condition_id for s in r1}) == 1
    assert r0[0].condition_id != r1[0].condition_id  # balanced across participants
    assert r2[0].condition_id == r0[0].condition_id  # wraps with 2 conditions


def test_between_subject_respects_n():
    exp, _ = _exp_with_conditions(2, 5)
    result = get_strategy("between_subject").select(
        exp, 2, {}, rng=_rng(), participant_index=0
    )
    assert len(result) == 2
    assert len({s.condition_id for s in result}) == 1
