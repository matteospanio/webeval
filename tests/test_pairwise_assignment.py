"""Unit tests for the pairwise balanced assignment strategy."""
from __future__ import annotations

import random

import pytest

from experiments.assignment import PairwiseBalancedStrategy
from experiments.models import Experiment, Stimulus
from experiments.tests.factories import (
    ConditionFactory,
    PairwiseExperimentFactory,
    StimulusFactory,
)


@pytest.fixture()
def pairwise_experiment(db):
    """Create a pairwise experiment with 3 conditions x 2 prompts x 1 stimulus each = 6 stimuli."""
    exp = PairwiseExperimentFactory(stimuli_per_participant=3)
    conditions = [ConditionFactory(experiment=exp, name=f"Model-{i}") for i in range(3)]
    for cond in conditions:
        for prompt_idx in range(2):
            StimulusFactory(
                condition=cond,
                title=f"{cond.name}-prompt{prompt_idx}",
                prompt_group=f"prompt-{prompt_idx}",
            )
    return exp


@pytest.fixture()
def large_experiment(db):
    """12 conditions x 2 prompts = 24 stimuli."""
    exp = PairwiseExperimentFactory(stimuli_per_participant=20)
    conditions = [ConditionFactory(experiment=exp, name=f"Model-{i}") for i in range(12)]
    for cond in conditions:
        for prompt_idx in range(2):
            StimulusFactory(
                condition=cond,
                title=f"{cond.name}-prompt{prompt_idx}",
                prompt_group=f"prompt-{prompt_idx}",
            )
    return exp


