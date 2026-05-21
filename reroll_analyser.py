import json
import os
import random
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

SLOTS = 3
CHAIN_REQ = 3
SIMULATIONS = 200_000
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
        "Increases HP recovery amount by 8%.",
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
NONE_STATE = frozenset()
NONE_STATE = frozenset()
VALUE_TO_CATEGORY = {}
for category, values in STAT_CATEGORIES.items():
    for value in values:
        VALUE_TO_CATEGORY[value] = category


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


def process_run(run):
    no_dupes = [run[0]]
    for i in range(1, len(run)):
        if not is_duplicate(run[i - 1], run[i]):
            no_dupes.append(run[i])
    if len(no_dupes) <= CHAIN_REQ:
        return []

    locked_indices = {slot: set() for slot in ("s1", "s2", "s3")}

    for slot in ("s1", "s2", "s3"):
        if run[0][slot] == run[1][slot]:
            # 0 follows a forward looking rule so just do it manually
            locked_indices[slot].add(0)

        i = 0
        while i < len(no_dupes):
            j = i + 1
            while j < len(no_dupes) and no_dupes[j][slot] == no_dupes[i][slot]:
                j += 1
            run_length = j - i
            if run_length >= CHAIN_REQ:
                for k in range(i + 1, j):
                    locked_indices[slot].add(k)
            i = j

    cleaned = []
    for i, row in enumerate(no_dupes):
        locked = frozenset(
            VALUE_TO_CATEGORY[row[slot]]
            for slot in ("s1", "s2", "s3")
            if i in locked_indices[slot]
        )
        row["locked"] = locked
        cleaned.append(row)

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


def aggregate_unlocked_counts(state_value_counts):
    aggregated = defaultdict(int)
    for counts in state_value_counts.values():
        for val, count in counts.items():
            aggregated[val] += count
    return aggregated


def aggregate_unlocked_counts_by_locked_slots(state_value_counts, max_locked_slots):
    aggregated = defaultdict(int)
    for state, counts in state_value_counts.items():
        if len(state) <= max_locked_slots:
            for val, count in counts.items():
                aggregated[val] += count
    return aggregated


def estimate_category_weights(state_value_counts, max_locked_slots=None):
    print("Estimating weights for", max_locked_slots)
    if max_locked_slots is None:
        unlocked_counts = aggregate_unlocked_counts(state_value_counts)
    else:
        unlocked_counts = aggregate_unlocked_counts_by_locked_slots(
            state_value_counts, max_locked_slots
        )
    category_counts = defaultdict(int)
    for val, count in unlocked_counts.items():
        category_counts[VALUE_TO_CATEGORY[val]] += count
    total = sum(category_counts.values())
    return {category: count / total for category, count in category_counts.items()}


def build_tier_weights(state_value_counts, max_locked_slots=None):
    print("Building weights for", max_locked_slots)
    if max_locked_slots is None:
        unlocked_counts = aggregate_unlocked_counts(state_value_counts)
    else:
        unlocked_counts = aggregate_unlocked_counts_by_locked_slots(
            state_value_counts, max_locked_slots
        )
    grouped = defaultdict(lambda: defaultdict(int))
    for val, count in unlocked_counts.items():
        grouped[VALUE_TO_CATEGORY[val]][val] += count
    result = {}
    for category, vals in grouped.items():
        total = sum(vals.values())
        result[category] = {val: cnt / total for val, cnt in vals.items()}
    return result


def weighted_choice(weight_map):
    total = sum(weight_map.values())
    r = random.uniform(0, total)
    cumulative = 0
    for key, value in weight_map.items():
        cumulative += value
        if r <= cumulative:
            return key
    return list(weight_map)[-1]


def generate_roll(category_weights, tier_weights, locked=None):
    if locked is None:
        locked = []

    result = list(locked)
    used_categories = {VALUE_TO_CATEGORY[v] for v in result}
    available_categories = [c for c in category_weights if c not in used_categories]
    remaining = SLOTS - len(result)
    for _ in range(remaining):
        cat_weights = {c: category_weights[c] for c in available_categories}
        category = weighted_choice(cat_weights)
        value = weighted_choice(tier_weights[category])
        result.append(value)
        available_categories.remove(category)
    return result


def estimate_probability(
    target_values, category_weights, tier_weights, locked=None, simulations=SIMULATIONS
):
    hits = 0
    target_values = set(target_values)
    for _ in range(simulations):
        roll = generate_roll(category_weights, tier_weights, locked)
        if target_values.issubset(set(roll)):
            hits += 1
    return hits / simulations


def expected_rolls(
    target_values, category_weights, tier_weights, locked=None, simulations=SIMULATIONS
):
    p = estimate_probability(
        target_values, category_weights, tier_weights, locked, simulations
    )
    if p <= 0:
        return float("inf")
    return 1 / p


