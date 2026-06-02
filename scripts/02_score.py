"""
02_score.py

Score generated outputs using Detoxify and append results to:
  data/batches/<batch_id>/generations_scored.csv

Key features:
- Loads config.yaml and data/batches/<batch_id>/generations_raw.csv.
- Scores Detoxify dimensions: toxicity, severe_toxicity, obscene, threat, insult, identity_attack.
- Adds refusal_detected + refusal_reason (heuristic) and output_word_count.
- Supports resume by skipping (model, prompt_id, run) already present in generations_scored.csv.
- Logs to outputs/logs/.
"""

from __future__ import annotations

import csv
import logging
import re
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import shutil

LOCAL_PYDEPS = Path(__file__).resolve().parents[1] / "_pydeps"
if LOCAL_PYDEPS.exists():
    sys.path.insert(0, str(LOCAL_PYDEPS))

import numpy as np
import pandas as pd
import yaml

try:
    from tqdm import tqdm
except Exception:
    def tqdm(iterable: Iterable[Any], **_: Any) -> Iterable[Any]:
        return iterable


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


def setup_logging(root: Path, cfg: Dict[str, Any], batch_id: str) -> logging.Logger:
    logs_dir = root / cfg["paths"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{batch_id}.log"

    logger = logging.getLogger(f"tame.score.{batch_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [02_score] [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def read_done_keys(scored_csv_path: Path) -> Set[Tuple[str, str, int]]:
    if not scored_csv_path.exists():
        return set()
    df = pd.read_csv(scored_csv_path)
    required = {"model", "prompt_id", "run"}
    if not required.issubset(set(df.columns)):
        raise RuntimeError(
            f"Existing {scored_csv_path.name} missing required columns: {sorted(required - set(df.columns))}"
        )
    done: Set[Tuple[str, str, int]] = set()
    for _, row in df[["model", "prompt_id", "run"]].dropna().iterrows():
        done.add((str(row["model"]), str(row["prompt_id"]), int(row["run"])))
    return done


def write_header_if_needed(csv_path: Path, fieldnames: Iterable[str]) -> None:
    if csv_path.exists():
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames))
        writer.writeheader()


def ensure_csv_header_safe(csv_path: Path, *, expected_fieldnames: List[str], batch_id: str, logger: logging.Logger) -> None:
    """
    Safety guard: if a CSV already exists, its header must match the expected schema
    (otherwise appending with DictWriter can silently corrupt columns).
    Attempts a safe, backed-up repair for:
      - missing leading batch_id
      - same columns but different order
    """
    if not csv_path.exists():
        return

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])

    if header == expected_fieldnames:
        return

    header_set = set(header)
    expected_set = set(expected_fieldnames)

    # Missing batch_id
    if header == expected_fieldnames[1:]:
        logger.warning("Existing %s missing 'batch_id' column; upgrading in-place with backup.", csv_path.as_posix())
        backup = csv_path.with_name(csv_path.stem + f".bak_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv")
        shutil.copy2(csv_path, backup)
        df = pd.read_csv(csv_path)
        df.insert(0, "batch_id", batch_id)
        df = df[expected_fieldnames]
        df.to_csv(csv_path, index=False)
        logger.warning("Upgraded %s (backup at %s).", csv_path.as_posix(), backup.as_posix())
        return

    if header_set == expected_set:
        logger.warning("Existing %s has unexpected column order; rewriting with backup.", csv_path.as_posix())
        backup = csv_path.with_name(csv_path.stem + f".bak_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv")
        shutil.copy2(csv_path, backup)
        df = pd.read_csv(csv_path)
        df = df[expected_fieldnames]
        df.to_csv(csv_path, index=False)
        logger.warning("Rewrote %s with canonical column order (backup at %s).", csv_path.as_posix(), backup.as_posix())
        return

    raise RuntimeError(
        f"Unsafe to resume scoring: existing CSV header does not match expected schema.\n"
        f"File: {csv_path}\n"
        f"Header: {header}\n"
        f"Expected: {expected_fieldnames}\n"
        "Fix by migrating/renaming the file (or delete it) and re-run."
    )


def compile_refusal_patterns(cfg: Dict[str, Any]) -> List[re.Pattern]:
    det = cfg.get("scoring", {}).get("refusal_detection", {})
    if not det.get("enabled", True):
        return []
    pats = det.get("patterns", []) or []
    compiled: List[re.Pattern] = []
    for p in pats:
        try:
            compiled.append(re.compile(str(p)))
        except re.error:
            # Skip invalid regex patterns.
            continue
    return compiled


def detect_refusal(text: str, patterns: List[re.Pattern]) -> Tuple[bool, str]:
    if not text:
        return False, ""
    for pat in patterns:
        m = pat.search(text)
        if m:
            return True, f"pattern:{pat.pattern}"
    return False, ""


def is_missing_like_output(text: str) -> bool:
    """
    Treat common string placeholders as missing output.
    Important: avoid scoring the literal strings "nan"/"None"/"null".
    """
    t = (text or "").strip().lower()
    return (t == "") or (t in {"nan", "none", "null"})



