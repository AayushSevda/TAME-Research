from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "data" / "prompts_batch_9.csv"
BASE_PROMPTS = [
    ROOT / "data" / "prompts.csv",
    ROOT / "data" / "prompts_batch_4.csv",
    ROOT / "data" / "prompts_batch_5.csv",
    ROOT / "data" / "prompts_batch_6.csv",
    ROOT / "data" / "prompts_batch_7.csv",
    ROOT / "data" / "prompts_batch_8.csv",
]

sys.path.insert(0, str(ROOT / "data"))
from batch_9_groups import GROUPS  # noqa: E402


SOURCE = "custom_batch_9_extreme_hard_tricky_v6"
RISK_LEVEL = "high"
EXPECTED_GROUPS = 135
EXPECTED_ROWS = 540
EXPECTED_PREFIX = "B9"


def norm_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def label_terms(label: str) -> List[str]:
    if label == "neutral":
        return []
    terms: List[str] = []
    phrase = norm_text(label.replace("_", " "))
    if phrase:
        terms.append(phrase)
    for part in re.split(r"[+_]", label):
        token = norm_text(part)
        if token and token not in {"neutral", "status", "identity"}:
            terms.append(token)
    return list(dict.fromkeys(terms))


def build_rows(groups: Iterable[Dict[str, object]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    idx = 1
    for group in groups:
        pair_group = str(group["pair_group"])
        for prompt_type, key in (
            ("neutral", "neutral"),
            ("single_identity", "single_a"),
            ("single_identity", "single_b"),
            ("intersectional", "intersectional"),
        ):
            identity_axis, identity_label, prompt = group[key]  # type: ignore[index,misc]
            rows.append(
                {
                    "prompt_id": f"{EXPECTED_PREFIX}_P{idx:04d}",
                    "pair_group": pair_group,
                    "prompt_type": prompt_type,
                    "identity_axis": str(identity_axis),
                    "identity_label": str(identity_label),
                    "prompt": str(prompt),
                    "source": SOURCE,
                    "risk_level": RISK_LEVEL,
                }
            )
            idx += 1
    return rows


def load_prompts(path: Path) -> List[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [str(r.get("prompt", "")).strip() for r in reader if str(r.get("prompt", "")).strip()]


def likely_near_duplicate(a: str, b: str) -> bool:
    a_len = len(a)
    b_len = len(b)
    if abs(a_len - b_len) > max(8, int(max(a_len, b_len) * 0.03)):
        return False
    a_words = a.split()
    b_words = b.split()
    if a_words[:3] != b_words[:3] and a_words[-3:] != b_words[-3:]:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.97


def validate_rows(rows: List[Dict[str, str]]) -> None:
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
    if len(GROUPS) != EXPECTED_GROUPS:
        raise RuntimeError(f"GROUPS must contain exactly {EXPECTED_GROUPS} groups. Found: {len(GROUPS)}")
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"Expected {EXPECTED_ROWS} rows, found {len(rows)}")
    if not rows:
        raise RuntimeError("No rows were generated")

    missing_cols = [c for c in required if c not in rows[0]]
    if missing_cols:
        raise RuntimeError(f"Missing required columns: {missing_cols}")

    expected_ids = [f"{EXPECTED_PREFIX}_P{i:04d}" for i in range(1, EXPECTED_ROWS + 1)]
    ids = [r["prompt_id"] for r in rows]
    if ids != expected_ids:
        raise RuntimeError(f"Prompt IDs must be exactly {EXPECTED_PREFIX}_P0001..{EXPECTED_PREFIX}_P0540 in order")
    if len(set(ids)) != len(ids):
        raise RuntimeError("Duplicate prompt IDs detected")

    by_group: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_group[row["pair_group"]].append(row)
        for col in required:
            if not str(row.get(col, "")).strip():
                raise RuntimeError(f"Empty required value for column '{col}' in prompt_id={row['prompt_id']}")
        if row["source"] != SOURCE:
            raise RuntimeError(f"Invalid source at {row['prompt_id']}")
        if row["risk_level"] != RISK_LEVEL:
            raise RuntimeError(f"Invalid risk_level at {row['prompt_id']}")
        if row["prompt_type"] not in {"neutral", "single_identity", "intersectional"}:
            raise RuntimeError(f"Invalid prompt_type at {row['prompt_id']}: {row['prompt_type']}")

    if len(by_group) != EXPECTED_GROUPS:
        raise RuntimeError(f"Expected {EXPECTED_GROUPS} pair_groups, found {len(by_group)}")

    for pg, items in by_group.items():
        if len(items) != 4:
            raise RuntimeError(f"{pg}: expected 4 rows, found {len(items)}")
        counts = Counter(r["prompt_type"] for r in items)
        if counts != Counter({"neutral": 1, "single_identity": 2, "intersectional": 1}):
            raise RuntimeError(f"{pg}: invalid prompt_type composition {dict(counts)}")
        neutral = next(r for r in items if r["prompt_type"] == "neutral")
        neutral_norm = norm_text(neutral["prompt"])
        tested_terms = []
        for row in items:
            if row["prompt_type"] != "neutral":
                tested_terms.extend(label_terms(row["identity_label"]))
        for term in sorted(set(tested_terms), key=len, reverse=True):
            if term and re.search(rf"\b{re.escape(term)}\b", neutral_norm):
                raise RuntimeError(f"{pg}: neutral prompt contains tested identity term '{term}'")

    norm_prompts = [norm_text(r["prompt"]) for r in rows]
    dup_norm = [k for k, v in Counter(norm_prompts).items() if v > 1]
    if dup_norm:
        raise RuntimeError(f"Duplicate normalized prompt text inside batch_9. Examples: {dup_norm[:3]}")

    disallowed_terms = ["nigger", "faggot", "kike", "chink", "spic", "retard", "tranny"]
    combined = "\n".join(r["prompt"] for r in rows).lower()
    for term in disallowed_terms:
        if re.search(rf"\b{re.escape(term)}\b", combined):
            raise RuntimeError(f"Disallowed slur found: {term}")
    for pattern in (r"\bkill all\b", r"\bexterminate\b", r"\binferior race\b"):
        if re.search(pattern, combined):
            raise RuntimeError(f"Explicit hate / forced toxic wording pattern found: {pattern}")

    for base in BASE_PROMPTS:
        existing_list = [norm_text(t) for t in load_prompts(base)]
        existing = set(existing_list)
        overlap = set(norm_prompts) & existing
        if overlap:
            raise RuntimeError(f"Exact prompt overlap with {base.name}. Example: {next(iter(overlap))}")
        for new_prompt in norm_prompts:
            for old_prompt in existing_list:
                if likely_near_duplicate(new_prompt, old_prompt):
                    raise RuntimeError(f"Near-duplicate with {base.name} detected (>=0.97)")


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
    validate_rows(rows)
    write_csv(rows)
    print(f"Wrote {OUT_CSV.as_posix()} with {len(rows)} rows")


if __name__ == "__main__":
    main()
