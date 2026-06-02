from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
SERIES_DIR = ROOT / "data" / "series_2"
FINAL_CSV = SERIES_DIR / "prompts_s2_batch_10.csv"
EXTENSION_CSV = SERIES_DIR / "prompts_s2_batch_10_extension.csv"
GROUPS_FILE = SERIES_DIR / "s2_batch_10_groups.py"

sys.path.insert(0, str(SERIES_DIR))
from s2_batch_10_extension_groups import GROUPS_EXTENSION  # noqa: E402


SOURCE = "custom_series_2_batch_10_extension_v1"
RISK_LEVEL = "high_medium"
EXPECTED_EXTENSION_GROUPS = 95
EXPECTED_EXTENSION_ROWS = 380
EXPECTED_FINAL_ROWS = 570
PROMPT_PREFIX = "S2B10"
FIELDNAMES = [
    "prompt_id",
    "pair_group",
    "prompt_type",
    "identity_axis",
    "identity_label",
    "prompt",
    "source",
    "risk_level",
    "group_rule_exempt",
]


def norm_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def build_extension_rows() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    idx = 191
    for group in GROUPS_EXTENSION:
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
                    "prompt_id": f"{PROMPT_PREFIX}_P{idx:04d}",
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
    return rows


def validate_extension_rows(rows: List[Dict[str, str]]) -> None:
    if len(GROUPS_EXTENSION) != EXPECTED_EXTENSION_GROUPS:
        raise RuntimeError(f"Expected {EXPECTED_EXTENSION_GROUPS} extension groups, found {len(GROUPS_EXTENSION)}")
    if len(rows) != EXPECTED_EXTENSION_ROWS:
        raise RuntimeError(f"Expected {EXPECTED_EXTENSION_ROWS} extension rows, found {len(rows)}")

    expected_ids = [f"{PROMPT_PREFIX}_P{i:04d}" for i in range(191, 571)]
    ids = [row["prompt_id"] for row in rows]
    if ids != expected_ids:
        raise RuntimeError("Extension prompt IDs must be exactly S2B10_P0191..S2B10_P0570 in order")

    by_group: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        for field in FIELDNAMES:
            if field != "group_rule_exempt" and not str(row.get(field, "")).strip():
                raise RuntimeError(f"Empty {field} in {row.get('prompt_id')}")
        by_group[row["pair_group"]].append(row)

    if len(by_group) != EXPECTED_EXTENSION_GROUPS:
        raise RuntimeError(f"Expected {EXPECTED_EXTENSION_GROUPS} extension pair_groups, found {len(by_group)}")

    expected_counts = Counter({"neutral": 1, "single_identity": 2, "intersectional": 1})
    for pair_group, items in by_group.items():
        counts = Counter(row["prompt_type"] for row in items)
        if len(items) != 4 or counts != expected_counts:
            raise RuntimeError(f"{pair_group}: invalid extension composition {dict(counts)}")

    normalized = [norm_text(row["prompt"]) for row in rows]
    duplicates = [text for text, count in Counter(normalized).items() if count > 1]
    if duplicates:
        raise RuntimeError(f"Normalized duplicate prompts in extension. Examples: {duplicates[:3]}")

    combined = "\n".join(row["prompt"] for row in rows).lower()
    for term in ["nigger", "faggot", "kike", "chink", "spic", "retard", "tranny"]:
        if re.search(rf"\b{re.escape(term)}\b", combined):
            raise RuntimeError(f"Disallowed slur found in extension: {term}")
    for pattern in (r"\bkill all\b", r"\bexterminate\b", r"\binferior race\b"):
        if re.search(pattern, combined):
            raise RuntimeError(f"Explicit hate / forced toxic wording pattern found: {pattern}")


def validate_existing_prefix(rows: List[Dict[str, str]]) -> None:
    if len(rows) not in {190, EXPECTED_FINAL_ROWS}:
        raise RuntimeError(f"Expected existing S2B10 CSV to have 190 or 570 rows, found {len(rows)}")
    expected_prefix_ids = [f"{PROMPT_PREFIX}_P{i:04d}" for i in range(1, 191)]
    prefix_ids = [row["prompt_id"] for row in rows[:190]]
    if prefix_ids != expected_prefix_ids:
        raise RuntimeError("Existing S2B10 prefix IDs are not exactly S2B10_P0001..S2B10_P0190")


def append_extension_to_final(extension_rows: List[Dict[str, str]]) -> None:
    existing_rows = read_rows(FINAL_CSV)
    validate_existing_prefix(existing_rows)

    if len(existing_rows) == EXPECTED_FINAL_ROWS:
        existing_extension_ids = [row["prompt_id"] for row in existing_rows[190:]]
        expected_extension_ids = [row["prompt_id"] for row in extension_rows]
        if existing_extension_ids != expected_extension_ids:
            raise RuntimeError("Final CSV already has 570 rows, but extension IDs do not match expected IDs")
        print(f"{FINAL_CSV.as_posix()} already contains the extension; leaving it unchanged")
        return

    with FINAL_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerows(extension_rows)


def append_group_reference() -> None:
    marker = "GROUPS.extend(GROUPS_EXTENSION)"
    text = GROUPS_FILE.read_text(encoding="utf-8")
    if marker in text:
        return
    addition = (
        "\n\n# Add-only S2 Batch 10 extension groups. Existing group definitions above remain unchanged.\n"
        "from s2_batch_10_extension_groups import GROUPS_EXTENSION  # noqa: E402\n\n"
        "GROUPS.extend(GROUPS_EXTENSION)\n"
    )
    GROUPS_FILE.write_text(text.rstrip() + addition, encoding="utf-8")


def main() -> None:
    extension_rows = build_extension_rows()
    validate_extension_rows(extension_rows)
    write_rows(EXTENSION_CSV, extension_rows)
    append_extension_to_final(extension_rows)
    append_group_reference()

    final_rows = read_rows(FINAL_CSV)
    if len(final_rows) != EXPECTED_FINAL_ROWS:
        raise RuntimeError(f"Expected final S2B10 CSV to have {EXPECTED_FINAL_ROWS} rows, found {len(final_rows)}")
    expected_ids = [f"{PROMPT_PREFIX}_P{i:04d}" for i in range(1, EXPECTED_FINAL_ROWS + 1)]
    if [row["prompt_id"] for row in final_rows] != expected_ids:
        raise RuntimeError("Final S2B10 IDs are not exactly S2B10_P0001..S2B10_P0570")

    print(f"Wrote {EXTENSION_CSV.as_posix()} with {len(extension_rows)} rows")
    print(f"Extended {FINAL_CSV.as_posix()} to {len(final_rows)} rows")


if __name__ == "__main__":
    main()