def export_total(state_value_counts, filename="csvs/total.csv", max_locked_slots=None):
    print("Exporting", filename)
    categories = list(STAT_CATEGORIES.keys())
    grid = pd.DataFrame(
        None,
        index=range(1, 11),
        columns=categories,
        dtype=object,
    )
    grid.index.name = "Tier"
    if max_locked_slots is None:
        unlocked_counts = aggregate_unlocked_counts(state_value_counts)
    else:
        unlocked_counts = aggregate_unlocked_counts_by_locked_slots(
            state_value_counts, max_locked_slots
        )
    for val, cnt in unlocked_counts.items():
        category = VALUE_TO_CATEGORY[val]
        try:
            tier = STAT_CATEGORIES[category].index(val) + 1
            grid.at[tier, category] = cnt
        except ValueError:
            pass
    grid.to_csv(filename, sep=";")


def export_expected_rolls(
    category_weights, tier_weights, filename="csvs/expected_rolls.csv"
):
    print("Exporting", filename)
    categories = list(STAT_CATEGORIES.keys())
    grid = pd.DataFrame(None, index=range(1, 11), columns=categories, dtype=object)
    grid.index.name = "Tier"
    for category, values in STAT_CATEGORIES.items():
        for val in values:
            probability = estimate_probability(
                [val], category_weights, tier_weights, simulations=50_000
            )
            exp = float("inf") if probability <= 0 else 1 / probability
            try:
                tier = values.index(val) + 1
                grid.at[tier, category] = round(exp, 1)
            except ValueError:
                pass
    grid.to_csv(filename, sep=";")


def export_summary(
    total_rolls: int, category_weights, tier_weights, filename="csvs/summary.csv"
):
    print("Exporting", filename)
    rows = [
        ["Metric", "Value"],
        ["Total rolls", total_rolls],
        [],
        ["Category", "Estimated Weight"],
    ]
    for category in sorted(category_weights.keys()):
        rows.append([category, f"{category_weights[category] * 100:.4f}%"])
    rows += [[], ["Tier Distributions", ""]]
    for category in sorted(tier_weights.keys()):
        rows.append([category, ""])
        for val, p in sorted(tier_weights[category].items(), key=lambda x: -x[1]):
            rows.append([val, f"{p * 100:.4f}%"])
    pd.DataFrame(rows).to_csv(filename, header=False, index=False, sep=";")


def main():
    """
    Upload to https://docs.google.com/spreadsheets/d/1EfElTMXvO9lhbX_KAHAiAHZJ3bzjVye82a2VChNeln0
    Use the 0s so I know I didnt fuck up any maths..?
    """
    os.makedirs("csvs", exist_ok=True)
    runs = load_runs()
    state_counts, state_value_counts = compute(runs)

    # Non-locked states, so these are guaranteed to be correct
    # 0 locked
    category_weights_0 = estimate_category_weights(
        state_value_counts, max_locked_slots=0
    )
    tier_weights_0 = build_tier_weights(state_value_counts, max_locked_slots=0)

    export_total(
        state_value_counts, filename="csvs/total_0_locked.csv", max_locked_slots=0
    )
    export_expected_rolls(
        category_weights_0, tier_weights_0, filename="csvs/expected_rolls_0_locked.csv"
    )
    export_summary(
        state_counts[frozenset()],
        category_weights_0,
        tier_weights_0,
        filename="csvs/summary_0.csv",
    )

    # Adding in locked states I _think_ is fine but unsure
    # 1 locked
    category_weights_1 = estimate_category_weights(
        state_value_counts, max_locked_slots=1
    )
    tier_weights_1 = build_tier_weights(state_value_counts, max_locked_slots=1)

    export_total(
        state_value_counts, filename="csvs/total_1_locked.csv", max_locked_slots=1
    )
    export_expected_rolls(
        category_weights_1,
        tier_weights_1,
        filename="csvs/expected_rolls_1_locked.csv",
    )
    total_rolls_1 = sum(cnt for state, cnt in state_counts.items() if len(state) <= 1)
    export_summary(
        total_rolls_1,
        category_weights_1,
        tier_weights_1,
        filename="csvs/summary_1.csv",
    )

    # 2 locked
    category_weights = estimate_category_weights(state_value_counts)
    tier_weights = build_tier_weights(state_value_counts)

    export_total(state_value_counts, filename="csvs/total_2_locked.csv")
    export_expected_rolls(
        category_weights, tier_weights, filename="csvs/expected_rolls_2_locked.csv"
    )
    export_summary(
        sum(state_counts.values()),
        category_weights,
        tier_weights,
        filename="csvs/summary_2.csv",
    )


if __name__ == "__main__":
    main()
