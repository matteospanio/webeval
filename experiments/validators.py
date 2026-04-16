"""Validators for experiment-related fields."""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator


def audio_extension_validator():
    return FileExtensionValidator(allowed_extensions=list(settings.STIMULUS_ALLOWED_EXTENSIONS))


def audio_size_validator(file_obj) -> None:
    """Reject audio uploads larger than STIMULUS_MAX_UPLOAD_BYTES."""
    size = getattr(file_obj, "size", None)
    if size is None:
        return
    cap = settings.STIMULUS_MAX_UPLOAD_BYTES
    if size > cap:
        raise ValidationError(
            f"Audio file is {size} bytes; maximum allowed is {cap} bytes."
        )


def image_extension_validator():
    return FileExtensionValidator(
        allowed_extensions=list(settings.STIMULUS_ALLOWED_IMAGE_EXTENSIONS)
    )


def image_size_validator(file_obj) -> None:
    """Reject image uploads larger than STIMULUS_MAX_IMAGE_UPLOAD_BYTES."""
    size = getattr(file_obj, "size", None)
    if size is None:
        return
    cap = settings.STIMULUS_MAX_IMAGE_UPLOAD_BYTES
    if size > cap:
        raise ValidationError(
            f"Image file is {size} bytes; maximum allowed is {cap} bytes."
        )
