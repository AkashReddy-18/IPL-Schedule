"""Stand-alone validator: re-checks H1..H6 against the produced schedule."""
import json, os, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
INST = os.path.join(os.path.dirname(HERE), "inst_1")

teams   = json.load(open(os.path.join(INST, "teams.json")))
blkout  = json.load(open(os.path.join(INST, "blackouts.json")))
sched   = json.load(open(os.path.join(HERE, "schedule.json")))

home_v  = {t["code"]: t["home_venue"] for t in teams}
alt_v   = {t["code"]: t.get("alt_venue") for t in teams}
codes   = [t["code"] for t in teams]
blkset  = {(b["venue"], b["day"]) for b in blkout}

errors = []

# Resolve the *actual* venue for each match.
for s in sched:
    s["venue"] = alt_v[s["home_team"]] if s["is_alt_venue"] else home_v[s["home_team"]]

# H1 -- every unordered pair plays twice (once at each home)
pair_host_counts = Counter()
for s in sched:
    pair_host_counts[(s["home_team"], s["away_team"])] += 1
for ci in codes:
    for cj in codes:
        if ci == cj: continue
        if pair_host_counts[(ci, cj)] != 1:
            errors.append(f"H1 violation: {ci} hosting {cj} appears {pair_host_counts[(ci, cj)]} times")

# H2 -- 56 unique days
days = [s["day"] for s in sched]
if sorted(days) != list(range(1, 57)):
    errors.append(f"H2 violation: days are not {{1..56}}, got {sorted(days)}")

# H3 -- max-min played at end of any day <= 4
played = defaultdict(lambda: defaultdict(int))   # team -> day -> cumulative
for d in range(1, 57):
    todays = [s for s in sched if s["day"] == d]
    for c in codes:
        played[c][d] = played[c][d-1] if d > 1 else 0
    for s in todays:
        played[s["home_team"]][d] += 1
        played[s["away_team"]][d] += 1
for d in range(1, 57):
    vals = [played[c][d] for c in codes]
    if max(vals) - min(vals) > 4:
        errors.append(f"H3 violation: day {d} spread = {max(vals)-min(vals)} (teams {dict((c, played[c][d]) for c in codes)})")

# H4 -- min gap 2 days for each team
team_days = defaultdict(list)
for s in sched:
    team_days[s["home_team"]].append(s["day"])
    team_days[s["away_team"]].append(s["day"])
for c in codes:
    ds = sorted(team_days[c])
    if len(ds) != 14:
        errors.append(f"H3' team-match-count: {c} plays {len(ds)} matches")
    for i in range(len(ds)-1):
        if ds[i+1] - ds[i] < 2:
            errors.append(f"H4 violation: {c} plays on consecutive days {ds[i]}, {ds[i+1]}")

# H5 -- at most 2 alt-venue home matches per team
alt_counts = Counter()
for s in sched:
    if s["is_alt_venue"]:
        alt_counts[s["home_team"]] += 1
for c in codes:
    if alt_counts[c] > 2:
        errors.append(f"H5 violation: {c} used alt venue {alt_counts[c]} times")
    if alt_counts[c] > 0 and alt_v[c] is None:
        errors.append(f"H5 violation: {c} has no alt venue but is_alt_venue=true was set")

# H6 -- no match at blackout (venue, day)
for s in sched:
    if (s["venue"], s["day"]) in blkset:
        errors.append(f"H6 violation: match {s['home_team']} vs {s['away_team']} at {s['venue']} on day {s['day']} is blacked out")

print("=" * 60)
print(f"H1 pair balance:          {'OK' if not [e for e in errors if 'H1' in e] else 'FAIL'}")
print(f"H2 daily allocation:      {'OK' if not [e for e in errors if 'H2' in e] else 'FAIL'}")
print(f"H3 progressive balance:   {'OK' if not [e for e in errors if 'H3' in e] else 'FAIL'}")
print(f"H4 mandatory rest:        {'OK' if not [e for e in errors if 'H4' in e] else 'FAIL'}")
print(f"H5 alt-venue limit:       {'OK' if not [e for e in errors if 'H5' in e] else 'FAIL'}")
print(f"H6 blackout dates:        {'OK' if not [e for e in errors if 'H6' in e] else 'FAIL'}")
print("=" * 60)
if errors:
    print(f"{len(errors)} errors:")
    for e in errors: print("  -", e)
    sys.exit(1)
print("ALL HARD CONSTRAINTS SATISFIED")

# Extra audit: max spread and alt-venue tally.
spread_max = max(max([played[c][d] for c in codes]) - min([played[c][d] for c in codes]) for d in range(1, 57))
print(f"Max progressive spread across all days: {spread_max} (cap = 4)")
print(f"Alt-venue tally: {dict(alt_counts)}")
print(f"Per-team match count check: {sorted((c, len(team_days[c])) for c in codes)}")
