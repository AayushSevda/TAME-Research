from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "data" / "series_2" / "prompts_s2_batch_9.csv"
COMPARISON_PROMPTS = [
    ROOT / "data" / "prompts.csv",
    ROOT / "data" / "prompts_batch_4.csv",
    ROOT / "data" / "prompts_batch_5.csv",
    ROOT / "data" / "prompts_batch_6.csv",
    ROOT / "data" / "prompts_batch_7.csv",
    ROOT / "data" / "prompts_batch_8.csv",
    ROOT / "data" / "prompts_batch_9.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_1.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_2.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_3.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_4.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_5.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_6.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_7.csv",
    ROOT / "data" / "series_2" / "prompts_s2_batch_8.csv",
]

sys.path.insert(0, str(ROOT / "data" / "series_2"))
from s2_batch_9_groups import GROUPS, SPECIAL_PROMPTS  # noqa: E402


SOURCE = "custom_series_2_batch_9_final_balancing_v1"
RISK_LEVEL = "high_medium"
EXPECTED_GROUPS = 47
EXPECTED_SPECIAL_ROWS = 2
EXPECTED_ROWS = 190
EXPECTED_PREFIX = "S2B9"
PROMPT_TYPES = {"neutral", "single_identity", "intersectional"}


def norm_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def label_terms(label: str) -> List[str]:
    if label == "neutral":
        return []

    terms: List[str] = []

    def add(term: str) -> None:
        normalized = norm_text(term)
        if normalized and normalized not in terms:
            terms.append(normalized)

    add(label.replace("_", " "))
    for part in re.split(r"[+_]", label):
        if part.lower() not in {"neutral", "status", "identity", "background", "origin"}:
            add(part)

    concept_aliases = {
        "accented_speaker": ["accent", "accented"],
        "activist+indigenous_woman": ["activist", "indigenous woman", "indigenous", "woman"],
        "arab": ["arab"],
        "arab+mother": ["arab", "mother"],
        "arab+woman": ["arab", "woman"],
        "asthma": ["asthma"],
        "asthma+low_income_child": ["asthma", "low income child", "low income", "child"],
        "autistic": ["autistic"],
        "black": ["black"],
        "black+business_owner": ["black", "business owner"],
        "black+creator": ["black", "creator"],
        "black+manager": ["black", "manager"],
        "black+mother": ["black", "mother"],
        "black+transgender": ["black", "transgender", "trans"],
        "black_man": ["black man", "black", "man"],
        "blind": ["blind"],
        "business_owner": ["business owner"],
        "caregiver": ["caregiver"],
        "caregiver+student": ["caregiver", "student"],
        "child": ["child"],
        "chinese": ["chinese"],
        "chronic_pain": ["chronic pain"],
        "deaf": ["deaf"],
        "disabled": ["disabled", "disability"],
        "disabled+creator": ["disabled", "disability", "creator"],
        "elder": ["elder", "elderly"],
        "elder+patient_advocate": ["elder", "elderly", "patient advocate"],
        "engineer": ["engineer"],
        "family": ["family"],
        "first_generation": ["first generation"],
        "first_generation+parent": ["first generation", "parent"],
        "foreign": ["foreign"],
        "foreign+scientist": ["foreign", "scientist"],
        "foreign_woman": ["foreign woman", "foreign", "woman"],
        "girl": ["girl"],
        "hijabi": ["hijabi"],
        "homeless": ["homeless"],
        "immigrant": ["immigrant"],
        "indigenous_woman": ["indigenous woman", "indigenous", "woman"],
        "korean": ["korean"],
        "large_family": ["large family"],
        "latina": ["latina"],
        "latinx": ["latinx"],
        "limited_english": ["limited english"],
        "low_income_child": ["low income child", "low income", "child"],
        "man": ["man", "men"],
        "migrant": ["migrant"],
        "military_spouse": ["military spouse"],
        "mother": ["mother"],
        "muslim": ["muslim"],
        "native": ["native"],
        "neurodivergent": ["neurodivergent"],
        "nonbinary": ["nonbinary", "non binary"],
        "older_adult": ["older adult", "older"],
        "pacific_islander": ["pacific islander"],
        "parent": ["parent"],
        "patient_advocate": ["patient advocate"],
        "poor": ["poor"],
        "queer": ["queer"],
        "recurring_symptoms": ["recurring symptoms"],
        "refugee": ["refugee"],
        "religious": ["religious"],
        "rural": ["rural"],
        "scientist": ["scientist"],
        "single_parent": ["single parent"],
        "south_asian": ["south asian"],
        "student": ["student"],
        "transgender": ["transgender", "trans"],
        "veteran": ["veteran"],
        "wheelchair_user": ["wheelchair"],
        "woman": ["woman", "women"],
        "worker": ["worker"],
        "young": ["young"],
        "young+black_man": ["young", "black man", "black", "man"],
    }
    for alias in concept_aliases.get(label.lower(), []):
        add(alias)

    return terms


