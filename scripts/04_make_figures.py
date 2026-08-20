from __future__ import annotations

import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import yaml


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(root: Path) -> Dict[str, Any]:
    with (root / "config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_batches(cfg: Dict[str, Any]) -> List[str]:
    batches = cfg.get("batches")
    if isinstance(batches, list) and len(batches) > 0:
        out = []
        for b in batches:
            if isinstance(b, dict) and b.get("batch_id") is not None:
                out.append(str(b["batch_id"]))
        if out:
            return out
    return ["batch_1"]


def ensure_dirs(root: Path, cfg: Dict[str, Any]) -> None:
    paths = cfg["paths"]
    (root / paths["batches_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["figures_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["logs_dir"]).mkdir(parents=True, exist_ok=True)


def setup_logging(root: Path, cfg: Dict[str, Any], log_name: str) -> logging.Logger:
    logs_dir = root / cfg["paths"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{log_name}.log"

    logger = logging.getLogger(f"tame.figures.{log_name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [04_make_figures] [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def coerce_bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    s2 = s.fillna("").astype(str).str.strip().str.lower()
    return s2.isin(["true", "1", "yes", "y", "t"])


def mean_ci(x: np.ndarray, ci_level: float = 0.95) -> Tuple[float, float, float]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan"), float("nan"), float("nan")
    m = float(np.mean(x))
    if x.size < 2:
        return m, float("nan"), float("nan")
    se = float(np.std(x, ddof=1) / np.sqrt(x.size))
    from scipy.stats import t

    tcrit = float(t.ppf((1 + ci_level) / 2, df=x.size - 1))
    return m, m - tcrit * se, m + tcrit * se


def apply_style(cfg: Dict[str, Any]) -> None:
    style = cfg.get("figures", {}).get("style", {})
    grid = bool(style.get("grid", True))
    font_family = str(style.get("font_family", "DejaVu Sans"))
    mpl.rcParams["font.family"] = font_family
    mpl.rcParams["axes.grid"] = grid
    mpl.rcParams["grid.alpha"] = 0.25
    mpl.rcParams["figure.figsize"] = (7.0, 4.2)


def save_fig(fig: plt.Figure, out_path: Path, dpi: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def make_figures_for_df(df: pd.DataFrame, *, out_dir: Path, primary: str, dpi: int, ci_level: float) -> None:
    apply_style({"figures": {"style": {"grid": True, "font_family": "DejaVu Sans"}}})

    df[primary] = pd.to_numeric(df[primary], errors="coerce")
    if "refusal_detected" in df.columns:
        df["refusal_detected"] = coerce_bool_series(df["refusal_detected"])
    else:
        df["refusal_detected"] = False

    models = sorted(df["model"].dropna().unique().tolist())
    prompt_types = ["neutral", "single_identity", "intersectional"]

    # 1) Average primary metric by model
    means, lows, highs = [], [], []
    for m in models:
        x = df.loc[df["model"] == m, primary].to_numpy(float)
        mean, lo, hi = mean_ci(x, ci_level)
        means.append(mean)
        lows.append(mean - lo if np.isfinite(lo) else 0.0)
        highs.append(hi - mean if np.isfinite(hi) else 0.0)
    fig, ax = plt.subplots()
    ax.bar(models, means, yerr=[lows, highs], capsize=4, color="#4C78A8")
    ax.set_title(f"Average {primary} by model")
    ax.set_ylabel(primary)
    ax.set_xlabel("Model")
    save_fig(fig, out_dir / "avg_primary_by_model.png", dpi)

    # 2) Average primary metric by prompt type
    means, lows, highs = [], [], []
    for pt in prompt_types:
        x = df.loc[df["prompt_type"] == pt, primary].to_numpy(float)
        mean, lo, hi = mean_ci(x, ci_level)
        means.append(mean)
        lows.append(mean - lo if np.isfinite(lo) else 0.0)
        highs.append(hi - mean if np.isfinite(hi) else 0.0)
    fig, ax = plt.subplots()
    ax.bar(prompt_types, means, yerr=[lows, highs], capsize=4, color="#F58518")
    ax.set_title(f"Average {primary} by prompt type")
    ax.set_ylabel(primary)
    ax.set_xlabel("Prompt type")
    save_fig(fig, out_dir / "avg_primary_by_prompt_type.png", dpi)

    # 2b) Identity-attack by prompt type (requested)
    if "identity_attack" in df.columns:
        df["identity_attack"] = pd.to_numeric(df["identity_attack"], errors="coerce")
        means, lows, highs = [], [], []
        for pt in prompt_types:
            x = df.loc[df["prompt_type"] == pt, "identity_attack"].to_numpy(float)
            mean, lo, hi = mean_ci(x, ci_level)
            means.append(mean)
            lows.append(mean - lo if np.isfinite(lo) else 0.0)
            highs.append(hi - mean if np.isfinite(hi) else 0.0)
        fig, ax = plt.subplots()
        ax.bar(prompt_types, means, yerr=[lows, highs], capsize=4, color="#72B7B2")
        ax.set_title("Average identity_attack by prompt type")
        ax.set_ylabel("identity_attack")
        ax.set_xlabel("Prompt type")
        save_fig(fig, out_dir / "avg_identity_attack_by_prompt_type.png", dpi)

    # 3) TAME amplification factor by model (single vs intersectional)
    amp_col = f"{primary}_amplification_factor"
    if amp_col in df.columns:
        df[amp_col] = pd.to_numeric(df[amp_col], errors="coerce")
        fig, ax = plt.subplots()
        x = np.arange(len(models))
        width = 0.38
        single_means, inter_means = [], []
        single_err, inter_err = [], []
        for m in models:
            s = df[(df["model"] == m) & (df["prompt_type"] == "single_identity")][amp_col].to_numpy(float)
            it = df[(df["model"] == m) & (df["prompt_type"] == "intersectional")][amp_col].to_numpy(float)
            sm, slo, shi = mean_ci(s, ci_level)
            im, ilo, ihi = mean_ci(it, ci_level)
            single_means.append(sm)
            inter_means.append(im)
            single_err.append([sm - slo if np.isfinite(slo) else 0.0, shi - sm if np.isfinite(shi) else 0.0])
            inter_err.append([im - ilo if np.isfinite(ilo) else 0.0, ihi - im if np.isfinite(ihi) else 0.0])

        ax.bar(x - width / 2, single_means, width, label="Single identity", color="#54A24B")
        ax.bar(x + width / 2, inter_means, width, label="Intersectional", color="#B279A2")
        ax.errorbar(x - width / 2, single_means, yerr=np.array(single_err).T, fmt="none", ecolor="black", capsize=3)
        ax.errorbar(x + width / 2, inter_means, yerr=np.array(inter_err).T, fmt="none", ecolor="black", capsize=3)
        ax.set_xticks(x)
        ax.set_xticklabels(models)
        ax.set_title(f"TAME amplification factor ({primary}) by model")
        ax.set_ylabel(amp_col)
        ax.set_xlabel("Model")
        ax.legend(frameon=False)
        save_fig(fig, out_dir / "tame_amplification_by_model.png", dpi)

    # 4) Intersectional gap by model
    if "intersectional_gap" in df.columns:
        df["intersectional_gap"] = pd.to_numeric(df["intersectional_gap"], errors="coerce")
        means, lows, highs = [], [], []
        for m in models:
            x = df[(df["model"] == m) & (df["prompt_type"] == "intersectional")]["intersectional_gap"].to_numpy(float)
            mean, lo, hi = mean_ci(x, ci_level)
            means.append(mean)
            lows.append(mean - lo if np.isfinite(lo) else 0.0)
            highs.append(hi - mean if np.isfinite(hi) else 0.0)
        fig, ax = plt.subplots()
        ax.bar(models, means, yerr=[lows, highs], capsize=4, color="#E45756")
        ax.axhline(0.0, color="black", linewidth=1)
        ax.set_title(f"Intersectional gap ({primary}) by model")
        ax.set_ylabel("intersectional_gap")
        ax.set_xlabel("Model")
        save_fig(fig, out_dir / "intersectional_gap_by_model.png", dpi)

    # 5) Refusal rate by model
    fig, ax = plt.subplots()
    refusal_rates = [float(df.loc[df["model"] == m, "refusal_detected"].mean()) for m in models]
    ax.bar(models, refusal_rates, color="#9D755D")
    ax.set_ylim(0, max(0.05, min(1.0, max(refusal_rates) * 1.2 if refusal_rates else 0.1)))
    ax.set_title("Refusal rate by model (heuristic)")
    ax.set_ylabel("refusal_rate")
    ax.set_xlabel("Model")
    save_fig(fig, out_dir / "refusal_rate_by_model.png", dpi)

    # 6) Heatmap: mean primary by model × prompt_type
    pivot = df.pivot_table(index="model", columns="prompt_type", values=primary, aggfunc="mean")
    pivot = pivot.reindex(index=models, columns=prompt_types)
    fig, ax = plt.subplots(figsize=(7.0, 3.8))
    im = ax.imshow(pivot.to_numpy(float), aspect="auto", cmap="viridis")
    ax.set_xticks(np.arange(len(prompt_types)))
    ax.set_xticklabels(prompt_types)
    ax.set_yticks(np.arange(len(models)))
    ax.set_yticklabels(models)
    ax.set_title(f"Heatmap: mean {primary} (model × prompt_type)")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label(primary)
    save_fig(fig, out_dir / "heatmap_model_prompttype_primary.png", dpi)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate TAME figures (batch-aware).")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Generate figures for a single batch_id (skips combined by default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate figures for the dry_run scope (produced by `01_generate.py --dry-run`).",
    )
    parser.add_argument(
        "--write-combined",
        action="store_true",
        help="Also write combined figures even when --batch-id is specified (combined must exist).",
    )
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root)
    ensure_dirs(root, cfg)
    logger = setup_logging(root, cfg, "combined")

    paths = cfg["paths"]
    batches_dir = root / paths["batches_dir"]
    figures_dir = root / paths["figures_dir"]

    primary = str(cfg["analysis"]["primary_metric"])
    dpi = int(cfg.get("figures", {}).get("dpi", 300))
    ci_level = float(cfg["analysis"].get("ci_level", 0.95))

    # Batch selection
    if args.dry_run:
        batches = ["dry_run"]
    else:
        batches = get_batches(cfg)
        if args.batch_id:
            batches = [b for b in batches if str(b) == str(args.batch_id)]
            if not batches:
                raise RuntimeError(f"No batch found in config.yaml with batch_id='{args.batch_id}'")

    # Per-batch
    for bid in batches:
        analysis_path = batches_dir / bid / "tame_analysis.csv"
        if not analysis_path.exists():
            raise FileNotFoundError(f"Missing analysis file for batch '{bid}': {analysis_path}. Run 03_analyze.py first.")
        df = pd.read_csv(analysis_path)
        out_dir = figures_dir / bid
        make_figures_for_df(df, out_dir=out_dir, primary=primary, dpi=dpi, ci_level=ci_level)
        logger.info("Saved figures for %s to %s", bid, out_dir.as_posix())

    # Combined
    if (not args.dry_run) and (args.write_combined or (args.batch_id is None)):
        combined_path = batches_dir / "combined" / "tame_analysis.csv"
        if not combined_path.exists():
            raise FileNotFoundError(f"Missing combined analysis file: {combined_path}. Run 03_analyze.py first.")
        dfc = pd.read_csv(combined_path)
        out_dir = figures_dir / "combined"
        make_figures_for_df(dfc, out_dir=out_dir, primary=primary, dpi=dpi, ci_level=ci_level)
        logger.info("Saved figures for combined to %s", out_dir.as_posix())
    logger.info("Saved figures for combined to %s", out_dir.as_posix())


if __name__ == "__main__":
    main()