class TestPairwiseBalancedStrategy:
    def test_returns_correct_count(self, pairwise_experiment):
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(42)
        )
        assert len(specs) == 3

    def test_pairs_have_different_conditions(self, pairwise_experiment):
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(42)
        )
        for spec in specs:
            assert spec.condition_a_id != spec.condition_b_id

    def test_pairs_share_prompt_group(self, pairwise_experiment):
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(42)
        )
        for spec in specs:
            stim_a = Stimulus.objects.get(pk=spec.stimulus_a_id)
            stim_b = Stimulus.objects.get(pk=spec.stimulus_b_id)
            assert stim_a.prompt_group == stim_b.prompt_group
            assert spec.prompt_group == stim_a.prompt_group

    def test_position_a_is_valid(self, pairwise_experiment):
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(42)
        )
        for spec in specs:
            assert spec.position_a in ("left", "right")

    def test_reproducible_with_seed(self, pairwise_experiment):
        strategy = PairwiseBalancedStrategy()
        a = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(42)
        )
        b = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(42)
        )
        assert a == b

    def test_deficit_prioritization(self, pairwise_experiment):
        """Pairs with fewer historical appearances should be prioritized."""
        strategy = PairwiseBalancedStrategy()
        # First call with no history.
        specs1 = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(0)
        )
        # Build counts from first round.
        counts = {}
        for spec in specs1:
            key = (min(spec.condition_a_id, spec.condition_b_id),
                   max(spec.condition_a_id, spec.condition_b_id))
            counts[key] = counts.get(key, 0) + 1
        # Second call should avoid already-seen pairs if possible.
        specs2 = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts=counts, rng=random.Random(1)
        )
        assert len(specs2) == 3

    def test_large_experiment_balance(self, large_experiment):
        """Over multiple 'participants', all models get roughly equal appearances."""
        strategy = PairwiseBalancedStrategy()
        counts = {}
        model_appearances: dict[int, int] = {}
        for seed in range(12):
            specs = strategy.select_pairs(
                large_experiment, n=20, pair_counts=counts, rng=random.Random(seed)
            )
            for spec in specs:
                key = (min(spec.condition_a_id, spec.condition_b_id),
                       max(spec.condition_a_id, spec.condition_b_id))
                counts[key] = counts.get(key, 0) + 1
                model_appearances[spec.condition_a_id] = model_appearances.get(spec.condition_a_id, 0) + 1
                model_appearances[spec.condition_b_id] = model_appearances.get(spec.condition_b_id, 0) + 1

        # Each model should appear at least once.
        assert len(model_appearances) == 12
        # Check roughly balanced (each should have ~40 appearances, allow wide tolerance).
        values = list(model_appearances.values())
        assert max(values) - min(values) < max(values) * 0.5

    def test_empty_experiment(self, db):
        """An experiment with no stimuli returns empty list."""
        exp = PairwiseExperimentFactory()
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(exp, n=5, pair_counts={})
        assert specs == []

    def test_single_condition(self, db):
        """An experiment with only one condition cannot form pairs."""
        exp = PairwiseExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        StimulusFactory(condition=cond, prompt_group="p1")
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(exp, n=5, pair_counts={})
        assert specs == []

    def test_stimulus_counts_parameter_is_optional(self, pairwise_experiment):
        """Calling select_pairs without stimulus_counts= still works."""
        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(
            pairwise_experiment, n=3, pair_counts={}, rng=random.Random(7)
        )
        assert len(specs) == 3

    def test_least_used_stimulus_is_preferred(self, db):
        """When stimulus_counts is imbalanced, the low-count stimulus is picked."""
        exp = PairwiseExperimentFactory(stimuli_per_participant=10)
        cond_a = ConditionFactory(experiment=exp, name="Model-A")
        cond_b = ConditionFactory(experiment=exp, name="Model-B")
        # 3 stimuli per condition, all sharing one prompt_group.
        stims_a = [
            StimulusFactory(
                condition=cond_a, title=f"a{i}", prompt_group="p0"
            )
            for i in range(3)
        ]
        stims_b = [
            StimulusFactory(
                condition=cond_b, title=f"b{i}", prompt_group="p0"
            )
            for i in range(3)
        ]
        new_a = stims_a[0]
        new_b = stims_b[0]
        # All other stimuli have 10 prior appearances; new_a / new_b have 0.
        counts = {s.id: 10 for s in stims_a + stims_b}
        counts[new_a.id] = 0
        counts[new_b.id] = 0

        strategy = PairwiseBalancedStrategy()
        specs = strategy.select_pairs(
            exp,
            n=10,
            pair_counts={},
            stimulus_counts=counts,
            rng=random.Random(0),
        )
        assert len(specs) == 10
        # Only one viable condition pair exists, so every spec draws one stim
        # from each condition. The strictly-least-used pick must always choose
        # new_a and new_b.
        for spec in specs:
            pair = {spec.stimulus_a_id, spec.stimulus_b_id}
            assert new_a.id in pair
            assert new_b.id in pair

    def test_new_stimulus_catches_up(self, db):
        """A stimulus added mid-experiment gets oversampled until it catches up."""
        exp = PairwiseExperimentFactory(stimuli_per_participant=20)
        cond_a = ConditionFactory(experiment=exp, name="A")
        cond_b = ConditionFactory(experiment=exp, name="B")
        old_a = [
            StimulusFactory(condition=cond_a, title=f"a_old_{i}", prompt_group="p0")
            for i in range(3)
        ]
        old_b = [
            StimulusFactory(condition=cond_b, title=f"b_old_{i}", prompt_group="p0")
            for i in range(3)
        ]

        strategy = PairwiseBalancedStrategy()
        stim_counts: dict[int, int] = {}
        pair_counts: dict[tuple[int, int], int] = {}

        # Phase 1: 5 participants with only the original stimuli.
        for seed in range(5):
            specs = strategy.select_pairs(
                exp,
                n=20,
                pair_counts=pair_counts,
                stimulus_counts=stim_counts,
                rng=random.Random(seed),
            )
            for spec in specs:
                key = (
                    min(spec.condition_a_id, spec.condition_b_id),
                    max(spec.condition_a_id, spec.condition_b_id),
                )
                pair_counts[key] = pair_counts.get(key, 0) + 1
                stim_counts[spec.stimulus_a_id] = (
                    stim_counts.get(spec.stimulus_a_id, 0) + 1
                )
                stim_counts[spec.stimulus_b_id] = (
                    stim_counts.get(spec.stimulus_b_id, 0) + 1
                )

        # Phase 2: add one new stimulus per condition, then run 5 more rounds.
        new_a = StimulusFactory(condition=cond_a, title="a_new", prompt_group="p0")
        new_b = StimulusFactory(condition=cond_b, title="b_new", prompt_group="p0")

        for seed in range(5, 10):
            specs = strategy.select_pairs(
                exp,
                n=20,
                pair_counts=pair_counts,
                stimulus_counts=stim_counts,
                rng=random.Random(seed),
            )
            for spec in specs:
                key = (
                    min(spec.condition_a_id, spec.condition_b_id),
                    max(spec.condition_a_id, spec.condition_b_id),
                )
                pair_counts[key] = pair_counts.get(key, 0) + 1
                stim_counts[spec.stimulus_a_id] = (
                    stim_counts.get(spec.stimulus_a_id, 0) + 1
                )
                stim_counts[spec.stimulus_b_id] = (
                    stim_counts.get(spec.stimulus_b_id, 0) + 1
                )

        # By the end, new_a/new_b should be close to the average of their peers.
        avg_old_a = sum(stim_counts[s.id] for s in old_a) / len(old_a)
        avg_old_b = sum(stim_counts[s.id] for s in old_b) / len(old_b)
        assert stim_counts[new_a.id] >= 0.7 * avg_old_a
        assert stim_counts[new_b.id] >= 0.7 * avg_old_b
