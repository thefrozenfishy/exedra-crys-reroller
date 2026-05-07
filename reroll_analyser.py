import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

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

ALL_STATS = set()

VALUE_TO_CATEGORY = {}
VALUE_TO_TIER = {}

for category, values in STAT_CATEGORIES.items():
    for tier, value in enumerate(values, start=1):
        ALL_STATS.add(value)
        VALUE_TO_CATEGORY[value] = category
        VALUE_TO_TIER[value] = tier


def pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def categorize(value: str) -> str:
    return VALUE_TO_CATEGORY[value]


def load_jsonl_files(logs_dir: str):
    rolls = []

    files = sorted(Path(logs_dir).glob("*.jsonl"))

    if not files:
        print(f"No .jsonl files found in {logs_dir}")
        sys.exit(1)

    for file in files:
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                try:
                    rolls.append(json.loads(line))
                except Exception as e:
                    print(f"Skipping invalid line in {file}: {e}")

    return rolls


def is_duplicate(a, b):
    return a["s1"] == b["s1"] and a["s2"] == b["s2"] and a["s3"] == b["s3"]


def detect_locked_slots(prev2, prev1, curr):
    locked = []

    for slot in ("s1", "s2", "s3"):
        if prev2[slot] == prev1[slot] == curr[slot]:
            locked.append(slot)

    return locked


def clean_rolls(raw_rolls):
    cleaned = []

    for roll in raw_rolls:
        if cleaned and is_duplicate(cleaned[-1], roll):
            continue

        roll = dict(roll)

        if len(cleaned) < 2:
            roll["locked_slots"] = []
            roll["free_slots"] = ["s1", "s2", "s3"]
        else:
            locked_slots = detect_locked_slots(
                cleaned[-2],
                cleaned[-1],
                roll,
            )

            free_slots = [s for s in ("s1", "s2", "s3") if s not in locked_slots]

            roll["locked_slots"] = locked_slots
            roll["free_slots"] = free_slots

        cleaned.append(roll)

    return cleaned


class SlotStats:
    def __init__(self):
        self.total = 0

        self.value_counts = defaultdict(int)
        self.category_counts = defaultdict(int)
        self.tier_counts = defaultdict(int)

    def record(self, value):
        cat = categorize(value)
        tier = VALUE_TO_TIER[value]

        self.total += 1

        self.value_counts[value] += 1
        self.category_counts[cat] += 1
        self.tier_counts[(cat, tier)] += 1


def compute_stats(cleaned_rolls):
    slot_stats = {
        "s1": SlotStats(),
        "s2": SlotStats(),
        "s3": SlotStats(),
    }

    global_value_counts = defaultdict(int)
    global_category_counts = defaultdict(int)

    total_free_slots = 0

    for roll in cleaned_rolls:
        for slot in roll["free_slots"]:
            value = roll[slot]
            cat = categorize(value)

            slot_stats[slot].record(value)

            global_value_counts[value] += 1
            global_category_counts[cat] += 1

            total_free_slots += 1

    return (
        slot_stats,
        global_value_counts,
        global_category_counts,
        total_free_slots,
    )


def print_report(
    cleaned_rolls,
    slot_stats,
    global_value_counts,
    global_category_counts,
    total_free_slots,
):
    print("=" * 70)
    print("REROLL ANALYSIS")
    print("=" * 70)

    print(f"Valid rolls: {len(cleaned_rolls)}")
    print(f"Total free slots analyzed: {total_free_slots}")

    locked_rolls = sum(1 for r in cleaned_rolls if r["locked_slots"])

    print(f"Rolls with locked slots: {locked_rolls}")

    print("\nGLOBAL CATEGORY DISTRIBUTION")
    print("-" * 70)

    for cat, count in sorted(
        global_category_counts.items(),
        key=lambda x: -x[1],
    ):
        print(f"{pct(count / total_free_slots):>8} " f"({count:>5}x) " f"{cat}")

    print("\nGLOBAL EXACT VALUE DISTRIBUTION")
    print("-" * 70)

    for value, count in sorted(
        global_value_counts.items(),
        key=lambda x: -x[1],
    ):
        cat = categorize(value)

        print(
            f"{pct(count / total_free_slots):>8} " f"({count:>5}x) " f"[{cat}] {value}"
        )

    print("\nTIER DISTRIBUTION WITHIN CATEGORY")
    print("-" * 70)

    for category, values in STAT_CATEGORIES.items():
        total_cat = global_category_counts.get(category, 0)

        if total_cat == 0:
            continue

        print(f"\n{category}")

        for value in values:
            count = global_value_counts.get(value, 0)

            if count == 0:
                continue

            print(f"    {pct(count / total_cat):>8} " f"({count:>5}x) " f"{value}")

    print("\nPER SLOT DISTRIBUTION")
    print("-" * 70)

    for slot in ("s1", "s2", "s3"):
        stats = slot_stats[slot]

        print(f"\n{slot} ({stats.total} free observations)")

        for cat, count in sorted(
            stats.category_counts.items(),
            key=lambda x: -x[1],
        ):
            print(f"    {pct(count / stats.total):>8} " f"({count:>5}x) " f"{cat}")


def print_search(query, global_value_counts, global_category_counts, total):
    q = query.lower()

    print("\nSEARCH RESULTS")
    print("-" * 70)

    found = False

    for cat, count in global_category_counts.items():
        if q in cat.lower():
            found = True

            print(
                f"[CATEGORY] {cat}\n"
                f"    Probability: {pct(count / total)}\n"
                f"    Occurrences: {count}"
            )

    for value, count in global_value_counts.items():
        if q in value.lower():
            found = True

            print(
                f"[VALUE] {value}\n"
                f"    Probability: {pct(count / total)}\n"
                f"    Occurrences: {count}"
            )

    if not found:
        print("No matches found.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", default="reroll_logs")
    parser.add_argument("--search", default=None)
    args = parser.parse_args()

    raw_rolls = load_jsonl_files(args.logs_dir)

    print(f"Loaded {len(raw_rolls)} raw rolls.")

    cleaned_rolls = clean_rolls(raw_rolls)

    print(f"Removed " f"{len(raw_rolls) - len(cleaned_rolls)} duplicate rolls.")

    (
        slot_stats,
        global_value_counts,
        global_category_counts,
        total_free_slots,
    ) = compute_stats(cleaned_rolls)

    print_report(
        cleaned_rolls,
        slot_stats,
        global_value_counts,
        global_category_counts,
        total_free_slots,
    )

    if args.search:
        print_search(
            args.search,
            global_value_counts,
            global_category_counts,
            total_free_slots,
        )


if __name__ == "__main__":
    main()
