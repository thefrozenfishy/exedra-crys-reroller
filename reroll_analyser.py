import json
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

VALUE_TO_CATEGORY = {}
for cat, vals in STAT_CATEGORIES.items():
    for v in vals:
        VALUE_TO_CATEGORY[v] = cat


def cat(v):
    return VALUE_TO_CATEGORY[v]


def pct(x):
    return f"{x*100:.2f}%"


def load_runs():
    runs = []

    for f in sorted(Path("reroll_logs").glob("*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            runs.append([json.loads(line) for line in fh if line.strip()])

    return runs


def is_duplicate(a, b):
    return a["s1"] == b["s1"] and a["s2"] == b["s2"] and a["s3"] == b["s3"]


def detect_locked(a, b, c):
    locked = set()
    for s in ("s1", "s2", "s3"):
        if a[s] == b[s] == c[s]:
            locked.add(cat(c[s]))
    return locked


def process_run(run):
    no_dupes = [run[0]]
    for i in range(1, len(run)):
        if not is_duplicate(run[i - 1], run[i]):
            no_dupes.append(run[i])
    if len(no_dupes) < 3:
        return []

    cleaned = []
    no_dupes[0]["locked"] = detect_locked(no_dupes[0], no_dupes[1], no_dupes[2])
    cleaned.append(no_dupes[0])
    no_dupes[1]["locked"] = detect_locked(no_dupes[0], no_dupes[1], no_dupes[2])
    cleaned.append(no_dupes[1])

    for i in range(2, len(no_dupes)):
        no_dupes[i]["locked"] = detect_locked(
            no_dupes[i - 2], no_dupes[i - 1], no_dupes[i]
        )
        cleaned.append(no_dupes[i])

    return cleaned


def compute(runs):
    state_counts = defaultdict(int)
    state_value_counts = defaultdict(lambda: defaultdict(int))
    global_value_counts = defaultdict(int)

    total_values = 0

    for run in runs:
        cleaned = process_run(run)

        for run_state in cleaned:
            state = frozenset(run_state["locked"])
            state_counts[state] += 1

            for slot in ("s1", "s2", "s3"):
                stat = run_state[slot]

                if cat(stat) in state:
                    continue

                state_value_counts[state][stat] += 1
                global_value_counts[stat] += 1
                total_values += 1

    return state_counts, state_value_counts, global_value_counts, total_values


def compute_marginal(state_counts, state_value_counts, total_values):
    marginal = defaultdict(float)

    total_states = sum(state_counts.values())

    for state, vcounts in state_value_counts.items():
        ps = state_counts[state] / total_states

        total_in_state = sum(vcounts.values())
        if total_in_state == 0:
            continue

        for v, cnt in vcounts.items():
            pv_given_s = cnt / total_in_state
            marginal[v] += pv_given_s * ps

    return marginal


def report(state_counts, global_counts, marginal):
    print("LOCK STATE DIST:")
    for s, c in sorted(state_counts.items(), key=lambda x: -x[1]):
        print(f"{set(s) if s else 'NONE'}: {c}")

    print("\nGLOBAL RAW (biased baseline)")
    for v, c in sorted(global_counts.items(), key=lambda x: -x[1]):
        print(f"{pct(c/sum(global_counts.values()))} {v}")

    print("\nMARGINALIZED TRUE PROBABILITY ESTIMATE")
    for v, p in sorted(marginal.items(), key=lambda x: -x[1]):
        print(f"{pct(p)} {v}")


def main():
    runs = load_runs()
    state_counts, state_value_counts, global_counts, total = compute(runs)
    marginal = compute_marginal(state_counts, state_value_counts, total)
    report(state_counts, global_counts, marginal)


if __name__ == "__main__":
    main()
