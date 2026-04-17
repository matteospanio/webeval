"""Bradley-Terry analysis of pairwise comparison data.

Reads a pairwise answers CSV exported from the webeval admin (or queries the
database directly) and fits a Bradley-Terry model per evaluation dimension.
Outputs paper-ready ranking tables in plain text and LaTeX.

Usage examples::

    # From an exported CSV
    uv run --group analysis python scripts/analyze_pairwise.py data/pairwise-answers.csv

    # Directly from the Django database
    uv run --group analysis python scripts/analyze_pairwise.py --from-db --experiment my-study

    # Write LaTeX to a file
    uv run --group analysis python scripts/analyze_pairwise.py data.csv --output-file results.tex
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fit a Bradley-Terry model to pairwise comparison data.",
    )
    p.add_argument(
        "csv_path",
        nargs="?",
        help="Path to the exported pairwise-answers CSV file.",
    )
    p.add_argument(
        "--from-db",
        action="store_true",
        help="Load data from the Django database instead of a CSV file.",
    )
    p.add_argument(
        "--experiment",
        help="Experiment slug (required when --from-db is used).",
    )
    p.add_argument(
        "--output-format",
        choices=["text", "latex", "both"],
        default="both",
        help="Output format (default: both).",
    )
    p.add_argument(
        "--output-file",
        help="Write LaTeX output to this file instead of stdout.",
    )
    args = p.parse_args(argv)

    if args.from_db:
        if not args.experiment:
            p.error("--experiment is required when using --from-db")
    elif not args.csv_path:
        p.error("a CSV path is required (or use --from-db --experiment <slug>)")

    return args


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = {
    "session_id",
    "model_a",
    "model_b",
    "position_a",
    "preferred",
    "question_id",
    "question_prompt",
}


def _parse_preferred(value: str) -> str:
    """Decode the ``preferred`` column, handling both JSON-encoded and plain values."""
    stripped = value.strip()
    if stripped.startswith('"'):
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            pass
    return stripped


def load_from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    missing = EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise SystemExit(
            f"CSV is missing expected columns: {', '.join(sorted(missing))}"
        )
    df["preferred"] = df["preferred"].apply(_parse_preferred)
    return df


def load_from_db(experiment_slug: str) -> pd.DataFrame:
    # Set up Django
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

    import django
    django.setup()

    from experiments.models import Experiment  # noqa: E402
    from survey.models import Response  # noqa: E402

    try:
        experiment = Experiment.objects.get(slug=experiment_slug)
    except Experiment.DoesNotExist:
        raise SystemExit(f"Experiment '{experiment_slug}' not found.")

    rows = (
        Response.objects.filter(
            session__experiment=experiment,
            session__submitted_at__isnull=False,
            pair_assignment__isnull=False,
        )
        .select_related(
            "session",
            "pair_assignment",
            "pair_assignment__stimulus_a__condition",
            "pair_assignment__stimulus_b__condition",
            "question",
        )
        .order_by("session__id", "pair_assignment__sort_order", "question__sort_order")
    )

    records = []
    for r in rows:
        pa = r.pair_assignment
        records.append(
            {
                "session_id": str(r.session_id),
                "submitted_at": (
                    r.session.submitted_at.isoformat()
                    if r.session.submitted_at
                    else ""
                ),
                "experiment": experiment.slug,
                "pair_index": pa.sort_order,
                "model_a": pa.stimulus_a.condition.name,
                "model_b": pa.stimulus_b.condition.name,
                "prompt_group": pa.prompt_group,
                "position_a": pa.position_a,
                "question_id": r.question_id,
                "question_prompt": r.question.prompt[:100],
                "preferred": r.get_answer(),
                "listen_duration_a_ms": pa.listen_duration_a_ms,
                "listen_duration_b_ms": pa.listen_duration_b_ms,
            }
        )
    if not records:
        raise SystemExit(f"No pairwise data found for experiment '{experiment_slug}'.")
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Winner / loser resolution
# ---------------------------------------------------------------------------

def resolve_winner(row: pd.Series) -> str:
    """Map the displayed A/B preference back to the actual model name.

    "A" = the participant preferred the LEFT sample.
    ``position_a`` tells us which model was on the left.
    """
    if row["preferred"] == "A":
        return row["model_a"] if row["position_a"] == "left" else row["model_b"]
    return row["model_b"] if row["position_a"] == "left" else row["model_a"]


def resolve_loser(row: pd.Series) -> str:
    if row["preferred"] == "A":
        return row["model_b"] if row["position_a"] == "left" else row["model_a"]
    return row["model_a"] if row["position_a"] == "left" else row["model_b"]


# ---------------------------------------------------------------------------
# Bradley-Terry MLE
# ---------------------------------------------------------------------------

def fit_bradley_terry(
    comparisons: pd.DataFrame,
    models: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Fit a Bradley-Terry model via maximum likelihood.

    Parameters
    ----------
    comparisons : DataFrame
        Must contain ``winner`` and ``loser`` columns with model names.
    models : list[str]
        Ordered list of all model names.  ``models[0]`` is the reference
        (its log-strength is fixed at 0).

    Returns
    -------
    beta : ndarray of shape (K,)
        Estimated log-strength for each model.
    se : ndarray of shape (K,)
        Standard errors (0 for the reference model).
    """
    K = len(models)
    idx = {m: i for i, m in enumerate(models)}

    # Build win-count matrix W[i, j] = times model i beat model j.
    W = np.zeros((K, K))
    for _, row in comparisons.iterrows():
        i = idx[row["winner"]]
        j = idx[row["loser"]]
        W[i, j] += 1

    # Negative log-likelihood (beta[0] fixed at 0).
    def nll(beta_free: np.ndarray) -> float:
        beta = np.empty(K)
        beta[0] = 0.0
        beta[1:] = beta_free
        val = 0.0
        for i in range(K):
            for j in range(i + 1, K):
                n_ij = W[i, j] + W[j, i]
                if n_ij == 0:
                    continue
                log_denom = np.logaddexp(beta[i], beta[j])
                val -= W[i, j] * (beta[i] - log_denom)
                val -= W[j, i] * (beta[j] - log_denom)
        return val

    result = minimize(nll, x0=np.zeros(K - 1), method="L-BFGS-B")
    if not result.success:
        print(f"  [warning] optimizer did not converge: {result.message}", file=sys.stderr)

    beta = np.empty(K)
    beta[0] = 0.0
    beta[1:] = result.x

    # Analytical Fisher information for the free parameters.
    H = np.zeros((K - 1, K - 1))
    for i in range(K):
        for j in range(i + 1, K):
            n_ij = W[i, j] + W[j, i]
            if n_ij == 0:
                continue
            p_ij = 1.0 / (1.0 + np.exp(beta[j] - beta[i]))
            fisher = n_ij * p_ij * (1.0 - p_ij)
            fi, fj = i - 1, j - 1  # free-parameter indices (-1 → reference)
            if fi >= 0:
                H[fi, fi] += fisher
            if fj >= 0:
                H[fj, fj] += fisher
            if fi >= 0 and fj >= 0:
                H[fi, fj] -= fisher
                H[fj, fi] -= fisher

    # Small ridge for numerical stability when pairs are sparse.
    H += 1e-8 * np.eye(K - 1)

    try:
        cov = np.linalg.inv(H)
        se_free = np.sqrt(np.maximum(np.diag(cov), 0.0))
    except np.linalg.LinAlgError:
        se_free = np.full(K - 1, np.nan)

    se = np.empty(K)
    se[0] = 0.0
    se[1:] = se_free

    return beta, se


