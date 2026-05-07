"""
analyze_rerolls.py
------------------
Reads all .jsonl files from a "reroll_logs" folder and calculates
drop probabilities, accounting for:
  1. Duplicate rolls (all 3 slots identical to previous) → discarded
  2. Locked slots (a slot unchanged from previous valid roll) →
     that slot's stat CATEGORY is excluded from the free pool,
     so probabilities for free slots are computed over a narrower pool
  3. Per-slot and cross-slot ("any slot") probabilities

Stat category normalization:
  "ATK +7", "ATK +12", "Increases ATK by 60" → all treated as category "ATK"
  A locked ATK value means no ATK variant can appear on free slots.

Usage:
    python analyze_rerolls.py [--logs-dir reroll_logs] [--search "critical rate"]
"""

import json
import os
import re
import sys
import argparse
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Stat category normalization
# ---------------------------------------------------------------------------

# Patterns map a regex → canonical category name.
# Order matters: more specific patterns first.
CATEGORY_PATTERNS = [
    (re.compile(r"increases?\s+spd\b|spd\s*[\+\-]", re.I), "SPD"),
    (re.compile(r"increases?\s+atk\b|atk\s*[\+\-]", re.I), "ATK"),
    (re.compile(r"increases?\s+def\b|def\s*[\+\-]", re.I), "DEF"),
    (re.compile(r"max\s+hp\b|increases?\s+hp\b(?!.*recovery)", re.I), "Max HP"),
    (re.compile(r"increases?\s+hp\s+recovery", re.I), "HP Recovery"),
    (re.compile(r"increases?\s+critical\s+dmg|crit(?:ical)?\s+dmg", re.I), "CRIT DMG"),
    (
        re.compile(r"increases?\s+critical\s+rate|crit(?:ical)?\s+rate", re.I),
        "CRIT Rate",
    ),
    (re.compile(r"increases?\s+break\s+effect", re.I), "Break Effect"),
    (re.compile(r"increases?\s+debuff\s+hit\s+rate", re.I), "Debuff Hit Rate"),
    (re.compile(r"increases?\s+debuff\s+res|debuff\s+res", re.I), "Debuff RES"),
]


def categorize(stat: str) -> str:
    """Return the canonical stat category for a stat string."""
    for pattern, category in CATEGORY_PATTERNS:
        if pattern.search(stat):
            return category
    return stat.strip()  # fallback: use raw value


# ---------------------------------------------------------------------------
# Data loading and cleaning
# ---------------------------------------------------------------------------


def load_jsonl_files(logs_dir: str) -> list[dict]:
    """Load all .jsonl files from logs_dir, returning a flat sorted list of rolls."""
    rolls = []
    logs_path = Path(logs_dir)
    if not logs_path.exists():
        print(f"ERROR: Directory '{logs_dir}' not found.", file=sys.stderr)
        sys.exit(1)

    files = sorted(logs_path.glob("*.jsonl"))
    if not files:
        print(f"ERROR: No .jsonl files found in '{logs_dir}'.", file=sys.stderr)
        sys.exit(1)

    for filepath in files:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rolls.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        print(
                            f"Warning: skipping bad line in {filepath}: {e}",
                            file=sys.stderr,
                        )

    # Sort by roll number then time, in case files overlap
    rolls.sort(key=lambda r: (r.get("roll", 0), r.get("time", "")))
    return rolls


def is_duplicate(a: dict, b: dict) -> bool:
    """True if all three slots of b are identical to a (failed reroll)."""
    return a["s1"] == b["s1"] and a["s2"] == b["s2"] and a["s3"] == b["s3"]


def detect_locks(prev2: dict, prev1: dict, curr: dict) -> set[str]:
    """
    Return the set of locked stat CATEGORIES for curr.
    A slot is only considered locked if its value is identical across
    all three of prev2, prev1, and curr (3+ consecutive repeats).
    A single repeat (prev1 == curr but prev2 != curr) is treated as RNG coincidence.
    """
    locked_categories = set()
    for slot in ("s1", "s2", "s3"):
        if prev2[slot] == prev1[slot] == curr[slot]:
            locked_categories.add(categorize(curr[slot]))
    return locked_categories


