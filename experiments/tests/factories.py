"""Factory-boy factories for the experiments app.

Factories produce ``draft``-state objects by default so tests can freely add
child rows; flip ``state`` on the returned instance when you need to test
lifecycle or structural-edit guards.
"""
from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from experiments.models import Condition, Experiment, Prompt, Question, Stimulus


class ExperimentFactory(DjangoModelFactory):
    class Meta:
        model = Experiment

    name = factory.Sequence(lambda n: f"Study {n}")
    slug = factory.Sequence(lambda n: f"study-{n}")
    description = "A listening study."
    consent_text = "I agree to participate."
    state = Experiment.State.DRAFT
    # Opt out of the pre-survey audio playback check by default so existing
    # flow tests don't need to POST an extra step. Tests that exercise the
    # check explicitly set ``require_audio_check=True``.
    require_audio_check = False


class ConditionFactory(DjangoModelFactory):
    class Meta:
        model = Condition

    experiment = factory.SubFactory(ExperimentFactory)
    name = factory.Sequence(lambda n: f"Condition {n}")


class StimulusFactory(DjangoModelFactory):
    class Meta:
        model = Stimulus

    condition = factory.SubFactory(ConditionFactory)
    title = factory.Sequence(lambda n: f"Clip {n}")
    kind = Stimulus.Kind.AUDIO
    audio = factory.django.FileField(
        filename="clip.mp3",
        data=b"ID3\x03\x00\x00\x00\x00\x00\x00",  # minimal MP3 header
    )
    is_active = True


class ImageStimulusFactory(StimulusFactory):
    """A stimulus whose kind is IMAGE — carries an image file, no audio."""

    kind = Stimulus.Kind.IMAGE
    audio = None
    image = factory.django.ImageField(
        filename="clip.png",
        # factory-boy synthesises a real PNG via Pillow.
    )


class TextStimulusFactory(StimulusFactory):
    """A text-only stimulus — no file uploads, just a body of text."""

    kind = Stimulus.Kind.TEXT
    audio = None
    text_body = factory.Sequence(lambda n: f"Prompt body #{n}: imagine a short melody.")


class RatingQuestionFactory(DjangoModelFactory):
    class Meta:
        model = Question

    experiment = factory.SubFactory(ExperimentFactory)
    section = Question.Section.STIMULUS
    type = Question.Type.RATING
    prompt = "How much do you like this?"
    required = True
    config = factory.LazyFunction(lambda: {"min": 0, "max": 100, "step": 1})


class ChoiceQuestionFactory(DjangoModelFactory):
    class Meta:
        model = Question

    experiment = factory.SubFactory(ExperimentFactory)
    section = Question.Section.DEMOGRAPHIC
    type = Question.Type.CHOICE
    prompt = "Gender"
    required = False
    config = factory.LazyFunction(
        lambda: {"choices": ["female", "male", "non-binary", "prefer not to say"], "multi": False}
    )


class TextQuestionFactory(DjangoModelFactory):
    class Meta:
        model = Question

    experiment = factory.SubFactory(ExperimentFactory)
    section = Question.Section.DEMOGRAPHIC
    type = Question.Type.TEXT
    prompt = "Any comments?"
    required = False
    config = factory.LazyFunction(lambda: {"max_length": 500})


class LikertQuestionFactory(DjangoModelFactory):
    class Meta:
        model = Question

    experiment = factory.SubFactory(ExperimentFactory)
    section = Question.Section.DEMOGRAPHIC
    type = Question.Type.LIKERT
    prompt = "I enjoy listening to music."
    required = True
    config = factory.LazyFunction(
        lambda: {
            "steps": 7,
            "labels": [
                "Strongly disagree",
                "Disagree",
                "Somewhat disagree",
                "Neutral",
                "Somewhat agree",
                "Agree",
                "Strongly agree",
            ],
        }
    )


class PairwiseExperimentFactory(ExperimentFactory):
    """A pairwise-mode experiment with sensible defaults."""

    mode = Experiment.Mode.PAIRWISE
    assignment_strategy = "pairwise_balanced"
    stimuli_per_participant = 5


class PairwiseAudioExperimentFactory(ExperimentFactory):
    """A pairwise-audio-mode experiment (audio prompt + two continuations)."""

    mode = Experiment.Mode.PAIRWISE_AUDIO
    assignment_strategy = "pairwise_balanced"
    stimuli_per_participant = 5


class PromptFactory(DjangoModelFactory):
    class Meta:
        model = Prompt

    experiment = factory.SubFactory(ExperimentFactory)
    prompt_group = factory.Sequence(lambda n: f"prompt-{n}")
    title = factory.Sequence(lambda n: f"Prompt {n}")
    audio = factory.django.FileField(
        filename="prompt.mp3",
        data=b"ID3\x03\x00\x00\x00\x00\x00\x00",  # minimal MP3 header
    )
