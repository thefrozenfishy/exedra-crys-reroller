import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

STAT_CATEGORIES = {
    "Max HP": [
        "Max HP +40",
        "Max HP +82",
        "Max HP +124",
        "Max HP +166",
        "Max HP +208",
        "Max HP +251",
        "Max HP +293",
        "Max HP +335",
        "Max HP +377",
        "Max HP +420",
    ],
    "ATK": [
        "ATK +6",
        "ATK +7",
        "ATK +8",
        "ATK +9",
        "ATK +10",
        "ATK +12",
        "ATK +15",
        "ATK +20",
        "ATK +30",
        "Increases ATK by 60.",
    ],
    "DEF": [
        "DEF +5",
        "DEF +6",
        "DEF +7",
        "DEF +8",
        "DEF +9",
        "DEF +10",
        "DEF +11",
        "DEF +15",
        "DEF +23",
        "DEF +45",
    ],
    "SPD": [
        "Increases SPD by 1.",
        "Increases SPD by 2.",
        "Increases SPD by 3.",
        "Increases SPD by 4.",
    ],
    "CRIT Rate": [
        "Increases critical rate by 0.5%.",
        "Increases critical rate by 1%.",
        "Increases critical rate by 1.5%.",
        "Increases critical rate by 2%.",
        "Increases critical rate by 2.5%.",
        "Increases critical rate by 3%.",
        "Increases critical rate by 3.5%.",
        "Increases critical rate by 4%.",
        "Increases critical rate by 4.5%.",
        "Increases critical rate by 5%.",
    ],
    "CRIT DMG": [
        "Increases critical DMG by 0.9%.",
        "Increases critical DMG by 1.8%.",
        "Increases critical DMG by 2.7%.",
        "Increases critical DMG by 3.6%.",
        "Increases critical DMG by 4.5%.",
        "Increases critical DMG by 5.4%.",
        "Increases critical DMG by 6.3%.",
        "Increases critical DMG by 7.2%.",
        "Increases critical DMG by 8.1%.",
        "Increases critical DMG by 10%.",
    ],
    "Break Effect": [
        "Increases break effect by 5%.",
        "Increases break effect by 5.2%.",
        "Increases break effect by 5.4%.",
        "Increases break effect by 5.6%.",
        "Increases break effect by 5.8%.",
        "Increases break effect by 6%.",
        "Increases break effect by 6.2%.",
        "Increases break effect by 6.4%.",
        "Increases break effect by 6.6%.",
        "Increases break effect by 12%.",
    ],
    "HP Recovery": [
        "Increases HP recovery amount by 1%.",
        "Increases HP recovery amount by 1.2%.",
        "Increases HP recovery amount by 1.5%.",
        "Increases HP recovery amount by 2%.",
        "Increases HP recovery amount by 2.5%.",
        "Increases HP recovery amount by 3%.",
        "Increases HP recovery amount by 3.5%.",
        "Increases HP recovery amount by 4%.",
        "Increases HP recovery amount by 4.5%.",
        "Increases HP recovery amount by 8%.",
        "Increases HP recovery amount by ...",
    ],
    "Debuff Hit Rate": [
        "Increases debuff hit rate by 1%.",
        "Increases debuff hit rate by 1.2%.",
        "Increases debuff hit rate by 1.5%.",
        "Increases debuff hit rate by 2%.",
        "Increases debuff hit rate by 2.5%.",
        "Increases debuff hit rate by 3%.",
        "Increases debuff hit rate by 3.5%.",
        "Increases debuff hit rate by 4%.",
        "Increases debuff hit rate by 4.5%.",
        "Increases debuff hit rate by 8%.",
    ],
    "Debuff RES": [
        "Increases debuff RES by 1%.",
        "Increases debuff RES by 1.2%.",
        "Increases debuff RES by 1.5%.",
        "Increases debuff RES by 2%.",
        "Increases debuff RES by 2.5%.",
        "Increases debuff RES by 3%.",
        "Increases debuff RES by 3.5%.",
        "Increases debuff RES by 4%.",
        "Increases debuff RES by 4.5%.",
        "Increases Debuff RES by 8%.",
    ],
}

VALUE_TO_CATEGORY = {}
for cat, vals in STAT_CATEGORIES.items():
    for v in vals:
        VALUE_TO_CATEGORY[v] = cat

NONE_STATE = frozenset()


def load_runs():
    runs = []
    seen = set()

    for zip_path in sorted(Path("reroll_logs").rglob("*.zip")):
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                if not info.filename.endswith(".jsonl"):
                    continue
                key = (Path(info.filename).name, info.file_size)
                if key in seen:
                    continue
                seen.add(key)
                with zf.open(info) as fh:
                    lines = fh.read().decode("utf-8").splitlines()
                    runs.append([json.loads(line) for line in lines if line.strip()])

    for f in sorted(Path("reroll_logs").rglob("*.jsonl")):
        size = f.stat().st_size
        key = (f.name, size)
        if key in seen:
            continue
        seen.add(key)
        with open(f, "r", encoding="utf-8") as fh:
            runs.append([json.loads(line) for line in fh if line.strip()])

    return runs