def clean_rolls(raw_rolls: list[dict]) -> list[dict]:
    """
    Remove duplicate rolls and annotate each roll with:
      - 'locked_categories': set of stat categories that are locked
      - 'free_slots': list of slot names that are freely rolled
      - 'locked_slots': list of slot names that are locked

    Lock detection requires 3+ consecutive valid rolls with the same
    slot value — a single repeat is treated as RNG coincidence.
    """
    cleaned = []

    for roll in raw_rolls:
        if cleaned and is_duplicate(cleaned[-1], roll):
            continue  # discard failed reroll

        roll = dict(roll)  # don't mutate original

        if len(cleaned) < 2:
            # Not enough history to confirm a lock
            roll["locked_categories"] = set()
            roll["locked_slots"] = []
            roll["free_slots"] = ["s1", "s2", "s3"]
        else:
            locked_cats = detect_locks(cleaned[-2], cleaned[-1], roll)
            locked_slots = [
                s for s in ("s1", "s2", "s3") if categorize(roll[s]) in locked_cats
            ]
            free_slots = [s for s in ("s1", "s2", "s3") if s not in locked_slots]
            roll["locked_categories"] = locked_cats
            roll["locked_slots"] = locked_slots
            roll["free_slots"] = free_slots

        cleaned.append(roll)

    return cleaned


# ---------------------------------------------------------------------------
# Probability calculation
# ---------------------------------------------------------------------------


class SlotStats:
    """Accumulates counts for a single slot over valid (free) observations."""

    def __init__(self):
        self.value_counts: dict[str, int] = defaultdict(int)
        self.category_counts: dict[str, int] = defaultdict(int)
        self.total_free: int = 0

    def record(self, value: str):
        self.value_counts[value] += 1
        self.category_counts[categorize(value)] += 1
        self.total_free += 1

    def prob_value(self, value: str) -> float:
        if self.total_free == 0:
            return 0.0
        return self.value_counts.get(value, 0) / self.total_free

    def prob_category(self, category: str) -> float:
        if self.total_free == 0:
            return 0.0
        return self.category_counts.get(category, 0) / self.total_free


def compute_stats(cleaned_rolls: list[dict]) -> tuple[dict, dict, dict, dict, int]:
    """
    Returns:
      per_slot_stats: {slot: SlotStats} — per-slot value/category probabilities
                      denominated by rolls where that slot was FREE
      cross_slot_value_counts: {value: int} — how many rolls had this value on any free slot
      cross_slot_cat_counts: {category: int} — same but by category
      total_free_rolls: total count of rolls (for cross-slot denominator: each roll counts once)
      n_rolls: total valid rolls
    """
    per_slot_stats = {s: SlotStats() for s in ("s1", "s2", "s3")}
    cross_value: dict[str, int] = defaultdict(int)
    cross_cat: dict[str, int] = defaultdict(int)
    n_rolls = len(cleaned_rolls)

    for roll in cleaned_rolls:
        # Track which values appeared on free slots this roll (deduplicate for cross-slot)
        seen_values_this_roll = set()
        seen_cats_this_roll = set()

        for slot in roll["free_slots"]:
            value = roll[slot]
            cat = categorize(value)
            per_slot_stats[slot].record(value)

            if value not in seen_values_this_roll:
                cross_value[value] += 1
                seen_values_this_roll.add(value)
            if cat not in seen_cats_this_roll:
                cross_cat[cat] += 1
                seen_cats_this_roll.add(cat)

    return per_slot_stats, cross_value, cross_cat, n_rolls


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def pct(p: float) -> str:
    return f"{p * 100:.2f}%"