# ---------------------------------------------------------------------------
# Win-rate statistics
# ---------------------------------------------------------------------------

def compute_win_rates(
    comparisons: pd.DataFrame, models: list[str]
) -> dict[str, dict[str, int | float]]:
    wins: dict[str, int] = {m: 0 for m in models}
    total: dict[str, int] = {m: 0 for m in models}
    for _, row in comparisons.iterrows():
        w, l = row["winner"], row["loser"]
        wins[w] += 1
        total[w] += 1
        total[l] += 1
    return {
        m: {"wins": wins[m], "total": total[m],
            "win_pct": 100.0 * wins[m] / total[m] if total[m] else 0.0}
        for m in models
    }


# ---------------------------------------------------------------------------
# Analysis orchestration
# ---------------------------------------------------------------------------

def analyze(df: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Run the full Bradley-Terry analysis.

    Returns
    -------
    per_dimension : dict mapping question label -> ranking DataFrame
    summary : DataFrame with models as rows, dimensions as columns
    """
    df = df.copy()
    df["winner"] = df.apply(resolve_winner, axis=1)
    df["loser"] = df.apply(resolve_loser, axis=1)

    models = sorted(set(df["model_a"]) | set(df["model_b"]))
    if len(models) < 2:
        raise SystemExit("Need at least 2 models for pairwise analysis.")

    # Group by question_id (stable) with question_prompt as label.
    question_labels: dict[str, str] = {}
    for _, row in df[["question_id", "question_prompt"]].drop_duplicates().iterrows():
        question_labels[str(row["question_id"])] = row["question_prompt"]

    per_dimension: dict[str, pd.DataFrame] = {}
    summary_data: dict[str, dict[str, str]] = {m: {} for m in models}

    for qid, label in sorted(question_labels.items(), key=lambda kv: kv[1]):
        subset = df[df["question_id"].astype(str) == qid]
        if subset.empty:
            continue

        beta, se = fit_bradley_terry(subset, models)
        wr = compute_win_rates(subset, models)

        # Build per-dimension table sorted by BT score descending.
        rows = []
        for i, m in enumerate(models):
            ci_lo = beta[i] - 1.96 * se[i]
            ci_hi = beta[i] + 1.96 * se[i]
            rows.append(
                {
                    "Model": m,
                    "BT Score": beta[i],
                    "CI Low": ci_lo,
                    "CI High": ci_hi,
                    "SE": se[i],
                    "Wins": wr[m]["wins"],
                    "Total": wr[m]["total"],
                    "Win%": wr[m]["win_pct"],
                }
            )
        dim_df = pd.DataFrame(rows).sort_values("BT Score", ascending=False).reset_index(drop=True)
        dim_df.index = dim_df.index + 1  # 1-based rank
        dim_df.index.name = "Rank"
        per_dimension[label] = dim_df

        # Populate summary.
        for i, m in enumerate(models):
            if np.isnan(se[i]):
                summary_data[m][label] = f"{beta[i]:+.2f}"
            else:
                summary_data[m][label] = f"{beta[i]:+.2f} \u00b1 {1.96*se[i]:.2f}"

    # Build summary DataFrame sorted by mean BT rank across dimensions.
    summary = pd.DataFrame(summary_data).T
    summary.index.name = "Model"

    # Compute mean rank for sorting.
    rank_cols = []
    for label, dim_df in per_dimension.items():
        rank_map = {row["Model"]: rank for rank, row in dim_df.iterrows()}
        rank_cols.append(pd.Series(rank_map, name=label))
    if rank_cols:
        mean_rank = pd.concat(rank_cols, axis=1).mean(axis=1)
        summary["_mean_rank"] = mean_rank
        summary = summary.sort_values("_mean_rank")
        summary = summary.drop(columns=["_mean_rank"])

    return per_dimension, summary


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def format_text(
    per_dimension: dict[str, pd.DataFrame],
    summary: pd.DataFrame,
) -> str:
    parts: list[str] = []

    for label, dim_df in per_dimension.items():
        parts.append(f"\n{'=' * 60}")
        parts.append(f"  {label}")
        parts.append(f"{'=' * 60}")
        lines = []
        header = (
            f"{'Rank':>4}  {'Model':<24} {'BT Score':>9}  "
            f"{'95% CI':>16}  {'Wins':>5} {'Total':>5} {'Win%':>6}"
        )
        lines.append(header)
        lines.append("\u2500" * len(header))
        for rank, row in dim_df.iterrows():
            ci = f"[{row['CI Low']:+.2f}, {row['CI High']:+.2f}]"
            lines.append(
                f"{rank:>4}  {row['Model']:<24} {row['BT Score']:>+9.3f}  "
                f"{ci:>16}  {row['Wins']:>5.0f} {row['Total']:>5.0f} {row['Win%']:>5.1f}%"
            )
        parts.append("\n".join(lines))

    parts.append(f"\n{'=' * 60}")
    parts.append("  Combined Summary (BT score \u00b1 95% CI)")
    parts.append(f"{'=' * 60}")
    parts.append(summary.to_string())

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LaTeX output
# ---------------------------------------------------------------------------

def _escape_latex(s: str) -> str:
    return s.replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def format_latex(
    per_dimension: dict[str, pd.DataFrame],
    summary: pd.DataFrame,
) -> str:
    dimensions = list(summary.columns)
    n_dims = len(dimensions)

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Bradley--Terry model scores with 95\% confidence intervals.}",
        r"\label{tab:bt-scores}",
        r"\begin{tabular}{l" + "c" * n_dims + "}",
        r"\toprule",
        "Model & " + " & ".join(_escape_latex(d) for d in dimensions) + r" \\",
        r"\midrule",
    ]

    for model in summary.index:
        cells = [_escape_latex(model)]
        for dim in dimensions:
            raw = summary.loc[model, dim]
            # Parse "score +/- ci" into LaTeX formatting.
            if "\u00b1" in str(raw):
                score_str, ci_str = str(raw).split("\u00b1")
                cells.append(
                    f"${score_str.strip()}" + r"_{\pm " + ci_str.strip() + r"}$"
                )
            else:
                cells.append(f"${raw}$")
        lines.append(" & ".join(cells) + r" \\")

    lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.from_db:
        df = load_from_db(args.experiment)
    else:
        df = load_from_csv(args.csv_path)

    per_dimension, summary = analyze(df)

    if args.output_format in ("text", "both"):
        print(format_text(per_dimension, summary))

    if args.output_format in ("latex", "both"):
        latex = format_latex(per_dimension, summary)
        if args.output_file:
            Path(args.output_file).write_text(latex, encoding="utf-8")
            print(f"\nLaTeX written to {args.output_file}")
        else:
            print(f"\n{'=' * 60}")
            print("  LaTeX Table")
            print(f"{'=' * 60}")
            print(latex)


if __name__ == "__main__":
    main()