def is_duplicate(a, b):
    return a["s1"] == b["s1"] and a["s2"] == b["s2"] and a["s3"] == b["s3"]


def detect_locked(rows):
    locked = set()
    for s in ("s1", "s2", "s3"):
        if len(val_set := set(r[s] for r in rows)) == 1:
            locked.add(VALUE_TO_CATEGORY[val_set.pop()])
    return frozenset(locked)


def process_run(run):
    chain_req = 3
    no_dupes = [run[0]]
    for i in range(1, len(run)):
        if not is_duplicate(run[i - 1], run[i]):
            no_dupes.append(run[i])
    if len(no_dupes) <= chain_req:
        return []

    cleaned = []
    for i in range(chain_req):
        no_dupes[i]["locked"] = detect_locked(no_dupes[i : i + chain_req])
        cleaned.append(no_dupes[i])

    for i in range(chain_req, len(no_dupes)):
        no_dupes[i]["locked"] = detect_locked(no_dupes[i - chain_req : i])
        cleaned.append(no_dupes[i])

    return cleaned


def compute(runs):
    state_counts = defaultdict(int)
    state_value_counts = defaultdict(lambda: defaultdict(int))

    for run in runs:
        for roll in process_run(run):
            state = roll["locked"]
            state_counts[state] += 1
            for slot in ("s1", "s2", "s3"):
                val = roll[slot]
                if VALUE_TO_CATEGORY[val] not in state:
                    state_value_counts[state][val] += 1

    return state_counts, state_value_counts


def compute_base_probs(state_value_counts):
    none_counts = state_value_counts.get(NONE_STATE, {})
    total = sum(none_counts.values())
    if total == 0:
        raise ValueError(
            "No 0-locked (NONE) rolls found -- cannot estimate base probabilities."
        )
    return {val: cnt / total for val, cnt in none_counts.items()}


def compute_expected_rolls(base_probs):
    zero_locked = {}
    one_locked = {}
    two_locked = {}
    for val, p in base_probs.items():
        if p > 0:
            zero_locked[val] = 1.0 / (p * 3)
            one_locked[val] = 1.0 / (p * 2)
            two_locked[val] = 1.0 / (p * 1)
    return zero_locked, one_locked, two_locked


def _make_grid():
    categories = list(STAT_CATEGORIES.keys())
    grid = pd.DataFrame(None, index=range(1, 11), columns=categories, dtype=object)
    grid.index.name = "Tier"
    return grid


def _place(grid, val, value):
    cat = VALUE_TO_CATEGORY.get(val)
    if cat is None:
        return
    try:
        tier = STAT_CATEGORIES[cat].index(val) + 1
        grid.at[tier, cat] = value
    except ValueError:
        pass


def export_prob(base_probs):
    grid = _make_grid()
    for val, p in base_probs.items():
        _place(grid, val, round(p * 3, 4))
    grid.to_csv("csvs/prob.csv")


def export_total(state_value_counts):
    grid = _make_grid()
    for val, cnt in state_value_counts.get(NONE_STATE, {}).items():
        _place(grid, val, cnt)
    grid.to_csv("csvs/total.csv")


def export_expected_rolls(zero_locked, one_locked, two_locked):
    for label, lookup in [
        ("0_locked", zero_locked),
        ("1_locked", one_locked),
        ("2_locked", two_locked),
    ]:
        grid = _make_grid()
        for val, exp in lookup.items():
            _place(grid, val, round(exp, 1))
        fname = f"csvs/expected_{label}.csv"
        grid.to_csv(fname)


def export_summary(state_counts, state_value_counts, base_probs):
    none_counts = state_value_counts.get(NONE_STATE, {})
    rows = [
        ["Metric", "Value"],
        ["Total rolls", sum(state_counts.values())],
        ["NONE-state rolls", state_counts.get(NONE_STATE, 0)],
        ["NONE-state free slot observations", sum(none_counts.values())],
        [],
        ["LOCK STATE DISTRIBUTION", "Roll count"],
    ]
    for state, cnt in sorted(state_counts.items(), key=lambda x: -x[1]):
        rows.append([",".join(sorted(state)) if state else "NONE", cnt])

    rows += [[], ["BASE PROBABILITIES (NONE-state, per slot)", "p"]]
    for val, p in sorted(base_probs.items(), key=lambda x: -x[1]):
        rows.append([val, f"{p * 100:.4f}%"])

    pd.DataFrame(rows).to_csv("csvs/summary.csv", header=False, index=False)


def main():
    """Upload to https://docs.google.com/spreadsheets/d/1EfElTMXvO9lhbX_KAHAiAHZJ3bzjVye82a2VChNeln0"""
    os.makedirs("csvs", exist_ok=True)
    runs = load_runs()
    state_counts, state_value_counts = compute(runs)
    base_probs = compute_base_probs(state_value_counts)
    zero_locked, one_locked, two_locked = compute_expected_rolls(base_probs)

    export_prob(base_probs)
    export_total(state_value_counts)
    export_expected_rolls(zero_locked, one_locked, two_locked)
    export_summary(state_counts, state_value_counts, base_probs)


if __name__ == "__main__":
    main()
