# PowerPlay — IPL Scheduling

Our submission for the CSEA × IIT Guwahati optimisation hackathon.

The problem: take 8 cricket franchises, 56 days, and 56 matches; figure out
who plays whom, when, and at which ground, so that broadcaster revenue is
maximised and travel / fatigue penalties are kept in check. There are six
hard constraints (pair balance, daily allocation, progressive balance,
rest days, alt-venue cap, blackouts) and an objective function `J` that
sums broadcaster bids + alt-venue bonuses minus five fatigue penalties
and a league-wide equity penalty.

We modelled it as a single CP-SAT (constraint-programming + SAT) problem
using Google OR-Tools.

## What we got

On the public instance `inst_1`, our schedule scores **J = 38.07 Cr**
after a 30-minute solve on 8 cores. All 6 hard constraints pass an
independent validator. CP-SAT's LP relaxation puts the upper bound at
~120 Cr, so there's still gap to close — more wall time would help.

## Running it

```bash
pip install -r requirements.txt
python solver.py           # writes schedule.json (the deliverable)
python validate.py         # checks H1-H6
python score.py            # recomputes J using PDF formulas
```

By default the solver gives itself 5 minutes. For a serious run:

```bash
SOLVER_TIME_LIMIT=1800 python solver.py
```

The solver looks for the 5 input JSONs in either the project root or an
`inst_1/` subfolder. Override with `INSTANCE_DIR=/path/to/instance`.

## Files

- `solver.py` — the actual solver. Builds the CP-SAT model, runs it,
  writes `schedule.json`.
- `schedule.json` — our 56-match schedule.
- `validate.py` — independent feasibility checker. Re-resolves each
  match's venue and re-derives every constraint from scratch.
- `score.py` — independent objective re-calculator. Recomputes `J` from
  the schedule in plain floating point (no integer scaling). Useful as a
  sanity check that the solver's claimed score matches what the grader
  will compute.
- `APPROACH.md` — write-up of the modelling: variables, constraints,
  linearisation tricks used for the convex penalty terms.
- `requirements.txt` — just `ortools`.

The three Python files are independent. `validate.py` and `score.py`
don't share any logic with `solver.py` — they each read `schedule.json`
and the input JSONs from scratch. So if the solver had a bug, the
auditors would catch it. We verified the solver's claimed `J = 38.0716`
agrees with the spec-faithful recompute of `J = 38.0430` within rounding
tolerance (0.07%).

## How `J` breaks down

This is what `score.py` reports for our current schedule:

```
R1  Broadcaster revenue   :  +97.89
R2  Alt-venue bonus       :  +17.10
C1  Travel cost           :  -20.05
C2  Distance fatigue      :   -3.21
C3  Stay penalty          :  -11.95
C4  Density               :  -12.60
C5  Gap                   :  -28.60
C6  Equity                :   -0.54
-------------------------------------
J                         :  +38.04 Cr
```

The big-ticket revenue comes from putting marquee matches (MI×CSK,
CSK×RCB) on weekends; the biggest single penalty is `C5` (gap), which
the spec heavily penalises for sub-3-day rest periods.

## A note on the alternate venues

Five teams have alt venues with revenue bonuses but worse travel costs:

| Team | Primary | Alt | Alt bonus |
|------|---------|-----|-----------|
| MI   | Mumbai | Pune | 0.3 Cr |
| DC   | Delhi | Raipur | 1.8 Cr |
| SRH  | Hyderabad | Vizag | 1.2 Cr |
| RR   | Jaipur | Guwahati | **3.2 Cr** |
| PBKS | Mohali | Dharamsala | 2.2 Cr |

`is_alt_venue: true` in the output means the home team chose its alt.
Hard constraint H5 caps it at 2 of a team's 7 home matches. The solver
ended up using all 8 available slots (2×4 teams + 1 for MI, whose bonus
is small).

## On AI assistance

Per the hackathon rules: we used **Anthropic's Claude** (via Claude Code)
to draft the model and documentation. The CP-SAT modelling choices,
linearisation strategy for the convex penalties, parameter tuning, and
final validation were ours.

## What we'd do with more time

A few things we know are imperfect but didn't have time to address:

1. **Warm-start.** CP-SAT spends a lot of search time finding *any*
   feasible solution. A greedy heuristic (round-robin matches with
   blackout-aware day picking) could be fed via `model.AddHint()` to cut
   the time-to-incumbent dramatically.
2. **C3 stay penalty.** We linearised it per-position-transition, which
   coincides with the PDF's merged-stay interpretation on our current
   schedule but could under-count in other topologies. Wiring up a true
   contiguous-run aggregator would close that gap.
3. **C2 distance bucketing.** We bucket at 50 km for the AddElement
   lookup; using the bucket centre instead of the lower bound would
   reduce a small (~5×10⁻⁴ Cr/team) systematic under-estimate.

None of these are bugs — they're known modelling shortcuts called out in
`APPROACH.md`. The validator and scorer would catch any drift.
