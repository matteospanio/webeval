"""End-to-end test for the pairwise comparison survey flow.

Creates a pairwise experiment with 3 conditions (models), 2 prompt groups,
walks a participant through consent → instructions → pairwise comparisons →
demographics → thanks, then verifies the session is complete and the pairwise
CSV export has the expected shape.
"""
from __future__ import annotations

import csv
import io
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    LikertQuestionFactory,
    PairwiseExperimentFactory,
    StimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def test_pairwise_full_pipeline():
    # 1. Build a pairwise experiment: 3 conditions x 2 prompt groups.
    exp = PairwiseExperimentFactory(
        slug="pw-e2e",
        name="Pairwise E2E",
        stimuli_per_participant=3,
    )
    conditions = [
        ConditionFactory(experiment=exp, name=f"Model-{i}") for i in range(3)
    ]
    for cond in conditions:
        for pg in ("prompt-0", "prompt-1"):
            StimulusFactory(
                condition=cond,
                title=f"{cond.name}-{pg}",
                prompt_group=pg,
            )

    # Two comparison questions (stimulus section).
    ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Which has better fidelity?",
        config={"choices": ["A", "B"], "multi": False},
        sort_order=0,
    )
    ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Which has better quality?",
        config={"choices": ["A", "B"], "multi": False},
        sort_order=1,
    )

    # One Likert demographic question.
    LikertQuestionFactory(
        experiment=exp,
        prompt="I enjoy listening to music.",
        sort_order=0,
    )

    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    # 2. Walk through the survey.
    client = Client()
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}),
        data={"agree": "on"},
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    # 3 pairwise comparisons, each shows 2 questions on one page.
    progress_snapshots: list[int] = []
    for trial in range(3):
        get_resp = client.get(
            reverse("survey:pairwise_play", kwargs={"slug": exp.slug})
        )
        assert get_resp.status_code == 200
        body = get_resp.content.decode()
        # Should show two audio elements.
        assert "Sample A" in body
        assert "Sample B" in body

        m = re.search(r'aria-valuenow="(\d+)"', body)
        if m:
            progress_snapshots.append(int(m.group(1)))

        # Find question ids and answer them.
        q_ids = re.findall(r'name="q_(\d+)"', body)
        data = {}
        for qid in set(q_ids):
            data[f"q_{qid}"] = "A"
        client.post(
            reverse("survey:pairwise_play", kwargs={"slug": exp.slug}),
            data=data,
        )

    # Progress should be monotonically non-decreasing.
    if progress_snapshots:
        assert progress_snapshots == sorted(progress_snapshots)

    # Demographics page.
    get_demo = client.get(
        reverse("survey:demographics", kwargs={"slug": exp.slug})
    )
    assert get_demo.status_code == 200
    body = get_demo.content.decode()
    q_ids = re.findall(r'name="q_(\d+)"', body)
    data = {}
    for qid in set(q_ids):
        data[f"q_{qid}"] = "3"  # Likert value
    client.post(
        reverse("survey:demographics", kwargs={"slug": exp.slug}),
        data=data,
    )

    # 3. Verify session completion.
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert session.submitted_at is not None

    # 4. Verify pairwise CSV export.
    staff = User.objects.create_user("pw-admin", "a@e.org", "pw", is_staff=True)
    staff_client = Client()
    staff_client.force_login(staff)

    csv_page = staff_client.get(
        reverse(
            "admin:experiments_experiment_pairwise_answers_csv",
            kwargs={"slug": exp.slug},
        )
    )
    assert csv_page.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_page.content.decode())))
    # 3 pairs x 2 questions = 6 rows.
    assert len(rows) == 6
    assert all(r["experiment"] == "pw-e2e" for r in rows)
    assert all(r["model_a"] != r["model_b"] for r in rows)
    # Each row should have a valid preferred value.
    assert all(r["preferred"] for r in rows)

    # 5. Details page should load without error.
    details_page = staff_client.get(
        reverse(
            "admin:experiments_experiment_details",
            kwargs={"slug": exp.slug},
        )
    )
    assert details_page.status_code == 200
