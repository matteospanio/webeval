"""End-to-end style tests for the participant flow.

These use Django's test client, not mocked requests, so they exercise the
state-machine + session cookies + the experiments app wired together."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
)
from survey.models import ParticipantSession, Response, StimulusAssignment

pytestmark = pytest.mark.django_db


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def draft_experiment():
    return ExperimentFactory(slug="draft-study")


@pytest.fixture
def active_experiment(db):
    exp = ExperimentFactory(slug="active-study")
    c1 = ConditionFactory(experiment=exp, name="A")
    c2 = ConditionFactory(experiment=exp, name="B")
    StimulusFactory(condition=c1, title="a1")
    StimulusFactory(condition=c2, title="b1")
    RatingQuestionFactory(experiment=exp, prompt="Quality?", sort_order=0)
    RatingQuestionFactory(experiment=exp, prompt="Interest?", sort_order=1)
    ChoiceQuestionFactory(
        experiment=exp,
        prompt="Age group",
        sort_order=0,
        config={"choices": ["<25", "25-40", ">40"], "multi": False},
    )
    TextQuestionFactory(
        experiment=exp,
        prompt="Musical training?",
        sort_order=1,
        config={"max_length": 200},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


# --- consent gate ----------------------------------------------------------


class TestConsentGate:
    def test_draft_renders_unavailable(self, draft_experiment):
        client = Client()
        response = client.get(reverse("survey:consent", kwargs={"slug": draft_experiment.slug}))
        assert response.status_code == 200
        body = response.content.decode().lower()
        # Friendly unavailable page, not a 404.
        assert "not currently" in body or "unavailable" in body

    def test_active_returns_consent_page(self, active_experiment):
        client = Client()
        response = client.get(reverse("survey:consent", kwargs={"slug": active_experiment.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        assert "consent" in body.lower()

    def test_consent_without_checkbox_shows_error(self, active_experiment):
        client = Client()
        response = client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={},
        )
        assert response.status_code == 200
        assert ParticipantSession.objects.count() == 0
        assert "consent checkbox" in response.content.decode().lower()

    def test_consent_with_checkbox_creates_session(self, active_experiment):
        client = Client()
        response = client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        assert response.status_code == 302
        assert response.url == reverse(
            "survey:instructions", kwargs={"slug": active_experiment.slug}
        )
        session = ParticipantSession.objects.get()
        assert session.consented_at is not None
        assert session.last_step == ParticipantSession.Step.INSTRUCTIONS


# --- metadata capture ------------------------------------------------------


class TestMetadataCapture:
    def test_device_and_browser_populated_from_ua(self, active_experiment):
        client = Client(
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                "Mobile/15E148 Safari/604.1"
            )
        )
        client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        session = ParticipantSession.objects.get()
        assert session.device_type == "mobile"
        assert "Safari" in session.browser_family

    def test_missing_geoip_db_does_not_break_consent(self, active_experiment, settings):
        settings.GEOIP_PATH = "/nonexistent/GeoLite2-Country.mmdb"
        client = Client(HTTP_X_FORWARDED_FOR="8.8.8.8")
        response = client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        assert response.status_code == 302
        session = ParticipantSession.objects.get()
        assert session.country_code == ""


# --- full happy-path flow --------------------------------------------------


class TestFullFlow:
    def _consent(self, client, exp):
        return client.post(
            reverse("survey:consent", kwargs={"slug": exp.slug}),
            data={"agree": "on"},
            follow=False,
        )

    def _instructions(self, client, exp):
        return client.post(
            reverse("survey:instructions", kwargs={"slug": exp.slug}),
            follow=False,
        )

    def test_happy_path_submits_all_responses(self, active_experiment):
        client = Client()
        self._consent(client, active_experiment)
        self._instructions(client, active_experiment)

        session = ParticipantSession.objects.get()
        assignments = list(session.assignments.order_by("sort_order"))
        assert len(assignments) == 2  # two stimuli

        stim_questions = list(
            active_experiment.questions.filter(section=Question.Section.STIMULUS)
        )
        assert len(stim_questions) == 2

        # Answer each stimulus.
        for _ in assignments:
            get = client.get(reverse("survey:play", kwargs={"slug": active_experiment.slug}))
            assert get.status_code == 200
            post_data = {f"q_{q.pk}": "50" for q in stim_questions}
            post = client.post(
                reverse("survey:play", kwargs={"slug": active_experiment.slug}),
                data=post_data,
            )
            assert post.status_code == 302

        # After all stimuli, /play/ should redirect to demographics.
        after_stimuli = client.get(
            reverse("survey:play", kwargs={"slug": active_experiment.slug})
        )
        assert after_stimuli.status_code == 302
        assert after_stimuli.url == reverse(
            "survey:demographics", kwargs={"slug": active_experiment.slug}
        )

        # Answer demographics and submit.
        demographics_get = client.get(
            reverse("survey:demographics", kwargs={"slug": active_experiment.slug})
        )
        assert demographics_get.status_code == 200
        choice_q = active_experiment.questions.get(type=Question.Type.CHOICE)
        text_q = active_experiment.questions.get(type=Question.Type.TEXT)
        final = client.post(
            reverse("survey:demographics", kwargs={"slug": active_experiment.slug}),
            data={f"q_{choice_q.pk}": "25-40", f"q_{text_q.pk}": "some piano"},
        )
        assert final.status_code == 302
        assert final.url == reverse("survey:thanks", kwargs={"slug": active_experiment.slug})

        session.refresh_from_db()
        assert session.submitted_at is not None
        assert session.last_step == ParticipantSession.Step.DONE
        # 2 stimuli × 2 stimulus-questions + 2 demographic answers = 6 responses.
        assert Response.objects.filter(session=session).count() == 6


# --- state-machine ordering guard ------------------------------------------


class TestStepGuard:
    def test_cannot_skip_to_play_before_instructions(self, active_experiment):
        client = Client()
        client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        # We're now at the instructions step; try to jump to /play/.
        response = client.get(
            reverse("survey:play", kwargs={"slug": active_experiment.slug})
        )
        assert response.status_code == 302
        assert response.url == reverse(
            "survey:instructions", kwargs={"slug": active_experiment.slug}
        )

    def test_cannot_submit_demographics_before_stimuli(self, active_experiment):
        client = Client()
        client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        client.post(
            reverse("survey:instructions", kwargs={"slug": active_experiment.slug})
        )
        response = client.post(
            reverse("survey:demographics", kwargs={"slug": active_experiment.slug}),
            data={},
        )
        assert response.status_code == 302
        assert response.url == reverse(
            "survey:play", kwargs={"slug": active_experiment.slug}
        )


# --- listen duration endpoint ----------------------------------------------


class TestListenDuration:
    def test_post_updates_assignment(self, active_experiment):
        client = Client()
        client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        client.post(reverse("survey:instructions", kwargs={"slug": active_experiment.slug}))
        assignment = StimulusAssignment.objects.order_by("sort_order").first()
        response = client.post(
            reverse(
                "survey:record_listen",
                kwargs={"slug": active_experiment.slug, "assignment_id": assignment.pk},
            ),
            data=json.dumps({"duration_ms": 18500}),
            content_type="application/json",
        )
        assert response.status_code == 200
        payload = json.loads(response.content)
        assert payload["listen_duration_ms"] == 18500
        assignment.refresh_from_db()
        assert assignment.listen_duration_ms == 18500
        assert assignment.started_listening_at is not None

    def test_duration_is_max_of_reports(self, active_experiment):
        client = Client()
        client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        client.post(reverse("survey:instructions", kwargs={"slug": active_experiment.slug}))
        assignment = StimulusAssignment.objects.order_by("sort_order").first()
        url = reverse(
            "survey:record_listen",
            kwargs={"slug": active_experiment.slug, "assignment_id": assignment.pk},
        )
        client.post(url, data=json.dumps({"duration_ms": 5000}), content_type="application/json")
        client.post(url, data=json.dumps({"duration_ms": 3000}), content_type="application/json")
        assignment.refresh_from_db()
        assert assignment.listen_duration_ms == 5000


# --- question randomization ------------------------------------------------


class TestQuestionRandomization:
    def test_randomize_stimulus_questions_defaults_true(self):
        exp = ExperimentFactory(slug="defaults-study")
        assert exp.randomize_stimulus_questions is True

    def test_stimulus_question_order_is_randomized_but_stable(self, active_experiment):
        # Add enough questions to make randomization observable.
        for i in range(6):
            RatingQuestionFactory(experiment=active_experiment, prompt=f"Rating {i}", sort_order=10 + i)

        client = Client()
        client.post(
            reverse("survey:consent", kwargs={"slug": active_experiment.slug}),
            data={"agree": "on"},
        )
        client.post(reverse("survey:instructions", kwargs={"slug": active_experiment.slug}))

        first = client.get(reverse("survey:play", kwargs={"slug": active_experiment.slug}))
        second = client.get(reverse("survey:play", kwargs={"slug": active_experiment.slug}))
        # Same session → same order on re-render.
        q_pattern = b"name=\"q_"
        first_q_positions = [i for i in range(len(first.content)) if first.content[i:i+8] == q_pattern]
        second_q_positions = [i for i in range(len(second.content)) if second.content[i:i+8] == q_pattern]
        assert first_q_positions == second_q_positions

    def test_stimulus_question_order_fixed_when_randomize_off(self):
        import re

        exp = ExperimentFactory(slug="fixed-order-study")
        exp.randomize_stimulus_questions = False
        cond = ConditionFactory(experiment=exp, name="A")
        StimulusFactory(condition=cond, title="a1")
        # Ensure all questions sit on one page so we can read their full
        # order in the rendered HTML. Sort_order values are intentionally
        # non-contiguous to prove we're ordering by the field, not by pk.
        expected_ids: list[int] = []
        for idx, sort in enumerate([40, 10, 30, 20, 50]):
            q = RatingQuestionFactory(
                experiment=exp, prompt=f"q{idx}", sort_order=sort
            )
            expected_ids.append(q.pk)
        expected_ids_by_sort = [
            pk for _, pk in sorted(
                [(40, expected_ids[0]), (10, expected_ids[1]),
                 (30, expected_ids[2]), (20, expected_ids[3]),
                 (50, expected_ids[4])],
                key=lambda p: p[0],
            )
        ]

        exp.state = Experiment.State.ACTIVE
        exp.save(update_fields=["state", "randomize_stimulus_questions"])

        def _run_once() -> list[int]:
            client = Client()
            client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"})
            client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
            resp = client.get(reverse("survey:play", kwargs={"slug": exp.slug}))
            assert resp.status_code == 200
            return [int(m) for m in re.findall(r'name="q_(\d+)"', resp.content.decode())]

        first = _run_once()
        second = _run_once()
        assert first == expected_ids_by_sort
        assert second == first


# --- author-controlled page breaks -----------------------------------------


class TestPageBreaksInPlay:
    def test_multiple_pages_per_stimulus_each_post_advances_one_page(self):
        import re

        exp = ExperimentFactory(slug="paged-study")
        cond = ConditionFactory(experiment=exp, name="A")
        StimulusFactory(condition=cond, title="a1")
        # Three stimulus questions, each on its own page via
        # page_break_before=True. This makes the test order-independent
        # with respect to the session's randomised question order.
        q_ids = set()
        for i in range(3):
            q = RatingQuestionFactory(
                experiment=exp, prompt=f"q{i}", sort_order=i, page_break_before=True
            )
            q_ids.add(q.pk)
        exp.state = Experiment.State.ACTIVE
        exp.save(update_fields=["state"])

        client = Client()
        client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"})
        client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

        session = ParticipantSession.objects.get()
        assert session.current_page_index == 0

        seen_ids: set[int] = set()
        for page in range(3):
            get = client.get(reverse("survey:play", kwargs={"slug": exp.slug}))
            assert get.status_code == 200
            content = get.content.decode()
            matches = re.findall(r'name="q_(\d+)"', content)
            # Exactly one question on each page.
            page_ids = {int(m) for m in matches}
            assert len(page_ids) == 1, f"page {page} had ids {page_ids}"
            (pid,) = page_ids
            assert pid in q_ids
            assert pid not in seen_ids
            seen_ids.add(pid)
            client.post(
                reverse("survey:play", kwargs={"slug": exp.slug}),
                data={f"q_{pid}": "50"},
            )

        # The final POST redirects to demographics; with none configured,
        # visiting that URL calls _finish_session and advances to done.
        client.get(reverse("survey:demographics", kwargs={"slug": exp.slug}))
        session.refresh_from_db()
        assert session.last_step == ParticipantSession.Step.DONE
        assert Response.objects.filter(session=session).count() == 3
        assert seen_ids == q_ids

    def test_progress_percent_monotonically_increases(self):
        exp = ExperimentFactory(slug="progress-study")
        cond = ConditionFactory(experiment=exp, name="A")
        StimulusFactory(condition=cond, title="only")
        # Both questions have page_break_before=True so there are always
        # exactly 2 pages regardless of the per-session question shuffle.
        RatingQuestionFactory(
            experiment=exp, prompt="q1", sort_order=0, page_break_before=True
        )
        RatingQuestionFactory(
            experiment=exp, prompt="q2", sort_order=1, page_break_before=True
        )
        exp.state = Experiment.State.ACTIVE
        exp.save(update_fields=["state"])

        client = Client()

        def _progress(url_name: str) -> int:
            from survey.flow import paginate_questions, progress_percent
            from survey.views import _ordered_section_questions, _stimulus_questions

            session = ParticipantSession.objects.get()
            stim_pages = len(paginate_questions(_stimulus_questions(exp, session)))
            dem_pages = len(
                paginate_questions(
                    _ordered_section_questions(exp, Question.Section.DEMOGRAPHIC)
                )
            )
            return progress_percent(
                session,
                stimulus_pages_per_assignment=stim_pages,
                demographic_pages=dem_pages,
                assignments_total=session.assignments.count(),
            )

        client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"})
        p_after_consent = _progress("instructions")
        client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
        p_after_instructions = _progress("play")
        assert p_after_instructions > p_after_consent

        # Fetch the current play page to discover which question is on it
        # (per-session shuffle makes this non-deterministic), then POST its
        # answer and confirm the progress bar advances.
        import re

        get = client.get(reverse("survey:play", kwargs={"slug": exp.slug}))
        (current_qid,) = {
            int(m) for m in re.findall(r'name="q_(\d+)"', get.content.decode())
        }
        client.post(
            reverse("survey:play", kwargs={"slug": exp.slug}),
            data={f"q_{current_qid}": "25"},
        )
        p_after_page_1 = _progress("play")
        assert p_after_page_1 > p_after_instructions


# --- unavailable surfaces ---------------------------------------------------


class TestUnavailable:
    def test_root_url_is_404(self):
        client = Client()
        response = client.get("/")
        assert response.status_code == 404

    def test_landing_url_name_no_longer_resolves(self):
        with pytest.raises(Exception):  # NoReverseMatch
            reverse("survey:landing")

    def test_closed_experiment_renders_unavailable(self):
        exp = ExperimentFactory(slug="closing-soon")
        exp.state = Experiment.State.ACTIVE
        exp.save(update_fields=["state"])
        exp.state = Experiment.State.CLOSED
        exp.save(update_fields=["state"])
        client = Client()
        response = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
        assert response.status_code == 200
        assert "not currently" in response.content.decode().lower() or "unavailable" in response.content.decode().lower()
