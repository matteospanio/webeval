"""Tests for the experiments admin forms.

``QuestionAdminForm`` hides the raw ``config`` JSONField and exposes per-type
helper fields; these tests exercise the round-trip between helper fields and
the final config dict stored on the model.
"""
from __future__ import annotations

import pytest

from experiments.forms import QuestionAdminForm
from experiments.models import Question
from experiments.tests.factories import ExperimentFactory, RatingQuestionFactory

pytestmark = pytest.mark.django_db


def _base_data(experiment, *, section="stimulus", question_type="rating", **overrides):
    data = {
        "experiment": experiment.pk,
        "section": section,
        "type": question_type,
        "prompt": "Test question",
        "help_text": "",
        "required": "on",
        "sort_order": 0,
    }
    data.update(overrides)
    return data


class TestRatingQuestionAdminForm:
    def test_builds_rating_config_from_helper_fields(self):
        exp = ExperimentFactory()
        form = QuestionAdminForm(
            data=_base_data(
                exp,
                question_type=Question.Type.RATING,
                rating_min=0,
                rating_max=100,
                rating_step=1,
            )
        )
        assert form.is_valid(), form.errors
        q = form.save()
        assert q.config == {"min": 0, "max": 100, "step": 1}
        assert q.type == Question.Type.RATING

    def test_missing_rating_helpers_surfaces_field_errors(self):
        exp = ExperimentFactory()
        form = QuestionAdminForm(
            data=_base_data(exp, question_type=Question.Type.RATING)
        )
        assert not form.is_valid()
        assert "rating_min" in form.errors
        assert "rating_max" in form.errors
        assert "rating_step" in form.errors


class TestChoiceQuestionAdminForm:
    def test_parses_choice_options_line_by_line(self):
        exp = ExperimentFactory()
        form = QuestionAdminForm(
            data=_base_data(
                exp,
                section="demographic",
                question_type=Question.Type.CHOICE,
                choice_options="  <25\n25-40\n>40  \n\n",
                choice_multi="on",
            )
        )
        assert form.is_valid(), form.errors
        q = form.save()
        assert q.config == {"choices": ["<25", "25-40", ">40"], "multi": True}

    def test_empty_options_surfaces_clean_error(self):
        exp = ExperimentFactory()
        form = QuestionAdminForm(
            data=_base_data(
                exp,
                section="demographic",
                question_type=Question.Type.CHOICE,
                choice_options="",
            )
        )
        assert not form.is_valid()
        assert "choice_options" in form.errors


class TestTextQuestionAdminForm:
    def test_builds_text_config(self):
        exp = ExperimentFactory()
        form = QuestionAdminForm(
            data=_base_data(
                exp,
                section="demographic",
                question_type=Question.Type.TEXT,
                text_max_length=500,
            )
        )
        assert form.is_valid(), form.errors
        q = form.save()
        assert q.config == {"max_length": 500}


class TestFormRoundtripFromInstance:
    def test_helper_fields_populated_from_existing_instance(self):
        exp = ExperimentFactory()
        q = RatingQuestionFactory(
            experiment=exp, config={"min": -5, "max": 5, "step": 1}
        )
        form = QuestionAdminForm(instance=q)
        assert form.fields["rating_min"].initial == -5
        assert form.fields["rating_max"].initial == 5
        assert form.fields["rating_step"].initial == 1