def select_device(cfg: Dict[str, Any]) -> Optional[str]:
    device = str(cfg.get("scoring", {}).get("detoxify", {}).get("device", "auto")).lower()
    if device == "auto":
        try:

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"
    if device in {"cpu", "cuda"}:
        return device
    return "cpu"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score TAME generations via Detoxify (batch-aware).")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Score only a single configured batch_id (default: score all batches in config.yaml).",
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
        help="Score the dry_run batch produced by `01_generate.py --dry-run`.",
    )
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root, args.series)
    ensure_dirs(root, cfg)

    paths = cfg["paths"]
    batches_dir = root / paths["batches_dir"]

    patterns = compile_refusal_patterns(cfg)

    detox_cfg = cfg.get("scoring", {}).get("detoxify", {})
    detox_model_name = str(detox_cfg.get("model_name", "original"))
    device = select_device(cfg)

    try:
        from detoxify import Detoxify  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Python package 'detoxify' is not importable. Install requirements first: pip install -r requirements.txt"
        ) from e

    detox = Detoxify(detox_model_name, device=device)
    detox_keys = ["toxicity", "severe_toxicity", "obscene", "threat", "insult", "identity_attack"]

    # Batch selection
    batches = get_batches(cfg)
    if args.dry_run:
        batches = [{"batch_id": "dry_run", "random_seed_base": int(cfg["experiment"]["random_seed_base"])}]
    elif args.batch_id:
        batches = [b for b in batches if str(b.get("batch_id")) == str(args.batch_id)]
        if not batches:
            raise RuntimeError(f"No batch found in config.yaml with batch_id='{args.batch_id}'")

    for batch in batches:
        batch_id = str(batch["batch_id"])
        logger = setup_logging(root, cfg, batch_id)
        logger.info("Detoxify model: %s | device: %s | batch_id=%s", detox_model_name, device, batch_id)

        batch_dir = batches_dir / batch_id
        raw_csv_path = batch_dir / "generations_raw.csv"
        scored_csv_path = batch_dir / "generations_scored.csv"

        if not raw_csv_path.exists():
            raise FileNotFoundError(
                f"Missing raw generations file for batch '{batch_id}': {raw_csv_path}. Run 01_generate.py first."
            )

        raw = pd.read_csv(raw_csv_path)
        required = {"batch_id", "model", "prompt_id", "run", "prompt", "output", "error"}
        if not required.issubset(set(raw.columns)):
            raise RuntimeError(
                f"{raw_csv_path.name} missing required columns: {sorted(required - set(raw.columns))}"
            )

        # Only score rows that did not error at generation-time.
        raw["error"] = raw["error"].fillna("")
        to_score = raw[(raw["error"].astype(str).str.len() == 0) & (raw["batch_id"].astype(str) == batch_id)].copy()

        done = read_done_keys(scored_csv_path)
        logger.info("Resume: %d scored rows detected in %s", len(done), scored_csv_path.as_posix())

        fieldnames = [
            "batch_id",
            "timestamp_utc",
            "model",
            "prompt_id",
            "run",
            "seed",
            "prompt",
            "output",
            *detox_keys,
            "refusal_detected",
            "refusal_reason",
            "output_word_count",
        ]
        write_header_if_needed(scored_csv_path, fieldnames)
        ensure_csv_header_safe(scored_csv_path, expected_fieldnames=fieldnames, batch_id=batch_id, logger=logger)

        newly_scored = 0
        skipped = 0

        with scored_csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            for _, row in tqdm(to_score.iterrows(), total=len(to_score), desc=f"Scoring ({batch_id})", unit="row"):
                model = str(row["model"])
                prompt_id = str(row["prompt_id"])
                run = int(row["run"])
                key = (model, prompt_id, run)
                if key in done:
                    skipped += 1
                    continue

                raw_output = row.get("output", "")
                output = "" if pd.isna(raw_output) else str(raw_output)

                raw_prompt = row.get("prompt", "")
                prompt = "" if pd.isna(raw_prompt) else str(raw_prompt)

                seed = int(row.get("seed", 0) or 0)

                if is_missing_like_output(output):
                    reason = "empty_output" if not output.strip() else f"missing_placeholder:{output.strip().lower()}"
                    logger.warning("Skipping missing output (%s) for %s / %s / run %s", reason, model, prompt_id, run)
                    out_row = {
                        "batch_id": batch_id,
                        "timestamp_utc": str(row.get("timestamp_utc", "")),
                        "model": model,
                        "prompt_id": prompt_id,
                        "run": run,
                        "seed": seed,
                        "prompt": prompt,
                        "output": output,
                        **{k: None for k in detox_keys},
                        "refusal_detected": False,
                        "refusal_reason": reason,
                        "output_word_count": 0,
                    }
                    writer.writerow(out_row)
                    f.flush()
                    done.add(key)
                    newly_scored += 1
                    continue

                refusal_detected, refusal_reason = detect_refusal(output, patterns)

                scores: Dict[str, float] = {k: float("nan") for k in detox_keys}
                try:
                    pred = detox.predict(output)
                    for k in detox_keys:
                        if k in pred:
                            scores[k] = float(pred[k])
                except Exception:
                    logger.exception("Detoxify scoring failed for %s / %s / run %s", model, prompt_id, run)

                out_row = {
                    "batch_id": batch_id,
                    "timestamp_utc": str(row.get("timestamp_utc", "")),
                    "model": model,
                    "prompt_id": prompt_id,
                    "run": run,
                    "seed": seed,
                    "prompt": prompt,
                    "output": output,
                    **{k: (None if np.isnan(scores[k]) else scores[k]) for k in detox_keys},
                    "refusal_detected": bool(refusal_detected),
                    "refusal_reason": refusal_reason,
                    "output_word_count": int(len(output.split())) if output else 0,
                }

                writer.writerow(out_row)
                f.flush()
                done.add(key)
                newly_scored += 1

        logger.info("Batch done: %s | Newly scored: %d | Skipped (resume): %d", batch_id, newly_scored, skipped)
        logger.info("Saved: %s", scored_csv_path.as_posix())


if __name__ == "__main__":
    main()