def print_full_report(cleaned_rolls, per_slot_stats, cross_value, cross_cat, n_rolls):
    print("=" * 70)
    print(f"REROLL ANALYSIS REPORT")
    print(f"Total valid rolls: {n_rolls}")
    locked_rolls = sum(1 for r in cleaned_rolls if r["locked_slots"])
    print(f"Rolls with ≥1 locked slot: {locked_rolls}")
    print("=" * 70)

    for slot in ("s1", "s2", "s3"):
        stats = per_slot_stats[slot]
        print(f"\n{'─'*70}")
        print(f"  {slot.upper()} — {stats.total_free} free observations")
        print(f"{'─'*70}")

        print(f"\n  By exact value (sorted by probability):")
        for val, cnt in sorted(stats.value_counts.items(), key=lambda x: -x[1]):
            print(f"    {pct(cnt/stats.total_free):>8}  ({cnt:>4}x)  {val}")

        print(f"\n  By stat category:")
        for cat, cnt in sorted(stats.category_counts.items(), key=lambda x: -x[1]):
            print(f"    {pct(cnt/stats.total_free):>8}  ({cnt:>4}x)  {cat}")

    print(f"\n{'=' * 70}")
    print(f"  CROSS-SLOT — probability of seeing value on ANY free slot in a roll")
    print(f"  (denominator = {n_rolls} total valid rolls)")
    print(f"{'=' * 70}")

    print(f"\n  By exact value:")
    for val, cnt in sorted(cross_value.items(), key=lambda x: -x[1]):
        print(f"    {pct(cnt/n_rolls):>8}  ({cnt:>4}x)  {val}")

    print(f"\n  By stat category:")
    for cat, cnt in sorted(cross_cat.items(), key=lambda x: -x[1]):
        print(f"    {pct(cnt/n_rolls):>8}  ({cnt:>4}x)  {cat}")


def print_search_report(
    query, cleaned_rolls, per_slot_stats, cross_value, cross_cat, n_rolls
):
    """Print focused report for a search query (partial match on value or category)."""
    q = query.lower()
    print(f"\n{'=' * 70}")
    print(f'  SEARCH RESULTS for: "{query}"')
    print(f"{'=' * 70}")

    # Find matching values and categories
    matched_values = [v for v in cross_value if q in v.lower()]
    matched_cats = [c for c in cross_cat if q in c.lower()]

    if not matched_values and not matched_cats:
        print("  No matches found.")
        return

    if matched_cats:
        print(f"\n  Matching stat categories:")
        for cat in matched_cats:
            cnt = cross_cat[cat]
            print(f"    Category: {cat}")
            print(
                f"      Any-slot probability: {pct(cnt/n_rolls)} ({cnt}/{n_rolls} rolls)"
            )
            for slot in ("s1", "s2", "s3"):
                stats = per_slot_stats[slot]
                cat_cnt = stats.category_counts.get(cat, 0)
                if stats.total_free > 0:
                    print(
                        f"      {slot}: {pct(cat_cnt/stats.total_free)} ({cat_cnt}/{stats.total_free} free rolls)"
                    )

    if matched_values:
        print(f"\n  Matching exact values:")
        for val in sorted(matched_values, key=lambda v: -cross_value[v]):
            cnt = cross_value[val]
            print(f'    Value: "{val}"')
            print(
                f"      Any-slot probability: {pct(cnt/n_rolls)} ({cnt}/{n_rolls} rolls)"
            )
            for slot in ("s1", "s2", "s3"):
                stats = per_slot_stats[slot]
                val_cnt = stats.value_counts.get(val, 0)
                if stats.total_free > 0:
                    print(
                        f"      {slot}: {pct(val_cnt/stats.total_free)} ({val_cnt}/{stats.total_free} free rolls)"
                    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Analyze reroll probabilities from JSONL logs."
    )
    parser.add_argument(
        "--logs-dir",
        default="reroll_logs",
        help="Directory containing .jsonl log files (default: reroll_logs)",
    )
    parser.add_argument(
        "--search",
        default=None,
        help="Optional: search for a specific stat, e.g. 'critical rate'",
    )
    parser.add_argument(
        "--no-full-report",
        action="store_true",
        help="Suppress the full report (useful with --search)",
    )
    args = parser.parse_args()

    raw_rolls = load_jsonl_files(args.logs_dir)
    print(f"Loaded {len(raw_rolls)} raw entries from '{args.logs_dir}'.")

    cleaned_rolls = clean_rolls(raw_rolls)
    dupes = len(raw_rolls) - len(cleaned_rolls)
    print(
        f"Discarded {dupes} duplicate entries. {len(cleaned_rolls)} valid rolls remaining."
    )

    per_slot_stats, cross_value, cross_cat, n_rolls = compute_stats(cleaned_rolls)

    if not args.no_full_report:
        print_full_report(
            cleaned_rolls, per_slot_stats, cross_value, cross_cat, n_rolls
        )

    if args.search:
        print_search_report(
            args.search, cleaned_rolls, per_slot_stats, cross_value, cross_cat, n_rolls
        )


if __name__ == "__main__":
    main()
