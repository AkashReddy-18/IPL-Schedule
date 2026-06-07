import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
INST = os.path.join(os.path.dirname(HERE), "inst_1")

schedule = json.load(open(os.path.join(HERE, "schedule.json")))
teams    = json.load(open(os.path.join(INST, "teams.json")))
travel   = json.load(open(os.path.join(INST, "travel_matrix.json")))
bids     = json.load(open(os.path.join(INST, "broadcaster_bids.json")))
params   = json.load(open(os.path.join(INST, "parameters.json")))

home_venue = {t["code"]: t["home_venue"] for t in teams}
alt_venue  = {t["code"]: t.get("alt_venue") for t in teams}
alt_bonus  = {t["code"]: t.get("alt_bonus_cr", 0.0) for t in teams}
bid_table  = {frozenset({b["team_a"], b["team_b"]}): b for b in bids}

WEEKEND = {d for d in range(1, 57) if (d - 1) % 7 in (5, 6)}
G = params["g_multiplier"]

def week_g(day):
    return G[(day - 1) // 7]

# Resolve actual venue for each scheduled match.
for s in schedule:
    s["venue"] = alt_venue[s["home_team"]] if s["is_alt_venue"] else home_venue[s["home_team"]]

# --- R1 Broadcaster revenue (three-tier per-match payoff) ----------------
R1 = 0.0
for s in schedule:
    bid = bid_table[frozenset({s["home_team"], s["away_team"]})]
    d = s["day"]
    if d in bid.get("preferred_days", []):
        R1 += bid["alpha_cr"]
    elif d in WEEKEND:
        R1 += bid["beta_cr"]
    else:
        R1 += bid["gamma_cr"]

# --- R2 Alternate-venue bonus --------------------------------------------
R2 = sum(alt_bonus[s["home_team"]] for s in schedule if s["is_alt_venue"])

# --- Per-team chronology with venue sequences ----------------------------
seq = defaultdict(list)   # team -> [(day, venue), ...]
for s in schedule:
    for role in ("home_team", "away_team"):
        seq[s[role]].append((s["day"], s["venue"]))
for t in seq:
    seq[t].sort()

# --- C1 Travel, C2 Distance, C5 Gap (per-team, transitions k=1..13) ------
kappa = params["kappa"];      p_exp = params["p_exponent"]
D0    = params["D0_limit_km"]
eta   = params["eta"];        q_exp = params["q_exponent"]
x0    = params["x0_limit_days"]
tau_s = params["tau_star_days"]
a_lo  = params["a_low_cr"];   a_hi  = params["a_high_cr"]

C1 = C2 = C3 = C5 = 0.0
D_team = {}
for t, ds in seq.items():
    D = 0.0
    for k in range(1, len(ds)):
        u, v = ds[k - 1][1], ds[k][1]
        dist, cost = travel[u][v]
        D += dist
        C1 += cost * week_g(ds[k][0])           # destination-day g multiplier
        tau = ds[k][0] - ds[k - 1][0]
        if tau <= tau_s:
            C5 += a_lo * (tau_s - tau) ** 2
        else:
            C5 += a_hi * (tau - tau_s) ** 2
    D_team[t] = D
    C2 += kappa * max(0.0, D - D0) ** p_exp

    # C3 Stay: merge consecutive same-venue matches into one stay (spec-faithful).
    H = home_venue[t]
    i = 0
    while i < len(ds) - 1:
        v = ds[i + 1][1]
        run_start = ds[i][0]
        j = i + 1
        while j + 1 < len(ds) and ds[j + 1][1] == v:
            j += 1
        length = ds[j][0] - run_start
        if v != H:
            C3 += eta * max(0, length - x0) ** q_exp
        i = j

# --- C4 Density (per match, 7-day prior window) --------------------------
d0r = params["delta_0_rust_cr"]
d2t = params["delta_2_tired_cr"]
d3c = params["delta_3_cooked_cr"]
delta_step = {0: d0r, 1: 0.0, 2: d2t, 3: d3c}

C4 = 0.0
for t, ds in seq.items():
    days = [d for d, _ in ds]
    for d in days:
        nu = sum(1 for d2 in days if d - 7 <= d2 < d)
        C4 += delta_step.get(nu, d3c)   # safety: clamp >3 to delta_3

# --- C6 Equity (league-wide) ---------------------------------------------
lam_eq = params["lambda_eq"]
D0_disp = params["delta_0_disparity_km"]
spread = max(D_team.values()) - min(D_team.values())
C6 = lam_eq * max(0.0, spread - D0_disp)

J = R1 + R2 - C1 - C2 - C3 - C4 - C5 - C6

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print("=" * 62)
print("OBJECTIVE BREAKDOWN  (re-computed from schedule.json, in Cr)")
print("=" * 62)
print(f"  R1  Broadcaster revenue   :  +{R1:9.4f}")
print(f"  R2  Alt-venue bonus       :  +{R2:9.4f}")
print(f"  C1  Travel cost           :  -{C1:9.4f}")
print(f"  C2  Distance fatigue      :  -{C2:9.4f}")
print(f"  C3  Stay (merged stays)   :  -{C3:9.4f}")
print(f"  C4  Density               :  -{C4:9.4f}")
print(f"  C5  Gap                   :  -{C5:9.4f}")
print(f"  C6  Equity                :  -{C6:9.4f}")
print("-" * 62)
print(f"  J  =  R1 + R2 - (C1..C5) - C6  =  {J:.4f}  Cr")
print("=" * 62)
print()
print("Per-team total distance D(T):")
for t, D in sorted(D_team.items(), key=lambda kv: -kv[1]):
    home_flag = "*" if D == max(D_team.values()) else ("." if D == min(D_team.values()) else " ")
    print(f"  {home_flag} {t}: {D:8.1f} km")
print(f"D_max - D_min = {spread:.1f} km   (equity threshold = {D0_disp})")
