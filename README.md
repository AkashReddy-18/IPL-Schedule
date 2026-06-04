# PowerPlay — T20 League Scheduling Submission

CSEA · IIT Guwahati · Optimisation Track

A complete solver for the 8-team / 56-day / 56-match IPL-style scheduling
problem, framed as a single CP-SAT integer-programming model.

---

## Current result

On the public instance (`inst_1`):

| Metric | Value |
|--------|-------|
| Objective J | **38.0716 Cr** (solver) · **38.0430 Cr** (independent re-scoring) |
| Hard constraints H1–H6 |  All satisfied (independently validated) |
| Solver wall time | 1 800 s (30 min) on 8 workers |
| Optimality gap | Best-bound = 120.16 Cr → 68 % of optimum captured |

---

## What's inside

| File              | Purpose |
|-------------------|---------|
| `solver.py`       | CP-SAT solver (Python / OR-Tools). Reads the 5 input JSONs, writes `schedule.json`. |
| `schedule.json`   | Final 56-match schedule produced by the solver — **the deliverable**. |
| `validate.py`     | Independent post-hoc validator. Re-checks the 6 hard constraints (H1–H6). |
| `score.py`        | Independent objective re-calculator. Recomputes J from the schedule using the exact PDF formulas (no integer scaling / linearisations) and reports a per-term breakdown. |
| `requirements.txt`| Python dependency list (just `ortools`). |
| `APPROACH.md`     | Algorithm explanation: variables, constraints, linearisations, objective. Written for the 20-pt "Solution Explanation" component. |
| `README.md`       | This file. |

### How the three programs relate

The three Python files are **fully independent** — none of them shares code
with the others. They each read `schedule.json` and the 5 instance JSONs,
and answer one question:

```
                       schedule.json
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
         solver.py     validate.py     score.py
        (PRODUCER)    (auditor #1)   (auditor #2)
                       feasibility     objective
                       "Are H1-H6      "Is the J
                       all OK?"        correct?"
```

- `solver.py` *produces* the schedule.
- `validate.py` *audits feasibility* — exits non-zero if any hard rule is broken.
- `score.py` *audits the score* — re-derives J in true floating point.

Crucially, neither auditor trusts the solver: each one re-derives its
answer from the schedule and the spec. If `score.py` returned a J wildly
different from what the solver reported, it would mean a linearisation is
silently dropping value the official grader won't accept. (On the current
schedule the two values agree to 0.07 %.)

---

## What is J?

`J` is the **objective function** — the single number the hackathon grades
schedules by. From the PDF:

```
J  =  Σ_m R(m)            ← R1  Broadcaster revenue per match (α / β / γ tiers)
    + Σ_m B_alt(m)        ← R2  Alternate-venue bonus per match
    − Σ_T Travel(T)       ← C1  City-to-city travel cost (scaled in late weeks)
    − Σ_T Distance(T)     ← C2  Convex p=1.5 penalty on excess total km
    − Σ_T Stay(T)         ← C3  Quadratic penalty on long non-home stays
    − Σ_T Density(T)      ← C4  Step penalty on 7-day match-count windows
    − Σ_T Gap(T)          ← C5  Asymmetric quadratic around τ* = 3 days
    − λ_eq · Equity       ← C6  League-wide max-min distance disparity
```

Units are **Crore INR**. Higher J = better schedule. Our current
schedule's per-term breakdown (from `score.py`):

| Term | Value (Cr) |
|------|----:|
| R1 Broadcaster revenue | +97.89 |
| R2 Alt-venue bonus     | +17.10 |
| C1 Travel              |  −20.05 |
| C2 Distance fatigue    |   −3.21 |
| C3 Stay (merged)       |  −11.95 |
| C4 Density             |  −12.60 |
| C5 Gap                 |  −28.60 |
| C6 Equity              |   −0.54 |
| **J total**            | **+38.04** |

---

## AI Assistant Declaration

This submission was authored with assistance from **Anthropic Claude**
(Claude Code). Claude was used for code drafting, linearisation strategy
(CP-SAT modelling of the convex penalty terms), and documentation. All
design decisions, parameter tuning, validation, and final inspection were
performed by the team.

---

## How to run

### 1 · Install dependencies
```bash
pip install -r requirements.txt
```

### 2 · Place the instance
The solver auto-detects the 5 input JSONs in either:
- the project root (parent of `solver.py`), or
- a sibling folder named `inst_1/`.

You can also override the location explicitly:
```bash
INSTANCE_DIR=/path/to/instance python solver.py
```

The 5 required files are: `teams.json`, `travel_matrix.json`,
`broadcaster_bids.json`, `blackouts.json`, `parameters.json`.

### 3 · Solve
```bash
# default: 300-second wall time, 8 workers
python solver.py

# longer run for a tighter optimality gap (recommended for the public/hidden test)
SOLVER_TIME_LIMIT=1800 python solver.py
```

The schedule is written to `schedule.json` next to `solver.py`.

### 4 · Validate feasibility (H1–H6)
```bash
python validate.py
```
Re-resolves each match's actual venue from the `is_alt_venue` flag,
re-derives the per-team chronologies, and asserts H1–H6 independently.
Exit code `0` on success, `1` otherwise.

### 5 · Re-score the objective
```bash
python score.py
```
Recomputes R1, R2, C1–C6 and J from the schedule using the **raw PDF
formulas in floating point** — no integer scaling, no bucketing, no
linearisation. Prints a labelled per-term breakdown and the per-team
distance table.

### Full workflow
```bash
pip install -r requirements.txt
python solver.py        # produces schedule.json
python validate.py      # asserts H1-H6   →  "ALL HARD CONSTRAINTS SATISFIED"
python score.py         # recomputes J    →  "J = 38.04 Cr"
```

---

## Parametric generalisation (+20-pt bonus criterion)

No problem parameters are hard-coded. The solver reads:

- `κ, p, η, q, x₀, D₀, δ-table, a_low, a_high, τ*, Δ₀, λ_eq` from `parameters.json`
- `g(w)` multiplier vector (length 8) from `parameters.json`
- All 28 broadcaster bids and preferred-day sets from `broadcaster_bids.json`
- Full 13 × 13 travel matrix (km + Cr) from `travel_matrix.json`
- All blackout `(venue, day)` tuples from `blackouts.json`
- Team identities, primary / alternate venues, alt bonuses from `teams.json`

The only string literals in the solver that match the input domain are the
five canonical JSON file names. The solver runs unchanged against any
perturbed instance that follows the same schema.

---

## Hardware

Developed on macOS / Python 3.14 with `ortools==9.15`. The solver is
configured for the spec'd evaluation environment (`num_search_workers = 8`)
and fits comfortably in 16 GB RAM.

---

## Output schema reminder

`schedule.json` contains exactly 56 entries, each:

```json
{
  "day": 1,                   // integer ∈ {1..56}, all unique
  "home_team": "CSK",         // team code
  "away_team": "DC",          // team code, != home_team
  "is_alt_venue": false       // true ⇒ played at the home team's alternate
}
```

`is_alt_venue = true` is only meaningful for the 5 teams with an alternate
venue (MI, DC, SRH, RR, PBKS). For CSK / RCB / KKR it is always `false`.
At most 2 of a team's 7 home matches may use the alternate (H5).
