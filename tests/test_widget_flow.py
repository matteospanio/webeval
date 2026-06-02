"""End-to-end participant flow exercising the new response widgets."""
from __future__ import annotations

import csv
import io
import json
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    MatrixQuestionFactory,
    NumericQuestionFactory,
    RankingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _answer_for(q: Question) -> dict:
    if q.type == Question.Type.MATRIX:
        cols = q.config["columns"]
        return {
            f"q_{q.pk}_r{i}": cols[-1] for i in range(len(q.config["rows"]))
        }
    if q.type == Question.Type.RANKING:
        return {
            f"q_{q.pk}_i{i}": str(i + 1) for i in range(len(q.config["items"]))
        }
    raise AssertionError(f"unexpected stimulus question type {q.type}")


def test_widget_flow_records_structured_answers():
    exp = ExperimentFactory(
        slug="widgets", name="Widgets study", require_audio_check=False
    )
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    # Each stimulus question on its own page so posting is order-independent.
    MatrixQuestionFactory(experiment=exp, sort_order=0, page_break_before=True)
    RankingQuestionFactory(experiment=exp, sort_order=1, page_break_before=True)
    NumericQuestionFactory(experiment=exp, sort_order=0)  # demographic
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"}
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    play_url = reverse("survey:play", kwargs={"slug": exp.slug})
    for _ in range(2):  # matrix page + ranking page
        body = client.get(play_url).content.decode()
        ids = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
        assert len(ids) == 1, body
        (qid,) = ids
        q = Question.objects.get(pk=qid)
        client.post(play_url, data=_answer_for(q))

    numeric_q = exp.questions.get(type=Question.Type.NUMERIC)
    client.post(
        reverse("survey:demographics", kwargs={"slug": exp.slug}),
        data={f"q_{numeric_q.pk}": "30"},
    )

    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert session.submitted_at is not None

    answers = {r.question.type: r.get_answer() for r in Response.objects.all()}
    assert answers[Question.Type.MATRIX] == {
        "Clarity": "High",
        "Musicality": "High",
    }
    assert answers[Question.Type.RANKING] == ["A", "B", "C"]
    assert answers[Question.Type.NUMERIC] == 30

    # The structured stimulus answers export as JSON in the answers CSV.
    staff = User.objects.create_user("w-admin", "a@e.org", "pw", is_staff=True)
    sc = Client()
    sc.force_login(staff)
    csv_page = sc.get(
        reverse(
            "admin:experiments_experiment_answers_csv", kwargs={"slug": exp.slug}
        )
    )
    rows = list(csv.DictReader(io.StringIO(csv_page.content.decode())))
    by_type = {r["question_type"]: json.loads(r["answer_value"]) for r in rows}
    assert by_type["matrix"] == {"Clarity": "High", "Musicality": "High"}
    assert by_type["ranking"] == ["A", "B", "C"]


def test_matrix_incomplete_rerenders_with_error():
    exp = ExperimentFactory(
        slug="matrix-required", name="Matrix", require_audio_check=False
    )
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    MatrixQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"}
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play_url = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play_url).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}

    # Answer only the first row → required matrix is incomplete → 400, no save.
    resp = client.post(play_url, data={f"q_{qid}_r0": "High"})
    assert resp.status_code == 400
    assert Response.objects.count() == 0
    # The partially-filled answer is preserved on re-render (first row stays
    # selected via _annotate_submitted + the get_item filter).
    assert "checked" in resp.content.decode()
