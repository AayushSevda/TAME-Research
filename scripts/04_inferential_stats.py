import os
import math
import itertools
import numpy as np
import pandas as pd

try:
    from scipy import stats
except ImportError as e:
    raise RuntimeError(
        "scipy is required. Install it with: py -3.11 -m pip install scipy"
    ) from e


OUT_DIR = "reports_final_v3"
os.makedirs(OUT_DIR, exist_ok=True)

SERIES_1_BATCHES = [f"batch_{i}" for i in range(1, 11)]
SERIES_2_BATCHES = [f"s2_batch_{i}" for i in range(1, 11)]

METRICS = [
    "toxicity",
    "severe_toxicity",
    "identity_attack",
    "insult",
    "threat",
    "obscene",
    "toxicity_amplification_factor",
    "intersectional_gap",
    "output_word_count",
]


def load_all_rows() -> pd.DataFrame:
    frames = []

    for batch in SERIES_1_BATCHES:
        path = f"data/batches/{batch}/tame_analysis.csv"
        if os.path.exists(path):
            df = pd.read_csv(path)
            df["series"] = "series_1"
            df["batch"] = batch
            frames.append(df)
        else:
            print(f"MISSING SERIES 1 FILE: {path}")

    for batch in SERIES_2_BATCHES:
        path = f"data/series_2/batches/{batch}/tame_analysis.csv"
        if os.path.exists(path):
            df = pd.read_csv(path)
            df["series"] = "series_2"
            df["batch"] = batch
            frames.append(df)
        else:
            print(f"MISSING SERIES 2 FILE: {path}")

    if not frames:
        raise RuntimeError("No tame_analysis.csv files found.")

    df_all = pd.concat(frames, ignore_index=True)

    for metric in METRICS:
        if metric in df_all.columns:
            df_all[metric] = pd.to_numeric(df_all[metric], errors="coerce")

    return df_all


def ci95_t(x: pd.Series):
    x = pd.to_numeric(x, errors="coerce").dropna()
    n = len(x)
    if n == 0:
        return np.nan, np.nan, np.nan, np.nan, np.nan

    mean = float(x.mean())

    if n == 1:
        return n, mean, np.nan, np.nan, np.nan

    sd = float(x.std(ddof=1))
    sem = sd / math.sqrt(n)
    tcrit = stats.t.ppf(0.975, df=n - 1)
    low = mean - tcrit * sem
    high = mean + tcrit * sem
    return n, mean, sd, low, high


def make_summary(df: pd.DataFrame, group_cols, name: str):
    rows = []

    available_metrics = [m for m in METRICS if m in df.columns]

    for keys, group in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)

        base = dict(zip(group_cols, keys))

        for metric in available_metrics:
            x = group[metric].dropna()
            n, mean, sd, low, high = ci95_t(x)

            row = base.copy()
            row.update(
                {
                    "metric": metric,
                    "n": n,
                    "mean": mean,
                    "std": sd,
                    "median": float(x.median()) if len(x) else np.nan,
                    "min": float(x.min()) if len(x) else np.nan,
                    "max": float(x.max()) if len(x) else np.nan,
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )
            rows.append(row)

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT_DIR, name), index=False)
    return out


def cohens_d_independent(x, y):
    x = pd.to_numeric(pd.Series(x), errors="coerce").dropna()
    y = pd.to_numeric(pd.Series(y), errors="coerce").dropna()

    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return np.nan

    sx, sy = x.std(ddof=1), y.std(ddof=1)
    pooled = math.sqrt(((nx - 1) * sx**2 + (ny - 1) * sy**2) / (nx + ny - 2))

    if pooled == 0:
        return np.nan

    return float((y.mean() - x.mean()) / pooled)


def cohens_d_paired(diff):
    diff = pd.to_numeric(pd.Series(diff), errors="coerce").dropna()
    if len(diff) < 2:
        return np.nan

    sd = diff.std(ddof=1)
    if sd == 0:
        return np.nan

    return float(diff.mean() / sd)


