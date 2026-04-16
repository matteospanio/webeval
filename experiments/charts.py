"""Server-rendered SVG charts embedded on the admin Experiment change view.

Using matplotlib with the headless ``Agg`` backend means the admin has
zero client-side JS dependencies — we just drop the raw SVG markup into
the template. Each helper returns a ``str`` of SVG so views can wrap it
in an ``HttpResponse`` with ``Content-Type: image/svg+xml`` or use
``|safe`` in a template.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # must come before pyplot import
import matplotlib.pyplot as plt  # noqa: E402

from experiments.models import Experiment  # noqa: E402

from .stats import pairwise_experiment_stats, per_stimulus_mean_ratings  # noqa: E402


def _svg_from_figure(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def mean_ratings_svg(experiment: Experiment) -> str:
    """Horizontal bar chart of per-stimulus mean ratings."""
    rows = per_stimulus_mean_ratings(experiment)
    fig, ax = plt.subplots(figsize=(6, max(2.0, 0.4 * max(len(rows), 1) + 1.0)))
    if not rows:
        ax.text(
            0.5,
            0.5,
            "No ratings yet",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=11,
            color="#666",
        )
        ax.set_axis_off()
        return _svg_from_figure(fig)

    labels = [f"{row['title']} ({row['condition']})" for row in rows]
    means = [row["mean"] for row in rows]
    ax.barh(labels, means, color="#345")
    ax.set_xlabel("Mean rating")
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for i, value in enumerate(means):
        ax.text(value, i, f" {value:.1f}", va="center", fontsize=9)
    return _svg_from_figure(fig)


def pairwise_win_rates_svg(experiment: Experiment) -> str:
    """Bar chart of total wins per model across all attributes."""
    stats = pairwise_experiment_stats(experiment)
    fig, ax = plt.subplots(figsize=(6, max(2.5, 0.4 * max(len(stats.per_model_wins), 1) + 1.0)))

    if not stats.per_model_wins:
        ax.text(
            0.5, 0.5, "No pairwise data yet",
            ha="center", va="center", transform=ax.transAxes,
            fontsize=11, color="#666",
        )
        ax.set_axis_off()
        return _svg_from_figure(fig)

    # Total wins per model (sum over all attributes).
    model_totals: dict[str, int] = {}
    for model, per_q in stats.per_model_wins.items():
        model_totals[model] = sum(per_q.values())

    models_sorted = sorted(model_totals, key=model_totals.get, reverse=True)
    values = [model_totals[m] for m in models_sorted]

    ax.barh(models_sorted, values, color="#345")
    ax.set_xlabel("Total wins")
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for i, v in enumerate(values):
        ax.text(v, i, f" {v}", va="center", fontsize=9)
    return _svg_from_figure(fig)
