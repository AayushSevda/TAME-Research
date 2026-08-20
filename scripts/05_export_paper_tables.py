from __future__ import annotations

import logging
import argparse
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
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
    (root / cfg["paths"]["tables_dir"]).mkdir(parents=True, exist_ok=True)
    (root / cfg["paths"]["logs_dir"]).mkdir(parents=True, exist_ok=True)
    (root / cfg["paths"]["batches_dir"]).mkdir(parents=True, exist_ok=True)


def setup_logging(root: Path, cfg: Dict[str, Any], log_name: str) -> logging.Logger:
    logs_dir = root / cfg["paths"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{log_name}.log"

    logger = logging.getLogger(f"tame.export_tables.{log_name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [05_export_tables] [%(levelname)s] %(message)s")
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


def df_to_markdown(df: pd.DataFrame) -> str:
    df2 = df.copy()
    for c in df2.columns:
        df2[c] = df2[c].apply(lambda v: "" if pd.isna(v) else str(v))

    headers = list(df2.columns)
    rows: List[List[str]] = [headers] + df2.values.tolist()
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(headers))]

    def fmt_row(row: List[str]) -> str:
        cells = [str(row[i]).ljust(widths[i]) for i in range(len(headers))]
        return "| " + " | ".join(cells) + " |"

    header_line = fmt_row(headers)
    sep_line = "| " + " | ".join(["-" * w for w in widths]) + " |"
    body_lines = [fmt_row(list(r)) for r in df2.values.tolist()]
    return "\n".join([header_line, sep_line] + body_lines) + "\n"


def write_both(df: pd.DataFrame, base_path: Path) -> None:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(base_path.with_suffix(".csv"), index=False)
    base_path.with_suffix(".md").write_text(df_to_markdown(df), encoding="utf-8")


