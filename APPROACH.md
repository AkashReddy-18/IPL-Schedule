# Optimization Approach: PowerPlay T20 Scheduling Solution

## 1. Problem at a glance

Schedule 56 matches over 56 consecutive days for 8 teams. Each unordered
pair plays twice (once at each team's home venue). 5 of 8 teams have an
optional **alternate** home venue offering a revenue bonus but with higher
travel costs. The objective is
$$
J = \sum_m R(m) + \sum_m B_\text{alt}(m)
   -\sum_T \bigl[\text{Travel}(T) + \text{Distance}(T) + \text{Stay}(T) + \text{Density}(T) + \text{Gap}(T)\bigr]
   - \lambda_\text{eq}\cdot \text{Equity}.
$$

We solve it as a single MIP using OR-Tools' **CP-SAT** (lazy clause /
no-good integer solver). All financials are scaled by `SCALE = 10_000`
(Cr → centi-lakhs) so the integer-only solver retains 4 decimals of
precision.

## 2. Decision variables

| Variable | Domain | Meaning |
|----------|--------|---------|
| `match_day[m]` | 1..56 | Day of match m (AllDifferent over all 56 matches) |
| `is_alt[m]` | {0,1} | Whether m is hosted at the home team's alternate venue (constant 0 for teams without one) |
| `on_day[m,d]` | bool | Channel: `match_day[m] == d` |
| `plays_on[T,d]` | bool | Team T plays on day d (sum of on_day over T's matches) |
| `pos[T,m,k]` | bool | Match m sits at chronological position k ∈ 1..14 in team T's sequence |
| `seq_day[T,k]` | 1..56 | Day of T's k-th match (channelled via pos) |
| `venue_at_pos[T,k]` | 0..12 | Venue index where T plays its k-th match |
| `team_total_dist[T]` | km | Total geographic distance travelled by T |

## 3. Hard constraints

| ID | Constraint | Encoding |
|----|-----------|----------|
| H1 | Pair balance | Structural — 56 matches pre-enumerated as ordered (home, away). |
| H2 | One match per day | `AllDifferent(match_day)` + `ExactlyOne(on_day[·,d])` per day. |
| H3 | Progressive balance ≤ 4 | `played_by_end[T,d]` cumulative; pairwise differences ≤ 4 across all teams, every day. |
| H4 | Min rest 2 days | `plays_on[T,d] + plays_on[T,d+1] ≤ 1` for all (T,d). Also enforced by `seq_day[T,k+1] ≥ seq_day[T,k] + 2`. |
| H5 | Alt venue ≤ 2 | `sum(is_alt[m] for m hosted by T) ≤ 2` for each team with an alternate. |
| H6 | Blackouts | For each (venue, day) blackout: if primary blocked, force `is_alt = 1` on that day (or forbid the match entirely if alt also blocked / nonexistent). Symmetric handling for alt blackouts. |

## 4. Linearisation techniques

CP-SAT is integer / linear — every non-linear term in the objective is
re-expressed via either an `AddElement` table lookup or a big-M / bool×int
envelope.

### 4.1 Per-position chronology (channelling)

For each team T:
- `pos[T,m,k]` booleans with `ExactlyOne` along each axis (one match per
  position, one position per match).
- `seq_day[T,k] == match_day[m]` whenever `pos[T,m,k] = 1` (via
  `OnlyEnforceIf`).
- `venue_at_pos[T,k]` is fixed (via `OnlyEnforceIf` over `pos` × `is_alt`)
  to either the primary or alternate venue index of the match's host.

### 4.2 Travel cost (C1)

Between consecutive positions:
```
flat_idx       = venue_at_pos[T,k] * 13 + venue_at_pos[T,k+1]
base_cost      = AddElement(flat_idx, COST_FLAT)   # CR-scaled int
g_factor       = AddElement(seq_day[T,k+1], G_BY_DAY)  # 1000·g(w)
scaled_cost    = base_cost * g_factor              # AddMultiplicationEquality
contrib        = scaled_cost / 1000                # AddDivisionEquality
```
`G_BY_DAY` is a 57-entry table mapping each day → 1000·g(week(day)) so
the step multiplier {1.0, 1.25, 1.6} is selected without auxiliary booleans.

### 4.3 Distance fatigue (C2) — convex p = 1.5

The team's total distance is computed as the sum of per-hop
`AddElement(flat_idx, DIST_FLAT)`. We then **bucket** the distance at 50-km
resolution and use a 565-entry pre-computed integer table:
```
b_idx          = AddDivisionEquality(total_d, 50)
c2             = AddElement(b_idx, C2_TABLE)
```
where `C2_TABLE[b] = round(SCALE · κ · max(0, 50b − D0)^{1.5})`.

### 4.4 Stay penalty (C3) — convex q = 2

```
gap            = seq_day[T,k] − seq_day[T,k-1]
stay_pen       = AddElement(gap, STAY_TABLE)
not_at_home    = (T is visitor) OR (T is host AND is_alt[m] = 1)
c3             = stay_pen · not_at_home   # big-M envelope
```

`STAY_TABLE[x] = round(SCALE · η · max(0, x − x0)^2)`. The
`not_at_home` indicator is computed by summing `pos × visit-flag`
contributions (per host-vs-visitor case), with one auxiliary bool per
host match for the AND with `is_alt`.

### 4.5 Density penalty (C4) — 4-step

For each (team, day):
```
cw[T,d]        = sum(plays_on[T,d'] for d' in (d-7, d-1))   # 0..3
dv             = AddElement(cw, DENSITY_TABLE)              # 4 entries
c4             = dv · plays_on[T,d]                         # big-M envelope
```

### 4.6 Gap penalty (C5) — asymmetric quadratic

```
gap            = seq_day[T,k] − seq_day[T,k-1]
gap_pen        = AddElement(gap, GAP_TABLE)
```
`GAP_TABLE[t]` precomputes `a_low·(τ*−τ)²` for τ ≤ τ* and
`a_high·(τ−τ*)²` otherwise.

### 4.7 Equity (C6)

```
d_max          = AddMaxEquality(team_total_dist[·])
d_min          = AddMinEquality(team_total_dist[·])
slack          ≥ d_max − d_min − Δ₀  (slack ≥ 0 by domain lower bound)
penalty        = λ_eq · slack
```

## 5. Objective

`Maximize(sum(revenue_terms) − sum(penalty_terms))`

Revenue:
- **R1**: per match, per day, a pre-tabulated payoff (α / β / γ) is
  multiplied by `on_day[m,d]` and summed.
- **R2**: per match with `is_alt = 1`, the team's alt bonus is paid.

## 6. Solver configuration

```python
solver.parameters.max_time_in_seconds = TIME_LIMIT_S   # env override
solver.parameters.num_search_workers  = 8              # matches eval HW
solver.parameters.log_search_progress = True
```

CP-SAT runs **anytime**: with longer wall time, the lower bound from the
LP relaxation tightens and the incumbent improves. 60 s yields a
feasible schedule satisfying all H1–H6; 1800 s+ closes most of the
optimality gap on the public instance.

## 7. Validation

`validate.py` is an independent script that:
1. Loads `schedule.json` and re-resolves the actual venue for each
   match using `is_alt_venue` ∈ `{primary, alternate}`.
2. Re-derives per-team chronologies from scratch.
3. Audits each of H1–H6 with separate counters.
4. Exits non-zero on any violation.

The validator does **not** share code with the solver — it provides
independent confidence that the constraints have not been smuggled
through the model.
