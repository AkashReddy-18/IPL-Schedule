# PowerPlay — IPL Scheduling

Our submission for the CSEA × IIT Guwahati optimisation hackathon.

## The problem in one paragraph

A franchise T20 league has 8 teams and runs over 56 days. Every team
plays every other team twice — once at each team's home ground — for a
total of 56 matches, exactly one per day. We have to decide who plays
whom on which day, and (for the five teams with a second "alternate"
home stadium) which of their two grounds hosts each home match. The
catch: broadcasters pay a premium for marquee matchups on weekends,
travel costs money, players need rest, and grounds have blackout days
they can't be used. The schedule is graded by an objective `J` that
sums revenue (broadcaster bids + alt-venue bonuses) and subtracts six
fatigue / travel / equity penalties.

## How we solved it

The whole thing fits naturally as a constraint-programming problem, so
we modelled it as a single CP-SAT (constraint-programming + SAT)
problem using Google OR-Tools. Every match gets a day variable and
(when applicable) an `is_alt_venue` boolean; hard constraints H1–H6 are
expressed directly on those variables; the six penalty terms are
linearised with a mix of step-table lookups (for the convex `p = 1.5`
distance fatigue), big-M envelopes (for booleans-times-integers in the
stay and density penalties), and standard `AddMaxEquality` / cumulative
counters for the equity term. The solver runs anytime — every extra
minute it gets, it finds a slightly better schedule. We let it run for
30 minutes per instance.

## What we got

| Instance | Feasibility | J (Cr) | Solver wall time |
|----------|-------------|-------:|-----------------:|
| `inst_1` | FEASIBLE    | **39.46** | 30 min        |
| `inst_2` | FEASIBLE    | **30.68** | 30 min        |

Both schedules pass our independent feasibility validator on all six
hard constraints. CP-SAT's LP relaxation puts the upper bound around
120 Cr, so there's still room to close with longer search.

## How `J` breaks down for inst_1

```
R1  Broadcaster revenue   :  +99.71
R2  Alt-venue bonus       :  +17.10
C1  Travel cost           :  -20.05
C2  Distance fatigue      :   -3.21
C3  Stay penalty          :  -12.35
C4  Density               :  -12.60
C5  Gap                   :  -28.60
C6  Equity                :   -0.54
-------------------------------------
J                         :  +39.46 Cr
```

The big-ticket revenue comes from putting marquee matchups (MI vs CSK,
CSK vs RCB) on weekends. The biggest single penalty is `C5` (gap),
because the spec heavily punishes sub-3-day rest periods between a
team's matches.

## A note on the alternate venues

Five teams have an alternate ground in a second city. Hosting there
earns a revenue bonus but costs more in travel. The trade-off is
capped at 2 of a team's 7 home matches by H5.

| Team | Primary | Alt | Alt bonus |
|------|---------|-----|-----------|
| MI   | Mumbai | Pune | 0.3 Cr |
| DC   | Delhi | Raipur | 1.8 Cr |
| SRH  | Hyderabad | Vizag | 1.2 Cr |
| RR   | Jaipur | Guwahati | **3.2 Cr** |
| PBKS | Mohali | Dharamsala | 2.2 Cr |

`is_alt_venue: true` in the schedule output means that match is hosted
at the team's alternate. The solver tends to burn all of RR and PBKS's
two-slot budget (those teams have the biggest bonuses), and is
selective with MI's because the 0.3 Cr bonus rarely justifies the
extra travel cost.

## Running it

```bash
pip install -r requirements.txt
python solver.py           # writes schedule.json
python validate.py         # checks H1–H6
python score.py            # recomputes J from the schedule
```

By default the solver gives itself 5 minutes. For a competitive run:

```bash
SOLVER_TIME_LIMIT=1800 python solver.py
```

The solver looks for the 5 input JSONs in either the project root or
an `inst_1/` sibling folder. Override with
`INSTANCE_DIR=/path/to/instance`. To target a specific output path use
`SCHEDULE_OUTPUT=/path/to/schedule.json`.

## Files in this folder

- `solver.py` — the CP-SAT model and search loop. Reads the 5 input
  JSONs, writes a `schedule.json`.
- `schedule.json` — our 56-match output.
- `validate.py` — independent feasibility checker. Reads the schedule
  back from disk and re-derives all six hard constraints from
  scratch. Exits non-zero on any violation.
- `score.py` — independent objective re-calculator. Reads the
  schedule and recomputes `J` term-by-term in plain floating point
  using the formulas straight from the PDF. Catches any drift between
  what the solver thinks it scored and what the math actually says.
- `APPROACH.md` — longer write-up of the modelling, including the
  linearisation tricks used for the convex penalty terms.
- `requirements.txt` — just `ortools`.

The three Python files are deliberately independent. `validate.py`
and `score.py` share no code with `solver.py` — they each parse
`schedule.json` from disk and re-derive everything. So if the solver
had a bug, the auditors would catch it.

## On AI assistance

Per the hackathon rules: we used **Anthropic's Claude** (via Claude
Code) to help draft the CP-SAT model, the linearisation strategy for
the convex penalty terms, and this documentation. The modelling
decisions, parameter tuning, and final validation were ours.
