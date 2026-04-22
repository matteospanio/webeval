"""REST API for batch-uploading stimuli.

One endpoint, ``POST /api/v1/experiments/<slug>/stimuli/``, accepts multipart
uploads from the :mod:`scripts.batch_upload_stimuli` script (or any other
staff-only client with a DRF token). It auto-creates the named ``Condition``
if missing, and is idempotent over SHA-256: re-uploading the same file under
the same experiment returns ``200 {"skipped": true}`` instead of creating a
duplicate row.
"""
from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apikeys.mixins import LogAPIKeyUsageMixin
from apikeys.permissions import HasScope

from .csv_exports import iter_pairwise_answers
from .models import Condition, Experiment, Prompt, Stimulus


class StimulusUploadSerializer(serializers.Serializer):
    condition = serializers.CharField(max_length=200)
    prompt_group = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    kind = serializers.ChoiceField(
        choices=Stimulus.Kind.choices, default=Stimulus.Kind.AUDIO
    )
    audio = serializers.FileField(required=False)
    image = serializers.FileField(required=False)
    text_body = serializers.CharField(required=False, allow_blank=True, default="")
    sort_order = serializers.IntegerField(required=False, default=0, min_value=0)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        kind = attrs["kind"]
        if kind == Stimulus.Kind.AUDIO and not attrs.get("audio"):
            raise serializers.ValidationError(
                {"audio": "Audio stimuli require an uploaded audio file."}
            )
        if kind == Stimulus.Kind.IMAGE and not attrs.get("image"):
            raise serializers.ValidationError(
                {"image": "Image stimuli require an uploaded image file."}
            )
        if kind == Stimulus.Kind.TEXT and not (attrs.get("text_body") or "").strip():
            raise serializers.ValidationError(
                {"text_body": "Text stimuli require non-empty text."}
            )
        return attrs


def _hash_upload(upload) -> str:
    hasher = hashlib.sha256()
    for chunk in upload.chunks():
        hasher.update(chunk)
    upload.seek(0)
    return hasher.hexdigest()


class StimulusUploadView(LogAPIKeyUsageMixin, APIView):
    """Create one ``Stimulus`` under a draft experiment.

    Response shape:

    * ``201`` — stimulus created. Body: ``{"id", "sha256", "duration_seconds",
      "condition", "created_condition"}``.
    * ``200`` + ``{"skipped": true, "reason": "duplicate_sha256", "sha256"}``
      when a stimulus with the same SHA-256 already exists under the same
      experiment.
    * ``400`` — validation errors (bad extension, oversized file, missing
      required field).
    * ``404`` — experiment slug unknown.
    * ``409`` — experiment is not in DRAFT state.
    """

    permission_classes = [HasScope("stimuli:upload")]

    def post(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        if experiment.state != Experiment.State.DRAFT:
            return Response(
                {
                    "detail": (
                        f"Experiment '{experiment.slug}' is "
                        f"{experiment.get_state_display().lower()}; stimuli can "
                        "only be added while it is in draft state."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        serializer = StimulusUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        media = data.get("audio") or data.get("image")
        if media is not None:
            digest = _hash_upload(media)
            existing = Stimulus.objects.filter(
                condition__experiment=experiment, sha256=digest
            ).first()
            if existing is not None:
                return Response(
                    {
                        "skipped": True,
                        "reason": "duplicate_sha256",
                        "sha256": digest,
                        "stimulus_id": existing.pk,
                    },
                    status=status.HTTP_200_OK,
                )

        try:
            with transaction.atomic():
                condition, created = Condition.objects.get_or_create(
                    experiment=experiment,
                    name=data["condition"],
                )
                stimulus = Stimulus(
                    condition=condition,
                    title=data["title"],
                    description=data.get("description", ""),
                    kind=data["kind"],
                    prompt_group=data.get("prompt_group", ""),
                    sort_order=data.get("sort_order", 0),
                    is_active=data.get("is_active", True),
                    text_body=data.get("text_body", ""),
                )
                if data.get("audio"):
                    stimulus.audio = data["audio"]
                if data.get("image"):
                    stimulus.image = data["image"]
                stimulus.full_clean()
                stimulus.save()
        except DjangoValidationError as exc:
            return Response(
                {"detail": "validation_error", "errors": exc.message_dict
                    if hasattr(exc, "message_dict") else exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stimulus.refresh_from_db()
        return Response(
            {
                "id": stimulus.pk,
                "sha256": stimulus.sha256,
                "duration_seconds": stimulus.duration_seconds,
                "condition": condition.name,
                "created_condition": created,
            },
            status=status.HTTP_201_CREATED,
        )


class PromptUploadSerializer(serializers.Serializer):
    prompt_group = serializers.CharField(max_length=200)
    title = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    audio = serializers.FileField()


class PromptUploadView(LogAPIKeyUsageMixin, APIView):
    """Create or update one ``Prompt`` under a draft experiment.

    Response shape mirrors :class:`StimulusUploadView`:

    * ``201`` — prompt created. Body: ``{"id", "sha256", "duration_seconds",
      "prompt_group"}``.
    * ``200`` + ``{"skipped": true, "reason": "duplicate_sha256", "sha256",
      "prompt_id"}`` when a prompt with the same SHA-256 already exists
      under the same experiment.
    * ``400`` — validation errors (bad extension, oversized file, duplicate
      ``prompt_group`` with a different audio file).
    * ``404`` — experiment slug unknown.
    * ``409`` — experiment is not in DRAFT state.
    """

    permission_classes = [HasScope("prompts:upload")]

    def post(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        if experiment.state != Experiment.State.DRAFT:
            return Response(
                {
                    "detail": (
                        f"Experiment '{experiment.slug}' is "
                        f"{experiment.get_state_display().lower()}; prompts can "
                        "only be added while it is in draft state."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        serializer = PromptUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        digest = _hash_upload(data["audio"])
        existing = Prompt.objects.filter(
            experiment=experiment, sha256=digest
        ).first()
        if existing is not None:
            return Response(
                {
                    "skipped": True,
                    "reason": "duplicate_sha256",
                    "sha256": digest,
                    "prompt_id": existing.pk,
                },
                status=status.HTTP_200_OK,
            )

        try:
            with transaction.atomic():
                prompt = Prompt(
                    experiment=experiment,
                    prompt_group=data["prompt_group"],
                    title=data.get("title", ""),
                    description=data.get("description", ""),
                    audio=data["audio"],
                )
                prompt.full_clean()
                prompt.save()
        except DjangoValidationError as exc:
            return Response(
                {
                    "detail": "validation_error",
                    "errors": exc.message_dict
                    if hasattr(exc, "message_dict")
                    else exc.messages,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        prompt.refresh_from_db()
        return Response(
            {
                "id": prompt.pk,
                "sha256": prompt.sha256,
                "duration_seconds": prompt.duration_seconds,
                "prompt_group": prompt.prompt_group,
            },
            status=status.HTTP_201_CREATED,
        )


class PairwiseAnswersView(LogAPIKeyUsageMixin, APIView):
    """Return all submitted pairwise-comparison answers for an experiment as JSON.

    One row per (session, pair, question). Only sessions with a
    ``submitted_at`` timestamp are included (abandoned sessions are dropped),
    matching the existing CSV export at
    ``/admin/experiments/experiment/<slug>/pairwise-answers.csv``.

    Consumed by :mod:`scripts.analyze_pairwise`.
    """

    permission_classes = [HasScope("pairwise-answers:read")]

    def get(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return Response(list(iter_pairwise_answers(experiment)))