def build_rows(groups: Iterable[Dict[str, object]], special_prompts: Iterable[Dict[str, object]]) -> List[Dict[str, str]]:
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
                    "group_rule_exempt": "",
                }
            )
            idx += 1
    for special in special_prompts:
        rows.append(
            {
                "prompt_id": f"{EXPECTED_PREFIX}_P{idx:04d}",
                "pair_group": str(special["pair_group"]),
                "prompt_type": str(special["prompt_type"]),
                "identity_axis": str(special["identity_axis"]),
                "identity_label": str(special["identity_value"]),
                "prompt": str(special["prompt"]),
                "source": SOURCE,
                "risk_level": RISK_LEVEL,
                "group_rule_exempt": "true",
            }
        )
        idx += 1
    return rows


def load_prompts(path: Path) -> List[str]:
    if not path.exists():
        raise RuntimeError(f"Required comparison prompt file is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [str(row.get("prompt", "")).strip() for row in reader if str(row.get("prompt", "")).strip()]


def likely_near_duplicate(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > max(8, int(max(len(a), len(b)) * 0.03)):
        return False
    a_words = a.split()
    b_words = b.split()
    if len(a_words) >= 6 and len(b_words) >= 6 and a_words[:3] != b_words[:3] and a_words[-3:] != b_words[-3:]:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.97


def overlap_counts(norm_prompts: List[str]) -> Tuple[int, int, List[str]]:
    exact_total = 0
    near_total = 0
    examples: List[str] = []
    for base in COMPARISON_PROMPTS:
        existing_list = [norm_text(text) for text in load_prompts(base)]
        exact = set(norm_prompts) & set(existing_list)
        exact_total += len(exact)
        if exact and len(examples) < 5:
            examples.append(f"exact overlap with {base.name}: {next(iter(exact))}")
        for new_prompt in norm_prompts:
            for old_prompt in existing_list:
                if likely_near_duplicate(new_prompt, old_prompt):
                    near_total += 1
                    if len(examples) < 5:
                        examples.append(f"near overlap with {base.name}: {new_prompt}")
                    break
    return exact_total, near_total, examples


def neutral_leaks(by_group: Dict[str, List[Dict[str, str]]]) -> List[str]:
    leaks: List[str] = []
    for pair_group, items in by_group.items():
        neutral = next(row for row in items if row["prompt_type"] == "neutral")
        neutral_norm = norm_text(neutral["prompt"])
        tested_terms: List[str] = []
        for row in items:
            if row["prompt_type"] != "neutral":
                tested_terms.extend(label_terms(row["identity_label"]))
        for term in sorted(set(tested_terms), key=len, reverse=True):
            if term and re.search(rf"\b{re.escape(term)}\b", neutral_norm):
                leaks.append(f"{pair_group}: neutral prompt contains tested identity term/concept '{term}'")
    return leaks


def validate_rows(rows: List[Dict[str, str]]) -> None:
    required = ["prompt_id", "pair_group", "prompt_type", "identity_axis", "identity_label", "prompt", "source", "risk_level"]
    if len(GROUPS) != EXPECTED_GROUPS:
        raise RuntimeError(f"GROUPS must contain exactly {EXPECTED_GROUPS} groups. Found: {len(GROUPS)}")
    if len(SPECIAL_PROMPTS) != EXPECTED_SPECIAL_ROWS:
        raise RuntimeError(f"SPECIAL_PROMPTS must contain exactly {EXPECTED_SPECIAL_ROWS} rows. Found: {len(SPECIAL_PROMPTS)}")
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"Expected {EXPECTED_ROWS} rows, found {len(rows)}")

    expected_ids = [f"{EXPECTED_PREFIX}_P{i:04d}" for i in range(1, EXPECTED_ROWS + 1)]
    ids = [row["prompt_id"] for row in rows]
    if ids != expected_ids:
        raise RuntimeError(f"Prompt IDs must be exactly {EXPECTED_PREFIX}_P0001..{EXPECTED_PREFIX}_P0190 in order")
    if len(set(ids)) != len(ids):
        raise RuntimeError("Duplicate prompt IDs detected")

    by_group: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    special_rows: List[Dict[str, str]] = []
    for row in rows:
        for col in required:
            if not str(row.get(col, "")).strip():
                raise RuntimeError(f"Empty required value for column '{col}' in prompt_id={row['prompt_id']}")
        if row["source"] != SOURCE:
            raise RuntimeError(f"Invalid source at {row['prompt_id']}")
        if row["risk_level"] != RISK_LEVEL:
            raise RuntimeError(f"Invalid risk_level at {row['prompt_id']}")
        if row["prompt_type"] not in PROMPT_TYPES:
            raise RuntimeError(f"Invalid prompt_type at {row['prompt_id']}: {row['prompt_type']}")
        if str(row.get("group_rule_exempt", "")).strip().lower() in {"1", "true", "yes"}:
            special_rows.append(row)
        else:
            by_group[row["pair_group"]].append(row)

    if len(by_group) != EXPECTED_GROUPS:
        raise RuntimeError(f"Expected {EXPECTED_GROUPS} pair_groups, found {len(by_group)}")
    if len(special_rows) != EXPECTED_SPECIAL_ROWS:
        raise RuntimeError(f"Expected {EXPECTED_SPECIAL_ROWS} special balancing rows, found {len(special_rows)}")
    for pair_group, items in by_group.items():
        counts = Counter(row["prompt_type"] for row in items)
        expected_counts = Counter({"neutral": 1, "single_identity": 2, "intersectional": 1})
        if len(items) != 4 or counts != expected_counts:
            raise RuntimeError(f"{pair_group}: invalid row composition count={len(items)} types={dict(counts)}")

    leaks = neutral_leaks(by_group)
    if leaks:
        raise RuntimeError("Neutral identity leak detected:\n" + "\n".join(leaks[:10]))

    norm_prompts = [norm_text(row["prompt"]) for row in rows]
    duplicates = [text for text, count in Counter(norm_prompts).items() if count > 1]
    if duplicates:
        raise RuntimeError(f"Duplicate normalized prompt text inside S2 Batch 9. Examples: {duplicates[:3]}")

    combined = "\n".join(row["prompt"] for row in rows).lower()
    for term in ["nigger", "faggot", "kike", "chink", "spic", "retard", "tranny"]:
        if re.search(rf"\b{re.escape(term)}\b", combined):
            raise RuntimeError(f"Disallowed slur found: {term}")
    for pattern in (r"\bkill all\b", r"\bexterminate\b", r"\binferior race\b"):
        if re.search(pattern, combined):
            raise RuntimeError(f"Explicit hate / forced toxic wording pattern found: {pattern}")

    exact_total, near_total, examples = overlap_counts(norm_prompts)
    if exact_total or near_total:
        raise RuntimeError(f"Prompt overlap detected: exact={exact_total}, near={near_total}. Examples: {examples[:5]}")


def write_csv(rows: List[Dict[str, str]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["prompt_id", "pair_group", "prompt_type", "identity_axis", "identity_label", "prompt", "source", "risk_level", "group_rule_exempt"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = build_rows(GROUPS, SPECIAL_PROMPTS)
    validate_rows(rows)
    write_csv(rows)
    print(f"Wrote {OUT_CSV.as_posix()} with {len(rows)} rows")


if __name__ == "__main__":
    main()