def independent_prompt_type_tests(df: pd.DataFrame):
    rows = []
    prompt_types = ["neutral", "single_identity", "intersectional"]

    for metric in ["toxicity", "identity_attack", "severe_toxicity", "insult", "threat", "obscene"]:
        if metric not in df.columns:
            continue

        for a, b in itertools.combinations(prompt_types, 2):
            x = df.loc[df["prompt_type"] == a, metric].dropna()
            y = df.loc[df["prompt_type"] == b, metric].dropna()

            if len(x) < 2 or len(y) < 2:
                continue

            test = stats.ttest_ind(x, y, equal_var=False, nan_policy="omit")
            rows.append(
                {
                    "metric": metric,
                    "comparison": f"{a} vs {b}",
                    "group_a": a,
                    "group_b": b,
                    "n_a": len(x),
                    "n_b": len(y),
                    "mean_a": float(x.mean()),
                    "mean_b": float(y.mean()),
                    "mean_difference_b_minus_a": float(y.mean() - x.mean()),
                    "welch_t": float(test.statistic),
                    "p_value": float(test.pvalue),
                    "cohens_d_independent": cohens_d_independent(x, y),
                }
            )

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT_DIR, "independent_prompt_type_tests.csv"), index=False)
    return out


def model_pair_tests(df: pd.DataFrame):
    rows = []
    models = sorted(df["model"].dropna().unique())

    for metric in ["toxicity", "identity_attack", "severe_toxicity", "insult", "threat", "obscene"]:
        if metric not in df.columns:
            continue

        for a, b in itertools.combinations(models, 2):
            x = df.loc[df["model"] == a, metric].dropna()
            y = df.loc[df["model"] == b, metric].dropna()

            if len(x) < 2 or len(y) < 2:
                continue

            test = stats.ttest_ind(x, y, equal_var=False, nan_policy="omit")
            rows.append(
                {
                    "metric": metric,
                    "comparison": f"{a} vs {b}",
                    "model_a": a,
                    "model_b": b,
                    "n_a": len(x),
                    "n_b": len(y),
                    "mean_a": float(x.mean()),
                    "mean_b": float(y.mean()),
                    "mean_difference_b_minus_a": float(y.mean() - x.mean()),
                    "welch_t": float(test.statistic),
                    "p_value": float(test.pvalue),
                    "cohens_d_independent": cohens_d_independent(x, y),
                }
            )

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT_DIR, "model_pair_tests.csv"), index=False)
    return out


def paired_prompt_group_tests(df: pd.DataFrame):
    if "pair_group" not in df.columns:
        print("No pair_group column found. Skipping paired prompt-group tests.")
        return pd.DataFrame()

    required_cols = {"model", "pair_group", "prompt_type", "toxicity"}
    if not required_cols.issubset(set(df.columns)):
        print("Required paired-test columns missing. Skipping paired prompt-group tests.")
        return pd.DataFrame()

    rows = []

    grouped = (
        df.groupby(["model", "pair_group", "prompt_type"], dropna=False)["toxicity"]
        .mean()
        .reset_index()
    )

    pivot = grouped.pivot_table(
        index=["model", "pair_group"],
        columns="prompt_type",
        values="toxicity",
        aggfunc="mean",
    ).reset_index()

    comparisons = [
        ("neutral", "single_identity"),
        ("neutral", "intersectional"),
        ("single_identity", "intersectional"),
    ]

    for model in sorted(pivot["model"].dropna().unique()):
        model_df = pivot[pivot["model"] == model]

        for a, b in comparisons:
            if a not in model_df.columns or b not in model_df.columns:
                continue

            sub = model_df[[a, b]].dropna()
            if len(sub) < 2:
                continue

            diff = sub[b] - sub[a]
            test = stats.ttest_rel(sub[b], sub[a], nan_policy="omit")

            n, mean_diff, sd_diff, ci_low, ci_high = ci95_t(diff)

            rows.append(
                {
                    "model": model,
                    "comparison": f"{a} vs {b}",
                    "group_a": a,
                    "group_b": b,
                    "paired_n": int(n),
                    "mean_a": float(sub[a].mean()),
                    "mean_b": float(sub[b].mean()),
                    "mean_difference_b_minus_a": float(mean_diff),
                    "diff_std": float(sd_diff) if not pd.isna(sd_diff) else np.nan,
                    "diff_ci95_low": float(ci_low) if not pd.isna(ci_low) else np.nan,
                    "diff_ci95_high": float(ci_high) if not pd.isna(ci_high) else np.nan,
                    "paired_t": float(test.statistic),
                    "p_value": float(test.pvalue),
                    "cohens_d_paired": cohens_d_paired(diff),
                }
            )

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT_DIR, "paired_prompt_group_tests_by_model.csv"), index=False)
    return out


