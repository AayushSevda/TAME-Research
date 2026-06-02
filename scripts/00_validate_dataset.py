"""
00_validate_dataset.py

Hard validation for the TAME experiment dataset to prevent silent failures and misleading results.

This script is designed to be run:
1) Before generation (validates prompts.csv only)
2) After generation + scoring (validates prompts + generations_raw + generations_scored)

Behavior:
- Always validates data/prompts.csv strictly (fails on any issue).
- Validates generations_raw.csv and generations_scored.csv only if the files exist.
  (So it won't fail before you've generated/scored.)

Run from project root:
  python scripts/00_validate_dataset.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

LOCAL_PYDEPS = Path(__file__).resolve().parents[1] / "_pydeps"
if LOCAL_PYDEPS.exists():
    sys.path.insert(0, str(LOCAL_PYDEPS))

import pandas as pd
import yaml


ALLOWED_PROMPT_TYPES = {"neutral", "single_identity", "intersectional"}


def truthy_series(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"1", "true", "yes"})
    )


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(root: Path, series: str = "series_1") -> Dict[str, Any]:
    cfg_path = root / ("config_series_2.yaml" if series == "series_2" else "config.yaml")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_batches(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns list of batch dicts with at least:
      - batch_id
      - random_seed_base
      - runs_per_prompt (optional; defaults to generation.runs_per_prompt)
    If cfg has no batches, returns a default single batch.
    """
    batches = cfg.get("batches")
    if isinstance(batches, list) and len(batches) > 0:
        out: List[Dict[str, Any]] = []
        for b in batches:
            if isinstance(b, dict) and b.get("batch_id"):
                out.append(b)
        if out:
            return out
    # Fallback: single batch
    return [{"batch_id": "batch_1", "random_seed_base": int(cfg["experiment"]["random_seed_base"])}]


def fail(msg: str) -> None:
    raise RuntimeError(msg)


