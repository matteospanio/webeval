"""Stimulus-assignment strategies.

A strategy decides which stimuli a given participant is shown (and in what
order). The ``StrategyBase`` interface is deliberately narrow so the survey
app can plug a strategy in by name (``experiment.assignment_strategy``) without
the strategy needing to know about the ``ParticipantSession`` or
``StimulusAssignment`` models.

Callers pass in a ``counts`` dict ``{stimulus_id: int}`` describing how many
times each stimulus has already been assigned across prior participants. The
strategy uses that together with Condition grouping to produce a balanced,
randomized selection.

Two registries exist:

* ``_REGISTRY`` — single-stimulus strategies (standard mode).
* ``_PAIRWISE_REGISTRY`` — pairwise comparison strategies.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from experiments.models import Experiment, Stimulus


# ---------------------------------------------------------------------------
# Pairwise data structures
# ---------------------------------------------------------------------------


@dataclass
class PairSpec:
    """One pairwise comparison trial to assign to a participant."""

    condition_a_id: int
    condition_b_id: int
    stimulus_a_id: int
    stimulus_b_id: int
    prompt_group: str
    position_a: str  # "left" or "right"


class UnknownStrategyError(KeyError):
    """Raised when an unregistered strategy name is looked up."""


class StrategyBase:
    """Base class for pluggable assignment strategies.

    Subclasses must set ``name`` and implement :meth:`select`.
    """

    name: str = ""

    def select(
        self,
        experiment: "Experiment",
        n: int | None,
        counts: Mapping[int, int],
        rng: random.Random | None = None,
    ) -> list["Stimulus"]:
        raise NotImplementedError


class BalancedRandomStrategy(StrategyBase):
    """Pick ``n`` stimuli from the experiment, balancing across conditions.

    Algorithm:
    1. Load all active stimuli, grouped by condition.
    2. If ``n`` is ``None`` or >= total active, shuffle and return everything.
    3. Otherwise, repeatedly pick one stimulus:
       a. Find the condition with the smallest "running used count"
          (ties broken randomly).
       b. Within that condition, pick the stimulus with the lowest historical
          ``counts`` value (again, ties broken randomly).
       c. Remove that stimulus from the pool and increment the condition's
          running-used count.
    4. Stop once ``n`` stimuli are selected or the pool is exhausted.

    This keeps each individual session's stimuli spread evenly across
    conditions and, over many sessions, causes total assignment counts to
    converge toward equality.
    """

    name = "balanced_random"

    def select(
        self,
        experiment: "Experiment",
        n: int | None,
        counts: Mapping[int, int],
        rng: random.Random | None = None,
    ) -> list["Stimulus"]:
        from experiments.models import Stimulus  # local import: avoid cycles at import time

        rng = rng or random.Random()

        active = list(
            Stimulus.objects.filter(
                condition__experiment=experiment,
                is_active=True,
            ).select_related("condition")
        )
        if not active:
            return []

        if n is None or n >= len(active):
            shuffled = list(active)
            rng.shuffle(shuffled)
            return shuffled

        # Group by condition.
        by_cond: dict[int, list[Stimulus]] = {}
        for stim in active:
            by_cond.setdefault(stim.condition_id, []).append(stim)

        running_used: dict[int, int] = {cond_id: 0 for cond_id in by_cond}

        selected: list[Stimulus] = []
        while len(selected) < n:
            # Drop exhausted conditions.
            candidates = {cid: pool for cid, pool in by_cond.items() if pool}
            if not candidates:
                break

            min_cond_count = min(running_used[cid] for cid in candidates)
            least_used = [cid for cid in candidates if running_used[cid] == min_cond_count]
            rng.shuffle(least_used)
            chosen_cond = least_used[0]

            pool = candidates[chosen_cond]
            # Pick the stimulus from this condition that has been assigned the
            # fewest times historically (random tiebreak).
            min_stim_count = min(counts.get(s.id, 0) for s in pool)
            least_used_stims = [s for s in pool if counts.get(s.id, 0) == min_stim_count]
            rng.shuffle(least_used_stims)
            chosen = least_used_stims[0]

            selected.append(chosen)
            pool.remove(chosen)
            running_used[chosen_cond] += 1

        return selected


_REGISTRY: dict[str, StrategyBase] = {
    BalancedRandomStrategy.name: BalancedRandomStrategy(),
}


def register_strategy(strategy: StrategyBase) -> None:
    """Register an extra strategy at import time (e.g. from a plug-in app)."""
    if not strategy.name:
        raise ValueError("strategy.name must be a non-empty string")
    _REGISTRY[strategy.name] = strategy


def get_strategy(name: str) -> StrategyBase:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownStrategyError(name) from exc


def available_strategies() -> Sequence[str]:
    return tuple(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Pairwise strategies
# ---------------------------------------------------------------------------


class PairwiseStrategyBase:
    """Base class for pairwise comparison assignment strategies."""

    name: str = ""

    def select_pairs(
        self,
        experiment: "Experiment",
        n: int | None,
        pair_counts: Mapping[tuple[int, int], int],
        rng: random.Random | None = None,
    ) -> list[PairSpec]:
        raise NotImplementedError


class PairwiseBalancedStrategy(PairwiseStrategyBase):
    """Balanced pairwise comparison assignment.

    Algorithm:
    1. Enumerate all C(N,2) condition pairs.
    2. Rank by deficit (target_count - actual_count), most under-represented
       first. Ties broken randomly.
    3. Select top ``n`` pairs.
    4. For each pair, pick a random prompt_group present in both conditions.
    5. For each pair, pick one active stimulus per condition for that prompt.
    6. Randomize left/right positioning.
    7. Shuffle trial order.
    """

    name = "pairwise_balanced"

    def select_pairs(
        self,
        experiment: "Experiment",
        n: int | None,
        pair_counts: Mapping[tuple[int, int], int],
        rng: random.Random | None = None,
    ) -> list[PairSpec]:
        from experiments.models import Stimulus

        rng = rng or random.Random()

        # Load all active stimuli grouped by condition, then by prompt_group.
        active = list(
            Stimulus.objects.filter(
                condition__experiment=experiment,
                is_active=True,
            )
            .exclude(prompt_group="")
            .select_related("condition")
        )
        if not active:
            return []

        # Build: {condition_id: {prompt_group: [stimulus, ...]}}
        by_cond_prompt: dict[int, dict[str, list[Stimulus]]] = {}
        for stim in active:
            by_cond_prompt.setdefault(stim.condition_id, {}).setdefault(
                stim.prompt_group, []
            ).append(stim)

        condition_ids = sorted(by_cond_prompt.keys())
        if len(condition_ids) < 2:
            return []

        # All unique condition pairs (canonical: smaller id first).
        all_pairs = list(itertools.combinations(condition_ids, 2))

        # Filter to pairs that share at least one prompt_group.
        viable_pairs = []
        for a, b in all_pairs:
            shared = set(by_cond_prompt[a].keys()) & set(by_cond_prompt[b].keys())
            if shared:
                viable_pairs.append((a, b))

        if not viable_pairs:
            return []

        if n is None:
            n = len(viable_pairs)

        # Rank by deficit.
        target = max(1, (n * 2) // len(condition_ids)) if len(viable_pairs) > n else 1
        deficit_list = [
            (pair, target - pair_counts.get(pair, 0)) for pair in viable_pairs
        ]
        # Shuffle first for random tiebreaking, then stable-sort by deficit desc.
        rng.shuffle(deficit_list)
        deficit_list.sort(key=lambda x: x[1], reverse=True)

        # Select n pairs (cycle if n > viable).
        selected_pairs: list[tuple[int, int]] = []
        idx = 0
        while len(selected_pairs) < n:
            selected_pairs.append(deficit_list[idx % len(deficit_list)][0])
            idx += 1

        # Build PairSpec for each selected pair.
        specs: list[PairSpec] = []
        for cond_a, cond_b in selected_pairs:
            shared_prompts = sorted(
                set(by_cond_prompt[cond_a].keys()) & set(by_cond_prompt[cond_b].keys())
            )
            prompt = rng.choice(shared_prompts)

            stim_a = rng.choice(by_cond_prompt[cond_a][prompt])
            stim_b = rng.choice(by_cond_prompt[cond_b][prompt])
            position_a = "left" if rng.random() < 0.5 else "right"

            specs.append(
                PairSpec(
                    condition_a_id=cond_a,
                    condition_b_id=cond_b,
                    stimulus_a_id=stim_a.id,
                    stimulus_b_id=stim_b.id,
                    prompt_group=prompt,
                    position_a=position_a,
                )
            )

        rng.shuffle(specs)
        return specs


_PAIRWISE_REGISTRY: dict[str, PairwiseStrategyBase] = {
    PairwiseBalancedStrategy.name: PairwiseBalancedStrategy(),
}


def register_pairwise_strategy(strategy: PairwiseStrategyBase) -> None:
    if not strategy.name:
        raise ValueError("strategy.name must be a non-empty string")
    _PAIRWISE_REGISTRY[strategy.name] = strategy


def get_pairwise_strategy(name: str) -> PairwiseStrategyBase:
    try:
        return _PAIRWISE_REGISTRY[name]
    except KeyError as exc:
        raise UnknownStrategyError(name) from exc


def available_pairwise_strategies() -> Sequence[str]:
    return tuple(_PAIRWISE_REGISTRY.keys())
