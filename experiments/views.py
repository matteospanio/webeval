"""Staff-only views for reproducibility exports."""
from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .exports import build_reproducibility_bundle
from .models import Experiment


@staff_member_required
def repro_json(request, slug: str):
    experiment = get_object_or_404(Experiment, slug=slug)
    bundle = build_reproducibility_bundle(experiment)
    response = JsonResponse(bundle, json_dumps_params={"indent": 2})
    response["Content-Disposition"] = (
        f'attachment; filename="{experiment.slug}-reproducibility.json"'
    )
    return response


@staff_member_required
def printable(request, slug: str):
    experiment = get_object_or_404(Experiment, slug=slug)
    bundle = build_reproducibility_bundle(experiment)
    return render(
        request,
        "experiments/printable.html",
        {"experiment": experiment, "bundle": bundle},
    )
