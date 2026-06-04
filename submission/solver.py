"""
PowerPlay -- T20 Cricket League Scheduling Solver (CP-SAT)
==========================================================

Solves the 8-team / 56-day / 56-match IPL-style scheduling problem with
multi-objective optimisation:

   max  J  =  Sum_m R(m)  +  Sum_m B_alt(m)
            -  Sum_T [ Travel(T) + Distance(T) + Stay(T) + Density(T) + Gap(T) ]
            -  lambda_eq * Equity(schedule)

LINEARISATION TECHNIQUES
------------------------
* `pos[T,m,k]` boolean indicators rank each team's 14 matches into a
  chronological sequence k = 1..14.  Each match maps to exactly one
  position, each position holds exactly one match, and `seq_day[T,k]` is
  channelled to `match_day[m]` via `OnlyEnforceIf(pos[T,m,k])`.

* Per-position venue is materialised as `venue_at_pos[T,k]` -- an IntVar
  in the 13-venue indexing.  Channelling: for each match m in M_T, when
  pos[T,m,k]=1 the venue is forced to either the primary or alternate
  cell of home(m), conditioned on `is_alt[m]`.

* Travel cost C1 between consecutive positions uses
  `AddElement(flat_idx, COST_FLAT_TABLE, base_cost)` where
  flat_idx = venue_at_pos[T,k]*13 + venue_at_pos[T,k+1].  The
  late-tournament multiplier g(w) is applied by multiplying with a small
  `g_factor[T,k]` IntVar derived from week(seq_day[T,k+1]) through
  another AddElement.

* The convex p=1.5 distance fatigue C2 is approximated by bucketing the
  team total distance into 50-km wide cells and using AddElement on a
  pre-computed integer lookup table (CR-scaled).

* Stay (C3) and gap (C5) quadratic penalties are pre-tabulated by their
  integer index (gap length in days) and accessed via AddElement.

* C4 step-wise density penalty uses AddElement on a 4-entry array indexed
  by the 7-day window match count cw[T,d].

* C6 equity max-min uses AddMaxEquality / AddMinEquality, then clips the
  slack to >= 0 via a non-negative IntVar.

All financial quantities are scaled by SCALE = 10_000 -> integer
centi-lakhs (Cr * 1e4) so the integer-only CP-SAT objective preserves
four decimals of precision.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ortools.sat.python import cp_model


# ---------------------------------------------------------------------------
# Paths -- accept either project root or root/inst_1.
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INSTANCE_DIR = os.environ.get("INSTANCE_DIR")
if not INSTANCE_DIR:
    for cand in (ROOT, os.path.join(ROOT, "inst_1")):
        if all(os.path.exists(os.path.join(cand, f)) for f in (
            "teams.json", "travel_matrix.json", "broadcaster_bids.json",
            "blackouts.json", "parameters.json"
        )):
            INSTANCE_DIR = cand
            break
if not INSTANCE_DIR:
    raise FileNotFoundError("JSON inputs not found. Set INSTANCE_DIR env var.")

OUTPUT_PATH = os.path.join(HERE, "schedule.json")

# Scaling factor to convert Cr to integer centi-lakhs (1e-4 Cr resolution)
# This allows the integer-only CP-SAT solver to handle currency with precision.
SCALE = 10_000           
DAYS = 56
N_TEAMS = 8
MATCHES_PER_TEAM = 14
N_MATCHES = 56
# Default time limit for the solver in seconds
TIME_LIMIT_S = float(os.environ.get("SOLVER_TIME_LIMIT", "300"))


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
# Helper to load JSON data from the instance directory
def load(name):
    with open(os.path.join(INSTANCE_DIR, name)) as fh:
        return json.load(fh)


teams_raw = load("teams.json")
travel_raw = load("travel_matrix.json")
bids_raw = load("broadcaster_bids.json")
blackouts_raw = load("blackouts.json")
params = load("parameters.json")

team_codes = [t["code"] for t in teams_raw]
home_venue = {t["code"]: t["home_venue"] for t in teams_raw}
alt_venue = {t["code"]: t.get("alt_venue") for t in teams_raw}
alt_bonus_cr = {t["code"]: t.get("alt_bonus_cr", 0.0) for t in teams_raw}

# All venues across teams (primary + alternate).  Indexed for AddElement.
all_venues = sorted(set(travel_raw.keys()))
V_INDEX = {v: i for i, v in enumerate(all_venues)}
N_VENUES = len(all_venues)  # 13

bid_table = {frozenset({b["team_a"], b["team_b"]}): b for b in bids_raw}
blackout_set = {(b["venue"], b["day"]) for b in blackouts_raw}

KAPPA = params["kappa"]
P_EXP = params["p_exponent"]
D0_LIMIT = params["D0_limit_km"]
ETA = params["eta"]
Q_EXP = params["q_exponent"]
X0_LIMIT = params["x0_limit_days"]
DELTA0_RUST = params["delta_0_rust_cr"]
DELTA2_TIRED = params["delta_2_tired_cr"]
DELTA3_COOKED = params["delta_3_cooked_cr"]
TAU_STAR = int(params["tau_star_days"])
A_LOW = params["a_low_cr"]
A_HIGH = params["a_high_cr"]
LAMBDA_EQ = params["lambda_eq"]
DELTA0_DISP = params["delta_0_disparity_km"]
G_MULT = list(params["g_multiplier"])  # length 8

# Weekend days: assume calendar starts on Monday -> days with idx mod 7 in {5,6}
WEEKEND_DAYS = {d for d in range(1, DAYS + 1) if ((d - 1) % 7) in (5, 6)}


def cr_to_int(x):
    return int(round(x * SCALE))


def week_of(day):
    return (day - 1) // 7 + 1


# ---------------------------------------------------------------------------
# Enumerate the 56 matches: 8 teams * 7 opponents (each ordered).  H1 is
# enforced structurally; each unordered pair contributes two distinct matches
# (one per host).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Match:
    idx: int
    home: str
    away: str

    @property
    def pair(self):
        return frozenset({self.home, self.away})


# Generate the 56 required matches: every team plays everyone else once at home.
matches: List[Match] = []
for i, ci in enumerate(team_codes):
    for cj in team_codes:
        if ci == cj:
            continue
        matches.append(Match(idx=len(matches), home=ci, away=cj))
assert len(matches) == N_MATCHES

matches_with_team = {c: [m.idx for m in matches if c in (m.home, m.away)] for c in team_codes}
for c in team_codes:
    assert len(matches_with_team[c]) == MATCHES_PER_TEAM

# ---------------------------------------------------------------------------
# Pre-computed lookup tables
# ---------------------------------------------------------------------------
# Flattened cost (CR-scaled int) and distance (int km) tables, size 13*13.
COST_FLAT = [0] * (N_VENUES * N_VENUES)
DIST_FLAT = [0] * (N_VENUES * N_VENUES)
for u in all_venues:
    for v in all_venues:
        d_km, c_cr = travel_raw[u][v]
        COST_FLAT[V_INDEX[u] * N_VENUES + V_INDEX[v]] = cr_to_int(c_cr)
        DIST_FLAT[V_INDEX[u] * N_VENUES + V_INDEX[v]] = int(round(d_km))

MAX_SEG_DIST = max(DIST_FLAT)
MAX_TEAM_DIST = MAX_SEG_DIST * (MATCHES_PER_TEAM - 1)  # 13 hops
MAX_SEG_COST = max(COST_FLAT)

# Week -> g multiplier as integer * 1000 (fixed-point for the per-hop cost).
G_INT = [int(round(g * 1000)) for g in G_MULT]  # length 8
# Per-day lookup of g_int by day index 1..56 (size DAYS+1 for AddElement).
G_BY_DAY = [0] + [G_INT[week_of(d) - 1] for d in range(1, DAYS + 1)]

# Bucket size (km) for C2 convex distance lookup.  50 km gives sub-Cr drift
# in the high-distance regime while keeping the table compact.
DIST_BUCKET = 50
DIST_TABLE_SIZE = MAX_TEAM_DIST // DIST_BUCKET + 2
C2_TABLE = [
    cr_to_int(KAPPA * (max(0.0, b * DIST_BUCKET - D0_LIMIT) ** P_EXP))
    for b in range(DIST_TABLE_SIZE)
]

STAY_TABLE = [
    cr_to_int(ETA * (max(0, x - X0_LIMIT) ** Q_EXP))
    for x in range(DAYS + 2)
]


def gap_int(tau):
    if tau <= TAU_STAR:
        return cr_to_int(A_LOW * (TAU_STAR - tau) ** 2)
    return cr_to_int(A_HIGH * (tau - TAU_STAR) ** 2)


GAP_TABLE = [gap_int(t) for t in range(DAYS + 2)]
DENSITY_TABLE = [
    cr_to_int(DELTA0_RUST),
    0,
    cr_to_int(DELTA2_TIRED),
    cr_to_int(DELTA3_COOKED),
]


print(f"[setup] instance: {INSTANCE_DIR}")
print(f"[setup] {N_MATCHES} matches, {len(blackout_set)} blackouts, "
      f"{N_VENUES} venues, dist buckets={DIST_TABLE_SIZE}")


# ===========================================================================
# CP-SAT MODEL
# ===========================================================================
model = cp_model.CpModel()

# --- match_day[m]: day on which match m is played (1..56), all-different ---
match_day = [model.NewIntVar(1, DAYS, f"d_m{m.idx}") for m in matches]
model.AddAllDifferent(match_day)

# --- is_alt[m] -------------------------------------------------------------
is_alt: List = []
for m in matches:
    if alt_venue[m.home]:
        is_alt.append(model.NewBoolVar(f"alt_m{m.idx}"))
    else:
        is_alt.append(model.NewConstant(0))

# --- on_day[m, d] : Boolean channel for match_day == d ---------------------
on_day = {}
for mi, m in enumerate(matches):
    bs = []
    for d in range(1, DAYS + 1):
        b = model.NewBoolVar(f"on_m{mi}_d{d}")
        on_day[(mi, d)] = b
        bs.append(b)
        model.Add(match_day[mi] == d).OnlyEnforceIf(b)
        model.Add(match_day[mi] != d).OnlyEnforceIf(b.Not())
    model.AddExactlyOne(bs)

for d in range(1, DAYS + 1):
    model.AddExactlyOne([on_day[(mi, d)] for mi in range(N_MATCHES)])

# --- plays_on[T, d]  -------------------------------------------------------
plays_on = {}
for c in team_codes:
    for d in range(1, DAYS + 1):
        v = model.NewBoolVar(f"play_{c}_d{d}")
        plays_on[(c, d)] = v
        model.Add(v == sum(on_day[(mi, d)] for mi in matches_with_team[c]))

# --- H4 rest day  ----------------------------------------------------------
for c in team_codes:
    for d in range(1, DAYS):
        model.Add(plays_on[(c, d)] + plays_on[(c, d + 1)] <= 1)

# --- H3 progressive balance -----------------------------------------------
played_by_end = {}
for c in team_codes:
    for d in range(1, DAYS + 1):
        v = model.NewIntVar(0, MATCHES_PER_TEAM, f"cum_{c}_d{d}")
        played_by_end[(c, d)] = v
        model.Add(v == sum(plays_on[(c, dd)] for dd in range(1, d + 1)))
for d in range(1, DAYS + 1):
    for i in range(N_TEAMS):
        for j in range(i + 1, N_TEAMS):
            ci, cj = team_codes[i], team_codes[j]
            model.Add(played_by_end[(ci, d)] - played_by_end[(cj, d)] <= 4)
            model.Add(played_by_end[(cj, d)] - played_by_end[(ci, d)] <= 4)

# --- H5 alt venue limit -----------------------------------------------------
for c in team_codes:
    if alt_venue[c]:
        model.Add(sum(is_alt[mi] for mi in range(N_MATCHES) if matches[mi].home == c) <= 2)

# --- H6 blackout dates -----------------------------------------------------
for m in matches:
    primary_v = home_venue[m.home]
    altv = alt_venue[m.home]
    for d in range(1, DAYS + 1):
        pb = (primary_v, d) in blackout_set
        ab = altv is not None and (altv, d) in blackout_set
        if pb and altv is None:
            model.Add(on_day[(m.idx, d)] == 0)
        elif pb and not ab:
            # Must use alt on that day.
            model.Add(is_alt[m.idx] == 1).OnlyEnforceIf(on_day[(m.idx, d)])
        elif ab and not pb:
            model.Add(is_alt[m.idx] == 0).OnlyEnforceIf(on_day[(m.idx, d)])
        elif pb and ab:
            model.Add(on_day[(m.idx, d)] == 0)

print("[model] H1-H6 installed.")

# ===========================================================================
# CHRONOLOGY: pos[T, m, k] booleans
# ===========================================================================
pos: Dict[Tuple[str, int, int], cp_model.IntVar] = {}
seq_day: Dict[Tuple[str, int], cp_model.IntVar] = {}
venue_at_pos: Dict[Tuple[str, int], cp_model.IntVar] = {}

for c in team_codes:
    M_T = matches_with_team[c]
    for mi in M_T:
        for k in range(1, MATCHES_PER_TEAM + 1):
            pos[(c, mi, k)] = model.NewBoolVar(f"pos_{c}_m{mi}_k{k}")
        # Exactly one position per match
        model.AddExactlyOne([pos[(c, mi, k)] for k in range(1, MATCHES_PER_TEAM + 1)])
    for k in range(1, MATCHES_PER_TEAM + 1):
        # Exactly one match per position
        model.AddExactlyOne([pos[(c, mi, k)] for mi in M_T])

        sd = model.NewIntVar(1, DAYS, f"seqday_{c}_k{k}")
        seq_day[(c, k)] = sd
        for mi in M_T:
            model.Add(match_day[mi] == sd).OnlyEnforceIf(pos[(c, mi, k)])

        # venue_at_pos[T, k]
        vap = model.NewIntVar(0, N_VENUES - 1, f"vap_{c}_k{k}")
        venue_at_pos[(c, k)] = vap
        for mi in M_T:
            m = matches[mi]
            primary_idx = V_INDEX[home_venue[m.home]]
            altv = alt_venue[m.home]
            if altv is None:
                model.Add(vap == primary_idx).OnlyEnforceIf(pos[(c, mi, k)])
            else:
                alt_idx = V_INDEX[altv]
                # is_alt = 0 -> primary; is_alt = 1 -> alt
                model.Add(vap == primary_idx).OnlyEnforceIf(
                    [pos[(c, mi, k)], is_alt[mi].Not()]
                )
                model.Add(vap == alt_idx).OnlyEnforceIf(
                    [pos[(c, mi, k)], is_alt[mi]]
                )

    # Monotonic ordering with min rest 2 days (consistent with H4).
    for k in range(1, MATCHES_PER_TEAM):
        model.Add(seq_day[(c, k + 1)] >= seq_day[(c, k)] + 2)

print("[model] Position channelling installed.")


# ===========================================================================
# REVENUE
# ===========================================================================
rev_terms: List = []

# R1 broadcaster bid -- per-day lookup table per match.
for m in matches:
    bid = bid_table[m.pair]
    pref = set(bid.get("preferred_days", []))
    alpha = cr_to_int(bid["alpha_cr"])
    beta = cr_to_int(bid["beta_cr"])
    gamma = cr_to_int(bid["gamma_cr"])
    # Pre-compute payoff per day.
    payoff_by_day = []
    for d in range(1, DAYS + 1):
        if d in pref:
            payoff_by_day.append(alpha)
        elif d in WEEKEND_DAYS:
            payoff_by_day.append(beta)
        else:
            payoff_by_day.append(gamma)
    # Sum on_day * payoff for non-zero days.
    for d, p in enumerate(payoff_by_day, start=1):
        if p:
            rev_terms.append(p * on_day[(m.idx, d)])

# R2 alt-venue bonus
for m in matches:
    b = cr_to_int(alt_bonus_cr[m.home])
    if b and alt_venue[m.home]:
        rev_terms.append(b * is_alt[m.idx])


# ===========================================================================
# PENALTIES
# ===========================================================================
pen_terms: List = []
team_total_dist: Dict[str, cp_model.IntVar] = {}

# --- C1 travel cost + accumulate D(T) for C2 ------------------------------
# For each team T and each transition k -> k+1:
#   flat_idx = venue_at_pos[T,k] * N_VENUES + venue_at_pos[T,k+1]
#   base_cost = COST_FLAT[flat_idx]          (CR-scaled int)
#   base_dist = DIST_FLAT[flat_idx]          (km)
#   g_factor  = G_BY_DAY[seq_day[T,k+1]]     (x1000 multiplier)
#   contribution = base_cost * g_factor / 1000  --> added to penalty
# ---------------------------------------------------------------------------
for c in team_codes:
    dist_terms = []
    for k in range(1, MATCHES_PER_TEAM):
        v1 = venue_at_pos[(c, k)]
        v2 = venue_at_pos[(c, k + 1)]
        # flat = v1 * N_VENUES + v2
        flat = model.NewIntVar(0, N_VENUES * N_VENUES - 1, f"flat_{c}_k{k}")
        model.Add(flat == v1 * N_VENUES + v2)

        base_cost = model.NewIntVar(0, MAX_SEG_COST, f"bc_{c}_k{k}")
        model.AddElement(flat, COST_FLAT, base_cost)
        base_dist = model.NewIntVar(0, MAX_SEG_DIST, f"bd_{c}_k{k}")
        model.AddElement(flat, DIST_FLAT, base_dist)
        dist_terms.append(base_dist)

        # g(week) by day, via AddElement over G_BY_DAY indexed by seq_day.
        g_fac = model.NewIntVar(min(G_INT), max(G_INT), f"g_{c}_k{k}")
        model.AddElement(seq_day[(c, k + 1)], G_BY_DAY, g_fac)

        # scaled_cost = base_cost * g_fac
        scaled = model.NewIntVar(0, MAX_SEG_COST * max(G_INT), f"sc_{c}_k{k}")
        model.AddMultiplicationEquality(scaled, [base_cost, g_fac])

        # Add scaled/1000 to penalty terms.  Use integer division aux.
        contrib = model.NewIntVar(0, MAX_SEG_COST * max(G_INT) // 1000 + 1, f"ct_{c}_k{k}")
        model.AddDivisionEquality(contrib, scaled, 1000)
        pen_terms.append(contrib)

    # Team total distance (km).
    td = model.NewIntVar(0, MAX_TEAM_DIST, f"D_{c}")
    model.Add(td == sum(dist_terms))
    team_total_dist[c] = td

    # C2 convex distance fatigue via bucketed lookup.
    b_idx = model.NewIntVar(0, DIST_TABLE_SIZE - 1, f"b_{c}")
    model.AddDivisionEquality(b_idx, td, DIST_BUCKET)
    c2 = model.NewIntVar(0, max(C2_TABLE), f"c2_{c}")
    model.AddElement(b_idx, C2_TABLE, c2)
    pen_terms.append(c2)

print("[model] C1 + C2 installed.")


# --- C3 stay penalty ------------------------------------------------------
MAX_STAY_PEN = max(STAY_TABLE)
for c in team_codes:
    M_T = matches_with_team[c]
    for k in range(2, MATCHES_PER_TEAM + 1):
        # gap to current position (will be the stay length at venue_at_pos[T,k])
        gap = model.NewIntVar(2, DAYS, f"sg_{c}_k{k}")
        model.Add(gap == seq_day[(c, k)] - seq_day[(c, k - 1)])
        spen = model.NewIntVar(0, MAX_STAY_PEN, f"sp_{c}_k{k}")
        model.AddElement(gap, STAY_TABLE, spen)

        # Not-at-home indicator at position k.
        nh_terms = []
        for mi in M_T:
            m = matches[mi]
            if m.home != c:
                # T is the visitor.  Always non-home venue.
                nh_terms.append(pos[(c, mi, k)])
            else:
                # T is host.  Non-home iff is_alt[mi] = 1.
                ax = model.NewBoolVar(f"hostalt_{c}_k{k}_m{mi}")
                model.AddBoolAnd([pos[(c, mi, k)], is_alt[mi]]).OnlyEnforceIf(ax)
                model.AddBoolOr(
                    [pos[(c, mi, k)].Not(), is_alt[mi].Not()]
                ).OnlyEnforceIf(ax.Not())
                nh_terms.append(ax)
        nh = model.NewBoolVar(f"nh_{c}_k{k}")
        model.Add(nh == sum(nh_terms))

        # contrib = spen * nh
        c3 = model.NewIntVar(0, MAX_STAY_PEN, f"c3_{c}_k{k}")
        model.Add(c3 <= MAX_STAY_PEN * nh)
        model.Add(c3 <= spen)
        model.Add(c3 >= spen - MAX_STAY_PEN * (1 - nh))
        pen_terms.append(c3)

print("[model] C3 installed.")


# --- C4 density penalty ---------------------------------------------------
MAX_DENS_PEN = max(DENSITY_TABLE)
for c in team_codes:
    for d in range(1, DAYS + 1):
        lo = max(1, d - 7)
        window = list(range(lo, d))
        cw = model.NewIntVar(0, 3, f"cw_{c}_d{d}")
        if window:
            model.Add(cw == sum(plays_on[(c, dd)] for dd in window))
        else:
            model.Add(cw == 0)
        dv = model.NewIntVar(0, MAX_DENS_PEN, f"dv_{c}_d{d}")
        model.AddElement(cw, DENSITY_TABLE, dv)
        c4 = model.NewIntVar(0, MAX_DENS_PEN, f"c4_{c}_d{d}")
        model.Add(c4 <= MAX_DENS_PEN * plays_on[(c, d)])
        model.Add(c4 <= dv)
        model.Add(c4 >= dv - MAX_DENS_PEN * (1 - plays_on[(c, d)]))
        pen_terms.append(c4)

print("[model] C4 installed.")


# --- C5 gap penalty -------------------------------------------------------
MAX_GAP_PEN = max(GAP_TABLE)
for c in team_codes:
    for k in range(2, MATCHES_PER_TEAM + 1):
        g = model.NewIntVar(2, DAYS, f"gpv_{c}_k{k}")
        model.Add(g == seq_day[(c, k)] - seq_day[(c, k - 1)])
        gp = model.NewIntVar(0, MAX_GAP_PEN, f"gp_{c}_k{k}")
        model.AddElement(g, GAP_TABLE, gp)
        pen_terms.append(gp)

print("[model] C5 installed.")


# --- C6 equity ------------------------------------------------------------
d_max = model.NewIntVar(0, MAX_TEAM_DIST, "Dmax")
d_min = model.NewIntVar(0, MAX_TEAM_DIST, "Dmin")
model.AddMaxEquality(d_max, list(team_total_dist.values()))
model.AddMinEquality(d_min, list(team_total_dist.values()))
slack = model.NewIntVar(0, MAX_TEAM_DIST, "eq_slack")
model.Add(slack >= d_max - d_min - int(round(DELTA0_DISP)))
LAMBDA_EQ_SCALED = int(round(LAMBDA_EQ * SCALE))
pen_terms.append(LAMBDA_EQ_SCALED * slack)

print("[model] C6 installed.")


# ===========================================================================
# OBJECTIVE
# ===========================================================================
model.Maximize(sum(rev_terms) - sum(pen_terms))


# ===========================================================================
# SOLVE
# ===========================================================================
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = TIME_LIMIT_S
solver.parameters.num_search_workers = 8
solver.parameters.log_search_progress = True
print(f"[solve] starting CP-SAT  (limit {TIME_LIMIT_S}s, 8 workers)")
status = solver.Solve(model)
status_name = solver.StatusName(status)
print(f"[solve] status={status_name}  obj={solver.ObjectiveValue() / SCALE:.4f} Cr")

if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    print("[solve] no feasible solution.", file=sys.stderr)
    sys.exit(1)


# ===========================================================================
# OUTPUT
# ===========================================================================
result = []
for d in range(1, DAYS + 1):
    for mi, m in enumerate(matches):
        if solver.Value(on_day[(mi, d)]) == 1:
            alt_val = solver.Value(is_alt[mi]) if hasattr(is_alt[mi], "Index") else 0
            result.append({
                "day": d,
                "home_team": m.home,
                "away_team": m.away,
                "is_alt_venue": bool(alt_val),
            })
            break

assert len(result) == DAYS

with open(OUTPUT_PATH, "w") as fh:
    json.dump(result, fh, indent=2)

print(f"[done] wrote {OUTPUT_PATH}  ({len(result)} matches)")