def overall_paired_prompt_group_tests(df: pd.DataFrame):
    if "pair_group" not in df.columns:
        return pd.DataFrame()

    grouped = (
        df.groupby(["model", "pair_group", "prompt_type"], dropna=False)["toxicity"]
        .mean()
        .reset_index()
    )

    pivot = grouped.pivot_table(
        index=["model", "pair_group"],
        columns="prompt_type",
        values="toxicity",
        aggfunc="mean",
    ).reset_index()

    rows = []
    comparisons = [
        ("neutral", "single_identity"),
        ("neutral", "intersectional"),
        ("single_identity", "intersectional"),
    ]

    for a, b in comparisons:
        if a not in pivot.columns or b not in pivot.columns:
            continue

        sub = pivot[[a, b]].dropna()
        if len(sub) < 2:
            continue

        diff = sub[b] - sub[a]
        test = stats.ttest_rel(sub[b], sub[a], nan_policy="omit")
        n, mean_diff, sd_diff, ci_low, ci_high = ci95_t(diff)

        rows.append(
            {
                "comparison": f"{a} vs {b}",
                "group_a": a,
                "group_b": b,
                "paired_n": int(n),
                "mean_a": float(sub[a].mean()),
                "mean_b": float(sub[b].mean()),
                "mean_difference_b_minus_a": float(mean_diff),
                "diff_std": float(sd_diff) if not pd.isna(sd_diff) else np.nan,
                "diff_ci95_low": float(ci_low) if not pd.isna(ci_low) else np.nan,
                "diff_ci95_high": float(ci_high) if not pd.isna(ci_high) else np.nan,
                "paired_t": float(test.statistic),
                "p_value": float(test.pvalue),
                "cohens_d_paired": cohens_d_paired(diff),
            }
        )

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT_DIR, "paired_prompt_group_tests_overall.csv"), index=False)
    return out


def main():
    df = load_all_rows()

    df.to_csv(os.path.join(OUT_DIR, "tame_all_rows_20000.csv"), index=False)

    print("=== TAME ROW-LEVEL INFERENTIAL REPORT ===")
    print("Total rows:", len(df))
    print("\nRows by model:")
    print(df.groupby("model").size())
    print("\nRows by prompt_type:")
    print(df.groupby("prompt_type").size())

    model_summary = make_summary(df, ["model"], "model_summary_with_ci95.csv")
    prompt_summary = make_summary(df, ["prompt_type"], "prompt_type_summary_with_ci95.csv")
    series_summary = make_summary(df, ["series"], "series_summary_with_ci95.csv")
    batch_summary = make_summary(df, ["series", "batch"], "batch_summary_with_ci95.csv")
    model_prompt_summary = make_summary(df, ["model", "prompt_type"], "model_prompt_type_summary_with_ci95.csv")

    independent_tests = independent_prompt_type_tests(df)
    model_tests = model_pair_tests(df)
    paired_by_model = paired_prompt_group_tests(df)
    paired_overall = overall_paired_prompt_group_tests(df)

    print("\nSaved row-level combined file:")
    print(f"{OUT_DIR}/tame_all_rows_20000.csv")

    print("\nSaved summaries:")
    print(f"{OUT_DIR}/model_summary_with_ci95.csv")
    print(f"{OUT_DIR}/prompt_type_summary_with_ci95.csv")
    print(f"{OUT_DIR}/series_summary_with_ci95.csv")
    print(f"{OUT_DIR}/batch_summary_with_ci95.csv")
    print(f"{OUT_DIR}/model_prompt_type_summary_with_ci95.csv")

    print("\nSaved tests:")
    print(f"{OUT_DIR}/independent_prompt_type_tests.csv")
    print(f"{OUT_DIR}/model_pair_tests.csv")
    print(f"{OUT_DIR}/paired_prompt_group_tests_by_model.csv")
    print(f"{OUT_DIR}/paired_prompt_group_tests_overall.csv")

    print("\nPrompt-type toxicity summary:")
    tox_prompt = prompt_summary[prompt_summary["metric"] == "toxicity"].copy()
    print(tox_prompt.to_string(index=False))

    if len(paired_overall):
        print("\nOverall paired prompt-group toxicity tests:")
        print(paired_overall.to_string(index=False))

    print("\nDONE: ROW-LEVEL STATISTICAL REPORT CREATED")


if __name__ == "__main__":
    main()