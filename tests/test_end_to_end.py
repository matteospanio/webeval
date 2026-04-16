"""End-to-end smoke test for the full webeval pipeline.

Creates an active experiment via the ORM (as the admin would), simulates a
participant completing the survey via the Django test client, and verifies
that the admin details page sees the data and that the answers CSV contains
the expected rows. This is the scripted equivalent of the manual QA pass
in the M5 milestone — it exercises every app at once and would have caught
any wiring regression between them.
"""
from __future__ import annotations

import csv
import io
import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question, Stimulus
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    ImageStimulusFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def test_full_pipeline_creates_data_visible_in_admin_and_csv():
    # 1. Build a complete experiment the way the admin would — mix of
    #    audio / image / text stimuli to exercise the kind discriminator.
    exp = ExperimentFactory(slug="e2e-study", name="E2E study", require_audio_check=True)
    cond_a = ConditionFactory(experiment=exp, name="GPT-v1")
    cond_b = ConditionFactory(experiment=exp, name="baseline")
    cond_c = ConditionFactory(experiment=exp, name="prompted")
    StimulusFactory(condition=cond_a, title="clip-a", sort_order=0)
    ImageStimulusFactory(condition=cond_b, title="clip-b", sort_order=0)
    TextStimulusFactory(condition=cond_c, title="clip-c", sort_order=0)
    # Both questions have page_break_before=True so the test is independent
    # of the per-session random question order: there is always one question
    # per page regardless of shuffle outcome.
    RatingQuestionFactory(
        experiment=exp, prompt="Quality?", sort_order=0, page_break_before=True
    )
    RatingQuestionFactory(
        experiment=exp,
        prompt="Novelty?",
        sort_order=1,
        page_break_before=True,
    )
    ChoiceQuestionFactory(
        experiment=exp,
        prompt="Age bracket",
        config={"choices": ["<25", "25-40", ">40"], "multi": False},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    # 2. Participant runs through consent → instructions → stimuli → demographics.
    #    With page_break_before=True on the second question, each stimulus now
    #    has TWO pages; each Next POST submits the single question on that page.
    client = Client()
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}),
        data={"agree": "on"},
    )

    # Audio check page (the experiment has an audio stimulus + default toggle).
    audio_check_url = reverse("survey:audio_check", kwargs={"slug": exp.slug})
    assert client.get(audio_check_url).status_code == 200
    ac_response = client.post(audio_check_url, data={"can_hear": "yes"})
    assert ac_response.status_code in (302, 303)

    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    import re

    # Each of the three stimuli needs two page POSTs (one per page).
    progress_snapshots: list[int] = []
    for _ in range(3 * 2):
        get_response = client.get(reverse("survey:play", kwargs={"slug": exp.slug}))
        assert get_response.status_code == 200
        body = get_response.content.decode()
        # Each page renders its media (audio|figure|blockquote) — sanity check.
        assert any(
            marker in body
            for marker in ("<audio", "<figure", "<blockquote")
        )
        # Extract the progress percent from the rendered aria-valuenow attribute.
        m = re.search(r'aria-valuenow="(\d+)"', body)
        assert m is not None
        progress_snapshots.append(int(m.group(1)))
        # Exactly one rating question is on the page.
        q_ids = {int(x) for x in re.findall(r'name="q_(\d+)"', body)}
        assert len(q_ids) == 1
        (qid,) = q_ids
        client.post(
            reverse("survey:play", kwargs={"slug": exp.slug}),
            data={f"q_{qid}": "75"},
        )

    # Progress percent must be monotonically non-decreasing across pages.
    assert progress_snapshots == sorted(progress_snapshots)
    # And it must strictly grow at least once (not constant).
    assert progress_snapshots[-1] > progress_snapshots[0]

    choice_q = exp.questions.get(type=Question.Type.CHOICE)
    client.post(
        reverse("survey:demographics", kwargs={"slug": exp.slug}),
        data={f"q_{choice_q.pk}": "25-40"},
    )

    # Session should now be complete.
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert session.submitted_at is not None

    # 3. Admin details page (staff-only) should see the completed session.
    staff = User.objects.create_user("e2e-admin", "a@e.org", "pw", is_staff=True)
    staff_client = Client()
    staff_client.force_login(staff)

    stats_page = staff_client.get(
        reverse("admin:experiments_experiment_details", kwargs={"slug": exp.slug})
    )
    assert stats_page.status_code == 200
    body = stats_page.content.decode()
    assert "E2E study" in body
    assert "clip-a" in body

    # 4. CSV export should have 6 rating rows (3 stimuli × 2 rating questions)
    #    for the one completed session.
    csv_page = staff_client.get(
        reverse("admin:experiments_experiment_answers_csv", kwargs={"slug": exp.slug})
    )
    assert csv_page.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_page.content.decode())))
    assert len(rows) == 6
    assert {row["condition"] for row in rows} == {"GPT-v1", "baseline", "prompted"}
    for row in rows:
        assert json.loads(row["answer_value"]) == 75

    # 5. Demographics CSV should have one row with the chosen bracket.
    demo_csv = staff_client.get(
        reverse("admin:experiments_experiment_demographics_csv", kwargs={"slug": exp.slug})
    )
    demo_rows = list(csv.DictReader(io.StringIO(demo_csv.content.decode())))
    assert len(demo_rows) == 1
    (demo_col,) = [c for c in demo_rows[0] if c.startswith("q_")]
    assert json.loads(demo_rows[0][demo_col]) == "25-40"
