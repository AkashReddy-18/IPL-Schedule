# PowerPlay — T20 League Scheduling Submission

CSEA · IIT Guwahati · Optimisation Track

## What's inside

| File              | Purpose                                                       |
|-------------------|---------------------------------------------------------------|
| `solver.py`       | CP-SAT solver (Python / OR-Tools). Reads the 5 input JSONs, writes `schedule.json`. |
| `schedule.json`   | Final 56-match schedule produced by the solver (the deliverable). |
| `validate.py`     | Independent post-hoc validator that re-checks all 6 hard constraints (H1–H6). |
| `requirements.txt`| Python dependencies (only `ortools`).                         |
| `APPROACH.md`     | Algorithm explanation: variables, constraints, linearisations, objective. |

## AI Assistant Declaration

Per the hackathon rules: this submission was authored with assistance from
**Anthropic Claude** (Claude Code). Claude was used for code drafting,
linearisation strategy, and documentation. All design decisions, parameter
tuning, validation, and final inspection were performed by the team.

### Implementation Notes
- The solver uses integer centi-lakhs (Cr * 10,000) for all cost calculations to maintain high precision while adhering to the integer-only constraints of the CP-SAT engine.

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Place the test instance
The solver auto-detects the 5 input JSONs in one of two locations:
- the project root (alongside `solver.py`'s parent directory), or
- a sibling folder named `inst_1/`.

You may also override the location explicitly:
```bash
INSTANCE_DIR=/path/to/instance python solver.py
```

The 5 required files are: `teams.json`, `travel_matrix.json`,
`broadcaster_bids.json`, `blackouts.json`, `parameters.json`.

### 3. Solve

```bash
# default: 300-second wall time, 8 workers
python solver.py

# tighten the optimality gap with more wall time (recommended: 30 min)
SOLVER_TIME_LIMIT=1800 python solver.py
```

The schedule is written to `schedule.json` in the same directory as
`solver.py`.

### 4. Validate

```bash
python validate.py
```
This re-resolves every match's actual venue via the `is_alt_venue` flag,
re-derives the per-team chronologies, and asserts each of H1–H6 against the
solver's output. Exit code `0` on success.

## Parametric generalisation

No problem parameters are hard-coded. The solver reads:
- κ, p, η, q, x₀, D₀, δ-table, a_low, a_high, τ\*, Δ₀, λ_eq from `parameters.json`
- g(w) multiplier vector (length 8) from `parameters.json`
- All 28 broadcaster bids and preferred-day sets from `broadcaster_bids.json`
- Full 13×13 travel matrix from `travel_matrix.json`
- All blackout (venue, day) tuples from `blackouts.json`
- Team identities, primary / alternate venues, alt bonuses from `teams.json`

The only string literals in the solver that match the input domain are the
five canonical JSON file names; everything else is read dynamically.

## Hardware

Developed on macOS / Python 3.14 with `ortools==9.15`. The CP-SAT solver
is configured for the spec'd evaluation box (`num_search_workers=8`,
fits in 16 GB RAM).

## Output schema reminder

`schedule.json` contains exactly 56 entries:

```json
{
  "day": 1,                   // integer ∈ {1..56}, all unique
  "home_team": "CSK",         // team code
  "away_team": "DC",          // team code, != home_team
  "is_alt_venue": false       // true ⇒ played at the home team's alternate
}
```