def export_scope(*, scope: str, cfg: Dict[str, Any], root: Path, logger: logging.Logger) -> None:
    paths = cfg["paths"]
    batches_dir = root / paths["batches_dir"]
    tables_root = root / paths["tables_dir"]

    analysis_path = batches_dir / scope / "tame_analysis.csv"
    if not analysis_path.exists():
        raise FileNotFoundError(f"Missing analysis file for scope '{scope}': {analysis_path}. Run 03_analyze.py first.")

    df = pd.read_csv(analysis_path)
    primary = str(cfg["analysis"]["primary_metric"])
    if primary in df.columns:
        df[primary] = pd.to_numeric(df[primary], errors="coerce")
    if "refusal_detected" in df.columns:
        df["refusal_detected"] = coerce_bool_series(df["refusal_detected"])
    else:
        df["refusal_detected"] = False

    out_dir = tables_root / scope

    # Experimental setup (mostly shared across scopes)
    gen = cfg["generation"]
    detox = cfg.get("scoring", {}).get("detoxify", {})

    # Infer runs_per_prompt from the analysis file itself to stay correct under per-batch overrides.
    # (For combined scope, this may be a mix; we report the observed set.)
    run_set = sorted(
        set(
            df.groupby(["batch_id", "model", "prompt_id"])["run"]
            .nunique()
            .dropna()
            .astype(int)
            .tolist()
        )
    )
    runs_label = str(run_set[0]) if len(run_set) == 1 else "mixed:" + ",".join(str(x) for x in run_set)

    batch_cfg = next((b for b in (cfg.get("batches") or []) if str(b.get("batch_id")) == str(scope)), None)
    prompts_csv = str((batch_cfg or {}).get("prompts_csv") or cfg["paths"]["prompts_csv"])

    setup = pd.DataFrame(
        [
            ["Scope", scope],
            ["Experiment name", cfg["experiment"]["name"]],
            ["Models", ", ".join(gen["models"])],
            ["Prompts (unique)", str(int(df["prompt_id"].nunique()))],
            ["Runs per prompt (observed)", runs_label],
            ["Prompts CSV (configured)", prompts_csv],
            ["System prompt", str(gen["system_prompt"])],
            ["Temperature", str(gen["options"]["temperature"])],
            ["Top-p", str(gen["options"]["top_p"])],
            ["Num predict", str(gen["options"]["num_predict"])],
            ["Scorer", "Detoxify"],
            ["Detoxify model", str(detox.get("model_name", "original"))],
        ],
        columns=["Item", "Value"],
    )
    write_both(setup, out_dir / "experimental_setup")

    # Model comparison
    model_overall = (
        df.groupby("model", as_index=False)
        .agg(
            n=("prompt_id", "count"),
            mean_primary=(primary, "mean"),
            std_primary=(primary, "std"),
            refusal_rate=("refusal_detected", "mean"),
        )
        .sort_values("model")
    )
    write_both(model_overall, out_dir / "model_comparison")

    # Prompt type comparison
    pt_overall = (
        df.groupby("prompt_type", as_index=False)
        .agg(
            n=("prompt_id", "count"),
            mean_primary=(primary, "mean"),
            std_primary=(primary, "std"),
            refusal_rate=("refusal_detected", "mean"),
        )
        .sort_values("prompt_type")
    )
    write_both(pt_overall, out_dir / "prompt_type_comparison")

    # TAME amplification (primary metric)
    amp_col = f"{primary}_amplification_factor"
    if amp_col in df.columns:
        tame = (
            df[df["prompt_type"].isin(["single_identity", "intersectional"])]
            .groupby(["model", "prompt_type"], as_index=False)
            .agg(
                n=(amp_col, "count"),
                mean_amplification=(amp_col, "mean"),
                std_amplification=(amp_col, "std"),
            )
            .sort_values(["model", "prompt_type"])
        )
        write_both(tame, out_dir / "tame_amplification")

    # Intersectional gap
    if "intersectional_gap" in df.columns:
        ig = (
            df[df["prompt_type"] == "intersectional"]
            .groupby("model", as_index=False)
            .agg(
                n=("intersectional_gap", "count"),
                mean_intersectional_gap=("intersectional_gap", "mean"),
                std_intersectional_gap=("intersectional_gap", "std"),
            )
            .sort_values("model")
        )
        write_both(ig, out_dir / "intersectional_gap")

    # Statistical significance (already created by 03_analyze.py into outputs/tables/<scope>/)
    tests_path = out_dir / "statistical_significance_tests.csv"
    if tests_path.exists():
        tests = pd.read_csv(tests_path)
        # Compact formatting
        for c in ["p_value", "statistic", "mean_diff", "ci_low", "ci_high"]:
            if c in tests.columns:
                tests[c] = pd.to_numeric(tests[c], errors="coerce").map(lambda v: "" if pd.isna(v) else f"{v:.6g}")
        write_both(tests, out_dir / "statistical_significance")
    else:
        logger.warning("No statistical_significance_tests.csv found for scope '%s' (expected at %s).", scope, tests_path.as_posix())

    logger.info("Exported paper-ready tables for %s to %s", scope, out_dir.as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description="Export paper-ready tables (batch-aware).")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Export tables for a single batch_id (skips combined by default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Export tables for the dry_run scope (produced by `01_generate.py --dry-run`).",
    )
    parser.add_argument(
        "--write-combined",
        action="store_true",
        help="Also export combined tables even when --batch-id is specified (combined must exist).",
    )
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root)
    ensure_dirs(root, cfg)
    logger = setup_logging(root, cfg, "combined")

    # Export each batch + combined
    if args.dry_run:
        batches = ["dry_run"]
    else:
        batches = get_batches(cfg)
        if args.batch_id:
            batches = [b for b in batches if str(b) == str(args.batch_id)]
            if not batches:
                raise RuntimeError(f"No batch found in config.yaml with batch_id='{args.batch_id}'")

    for bid in batches:
        export_scope(scope=bid, cfg=cfg, root=root, logger=logger)

    if (not args.dry_run) and (args.write_combined or (args.batch_id is None)):
        export_scope(scope="combined", cfg=cfg, root=root, logger=logger)


if __name__ == "__main__":
    main()
