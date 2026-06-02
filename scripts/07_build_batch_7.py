from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "data" / "prompts_batch_7.csv"
BASE_PROMPTS = [
    ROOT / "data" / "prompts.csv",
    ROOT / "data" / "prompts_batch_4.csv",
    ROOT / "data" / "prompts_batch_5.csv",
    ROOT / "data" / "prompts_batch_6.csv",
]

sys.path.insert(0, str(ROOT / "data"))
from batch_7_groups import GROUPS  # noqa: E402


def norm_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parts(label: str) -> List[str]:
    return [p for p in re.split(r"[+_]", label) if p]


def build_rows(groups: Iterable[Dict[str, object]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    idx = 1
    for group in groups:
        pair_group = str(group["pair_group"])
        neutral = group["neutral"]
        single_a = group["single_a"]
        single_b = group["single_b"]
        intersectional = group["intersectional"]
        for prompt_type, triple in (
            ("neutral", neutral),
            ("single_identity", single_a),
            ("single_identity", single_b),
            ("intersectional", intersectional),
        ):
            identity_axis, identity_label, prompt = triple  # type: ignore[misc]
            rows.append(
                {
                    "prompt_id": f"B7_P{idx:04d}",
                    "pair_group": pair_group,
                    "prompt_type": str(prompt_type),
                    "identity_axis": str(identity_axis),
                    "identity_label": str(identity_label),
                    "prompt": str(prompt),
                    "source": "custom_batch_7_healthcare_hard_tricky_v4",
                    "risk_level": "high",
                }
            )
            idx += 1
    return rows


def load_prompts(path: Path) -> List[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [str(r.get("prompt", "")).strip() for r in reader if str(r.get("prompt", "")).strip()]


def validate_rows(rows: List[Dict[str, str]]) -> List[str]:
    required = [
        "prompt_id",
        "pair_group",
        "prompt_type",
        "identity_axis",
        "identity_label",
        "prompt",
        "source",
        "risk_level",
    ]
    if len(GROUPS) != 75:
        raise RuntimeError(f"GROUPS must contain exactly 75 groups. Found: {len(GROUPS)}")
    if len(rows) != 300:
        raise RuntimeError(f"Expected 300 rows, found {len(rows)}")

    ids = [r["prompt_id"] for r in rows]
    expected = [f"B7_P{i:04d}" for i in range(1, 301)]
    if ids != expected:
        raise RuntimeError("Prompt IDs must be exactly B7_P0001..B7_P0300 in order")
    if len(set(ids)) != len(ids):
        raise RuntimeError("Duplicate prompt IDs detected")

    missing_cols = [c for c in required if c not in rows[0]]
    if missing_cols:
        raise RuntimeError(f"Missing required columns: {missing_cols}")

    by_group: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_group[row["pair_group"]].append(row)
        for col in required:
            if not str(row.get(col, "")).strip():
                raise RuntimeError(f"Empty required value for column '{col}' in prompt_id={row['prompt_id']}")
        if row["source"] != "custom_batch_7_healthcare_hard_tricky_v4":
            raise RuntimeError(f"Invalid source at {row['prompt_id']}")
        if row["risk_level"] != "high":
            raise RuntimeError(f"Invalid risk_level at {row['prompt_id']}")
        if row["prompt_type"] not in {"neutral", "single_identity", "intersectional"}:
            raise RuntimeError(f"Invalid prompt_type at {row['prompt_id']}: {row['prompt_type']}")

    if len(by_group) != 75:
        raise RuntimeError(f"Expected 75 pair_groups, found {len(by_group)}")

    warnings: List[str] = []
    for pg, items in by_group.items():
        if len(items) != 4:
            raise RuntimeError(f"{pg}: expected 4 rows, found {len(items)}")
        ptypes = sorted(r["prompt_type"] for r in items)
        if ptypes != ["intersectional", "neutral", "single_identity", "single_identity"]:
            raise RuntimeError(f"{pg}: invalid prompt_type composition {ptypes}")
        neutral = next(r for r in items if r["prompt_type"] == "neutral")
        singles = [r for r in items if r["prompt_type"] == "single_identity"]
        for single in singles:
            for label in parts(single["identity_label"]):
                label_norm = norm_text(label)
                if label_norm and re.search(rf"\b{re.escape(label_norm)}\b", norm_text(neutral["prompt"])):
                    warnings.append(f"{pg}: neutral prompt contains identity label '{label}'")

    norm_prompts = [norm_text(r["prompt"]) for r in rows]
    cnt = Counter(norm_prompts)
    dup_norm = [k for k, v in cnt.items() if v > 1]
    if dup_norm:
        raise RuntimeError(f"Duplicate normalized prompt text inside batch_7. Examples: {dup_norm[:3]}")

    # Near-duplicate guard within batch_7.
    for i in range(len(norm_prompts)):
        for j in range(i + 1, len(norm_prompts)):
            s = SequenceMatcher(None, norm_prompts[i], norm_prompts[j]).ratio()
            if s >= 0.995:
                raise RuntimeError(f"Potential accidental near-duplicate within batch_7 at rows {i+1} and {j+1} ({s:.3f})")

    slurs = ["nigger", "faggot", "kike", "chink", "spic", "retard", "tranny"]
    combined = "\n".join(r["prompt"] for r in rows).lower()
    for term in slurs:
        if term in combined:
            raise RuntimeError(f"Disallowed slur found: {term}")

    for base in BASE_PROMPTS:
        existing = set(norm_text(t) for t in load_prompts(base))
        overlap = set(norm_prompts) & existing
        if overlap:
            raise RuntimeError(f"Exact prompt overlap with {base.name}. Example: {next(iter(overlap))}")
        existing_list = [norm_text(t) for t in load_prompts(base)]
        for np_text in norm_prompts:
            for old in existing_list:
                if SequenceMatcher(None, np_text, old).ratio() >= 0.97:
                    raise RuntimeError(f"Near-duplicate with {base.name} detected (>=0.97)")
    return warnings


def write_csv(rows: List[Dict[str, str]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "prompt_id",
                "pair_group",
                "prompt_type",
                "identity_axis",
                "identity_label",
                "prompt",
                "source",
                "risk_level",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = build_rows(GROUPS)
    warnings = validate_rows(rows)
    write_csv(rows)
    print(f"Wrote {OUT_CSV.as_posix()} with {len(rows)} rows")
    for warning in warnings:
        print(f"WARNING: {warning}")


if __name__ == "__main__":
    main()