def require_columns(df: pd.DataFrame, required: Iterable[str], *, file_label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        fail(f"{file_label}: missing required columns: {missing}")


def validate_prompts(prompts_path: Path) -> pd.DataFrame:
    if not prompts_path.exists():
        fail(f"prompts.csv not found: {prompts_path}")

    prompts = pd.read_csv(prompts_path)
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
    require_columns(prompts, required_cols, file_label="data/prompts.csv")

    # prompt_id must be unique
    if prompts["prompt_id"].duplicated().any():
        dups = prompts.loc[prompts["prompt_id"].duplicated(), "prompt_id"].astype(str).unique().tolist()
        fail(f"data/prompts.csv: prompt_id must be unique. Duplicates found: {dups[:20]}" + (" ..." if len(dups) > 20 else ""))

    # prompt_type allowed values only
    bad_types = sorted(set(prompts["prompt_type"].dropna().astype(str)) - ALLOWED_PROMPT_TYPES)
    if bad_types:
        fail(f"data/prompts.csv: invalid prompt_type values: {bad_types}. Allowed: {sorted(ALLOWED_PROMPT_TYPES)}")

    exempt_mask = truthy_series(prompts["group_rule_exempt"]) if "group_rule_exempt" in prompts.columns else pd.Series(False, index=prompts.index)
    regular_prompts = prompts.loc[~exempt_mask].copy()

    # Each non-exempt pair_group must have 1 neutral, 2 single_identity, 1 intersectional
    counts = (
        regular_prompts.groupby(["pair_group", "prompt_type"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )
    # Build a table with zeros for missing types
    pivot = counts.pivot(index="pair_group", columns="prompt_type", values="count").fillna(0).astype(int)
    for t in ALLOWED_PROMPT_TYPES:
        if t not in pivot.columns:
            pivot[t] = 0
    pivot = pivot[["neutral", "single_identity", "intersectional"]]

    bad_groups = pivot[
        (pivot["neutral"] != 1) | (pivot["single_identity"] != 2) | (pivot["intersectional"] != 1)
    ]
    if not bad_groups.empty:
        lines = ["data/prompts.csv: invalid pair_group composition (expected neutral=1, single_identity=2, intersectional=1)."]
        for pg, row in bad_groups.head(50).iterrows():
            lines.append(f"  pair_group={pg}: neutral={row['neutral']}, single_identity={row['single_identity']}, intersectional={row['intersectional']}")
        if len(bad_groups) > 50:
            lines.append(f"  ... and {len(bad_groups) - 50} more groups")
        fail("\n".join(lines))

    return prompts


def expected_keys(models: List[str], prompt_ids: List[str], runs_per_prompt: int) -> Set[Tuple[str, str, int]]:
    exp: Set[Tuple[str, str, int]] = set()
    for m in models:
        for pid in prompt_ids:
            for r in range(1, runs_per_prompt + 1):
                exp.add((m, pid, r))
    return exp


def validate_stage_file(
    *,
    file_path: Path,
    file_label: str,
    batch_id: str,
    models: List[str],
    prompt_ids: List[str],
    runs_per_prompt: int,
    require_complete: bool,
    allow_resume_partial: bool = False,
    resume_start_prompt_id: str = "",
) -> None:
    if not file_path.exists():
        # Designed behavior: prompts-only validation can run before generation/scoring.
        print(f"[validate] {file_label}: not found (skipping; run generation/scoring first)")
        return

    df = pd.read_csv(file_path)
    require_columns(df, ["batch_id", "model", "prompt_id", "run"], file_label=file_label)

    # batch_id must match the directory batch_id (prevents accidental mixing)
    bad_batch = df.loc[df["batch_id"].astype(str) != str(batch_id)]
    if not bad_batch.empty:
        ex = bad_batch[["batch_id", "model", "prompt_id", "run"]].head(10).to_string(index=False)
        fail(
            f"{file_label}: batch_id mismatch. Expected batch_id='{batch_id}' for all rows. Examples:\n{ex}"
        )

    df["model"] = df["model"].astype(str)
    df["prompt_id"] = df["prompt_id"].astype(str)
    df["run"] = pd.to_numeric(df["run"], errors="coerce")
    if df["run"].isna().any():
        bad = df[df["run"].isna()].head(10)
        fail(f"{file_label}: 'run' column contains non-numeric values. Examples:\n{bad[['model','prompt_id','run']].to_string(index=False)}")
    df["run"] = df["run"].astype(int)

    # Check duplicates
    dup = df.groupby(["model", "prompt_id", "run"], as_index=False).size().query("size != 1")
    if not dup.empty:
        lines = [f"{file_label}: duplicates or invalid multiplicity detected (expected exactly 1 row per model/prompt_id/run)."]
        for _, r in dup.head(50).iterrows():
            lines.append(f"  model={r['model']}, prompt_id={r['prompt_id']}, run={int(r['run'])}, rows={int(r['size'])}")
        if len(dup) > 50:
            lines.append(f"  ... and {len(dup) - 50} more")
        fail("\n".join(lines))

    if require_complete:
        seen = set(zip(df["model"], df["prompt_id"], df["run"]))
        exp = expected_keys(models, prompt_ids, runs_per_prompt)
        missing = sorted(exp - seen)
        extra = sorted(seen - exp)

        if missing and allow_resume_partial:
            if resume_start_prompt_id not in prompt_ids:
                fail(
                    f"{file_label}: allow_resume_partial is enabled, but "
                    f"resume_start_prompt_id='{resume_start_prompt_id}' is not in prompts."
                )
            resume_start_idx = prompt_ids.index(resume_start_prompt_id)
            prompt_index = {pid: idx for idx, pid in enumerate(prompt_ids)}
            missing_before_resume = [
                (m, pid, r)
                for m, pid, r in missing
                if prompt_index.get(pid, len(prompt_ids)) < resume_start_idx
            ]
            if not missing_before_resume and not extra:
                print(
                    f"[validate] {file_label}: schema/duplicates OK ({len(df)} rows). "
                    f"Resume extension detected; missing rows start at {resume_start_prompt_id}."
                )
                return

        if missing:
            lines = [f"{file_label}: missing expected rows (model/prompt_id/run). Showing up to 50:"]
            for m, pid, r in missing[:50]:
                lines.append(f"  missing model={m}, prompt_id={pid}, run={r}")
            if len(missing) > 50:
                lines.append(f"  ... and {len(missing) - 50} more missing rows")
            fail("\n".join(lines))

        # If there are extra rows not in prompts/config, fail (prevents silent contamination)
        if extra:
            lines = [f"{file_label}: extra unexpected rows found (not in expected model/prompt_id/run set). Showing up to 50:"]
            for m, pid, r in extra[:50]:
                lines.append(f"  extra model={m}, prompt_id={pid}, run={r}")
            if len(extra) > 50:
                lines.append(f"  ... and {len(extra) - 50} more extra rows")
            fail("\n".join(lines))

        print(f"[validate] {file_label}: OK ({len(df)} rows)")
    else:
        print(
            f"[validate] {file_label}: schema/duplicates OK ({len(df)} rows). "
            "Completeness check skipped (generation may be in-progress / resume mode)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate TAME dataset (prompts + per-batch stage files).")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Validate only a single configured batch_id (default: validate all batches in config.yaml).",
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
        help="Validate the dry_run scope (first 2 pair_groups, runs_per_prompt=1), matching `01_generate.py --dry-run`.",
    )
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root, args.series)

    default_prompts_path = root / cfg["paths"]["prompts_csv"]
    batches_dir = root / cfg["paths"]["batches_dir"]

    models = list(cfg["generation"]["models"])

    # Optional dry-run overrides
    if args.dry_run:
        prompts = validate_prompts(default_prompts_path)
        print(
            f"[validate] {default_prompts_path.as_posix()}: OK ({len(prompts)} rows, {prompts['pair_group'].nunique()} groups)"
        )
        keep_groups = sorted(prompts["pair_group"].astype(str).unique().tolist())[:2]
        prompts = prompts[prompts["pair_group"].astype(str).isin(keep_groups)].copy()
        batches = [
            {
                "batch_id": "dry_run",
                "random_seed_base": int(cfg["experiment"]["random_seed_base"]),
                "runs_per_prompt": 1,
            }
        ]
        prompt_ids = prompts["prompt_id"].astype(str).tolist()
    else:
        batches = get_batches(cfg)
        if args.batch_id:
            batches = [b for b in batches if str(b.get("batch_id")) == str(args.batch_id)]
            if not batches:
                fail(f"No batch found in config.yaml with batch_id='{args.batch_id}'")

    for b in batches:
        bid = str(b["batch_id"])
        runs_per_prompt = int(b.get("runs_per_prompt", cfg["generation"]["runs_per_prompt"]))

        if not args.dry_run:
            prompts_path = root / str(b.get("prompts_csv") or cfg["paths"]["prompts_csv"])
            prompts = validate_prompts(prompts_path)
            print(
                f"[validate] {prompts_path.as_posix()}: OK ({len(prompts)} rows, {prompts['pair_group'].nunique()} groups)"
            )
            prompt_ids = prompts["prompt_id"].astype(str).tolist()

        batch_dir = batches_dir / bid
        raw_path = batch_dir / "generations_raw.csv"
        scored_path = batch_dir / "generations_scored.csv"

        validate_stage_file(
            file_path=raw_path,
            file_label=f"{cfg['paths']['batches_dir']}/{bid}/generations_raw.csv",
            batch_id=bid,
            models=models,
            prompt_ids=prompt_ids,
            runs_per_prompt=runs_per_prompt,
            require_complete=scored_path.exists(),
            allow_resume_partial=bool(b.get("allow_resume_partial_stage_files", False)),
            resume_start_prompt_id=str(b.get("resume_start_prompt_id", "")),
        )
        validate_stage_file(
            file_path=scored_path,
            file_label=f"{cfg['paths']['batches_dir']}/{bid}/generations_scored.csv",
            batch_id=bid,
            models=models,
            prompt_ids=prompt_ids,
            runs_per_prompt=runs_per_prompt,
            require_complete=True,
            allow_resume_partial=bool(b.get("allow_resume_partial_stage_files", False)),
            resume_start_prompt_id=str(b.get("resume_start_prompt_id", "")),
        )

    print("[validate] OK All applicable checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Make failure loud and explicit for research reproducibility.
        print(f"[validate] VALIDATION FAILED: {e}", file=sys.stderr)
        raise
