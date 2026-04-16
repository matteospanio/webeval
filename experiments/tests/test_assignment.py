"""Tests for experiments.assignment: pluggable strategy interface + the initial
BalancedRandomStrategy implementation."""
from __future__ import annotations

import random
from collections import Counter

import pytest

from experiments.assignment import (
    BalancedRandomStrategy,
    UnknownStrategyError,
    get_strategy,
)
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    StimulusFactory,
)

pytestmark = pytest.mark.django_db


class TestStrategyRegistry:
    def test_balanced_random_is_registered_under_its_name(self):
        strategy = get_strategy("balanced_random")
        assert isinstance(strategy, BalancedRandomStrategy)

    def test_unknown_strategy_raises(self):
        with pytest.raises(UnknownStrategyError):
            get_strategy("does_not_exist")


class TestBalancedRandomStrategy:
    def _setup_two_by_three(self):
        exp = ExperimentFactory()
        c_a = ConditionFactory(experiment=exp, name="A")
        c_b = ConditionFactory(experiment=exp, name="B")
        stimuli_a = [StimulusFactory(condition=c_a, is_active=True) for _ in range(3)]
        stimuli_b = [StimulusFactory(condition=c_b, is_active=True) for _ in range(3)]
        return exp, c_a, c_b, stimuli_a, stimuli_b

    def test_returns_empty_list_when_no_stimuli(self):
        exp = ExperimentFactory()
        strategy = BalancedRandomStrategy()
        assert strategy.select(experiment=exp, n=4, counts={}) == []

    def test_excludes_inactive_stimuli(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        active = StimulusFactory(condition=cond, is_active=True)
        StimulusFactory(condition=cond, is_active=False)
        strategy = BalancedRandomStrategy()
        picked = strategy.select(experiment=exp, n=5, counts={})
        assert [s.id for s in picked] == [active.id]

    def test_respects_n_limit(self):
        exp, *_ = self._setup_two_by_three()
        strategy = BalancedRandomStrategy()
        picked = strategy.select(experiment=exp, n=2, counts={})
        assert len(picked) == 2

    def test_n_none_returns_all_active_stimuli(self):
        exp, *_ = self._setup_two_by_three()
        strategy = BalancedRandomStrategy()
        picked = strategy.select(experiment=exp, n=None, counts={})
        assert len(picked) == 6

    def test_single_selection_balances_conditions(self):
        exp, c_a, c_b, *_ = self._setup_two_by_three()
        strategy = BalancedRandomStrategy()
        # n=2: one stimulus from each of the two conditions.
        picked = strategy.select(experiment=exp, n=2, counts={})
        cond_ids = {s.condition_id for s in picked}
        assert cond_ids == {c_a.id, c_b.id}

    def test_no_duplicate_stimuli_in_one_selection(self):
        exp, *_ = self._setup_two_by_three()
        strategy = BalancedRandomStrategy()
        picked = strategy.select(experiment=exp, n=6, counts={})
        ids = [s.id for s in picked]
        assert len(ids) == len(set(ids))

    def test_repeated_calls_balance_conditions_over_many_participants(self):
        exp, c_a, c_b, *_ = self._setup_two_by_three()
        strategy = BalancedRandomStrategy()
        rng = random.Random(0)

        counts: Counter = Counter()
        by_condition: Counter = Counter()
        for _ in range(400):
            picked = strategy.select(experiment=exp, n=2, counts=dict(counts), rng=rng)
            for s in picked:
                counts[s.id] += 1
                by_condition[s.condition_id] += 1

        # Perfect balance would be 400 picks per condition (400 participants * n=2 / 2 conditions).
        diff = abs(by_condition[c_a.id] - by_condition[c_b.id])
        assert diff <= 20, f"conditions imbalanced: {dict(by_condition)}"

    def test_reproducible_with_seeded_rng(self):
        exp, *_ = self._setup_two_by_three()
        strategy = BalancedRandomStrategy()
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        picked_a = strategy.select(experiment=exp, n=4, counts={}, rng=rng_a)
        picked_b = strategy.select(experiment=exp, n=4, counts={}, rng=rng_b)
        assert [s.id for s in picked_a] == [s.id for s in picked_b]
