from __future__ import annotations

import logging
import argparse
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from statistics import NormalDist
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

LOCAL_PYDEPS = Path(__file__).resolve().parents[1] / "_pydeps"
if LOCAL_PYDEPS.exists():
    sys.path.insert(0, str(LOCAL_PYDEPS))

import numpy as np
import pandas as pd
import yaml

try:
    import scipy.stats as stats
except Exception:
    class _StatsResult:
        def __init__(self, statistic: float, pvalue: float) -> None:
            self.statistic = statistic
            self.pvalue = pvalue

    class _TFallback:
        @staticmethod
        def ppf(q: float, df: int) -> float:
            return NormalDist().inv_cdf(q)

    class _StatsFallback:
        t = _TFallback()

        @staticmethod
        def shapiro(_: np.ndarray) -> _StatsResult:
            return _StatsResult(float("nan"), 1.0)

        @staticmethod
        def ttest_rel(y: np.ndarray, x: np.ndarray, nan_policy: str = "omit") -> _StatsResult:
            del nan_policy
            diff = y - x
            diff = diff[np.isfinite(diff)]
            if diff.size < 2:
                return _StatsResult(float("nan"), float("nan"))
            sem = float(np.nanstd(diff, ddof=1) / math.sqrt(diff.size))
            statistic = float(np.nanmean(diff) / sem) if sem > 0 else 0.0
            pvalue = 2.0 * (1.0 - NormalDist().cdf(abs(statistic)))
            return _StatsResult(statistic, pvalue)

        @staticmethod
        def wilcoxon(_: np.ndarray) -> _StatsResult:
            raise RuntimeError("scipy is unavailable; wilcoxon fallback is not implemented")

    stats = _StatsFallback()


