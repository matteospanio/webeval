"""Tests for the experiments app: lifecycle, structural-edit guard, validators,
question config validation, and Stimulus-on-save auto-fields."""
from __future__ import annotations

import hashlib
import io
import struct

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from experiments.models import Experiment, Prompt, Question, Stimulus
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    ImageStimulusFactory,
    PairwiseAudioExperimentFactory,
    PromptFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
    TextStimulusFactory,
)
from survey.flow import paginate_questions

pytestmark = pytest.mark.django_db


# --- Experiment lifecycle ----------------------------------------------------


class TestExperimentLifecycle:
    def test_default_state_is_draft(self):
        exp = ExperimentFactory()
        assert exp.state == Experiment.State.DRAFT

    def test_draft_to_active_allowed(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.ACTIVE
        exp.full_clean()
        exp.save()
        exp.refresh_from_db()
        assert exp.state == Experiment.State.ACTIVE

    def test_active_to_closed_allowed(self):
        exp = ExperimentFactory(state=Experiment.State.ACTIVE)
        exp.state = Experiment.State.CLOSED
        exp.full_clean()
        exp.save()
        assert exp.state == Experiment.State.CLOSED


# --- Structural-edit guard ---------------------------------------------------


class TestStructuralEditGuard:
    def test_can_add_question_to_draft_experiment(self):
        exp = ExperimentFactory(state=Experiment.State.DRAFT)
        q = RatingQuestionFactory.build(experiment=exp)
        q.full_clean()
        q.save()
        assert exp.questions.count() == 1

    def test_cannot_add_question_to_active_experiment(self):
        exp = ExperimentFactory(state=Experiment.State.ACTIVE)
        q = RatingQuestionFactory.build(experiment=exp)
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_cannot_add_condition_to_active_experiment(self):
        exp = ExperimentFactory(state=Experiment.State.ACTIVE)
        cond = ConditionFactory.build(experiment=exp)
        with pytest.raises(ValidationError):
            cond.full_clean()

    def test_cannot_add_stimulus_to_active_experiment(self):
        active = ExperimentFactory(state=Experiment.State.ACTIVE)
        condition = ConditionFactory.build(experiment=active, name="c1")
        # Condition itself is blocked, but assuming it existed pre-activation,
        # we simulate by activating after the condition is created.
        draft = ExperimentFactory()
        cond_in_draft = ConditionFactory(experiment=draft, name="c2")
        draft.state = Experiment.State.ACTIVE
        draft.save()  # skip full_clean for the lifecycle flip
        stim = StimulusFactory.build(condition=cond_in_draft)
        with pytest.raises(ValidationError):
            stim.full_clean()

    def test_cannot_delete_question_from_active_experiment(self):
        exp = ExperimentFactory()
        q = RatingQuestionFactory(experiment=exp)
        exp.state = Experiment.State.ACTIVE
        exp.save()
        with pytest.raises(ValidationError):
            q.delete()

    def test_cosmetic_experiment_fields_still_editable_when_active(self):
        exp = ExperimentFactory(state=Experiment.State.ACTIVE)
        exp.description = "updated description"
        exp.consent_text = "updated consent"
        exp.full_clean()
        exp.save()
        exp.refresh_from_db()
        assert exp.description == "updated description"


# --- Stimulus audio validator -----------------------------------------------


def _make_upload(name: str, data: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="audio/mpeg")


class TestStimulusValidator:
    def test_accepts_mp3(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(
            condition=cond,
            title="ok",
            audio=_make_upload("ok.mp3", b"ID3\x03\x00\x00" + b"\x00" * 100),
        )
        stim.full_clean()

    def test_rejects_txt_extension(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(
            condition=cond,
            title="bad",
            audio=_make_upload("bad.txt", b"not audio"),
        )
        with pytest.raises(ValidationError):
            stim.full_clean()

    def test_rejects_oversized_upload(self, settings):
        settings.STIMULUS_MAX_UPLOAD_BYTES = 1024  # 1 KB cap for the test
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(
            condition=cond,
            title="big",
            audio=_make_upload("big.mp3", b"\x00" * 2048),
        )
        with pytest.raises(ValidationError):
            stim.full_clean()


# --- Question config validation ---------------------------------------------


class TestQuestionConfigValidation:
    @pytest.mark.parametrize("bad_config", [5, "hello", [1, 2, 3]])
    def test_non_dict_config_raises_clean_validation_error(self, bad_config):
        """Previously a non-dict config crashed with AttributeError on .get()."""
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.STIMULUS,
            type=Question.Type.RATING,
            prompt="x",
            config=bad_config,
        )
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_rating_requires_min_max_step(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.STIMULUS,
            type=Question.Type.RATING,
            prompt="x",
            config={},
        )
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_rating_min_must_be_less_than_max(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.STIMULUS,
            type=Question.Type.RATING,
            prompt="x",
            config={"min": 100, "max": 0, "step": 1},
        )
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_rating_valid_custom_limits(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.STIMULUS,
            type=Question.Type.RATING,
            prompt="x",
            config={"min": -5, "max": 5, "step": 1},
        )
        q.full_clean()

    def test_choice_requires_non_empty_choices(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.DEMOGRAPHIC,
            type=Question.Type.CHOICE,
            prompt="x",
            config={"choices": []},
        )
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_choice_with_choices_is_valid(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.DEMOGRAPHIC,
            type=Question.Type.CHOICE,
            prompt="x",
            config={"choices": ["a", "b"], "multi": False},
        )
        q.full_clean()

    def test_text_requires_max_length(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.DEMOGRAPHIC,
            type=Question.Type.TEXT,
            prompt="x",
            config={},
        )
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_text_valid_config(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.DEMOGRAPHIC,
            type=Question.Type.TEXT,
            prompt="x",
            config={"max_length": 200},
        )
        q.full_clean()


# --- Stimulus autocompute on save -------------------------------------------


def _wav_bytes(num_samples: int = 16000, sample_rate: int = 8000) -> bytes:
    """Build a valid 1-channel 8-bit WAV file so mutagen can read its duration."""
    data_size = num_samples
    riff_size = 36 + data_size
    header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate, 1, 8)
    data = b"data" + struct.pack("<I", data_size) + b"\x80" * data_size
    return header + fmt + data


class TestStimulusAutoFields:
    def test_duration_and_sha256_computed_on_save(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        raw = _wav_bytes(num_samples=16000, sample_rate=8000)  # 2.0 seconds
        upload = SimpleUploadedFile("ok.wav", raw, content_type="audio/wav")
        stim = Stimulus(condition=cond, title="ok", audio=upload)
        stim.full_clean()
        stim.save()
        stim.refresh_from_db()
        assert stim.duration_seconds == pytest.approx(2.0, abs=0.01)
        assert stim.sha256 == hashlib.sha256(raw).hexdigest()

    def test_missing_duration_does_not_block_save(self):
        """If mutagen can't parse the file, save should still succeed with duration=None."""
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        upload = SimpleUploadedFile("garbage.mp3", b"ID3\x03\x00\x00" + b"\x00" * 32)
        stim = Stimulus(condition=cond, title="g", audio=upload)
        stim.full_clean()
        stim.save()
        assert stim.sha256 is not None
        # duration may be None or a fallback; should not raise.


# --- Stimulus kind (audio / image / text) -----------------------------------


class TestStimulusKind:
    def test_audio_default_factory_is_audio_kind(self):
        stim = StimulusFactory()
        assert stim.kind == Stimulus.Kind.AUDIO
        assert stim.audio
        assert not stim.image
        assert stim.text_body == ""

    def test_image_factory_produces_valid_image_stimulus(self):
        stim = ImageStimulusFactory()
        stim.full_clean()
        assert stim.kind == Stimulus.Kind.IMAGE
        assert stim.image
        assert not stim.audio
        # Images hash their file bytes but never get a duration.
        assert stim.sha256
        assert stim.duration_seconds is None

    def test_text_factory_produces_valid_text_stimulus(self):
        stim = TextStimulusFactory()
        stim.full_clean()
        assert stim.kind == Stimulus.Kind.TEXT
        assert stim.text_body
        assert not stim.audio
        assert not stim.image
        # Text stimuli carry no bytes, so nothing to hash or probe.
        assert stim.sha256 == ""
        assert stim.duration_seconds is None

    def test_audio_kind_requires_audio_file(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(condition=cond, title="no audio", kind=Stimulus.Kind.AUDIO)
        with pytest.raises(ValidationError):
            stim.full_clean()

    def test_image_kind_requires_image_file(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(condition=cond, title="no image", kind=Stimulus.Kind.IMAGE)
        with pytest.raises(ValidationError):
            stim.full_clean()

    def test_text_kind_requires_non_empty_text_body(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(
            condition=cond, title="no text", kind=Stimulus.Kind.TEXT, text_body="   "
        )
        with pytest.raises(ValidationError):
            stim.full_clean()

    def test_audio_kind_rejects_image_field(self):
        exp = ExperimentFactory()
        cond = ConditionFactory(experiment=exp)
        stim = Stimulus(
            condition=cond,
            title="mixed",
            kind=Stimulus.Kind.AUDIO,
            audio=SimpleUploadedFile("a.mp3", b"ID3\x03\x00\x00" + b"\x00" * 32),
        )
        # Attach an image via the descriptor so validation sees it.
        from django.core.files.uploadedfile import SimpleUploadedFile as F

        stim.image = F("x.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        with pytest.raises(ValidationError):
            stim.full_clean()


# --- Question page breaks ---------------------------------------------------


class TestQuestionPageBreak:
    def test_default_is_false(self):
        exp = ExperimentFactory()
        q = RatingQuestionFactory(experiment=exp)
        assert q.page_break_before is False


class TestPaginateQuestions:
    def _make_questions(self, breaks: list[bool]) -> list[Question]:
        exp = ExperimentFactory()
        questions: list[Question] = []
        for i, brk in enumerate(breaks):
            q = RatingQuestionFactory(
                experiment=exp, sort_order=i, page_break_before=brk
            )
            questions.append(q)
        return questions

    def test_empty_input_yields_no_pages(self):
        assert paginate_questions([]) == []

    def test_no_breaks_yields_single_page(self):
        qs = self._make_questions([False, False, False])
        pages = paginate_questions(qs)
        assert len(pages) == 1
        assert pages[0] == qs

    def test_break_on_first_is_idempotent(self):
        # The first question always starts a new page, regardless of flag.
        qs = self._make_questions([True, False, False])
        pages = paginate_questions(qs)
        assert len(pages) == 1

    def test_middle_break_splits_into_two_pages(self):
        qs = self._make_questions([False, True, False])
        pages = paginate_questions(qs)
        assert len(pages) == 2
        assert pages[0] == [qs[0]]
        assert pages[1] == [qs[1], qs[2]]

    def test_every_break_gives_one_question_per_page(self):
        qs = self._make_questions([False, True, True, True])
        pages = paginate_questions(qs)
        assert [len(p) for p in pages] == [1, 1, 1, 1]

    def test_rating_config_allows_optional_min_max_labels(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.STIMULUS,
            type=Question.Type.RATING,
            prompt="x",
            config={
                "min": 0,
                "max": 100,
                "step": 1,
                "min_label": "hate it",
                "max_label": "love it",
            },
        )
        q.full_clean()

    def test_rating_rejects_non_string_label(self):
        exp = ExperimentFactory()
        q = Question(
            experiment=exp,
            section=Question.Section.STIMULUS,
            type=Question.Type.RATING,
            prompt="x",
            config={"min": 0, "max": 100, "step": 1, "min_label": 42},
        )
        with pytest.raises(ValidationError):
            q.full_clean()


# --- Prompt model + PAIRWISE_AUDIO activation validator ---------------------


class TestPromptModel:
    def test_prompt_save_autocomputes_sha256(self):
        exp = ExperimentFactory()
        prompt = PromptFactory(experiment=exp, prompt_group="g-1")
        prompt.refresh_from_db()
        assert prompt.sha256
        assert len(prompt.sha256) == 64

    def test_unique_per_experiment_and_prompt_group(self):
        exp = ExperimentFactory()
        PromptFactory(experiment=exp, prompt_group="dup")
        with pytest.raises(Exception):  # IntegrityError / ValidationError
            PromptFactory(experiment=exp, prompt_group="dup")

    def test_same_prompt_group_allowed_across_experiments(self):
        e1 = ExperimentFactory()
        e2 = ExperimentFactory()
        PromptFactory(experiment=e1, prompt_group="shared")
        PromptFactory(experiment=e2, prompt_group="shared")  # should not raise
        assert Prompt.objects.filter(prompt_group="shared").count() == 2

    def test_cannot_add_prompt_to_active_experiment(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.ACTIVE
        exp.save(update_fields=["state"])
        p = PromptFactory.build(experiment=exp, prompt_group="late")
        with pytest.raises(ValidationError):
            p.full_clean()

    def test_cannot_delete_prompt_from_active_experiment(self):
        exp = ExperimentFactory()
        prompt = PromptFactory(experiment=exp, prompt_group="keep")
        exp.state = Experiment.State.ACTIVE
        exp.save(update_fields=["state"])
        with pytest.raises(ValidationError):
            prompt.delete()


class TestPairwiseAudioActivation:
    def _build_minimal_audio_experiment(self):
        exp = PairwiseAudioExperimentFactory()
        cond_a = ConditionFactory(experiment=exp, name="A")
        cond_b = ConditionFactory(experiment=exp, name="B")
        StimulusFactory(condition=cond_a, prompt_group="g", title="a1")
        StimulusFactory(condition=cond_b, prompt_group="g", title="b1")
        return exp

    def test_activation_requires_prompt_for_every_group(self):
        exp = self._build_minimal_audio_experiment()
        exp.state = Experiment.State.ACTIVE
        with pytest.raises(ValidationError) as excinfo:
            exp.full_clean()
        assert "prompt_group" in str(excinfo.value).lower() or \
               "missing" in str(excinfo.value).lower()

    def test_activation_succeeds_when_prompts_present(self):
        exp = self._build_minimal_audio_experiment()
        PromptFactory(experiment=exp, prompt_group="g")
        exp.state = Experiment.State.ACTIVE
        exp.full_clean()  # should not raise

    def test_activation_rejects_non_audio_stimuli(self):
        exp = PairwiseAudioExperimentFactory()
        cond_a = ConditionFactory(experiment=exp, name="A")
        cond_b = ConditionFactory(experiment=exp, name="B")
        StimulusFactory(condition=cond_a, prompt_group="g")
        TextStimulusFactory(condition=cond_b, prompt_group="g")
        PromptFactory(experiment=exp, prompt_group="g")
        exp.state = Experiment.State.ACTIVE
        with pytest.raises(ValidationError) as excinfo:
            exp.full_clean()
        assert "audio" in str(excinfo.value).lower()

    def test_standard_pairwise_does_not_require_prompts(self):
        from experiments.tests.factories import PairwiseExperimentFactory

        exp = PairwiseExperimentFactory()
        cond_a = ConditionFactory(experiment=exp, name="A")
        cond_b = ConditionFactory(experiment=exp, name="B")
        StimulusFactory(condition=cond_a, prompt_group="g")
        StimulusFactory(condition=cond_b, prompt_group="g")
        exp.state = Experiment.State.ACTIVE
        exp.full_clean()  # no Prompt required in plain PAIRWISE mode
