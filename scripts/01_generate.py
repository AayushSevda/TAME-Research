from __future__ import annotations

import argparse
import csv
import concurrent.futures as cf
import hashlib
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib import request

LOCAL_PYDEPS = Path(__file__).resolve().parents[1] / "_pydeps"
if LOCAL_PYDEPS.exists():
    sys.path.insert(0, str(LOCAL_PYDEPS))

import pandas as pd
import yaml

try:
    from tqdm import tqdm
except Exception:
    def tqdm(iterable: Iterable[Any], **_: Any) -> Iterable[Any]:
        return iterable


def project_root() -> Path:
    # scripts/01_generate.py -> project root
    return Path(__file__).resolve().parents[1]


def load_config(root: Path, series: str = "series_1") -> Dict[str, Any]:
    cfg_path = root / ("config_series_2.yaml" if series == "series_2" else "config.yaml")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_batches(cfg: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    Returns an iterable of batch dicts. Each batch must have:
      - batch_id
      - random_seed_base
      - runs_per_prompt (optional; defaults to generation.runs_per_prompt)
    If cfg has no batches, returns a default single batch.
    """
    batches = cfg.get("batches")
    if isinstance(batches, list) and len(batches) > 0:
        out = []
        for b in batches:
            if isinstance(b, dict) and b.get("batch_id") is not None:
                out.append(b)
        if out:
            return out
    return [{"batch_id": "batch_1", "random_seed_base": int(cfg["experiment"]["random_seed_base"])}]

def ensure_dirs(root: Path, cfg: Dict[str, Any]) -> None:
    paths = cfg["paths"]
    (root / paths["outputs_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["batches_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["tables_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["figures_dir"]).mkdir(parents=True, exist_ok=True)
    (root / paths["logs_dir"]).mkdir(parents=True, exist_ok=True)


def setup_logging(root: Path, cfg: Dict[str, Any], batch_id: str) -> logging.Logger:
    logs_dir = root / cfg["paths"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Requirement: one log file per batch for unattended runs.
    log_path = logs_dir / f"{batch_id}.log"

    logger = logging.getLogger(f"tame.generate.{batch_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [01_generate] [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger



def deterministic_seed(seed_base: int, model: str, prompt_id: str, run_index: int) -> int:
    """
    Produce a stable 32-bit seed derived from experiment seed base + identifiers.
    """
    s = f"{seed_base}|{model}|{prompt_id}|{run_index}".encode("utf-8")
    digest = hashlib.md5(s).hexdigest()
    # 31-bit positive integer
    return (int(digest[:8], 16) ^ seed_base) & 0x7FFFFFFF


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_done_keys(raw_csv_path: Path) -> Set[Tuple[str, str, int]]:
    """
    Returns set of (model, prompt_id, run) already present in generations_raw.csv.
    """
    if not raw_csv_path.exists():
        return set()
    try:
        df = pd.read_csv(raw_csv_path)
    except Exception:
        # If the file is partially written/corrupted, do not assume it's empty;
        # let the user fix it explicitly.
        raise RuntimeError(f"Failed to read existing raw generations file: {raw_csv_path}")

    # batch_id is expected but not required for resume (defensive).
    required = {"model", "prompt_id", "run"}
    if not required.issubset(set(df.columns)):
        raise RuntimeError(
            f"Existing {raw_csv_path.name} missing required columns: {sorted(required - set(df.columns))}"
        )
    done: Set[Tuple[str, str, int]] = set()
    for _, row in df[["model", "prompt_id", "run"]].dropna().iterrows():
        done.add((str(row["model"]), str(row["prompt_id"]), int(row["run"])))
    return done


def migrate_legacy_raw_if_needed(root: Path, cfg: Dict[str, Any], *, batch_id: str, logger: logging.Logger) -> None:
    """
    Safe migration for legacy single-file runs:
      legacy: data/generations_raw.csv
      new:    data/batches/<batch_id>/generations_raw.csv

    Migration rule (as requested):
    - If legacy file exists AND new batch file does not exist, copy legacy → new.
    - Add batch_id column if missing.
    - Do NOT delete legacy file.
    """
    if batch_id != "batch_1":
        return

    legacy_path = root / "data" / "generations_raw.csv"
    batches_dir = root / cfg["paths"]["batches_dir"]
    new_path = batches_dir / batch_id / "generations_raw.csv"

    if not legacy_path.exists():
        return
    if new_path.exists():
        return

    logger.warning("Legacy generations detected at %s", legacy_path.as_posix())
    logger.warning("Migrating legacy generations into %s (copy; legacy file is kept).", new_path.as_posix())

    df = pd.read_csv(legacy_path)
    if "batch_id" not in df.columns:
        df.insert(0, "batch_id", batch_id)
    else:
        df["batch_id"] = df["batch_id"].astype(str).fillna(batch_id)

    # Normalize column names if older variants exist
    if "latency" in df.columns and "latency_s" not in df.columns:
        df = df.rename(columns={"latency": "latency_s"})
    if "word_count" in df.columns and "output_word_count" not in df.columns:
        df = df.rename(columns={"word_count": "output_word_count"})

    # Ensure required columns exist (fill if missing)
    required_cols = [
        "batch_id",
        "timestamp_utc",
        "model",
        "prompt_id",
        "run",
        "seed",
        "prompt",
        "output",
        "latency_s",
        "output_word_count",
        "error",
    ]
    for c in required_cols:
        if c not in df.columns:
            if c == "error":
                df[c] = ""
            elif c in {"latency_s", "output_word_count", "run", "seed"}:
                df[c] = 0
            else:
                df[c] = ""

    # Coerce dtypes conservatively
    df["run"] = pd.to_numeric(df["run"], errors="coerce").fillna(0).astype(int)
    df["seed"] = pd.to_numeric(df["seed"], errors="coerce").fillna(0).astype(int)
    df["latency_s"] = pd.to_numeric(df["latency_s"], errors="coerce")
    if df["output_word_count"].isna().any():
        # recompute if needed
        df["output_word_count"] = df["output"].fillna("").astype(str).apply(lambda s: len(s.split()))
    df["output_word_count"] = pd.to_numeric(df["output_word_count"], errors="coerce").fillna(0).astype(int)

    new_path.parent.mkdir(parents=True, exist_ok=True)
    df = df[required_cols]
    df.to_csv(new_path, index=False)
    logger.warning("Migration complete: %d legacy rows copied into %s", len(df), new_path.as_posix())
    logger.warning(
        "NOTE: migrated rows keep their original seed values. For strict reproducibility, consider setting batch_1 "
        "random_seed_base to match the legacy seed regime OR re-running batch_1 from scratch."
    )


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

    We attempt a safe, backed-up repair for a small set of known legacy cases:
    - header missing leading 'batch_id'
    - header has same columns but different order
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

    # Known legacy: no batch_id column
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

    # Same columns, different order
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
        f"Unsafe to resume: existing CSV header does not match expected schema.\n"
        f"File: {csv_path}\n"
        f"Header: {header}\n"
        f"Expected: {expected_fieldnames}\n"
        "Fix by migrating/renaming the file (or delete it) and re-run."
    )


def check_ollama_models_installed(logger: logging.Logger, models: Iterable[str]) -> None:
    """
    Verifies that each configured model appears in `ollama.list()`.
    """
    # NOTE: This function intentionally supports multiple Ollama Python package response formats.
    # The CLI `ollama list` output is not the same as `ollama.list()` return type.
    ollama = load_ollama_client()

    try:
        listing = ollama.list()
    except Exception as e:
        raise RuntimeError(
            "Could not query Ollama for installed models. "
            "Make sure the Ollama app/service is running, then try `ollama list` in CMD."
        ) from e

    installed: Set[str] = set()

    def normalize_model_name(name: Any) -> str:
        return str(name).strip().split(":")[0]

    def add_model_name(name: Any) -> None:
        if name is None:
            return
        full_name = str(name).strip()
        if not full_name:
            return
        installed.add(full_name)
        installed.add(normalize_model_name(full_name))

    def parse_model_item(item: Any) -> None:
        """
        Extract model names from either dict-style or object-style Ollama model entries.
        """
        if item is None:
            return

        if isinstance(item, dict):
            for key in ("name", "model", "model_name"):
                if key in item:
                    add_model_name(item[key])
            return

        for attr in ("name", "model", "model_name"):
            if hasattr(item, attr):
                add_model_name(getattr(item, attr))

    # Case 1: dict response
    if isinstance(listing, dict):
        model_items = listing.get("models", [])
        if isinstance(model_items, list):
            for item in model_items:
                parse_model_item(item)

    # Case 2: list response
    elif isinstance(listing, list):
        for item in listing:
            parse_model_item(item)

    # Case 3: object response with `.models`
    elif hasattr(listing, "models"):
        model_items = getattr(listing, "models")
        if isinstance(model_items, list):
            for item in model_items:
                parse_model_item(item)

    missing: List[str] = []
    for requested in models:
        requested_full = str(requested).strip()
        requested_base = normalize_model_name(requested_full)
        if requested_full not in installed and requested_base not in installed:
            missing.append(str(requested))

    if missing:
        logger.error("Missing Ollama models: %s", ", ".join(missing))
        logger.error("Installed models seen by Python: %s", ", ".join(sorted(installed)) or "(none)")
        logger.error("Raw ollama.list() response: %r", listing)
        raise RuntimeError(
            "Some configured models are not installed or could not be detected by the Ollama Python package. "
            "First confirm `ollama list` works in CMD. If it does, inspect the logged raw ollama.list() response."
        )

    logger.info("Installed Ollama models detected: %s", ", ".join(sorted(installed)))


class OllamaHttpClient:
    """Minimal fallback client for local Ollama when the Python package is unavailable."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", request_timeout_s: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_s = request_timeout_s

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.request_timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get_json(self, path: str) -> Dict[str, Any]:
        with request.urlopen(f"{self.base_url}{path}", timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list(self) -> Dict[str, Any]:
        return self._get_json("/api/tags")

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        system: str,
        options: Dict[str, Any],
        stream: bool,
        think: Optional[bool] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "options": options,
            "stream": stream,
        }
        if think is not None:
            payload["think"] = think
        return self._post_json("/api/generate", payload)


def load_ollama_client(request_timeout_s: int = 180) -> Any:
    try:
        import ollama  # type: ignore

        return ollama
    except Exception:
        return OllamaHttpClient(request_timeout_s=request_timeout_s)


@dataclass(frozen=True)
class GenerationJob:
    model: str
    prompt_id: str
    run_index: int
    prompt: str


def build_jobs(
    prompts_df: pd.DataFrame, models: Iterable[str], runs_per_prompt: int
) -> Iterable[GenerationJob]:
    for model in models:
        for _, row in prompts_df.iterrows():
            prompt_id = str(row["prompt_id"])
            prompt = str(row["prompt"])
            for r in range(1, runs_per_prompt + 1):
                yield GenerationJob(model=model, prompt_id=prompt_id, run_index=r, prompt=prompt)


def prompts_path_for_batch(root: Path, cfg: Dict[str, Any], batch: Dict[str, Any]) -> Path:
    """
    Per-batch prompt file override:
      - if batch has `prompts_csv`, use it
      - else use cfg.paths.prompts_csv
    """
    paths = cfg["paths"]
    rel = batch.get("prompts_csv") or paths["prompts_csv"]
    return root / str(rel)


def load_prompts_df(prompts_path: Path) -> pd.DataFrame:
    if not prompts_path.exists():
        raise FileNotFoundError(f"Missing prompts file: {prompts_path}")
    df = pd.read_csv(prompts_path)
    required_cols = {"prompt_id", "prompt"}
    if not required_cols.issubset(df.columns):
        raise RuntimeError(
            f"{prompts_path.as_posix()}: missing required columns: {sorted(required_cols - set(df.columns))}"
        )
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate TAME outputs via Ollama (batch-aware).")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Run only a single configured batch_id (default: run all batches in config.yaml).",
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
        help="Quick end-to-end smoke run: first 2 pair_groups, runs_per_prompt=1, writes to data/batches/dry_run/.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Diagnostic safety limit: attempt at most N non-completed generation jobs, then stop.",
    )
    parser.add_argument(
        "--limit-prompts",
        type=int,
        default=None,
        help="Diagnostic safety limit: load only the first N prompts from each selected batch.",
    )
    parser.add_argument(
        "--only-model",
        default=None,
        help="Diagnostic safety filter: run only this configured model name.",
    )
    parser.add_argument(
        "--verbose-jobs",
        action="store_true",
        help="Print per-job start/finish success logs (default off to keep tqdm output clean).",
    )
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root, args.series)
    ensure_dirs(root, cfg)

    paths = cfg["paths"]
    default_prompts_path = root / paths["prompts_csv"]

    models = list(cfg["generation"]["models"])
    if args.only_model:
        if args.only_model not in models:
            raise RuntimeError(f"--only-model '{args.only_model}' is not in configured models: {models}")
        models = [args.only_model]
    timeout_s = int(cfg["generation"].get("request_timeout_s", 120))
    think_cfg = cfg["generation"].get("think", None)
    think_opt: Optional[bool]
    if think_cfg is None:
        think_opt = None
    elif isinstance(think_cfg, bool):
        think_opt = think_cfg
    else:
        think_opt = str(think_cfg).strip().lower() in {"1", "true", "yes", "on"}

    ollama = load_ollama_client(request_timeout_s=timeout_s)

    sys_prompt = str(cfg["generation"]["system_prompt"])
    opts_cfg = cfg["generation"]["options"]
    options_base = {
        "temperature": float(opts_cfg["temperature"]),
        "top_p": float(opts_cfg["top_p"]),
        "num_predict": int(opts_cfg["num_predict"]),
    }
    # Optional safety knob (context window). Keep it config-driven to stay reproducible.
    if "num_ctx" in opts_cfg and opts_cfg["num_ctx"] is not None:
        options_base["num_ctx"] = int(opts_cfg["num_ctx"])

    batches_dir = root / paths["batches_dir"]

    # Dry-run mode: limit scope without changing config.yaml
    batch_overrides: Optional[List[Dict[str, Any]]] = None
    dry_run_prompts_df: Optional[pd.DataFrame] = None
    if args.dry_run:
        prompts_df = load_prompts_df(default_prompts_path)
        # Need pair_group to filter; prompts.csv has it.
        if "pair_group" not in prompts_df.columns:
            raise RuntimeError("Dry run requires prompts.csv to include a 'pair_group' column.")
        keep_groups = sorted(prompts_df["pair_group"].astype(str).unique().tolist())[:2]
        dry_run_prompts_df = prompts_df[prompts_df["pair_group"].astype(str).isin(keep_groups)].copy()
        batch_overrides = [
            {
                "batch_id": "dry_run",
                "random_seed_base": int(cfg["experiment"]["random_seed_base"]),
                "runs_per_prompt": 1,
            }
        ]

    batches = list(batch_overrides) if batch_overrides is not None else list(get_batches(cfg))
    if args.batch_id and batch_overrides is None:
        batches = [b for b in batches if str(b.get("batch_id")) == str(args.batch_id)]
        if not batches:
            raise RuntimeError(f"No batch found in config.yaml with batch_id='{args.batch_id}'")

    for batch in batches:
        batch_id = str(batch["batch_id"])
        seed_base = int(batch.get("random_seed_base", cfg["experiment"]["random_seed_base"]))
        runs_per_prompt = int(batch.get("runs_per_prompt", cfg["generation"]["runs_per_prompt"]))
        logger = setup_logging(root, cfg, batch_id)

        if args.dry_run:
            assert dry_run_prompts_df is not None
            prompts_df = dry_run_prompts_df
            prompts_path_used = default_prompts_path
        else:
            prompts_path_used = prompts_path_for_batch(root, cfg, batch)
            prompts_df = load_prompts_df(prompts_path_used)
            if args.limit_prompts is not None:
                if args.limit_prompts < 1:
                    raise RuntimeError("--limit-prompts must be >= 1")
                prompts_df = prompts_df.head(args.limit_prompts).copy()

        logger.info("Experiment: %s | batch_id=%s | seed_base=%s", cfg["experiment"]["name"], batch_id, seed_base)
        logger.info("Series: %s | batches_dir=%s", args.series, batches_dir.as_posix())
        logger.info("Prompts file: %s", prompts_path_used.as_posix())
        logger.info("Models: %s", ", ".join(models))
        logger.info("Request timeout: %ss | think=%s", timeout_s, think_opt)
        jobs = list(build_jobs(prompts_df, models, runs_per_prompt))
        total = len(jobs)
        logger.info("Prompts: %d | Runs per prompt: %d | Total jobs this batch: %d", len(prompts_df), runs_per_prompt, total)

        check_ollama_models_installed(logger, models)

        batch_dir = batches_dir / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        raw_csv_path = batch_dir / "generations_raw.csv"
        failed_csv_path = batch_dir / "generations_failed.csv"

        # One-time safe migration for legacy runs into batch_1
        migrate_legacy_raw_if_needed(root, cfg, batch_id=batch_id, logger=logger)

        fieldnames = [
            "batch_id",
            "timestamp_utc",
            "model",
            "prompt_id",
            "run",
            "seed",
            "prompt",
            "output",
            "latency_s",
            "output_word_count",
            "error",
        ]
        write_header_if_needed(raw_csv_path, fieldnames)
        ensure_csv_header_safe(raw_csv_path, expected_fieldnames=fieldnames, batch_id=batch_id, logger=logger)

        # Failed generations are written separately and are NOT considered completed keys.
        write_header_if_needed(failed_csv_path, fieldnames)
        ensure_csv_header_safe(failed_csv_path, expected_fieldnames=fieldnames, batch_id=batch_id, logger=logger)

        done_keys = read_done_keys(raw_csv_path)
        logger.info("Resume: %d completed generations detected in %s", len(done_keys), raw_csv_path.as_posix())

        completed_now = 0
        skipped = 0
        attempted = 0

        with raw_csv_path.open("a", newline="", encoding="utf-8") as f_ok, failed_csv_path.open(
            "a", newline="", encoding="utf-8"
        ) as f_fail:
            writer_ok = csv.DictWriter(f_ok, fieldnames=fieldnames)
            writer_fail = csv.DictWriter(f_fail, fieldnames=fieldnames)

            for job_number, job in enumerate(tqdm(jobs, total=total, desc=f"Generating ({batch_id})", unit="gen"), start=1):
                key = (job.model, job.prompt_id, job.run_index)
                if key in done_keys:
                    skipped += 1
                    continue
                if args.max_jobs is not None and attempted >= args.max_jobs:
                    logger.info("Stopping early because --max-jobs=%d was reached", args.max_jobs)
                    break
                attempted += 1

                seed = deterministic_seed(seed_base, job.model, job.prompt_id, job.run_index)
                options = dict(options_base)
                options["seed"] = seed

                t0 = time.monotonic()
                output_text = ""
                error_msg = ""
                if args.verbose_jobs:
                    logger.info(
                        "Starting job %d/%d | attempt=%d | model=%s | prompt_id=%s | run=%s | prompt_chars=%d",
                        job_number,
                        total,
                        attempted,
                        job.model,
                        job.prompt_id,
                        job.run_index,
                        len(job.prompt),
                    )
                try:
                    # Best-effort timeout guard. Note: cancelling a timed-out thread won't necessarily
                    # stop the underlying request, but it prevents the whole run from stalling.
                    def _do_generate() -> Any:
                        kwargs: Dict[str, Any] = {
                            "model": job.model,
                            "prompt": job.prompt,
                            "system": sys_prompt,
                            "options": options,
                            "stream": False,
                        }
                        if think_opt is not None:
                            kwargs["think"] = think_opt
                        try:
                            return ollama.generate(**kwargs)
                        except TypeError:
                            # Older Python clients may not yet support Ollama's `think` parameter.
                            # The fallback HTTP client above does support it.
                            if "think" in kwargs:
                                kwargs.pop("think")
                                return ollama.generate(**kwargs)
                            raise

                    ex = cf.ThreadPoolExecutor(max_workers=1)
                    try:
                        fut = ex.submit(_do_generate)
                        res = fut.result(timeout=timeout_s)
                    finally:
                        ex.shutdown(wait=False, cancel_futures=True)

                    # Defensive parsing across versions.
                    if isinstance(res, dict) and "response" in res:
                        output_text = str(res["response"])
                    elif isinstance(res, str):
                        output_text = res
                    else:
                        output_text = str(res)
                except cf.TimeoutError:
                    error_msg = f"TimeoutError: generation exceeded {timeout_s}s"
                    logger.error("Timeout for %s / %s / run %s", job.model, job.prompt_id, job.run_index)
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {e}"
                    logger.exception("Generation error for %s / %s / run %s", job.model, job.prompt_id, job.run_index)
                finally:
                    latency = max(0.0, time.monotonic() - t0)

                row = {
                    "batch_id": batch_id,
                    "timestamp_utc": utc_now_iso(),
                    "model": job.model,
                    "prompt_id": job.prompt_id,
                    "run": job.run_index,
                    "seed": seed,
                    "prompt": job.prompt,
                    "output": output_text,
                    "latency_s": round(latency, 6),
                    "output_word_count": int(len(output_text.split())) if output_text else 0,
                    "error": error_msg,
                }

                # INTEGRITY RULE:
                # Only successful generations count as completed. Failures are logged separately and remain retryable.
                is_empty = not str(output_text).strip()
                is_failed = bool(error_msg) or is_empty
                if is_failed:
                    if is_empty and not error_msg:
                        row["error"] = "empty_output"
                    writer_fail.writerow(row)
                    f_fail.flush()
                    if is_empty:
                        logger.warning("Failed generation (empty_output) for %s / %s / run %s", job.model, job.prompt_id, job.run_index)
                else:
                    writer_ok.writerow(row)
                    f_ok.flush()  # crash-safe append
                    done_keys.add(key)
                    completed_now += 1
                if args.verbose_jobs or is_failed:
                    logger.info(
                        "Finished job | model=%s | prompt_id=%s | run=%s | elapsed_s=%.3f | output_chars=%d | output_words=%d | failed=%s | error=%s",
                        job.model,
                        job.prompt_id,
                        job.run_index,
                        latency,
                        len(output_text or ""),
                        row["output_word_count"],
                        is_failed,
                        row["error"],
                    )

        logger.info("Batch done: %s | Newly completed: %d | Skipped (resume): %d | Attempted: %d", batch_id, completed_now, skipped, attempted)
        logger.info("Saved: %s", raw_csv_path.as_posix())


if __name__ == "__main__":
    main()