ALLOWED_PROMPT_TYPES = {"neutral", "single_identity", "intersectional"}
SPECIAL_PAIR_GROUP_RE = re.compile(r"^s2b\d+_special_balanc(?:e|er)_", re.IGNORECASE)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(root: Path, series: str = "series_1") -> Dict[str, Any]:
    cfg_path = root / ("config_series_2.yaml" if series == "series_2" else "config.yaml")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_batches(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    batches = cfg.get("batches")
    if isinstance(batches, list) and len(batches) > 0:
        out: List[Dict[str, Any]] = []
        for b in batches:
            if isinstance(b, dict) and b.get("batch_id") is not None:
                out.append(b)
        if out:
            return out
    return [{"batch_id": "batch_1", "random_seed_base": int(cfg["experiment"]["random_seed_base"])}]


def ensure_dirs(root: Path, cfg: Dict[str, Any]) -> None:
    paths = cfg["paths"]
    (root / paths["batches_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["outputs_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["tables_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["figures_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["logs_dir"]).mkdir(parents=True, exist_ok=True)


def setup_logging(root: Path, cfg: Dict[str, Any], log_name: str) -> logging.Logger:
    logs_dir = root / cfg["paths"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{log_name}.log"

    logger = logging.getLogger(f"tame.analyze.{log_name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [03_analyze] [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def coerce_bool_series(s: pd.Series) -> pd.Series:
    """
    Robust boolean parsing for CSV-loaded columns.
    Avoids pandas astype(bool) on strings (which would make all non-empty values True).
    """
    if s.dtype == bool:
        return s
    s2 = s.fillna("").astype(str).str.strip().str.lower()
    return s2.isin(["true", "1", "yes", "y", "t"])


def exempt_group_rule_series(df: pd.DataFrame) -> pd.Series:
    """
    Mark special balancing rows that are intentionally exempt from the strict
    4-row pair_group structure used by TAME paired comparisons.
    """
    explicit = (
        coerce_bool_series(df["group_rule_exempt"])
        if "group_rule_exempt" in df.columns
        else pd.Series(False, index=df.index)
    )
    pair_group = df["pair_group"].fillna("").astype(str)
    implicit = pair_group.str.match(SPECIAL_PAIR_GROUP_RE)
    return explicit | implicit


def eligible_pair_groups(prompts: pd.DataFrame) -> Set[str]:
    """
    Return pair_groups that fully satisfy the standard TAME structure and are
    not marked as special/exempt.
    """
    prompts = prompts.copy()
    prompts["_group_rule_exempt"] = exempt_group_rule_series(prompts)

    counts = prompts.groupby(["pair_group", "prompt_type"], as_index=False).size().rename(columns={"size": "count"})
    pivot = counts.pivot(index="pair_group", columns="prompt_type", values="count").fillna(0).astype(int)
    for t in ALLOWED_PROMPT_TYPES:
        if t not in pivot.columns:
            pivot[t] = 0
    full_groups = pivot[(pivot["neutral"] == 1) & (pivot["single_identity"] == 2) & (pivot["intersectional"] == 1)]

    exempt_groups = prompts.groupby("pair_group")["_group_rule_exempt"].all()
    return {
        str(pg)
        for pg in full_groups.index
        if bool(exempt_groups.get(pg, False)) is False
    }


def validate_prompts(prompts: pd.DataFrame) -> None:
    required_cols = [
        "prompt_id",
        "pair_group",
        "prompt_type",
        "identity_axis",
        "identity_label",
        "prompt",
        "source",
        "risk_level",
    ]
    missing = [c for c in required_cols if c not in prompts.columns]
    if missing:
        raise RuntimeError(f"data/prompts.csv missing required columns: {missing}")

    if prompts["prompt_id"].duplicated().any():
        raise RuntimeError("data/prompts.csv has duplicate prompt_id values (must be unique).")

    bad_types = sorted(set(prompts["prompt_type"].dropna().astype(str)) - ALLOWED_PROMPT_TYPES)
    if bad_types:
        raise RuntimeError(f"data/prompts.csv contains invalid prompt_type values: {bad_types}")

    prompts = prompts.copy()
    prompts["_group_rule_exempt"] = exempt_group_rule_series(prompts)
    counts = prompts.groupby(["pair_group", "prompt_type"], as_index=False).size().rename(columns={"size": "count"})
    pivot = counts.pivot(index="pair_group", columns="prompt_type", values="count").fillna(0).astype(int)
    for t in ALLOWED_PROMPT_TYPES:
        if t not in pivot.columns:
            pivot[t] = 0
    bad_groups = pivot[(pivot["neutral"] != 1) | (pivot["single_identity"] != 2) | (pivot["intersectional"] != 1)]
    exempt_groups = prompts.groupby("pair_group")["_group_rule_exempt"].all()
    mixed_groups = prompts.groupby("pair_group")["_group_rule_exempt"].nunique()
    mixed_groups = mixed_groups[mixed_groups > 1]
    if not mixed_groups.empty:
        example = mixed_groups.head(10).reset_index().to_string(index=False)
        raise RuntimeError(
            "data/prompts.csv has pair_groups with mixed exempt/non-exempt rows. "
            "Special balancing groups must be consistently exempt.\n"
            f"Examples:\n{example}"
        )
    bad_groups = bad_groups[[not bool(exempt_groups.get(pg, False)) for pg in bad_groups.index]]
    if not bad_groups.empty:
        example = bad_groups.head(10).reset_index().to_string(index=False)
        raise RuntimeError(
            "data/prompts.csv has invalid pair_group composition. Expected neutral=1, single_identity=2, intersectional=1.\n"
            f"Examples:\n{example}"
        )


def expected_keys(models: List[str], prompt_ids: List[str], runs_per_prompt: int, batch_id: str) -> Set[Tuple[str, str, int, str]]:
    exp: Set[Tuple[str, str, int, str]] = set()
    for m in models:
        for pid in prompt_ids:
            for r in range(1, runs_per_prompt + 1):
                exp.add((m, pid, r, batch_id))
    return exp


def validate_scored_batch(
    *,
    batch_id: str,
    scored_path: Path,
    models: List[str],
    prompt_ids: List[str],
    runs_per_prompt: int,
) -> None:
    if not scored_path.exists():
        raise FileNotFoundError(
            f"Missing scored generations for batch '{batch_id}': {scored_path}. "
            "Run 02_score.py and then validate with 00_validate_dataset.py."
        )
    df = pd.read_csv(scored_path)
    required = {"batch_id", "model", "prompt_id", "run"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"{scored_path.as_posix()} missing required columns: {missing}")

    # Check batch_id consistency
    bad = df.loc[df["batch_id"].astype(str) != str(batch_id)]
    if not bad.empty:
        ex = bad[["batch_id", "model", "prompt_id", "run"]].head(10).to_string(index=False)
        raise RuntimeError(f"{scored_path.as_posix()}: batch_id mismatch. Expected '{batch_id}'. Examples:\n{ex}")

    df["model"] = df["model"].astype(str)
    df["prompt_id"] = df["prompt_id"].astype(str)
    df["run"] = pd.to_numeric(df["run"], errors="coerce")
    if df["run"].isna().any():
        raise RuntimeError(f"{scored_path.as_posix()}: run column contains non-numeric values.")
    df["run"] = df["run"].astype(int)

    dup = df.groupby(["model", "prompt_id", "run"], as_index=False).size().query("size != 1")
    if not dup.empty:
        ex = dup.head(20).to_string(index=False)
        raise RuntimeError(
            f"{scored_path.as_posix()}: expected exactly 1 row per (model,prompt_id,run). Duplicates found.\n{ex}"
        )

    seen = set((m, pid, r, batch_id) for m, pid, r in zip(df["model"], df["prompt_id"], df["run"]))
    exp = expected_keys(models, prompt_ids, runs_per_prompt, batch_id)
    missing_keys = sorted(exp - seen)
    if missing_keys:
        lines = [f"{scored_path.as_posix()}: missing expected rows (showing up to 50):"]
        for m, pid, r, _ in missing_keys[:50]:
            lines.append(f"  missing model={m}, prompt_id={pid}, run={r}")
        if len(missing_keys) > 50:
            lines.append(f"  ... and {len(missing_keys) - 50} more")
        raise RuntimeError("\n".join(lines))


def mean_ci_t(x: np.ndarray, ci_level: float) -> Tuple[float, float, float]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan"), float("nan"), float("nan")
    m = float(np.mean(x))
    if x.size < 2:
        return m, float("nan"), float("nan")
    se = float(np.std(x, ddof=1) / np.sqrt(x.size))
    tcrit = float(stats.t.ppf((1.0 + ci_level) / 2.0, df=x.size - 1))
    return m, m - tcrit * se, m + tcrit * se


@dataclass
class TestResult:
    comparison: str
    scope: str  # batch_id or "combined"
    model: str
    metric: str
    test: str
    n: int
    statistic: float
    p_value: float
    mean_diff: float
    ci_low: float
    ci_high: float


def choose_paired_test(
    x: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float,
    ci_level: float,
    normality_test: str,
    min_samples_for_normality: int,
) -> Tuple[str, float, float, float, float, float, int]:
    mask = np.isfinite(x) & np.isfinite(y)
    x2 = x[mask]
    y2 = y[mask]
    n = int(x2.size)
    if n < 2:
        return "insufficient_data", float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), n

    diff = y2 - x2
    mean_diff, ci_low, ci_high = mean_ci_t(diff, ci_level)

    use_t = True
    if normality_test == "shapiro" and n >= min_samples_for_normality:
        try:
            p_norm = float(stats.shapiro(diff).pvalue)
            use_t = p_norm >= alpha
        except Exception:
            use_t = True

    if use_t:
        t = stats.ttest_rel(y2, x2, nan_policy="omit")
        return "paired_ttest", float(t.statistic), float(t.pvalue), mean_diff, ci_low, ci_high, n

    try:
        w = stats.wilcoxon(diff)
        return "wilcoxon", float(w.statistic), float(w.pvalue), mean_diff, ci_low, ci_high, n
    except Exception:
        t = stats.ttest_rel(y2, x2, nan_policy="omit")
        return "paired_ttest_fallback", float(t.statistic), float(t.pvalue), mean_diff, ci_low, ci_high, n


def safe_div(numer: pd.Series, denom: pd.Series, eps: float = 1e-6) -> pd.Series:
    return numer / (denom.astype(float) + eps)


def write_table(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def summarize(group: pd.DataFrame, col: str, ci_level: float) -> pd.Series:
    x = pd.to_numeric(group[col], errors="coerce").to_numpy(dtype=float)
    mean, lo, hi = mean_ci_t(x, ci_level)
    return pd.Series(
        {
            "mean": mean,
            "std": float(np.nanstd(x, ddof=1)) if np.isfinite(x).sum() >= 2 else float("nan"),
            "n": int(np.isfinite(x).sum()),
            "ci_low": lo,
            "ci_high": hi,
        }
    )


def analyze_scored(
    *,
    scope_name: str,
    scored: pd.DataFrame,
    prompts: pd.DataFrame,
    cfg: Dict[str, Any],
    analysis_path: Path,
    audit_path: Path,
    tables_dir: Path,
    logger: logging.Logger,
) -> None:
    prompts = prompts.copy()
    prompts["group_rule_exempt"] = exempt_group_rule_series(prompts)
    full_pair_groups = eligible_pair_groups(prompts)

    detox_cols = ["toxicity", "severe_toxicity", "obscene", "threat", "insult", "identity_attack"]
    for c in detox_cols:
        if c in scored.columns:
            scored[c] = pd.to_numeric(scored[c], errors="coerce")

    if "refusal_detected" in scored.columns:
        scored["refusal_detected"] = coerce_bool_series(scored["refusal_detected"])
    else:
        scored["refusal_detected"] = False

    # Join prompt metadata
    scored = scored.merge(
        prompts[
            [
                "prompt_id",
                "pair_group",
                "prompt_type",
                "identity_axis",
                "identity_label",
                "risk_level",
                "group_rule_exempt",
            ]
        ],
        on="prompt_id",
        how="left",
        validate="many_to_one",
    )

    required_meta = {"batch_id", "model", "pair_group", "prompt_type", "run"}
    missing_meta = sorted(required_meta - set(scored.columns))
    if missing_meta:
        raise RuntimeError(f"{scope_name}: scored data missing required columns after join: {missing_meta}")

    scored["group_rule_exempt"] = coerce_bool_series(scored["group_rule_exempt"])
    scored["pair_group_eligible"] = scored["pair_group"].astype(str).isin(full_pair_groups)

    primary = str(cfg["analysis"]["primary_metric"])
    secondaries = list(cfg["analysis"].get("secondary_metrics", []))
    metrics = [primary] + [m for m in secondaries if m in detox_cols and m != primary]

    # Neutral baselines per (batch_id, model, pair_group, run), only for full
    # non-exempt groups that support paired comparisons.
    baseline = (
        scored[(scored["prompt_type"] == "neutral") & (scored["pair_group_eligible"])]
        .groupby(["batch_id", "model", "pair_group", "run"], as_index=False)[detox_cols]
        .mean()
        .rename(columns={c: f"baseline_{c}" for c in detox_cols})
    )
    df = scored.merge(baseline, on=["batch_id", "model", "pair_group", "run"], how="left", validate="many_to_one")

    for m in metrics:
        df[f"{m}_amplification_factor"] = df[m] - df[f"baseline_{m}"]
        df[f"{m}_amplification_ratio"] = safe_div(df[m], df[f"baseline_{m}"])

    # Intersectional gap per (batch_id, model, pair_group, run)
    singles = df[(df["prompt_type"] == "single_identity") & (df["pair_group_eligible"])].copy()
    inter = df[(df["prompt_type"] == "intersectional") & (df["pair_group_eligible"])].copy()

    single_means = singles.groupby(["batch_id", "model", "pair_group", "run"], as_index=False)[metrics].mean()
    single_means = single_means.rename(columns={m: f"avg_single_{m}" for m in metrics})

    inter = inter.merge(single_means, on=["batch_id", "model", "pair_group", "run"], how="left", validate="many_to_one")
    for m in metrics:
        inter[f"intersectional_gap_{m}"] = inter[m] - inter[f"avg_single_{m}"]

    gap_cols = [f"intersectional_gap_{m}" for m in metrics]
    df = df.merge(
        inter[["batch_id", "model", "pair_group", "run", "prompt_id", *gap_cols]],
        on=["batch_id", "model", "pair_group", "run", "prompt_id"],
        how="left",
    )
    df["intersectional_gap"] = df.get(f"intersectional_gap_{primary}", np.nan)

    # Save row-level analysis (clean columns only)
    analysis_cols = [
        "batch_id",
        "timestamp_utc",
        "model",
        "prompt_id",
        "pair_group",
        "prompt_type",
        "identity_axis",
        "identity_label",
        "run",
        "seed",
        "risk_level",
        "group_rule_exempt",
        "pair_group_eligible",
        "prompt",
        "output",
        "refusal_detected",
        "refusal_reason",
        "output_word_count",
        *[c for c in detox_cols if c in df.columns],
        *[f"baseline_{c}" for c in detox_cols if f"baseline_{c}" in df.columns],
    ]
    for m in metrics:
        analysis_cols += [f"{m}_amplification_factor", f"{m}_amplification_ratio"]
    analysis_cols += [c for c in gap_cols if c in df.columns] + ["intersectional_gap"]
    analysis_cols = [c for c in analysis_cols if c in df.columns]

    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    df[analysis_cols].to_csv(analysis_path, index=False)
    logger.info("Saved %s analysis to %s", scope_name, analysis_path.as_posix())

    # Summary tables
    ci_level = float(cfg["analysis"]["ci_level"])
    alpha = float(cfg["analysis"]["alpha"])
    normality_test = str(cfg["analysis"].get("normality_test", "shapiro"))
    min_n_norm = int(cfg["analysis"].get("min_samples_for_normality", 8))

    tox_summary = (
        df.groupby(["batch_id", "model", "prompt_type"], as_index=False)
        .apply(lambda g: summarize(g, primary, ci_level))
        .reset_index(drop=True)
    )
    write_table(tox_summary, tables_dir / "summary_primary_metric_by_model_prompt_type.csv")

    tame_summary = (
        df[df["prompt_type"].isin(["single_identity", "intersectional"]) & df["pair_group_eligible"]]
        .groupby(["batch_id", "model", "prompt_type"], as_index=False)
        .apply(lambda g: summarize(g, f"{primary}_amplification_factor", ci_level))
        .reset_index(drop=True)
        .rename(columns={"mean": "mean_amplification_factor"})
    )
    write_table(tame_summary, tables_dir / "summary_tame_amplification_by_model.csv")

    ig_summary = (
        df[(df["prompt_type"] == "intersectional") & df["pair_group_eligible"]]
        .groupby(["batch_id", "model"], as_index=False)
        .apply(lambda g: summarize(g, "intersectional_gap", ci_level))
        .reset_index(drop=True)
        .rename(columns={"mean": "mean_intersectional_gap"})
    )
    write_table(ig_summary, tables_dir / "summary_intersectional_gap_by_model.csv")

    refusal = (
        df.groupby(["batch_id", "model"], as_index=False)
        .agg(n=("refusal_detected", "size"), refusal_rate=("refusal_detected", "mean"))
        .sort_values(["batch_id", "model"])
    )
    write_table(refusal, tables_dir / "refusal_rate_by_model.csv")

    # Statistical tests (paired where applicable), computed within each batch_id
    test_rows: List[TestResult] = []
    for (bid, model), dm in df.groupby(["batch_id", "model"]):
        dm = dm[dm["pair_group_eligible"]].copy()

        # Baseline per (pair_group, run)
        base_map = (
            dm[dm["prompt_type"] == "neutral"]
            .set_index(["pair_group", "run"])[primary]
            .to_dict()
        )

        singles_m = dm[dm["prompt_type"] == "single_identity"].copy()
        inter_m = dm[dm["prompt_type"] == "intersectional"].copy()

        # neutral vs single (each single)
        x = []
        y = []
        for _, r in singles_m.iterrows():
            k = (r["pair_group"], r["run"])
            if k in base_map and np.isfinite(base_map[k]) and np.isfinite(r[primary]):
                x.append(float(base_map[k]))
                y.append(float(r[primary]))
        if len(x) >= 2:
            test, stat, p, md, lo, hi, n = choose_paired_test(
                np.array(x),
                np.array(y),
                alpha=alpha,
                ci_level=ci_level,
                normality_test=normality_test,
                min_samples_for_normality=min_n_norm,
            )
            test_rows.append(TestResult("neutral_vs_single", str(bid), str(model), primary, test, n, stat, p, md, lo, hi))

        # neutral vs avg(single)
        single_avg = (
            singles_m.groupby(["pair_group", "run"], as_index=False)[primary]
            .mean()
            .rename(columns={primary: "single_avg"})
        )
        xn = []
        ys = []
        for _, r in single_avg.iterrows():
            k = (r["pair_group"], r["run"])
            if k in base_map and np.isfinite(base_map[k]) and np.isfinite(r["single_avg"]):
                xn.append(float(base_map[k]))
                ys.append(float(r["single_avg"]))
        if len(xn) >= 2:
            test, stat, p, md, lo, hi, n = choose_paired_test(
                np.array(xn),
                np.array(ys),
                alpha=alpha,
                ci_level=ci_level,
                normality_test=normality_test,
                min_samples_for_normality=min_n_norm,
            )
            test_rows.append(TestResult("neutral_vs_single_avg", str(bid), str(model), primary, test, n, stat, p, md, lo, hi))

        # neutral vs intersectional
        x = []
        y = []
        for _, r in inter_m.iterrows():
            k = (r["pair_group"], r["run"])
            if k in base_map and np.isfinite(base_map[k]) and np.isfinite(r[primary]):
                x.append(float(base_map[k]))
                y.append(float(r[primary]))
        if len(x) >= 2:
            test, stat, p, md, lo, hi, n = choose_paired_test(
                np.array(x),
                np.array(y),
                alpha=alpha,
                ci_level=ci_level,
                normality_test=normality_test,
                min_samples_for_normality=min_n_norm,
            )
            test_rows.append(TestResult("neutral_vs_intersectional", str(bid), str(model), primary, test, n, stat, p, md, lo, hi))

        # single_avg vs intersectional
        inter_vals = inter_m[["pair_group", "run", primary]].rename(columns={primary: "intersectional"})
        pair = single_avg.merge(inter_vals, on=["pair_group", "run"], how="inner")
        if len(pair) >= 2:
            test, stat, p, md, lo, hi, n = choose_paired_test(
                pair["single_avg"].to_numpy(float),
                pair["intersectional"].to_numpy(float),
                alpha=alpha,
                ci_level=ci_level,
                normality_test=normality_test,
                min_samples_for_normality=min_n_norm,
            )
            test_rows.append(TestResult("single_avg_vs_intersectional", str(bid), str(model), primary, test, n, stat, p, md, lo, hi))

    # model vs model comparisons on amplification factor (paired by batch_id + prompt_id + run)
    df_amp = df[df["prompt_type"].isin(["single_identity", "intersectional"]) & df["pair_group_eligible"]].copy()
    df_amp["amp"] = df_amp[f"{primary}_amplification_factor"]
    for pt in ["single_identity", "intersectional"]:
        sub = df_amp[df_amp["prompt_type"] == pt]
        pivot = sub.pivot_table(index=["batch_id", "prompt_id", "run"], columns="model", values="amp", aggfunc="mean")
        model_cols = [m for m in pivot.columns.tolist() if isinstance(m, str)]
        for m1, m2 in combinations(sorted(model_cols), 2):
            a = pivot[m1].to_numpy(float)
            b = pivot[m2].to_numpy(float)
            test, stat, p, md, lo, hi, n = choose_paired_test(
                a,
                b,
                alpha=alpha,
                ci_level=ci_level,
                normality_test=normality_test,
                min_samples_for_normality=min_n_norm,
            )
            test_rows.append(
                TestResult(
                    comparison=f"model_vs_model_amp_{pt}:{m1}_vs_{m2}",
                    scope=scope_name,
                    model="ALL",
                    metric=f"{primary}_amplification_factor",
                    test=test,
                    n=n,
                    statistic=stat,
                    p_value=p,
                    mean_diff=md,
                    ci_low=lo,
                    ci_high=hi,
                )
            )

    tests_df = pd.DataFrame([t.__dict__ for t in test_rows])
    write_table(tests_df, tables_dir / "statistical_significance_tests.csv")

    # Human audit sample (stratified by batch_id + model + prompt_type)
    rng = np.random.default_rng(int(cfg["experiment"]["random_seed_base"]))
    sample_parts = []
    for (bid, model, pt), g in df.groupby(["batch_id", "model", "prompt_type"]):
        n_take = 5 if pt == "neutral" else 8
        if len(g) == 0:
            continue
        take = g.sample(n=min(n_take, len(g)), random_state=int(rng.integers(0, 2**31 - 1)))
        sample_parts.append(take)
    audit = pd.concat(sample_parts, ignore_index=True) if sample_parts else df.head(0)
    audit_cols = [
        "batch_id",
        "model",
        "prompt_id",
        "pair_group",
        "prompt_type",
        "identity_axis",
        "identity_label",
        "run",
        "prompt",
        "output",
        "refusal_detected",
        "refusal_reason",
        primary,
        "identity_attack",
        "insult",
    ]
    audit_cols = [c for c in audit_cols if c in audit.columns]
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit[audit_cols].to_csv(audit_path, index=False)
    logger.info("Saved %s human audit sample to %s", scope_name, audit_path.as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TAME analysis (batch-aware).")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Analyze only a single batch_id (skips combined by default to avoid overwriting).",
    )
    parser.add_argument(
        "--series",
        choices=["series_1", "series_2"],
        default="series_1",
        help="Select config/output namespace. series_1 uses config.yaml; series_2 uses config_series_2.yaml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze the dry_run batch produced by `01_generate.py --dry-run` (uses first 2 pair_groups, runs_per_prompt=1; skips combined).",
    )
    parser.add_argument(
        "--write-combined",
        action="store_true",
        help="Also write combined outputs even when --batch-id is specified (combined will include only the selected batch).",
    )
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root, args.series)
    ensure_dirs(root, cfg)

    paths = cfg["paths"]
    default_prompts_path = root / paths["prompts_csv"]
    batches_dir = root / paths["batches_dir"]

    models = list(cfg["generation"]["models"])

    # Dry-run overrides (must match 01_generate.py --dry-run behavior)
    prompts_by_batch: Dict[str, pd.DataFrame] = {}
    prompts_path_by_batch: Dict[str, Path] = {}

    if args.dry_run:
        if not default_prompts_path.exists():
            raise FileNotFoundError(f"Missing prompts file: {default_prompts_path}")
        prompts = pd.read_csv(default_prompts_path)
        validate_prompts(prompts)
        if "pair_group" not in prompts.columns:
            raise RuntimeError("Dry run requires prompts.csv to include a 'pair_group' column.")
        keep_groups = sorted(prompts["pair_group"].astype(str).unique().tolist())[:2]
        prompts = prompts[prompts["pair_group"].astype(str).isin(keep_groups)].copy()
        batches = [{"batch_id": "dry_run", "random_seed_base": int(cfg["experiment"]["random_seed_base"])}]
        prompts_by_batch["dry_run"] = prompts
        prompts_path_by_batch["dry_run"] = default_prompts_path
    else:
        batches = get_batches(cfg)
        if args.batch_id:
            batches = [b for b in batches if str(b.get("batch_id")) == str(args.batch_id)]
            if not batches:
                raise RuntimeError(f"No batch found in config.yaml with batch_id='{args.batch_id}'")
        # Load/validate prompts per batch (supports per-batch prompts_csv override)
        for b in batches:
            bid = str(b["batch_id"])
            prompts_path = root / str(b.get("prompts_csv") or paths["prompts_csv"])
            if not prompts_path.exists():
                raise FileNotFoundError(f"Missing prompts file for batch '{bid}': {prompts_path}")
            p = pd.read_csv(prompts_path)
            validate_prompts(p)
            prompts_by_batch[bid] = p
            prompts_path_by_batch[bid] = prompts_path

    # Strict completeness check: all configured batches must have complete scored files.
    for b in batches:
        bid = str(b["batch_id"])
        runs_per_prompt = int(b.get("runs_per_prompt", cfg["generation"]["runs_per_prompt"])) if not args.dry_run else 1
        scored_path = batches_dir / bid / "generations_scored.csv"
        prompt_ids = prompts_by_batch[bid]["prompt_id"].astype(str).tolist()
        validate_scored_batch(
            batch_id=bid,
            scored_path=scored_path,
            models=models,
            prompt_ids=prompt_ids,
            runs_per_prompt=runs_per_prompt,
        )

    # Per-batch analysis
    for b in batches:
        bid = str(b["batch_id"])
        logger = setup_logging(root, cfg, bid)
        scored_path = batches_dir / bid / "generations_scored.csv"
        scored = pd.read_csv(scored_path)

        prompts = prompts_by_batch[bid]
        analysis_path = batches_dir / bid / "tame_analysis.csv"
        audit_path = batches_dir / bid / "human_audit_sample.csv"
        tables_dir = (root / paths["tables_dir"]) / bid
        analyze_scored(
            scope_name=bid,
            scored=scored,
            prompts=prompts,
            cfg=cfg,
            analysis_path=analysis_path,
            audit_path=audit_path,
            tables_dir=tables_dir,
            logger=logger,
        )

    # Combined analysis across all batches (skip for dry-run unless explicitly requested)
    if (not args.dry_run) and (args.write_combined or (args.batch_id is None)):
        # Safety guard: do NOT combine batches that use different prompt files.
        unique_prompt_files = sorted({p.as_posix() for p in prompts_path_by_batch.values()})
        if len(unique_prompt_files) > 1:
            raise RuntimeError(
                "Refusing to run combined analysis across batches with different prompt files.\n"
                f"Prompt files detected: {unique_prompt_files}\n"
                "Run with `--batch-id` to analyze a single batch, or ensure all combined batches use the same prompts_csv."
            )
        logger = setup_logging(root, cfg, "combined")
        scored_all = []
        for b in batches:
            bid = str(b["batch_id"])
            scored_path = batches_dir / bid / "generations_scored.csv"
            scored_all.append(pd.read_csv(scored_path))

        combined_scored = pd.concat(scored_all, ignore_index=True)
        combined_dir = batches_dir / "combined"
        combined_dir.mkdir(parents=True, exist_ok=True)
        # All prompt files are identical here by construction, so any batch's prompts are fine.
        any_bid = str(batches[0]["batch_id"])
        prompts = prompts_by_batch[any_bid]
        analyze_scored(
            scope_name="combined",
            scored=combined_scored,
            prompts=prompts,
            cfg=cfg,
            analysis_path=combined_dir / "tame_analysis.csv",
            audit_path=combined_dir / "human_audit_sample.csv",
            tables_dir=(root / paths["tables_dir"]) / "combined",
            logger=logger,
        )


if __name__ == "__main__":
    main()
