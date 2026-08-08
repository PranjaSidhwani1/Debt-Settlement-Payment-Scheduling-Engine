# Settlement Feasibility & Fee Engine

This repository implements a settlement affordability engine that evaluates whether a client can satisfy a settlement offer using a single escrow account. When feasible, it produces a payment schedule that keeps the account non-negative and collects the program fee as early as possible.

## What it does

- Reads `client.json`, `offer.json`, and `creditor_rules.json`.
- Computes the creditor offer total and the total program fee.
- Generates candidate payment schedules for allowed shapes: `even`, `balloon`, and `staircase`.
- Simulates the escrow ledger date-by-date, applying credits before debits.
- Validates each candidate against payment floors, token pay limits, tier minimums, bank fees, and horizon constraints.
- Selects the best feasible schedule by prioritizing early fee collection.
- If no feasible schedule exists, computes recovery information for minimum lump sum and minimum monthly increment.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Project structure

- `feasibility/models.py`
  - Data models for `Client`, `Offer`, `CreditorRules`, ledger entries, and results.
  - JSON parsing helpers, payment cadence builders, and date utilities.

- `feasibility/engine.py`
  - Core implementation of `evaluate_offer`.
  - Generates payment amounts and evaluates feasibility.
  - Allocates program fees and ranks schedules.

- `run.py`
  - Command-line runner that evaluates a case directory and prints JSON output.

- `tests/`
  - Test coverage for provided cases and additional edge cases.

## Solution architecture

The engine is implemented as a deterministic scheduling pipeline with three main layers:

- **Input & model layer** (`run.py`, `feasibility/models.py`)
- **Schedule generation layer** (`feasibility/engine.py` payment candidate builders)
- **Balance validation layer** (`feasibility/engine.py` fee allocation + ledger simulation)

### Component responsibilities

- `run.py`
  - Reads the case folder and loads `client.json`, `offer.json`, and `creditor_rules.json`.
  - Converts JSON into typed domain objects and dispatches `evaluate_offer()`.
  - Prints the final JSON output.

- `feasibility/models.py`
  - Defines `Client`, `Offer`, `CreditorRules`, ledger entry types, and output schemas.
  - Implements date helper functions, cadence generation, and JSON parsing.
  - Provides `default_first_payment_date()` and horizon-aware date utilities.

- `feasibility/engine.py`
  - Implements the core solver with a tight, deterministic search over allowed schedule options.
  - Computes offer totals, generates payment vectors, validates creditor rules, allocates fees, and simulates the escrow ledger.

### High-level data flow

1. Parse inputs into domain objects.
2. Compute `offer_total` and `program_fee_total` using exact round-half-up arithmetic in cents.
3. Build the candidate payment cadence from `offer.first_payment_date` up to `client.last_draft_date`.
4. Enumerate feasible `k` values: `1 .. min(rules.max_terms, rules.max_payments)` while staying within the cadence horizon.
5. For each `k`, build payment amount candidates for the allowed shapes.
6. Validate each candidate against floor rules, token-pay caps, tier minimums, non-decreasing order, and exact sum.
7. For valid payment vectors, allocate program fees to earliest allowable dates and simulate the complete ledger.
8. Select the best schedule by comparing fee collection timing and shape rank.
9. If no feasible schedule exists, compute recovery options and return infeasibility.

### Payment schedule generation

The solver supports three schedule shapes depending on creditor rules:

- `even`
  - Uses `_generate_even_payments()`.
  - Divides `offer_total` by `k` and distributes the remainder to later payments.
  - Ensures the sequence is non-decreasing and respects minimums.

- `balloon`
  - Uses `_generate_balloon_payments()` when `rules.is_ballooning_allowed` is true.
  - Assigns the earliest payments as close to the minimum allowed as possible.
  - Allocates the remaining balance into the final payment.
  - Valid only when token-pay and tier constraints remain satisfied.

- `staircase`
  - Uses `_generate_staircase_payments()` when ballooning is not required or even payments are disabled.
  - Builds a monotonic sequence with at most `rules.max_segments` distinct payment levels.
  - Enforces floors and relies on a deterministic step allocation strategy.

### Constraint validation

Each candidate vector is checked by `_is_valid_payment_sequence()`:

- All payments must be ≥ 0.
- Sequence must be non-decreasing.
- Sum of payments must equal `offer_total` exactly.
- Each payment must meet its position-specific floor from `min_payment_cents` and `min_payment_tiers`.
- The number of token payments equal to `min_payment_cents` must not exceed `rules.max_token_pays`.

Floor calculations are centralized in `_payment_floor()` to keep tier logic deterministic.

### Fee allocation strategy

The program fee is allocated as early as possible once a payment layout is selected.

- `_allocate_program_fee()` takes a proposed payment schedule and the candidate balance path.
- It greedily charges program fees on payment dates while preserving non-negative balance after bank fee and creditor payment.
- If the full fee cannot be charged on payment dates, the algorithm can extend allocation to additional fee-only dates before horizon.

This is the core mechanism that makes the chosen schedule “best” under the fee timing objective.

### Ledger simulation

Ledger validation is performed with a chronological event stream:

- Existing future ledger entries from `client.ledger` are included.
- Creditor payments, bank fees, and program fees are inserted at their scheduled dates.
- Events are sorted by date.
- On each date, all credits are applied before debits.
- The running balance is checked after each event to ensure it never goes negative.

This simulation is implemented in `_simulate_schedule()` and `_build_fixed_balance_path()`.

### Schedule ranking

The solver compares feasible candidates using a structured metric:

- Primary: fee allocation timing (earlier fee collection is better).
- Secondary: payment count and payment shape preference.
- Tertiary: deterministic tie-breaking to ensure repeatable output.

The ranking logic is centralized in `_try_schedule()` and `_choose_best_schedule()`.

### Infeasibility and recovery

When no schedule is feasible, the engine computes deterministic recovery options:

- Minimum lump sum required to make a feasible schedule possible.
- Minimum monthly increment to future drafts required to restore feasibility.

Recovery result generation uses guarded binary search and explicit guardrail checks, ensuring the output stays aligned with the intended assignment behavior.

## Usage

Evaluate an example case:

```bash
python run.py cases/case1_feasible_even
```

Run tests:

```bash
pytest -q
```

## Design approach

The engine works by generating and validating candidate schedules instead of solving a single closed-form formula. This provides a clear way to enforce all hard constraints and choose the most appropriate schedule for the fee-timing objective.

Key design principles:

- enforce all creditor rules explicitly,
- keep all values in integer cents,
- apply credits before debits on the same date,
- respect the cadence horizon (`last_draft_date`),
- choose feasible schedules that collect program fees as early as possible.

Alternatives considered:

- full integer programming: expressive but overkill for this scope,
- greedy smallest-`k` first: would not satisfy the ranking objective,
- single-shape solver: too inflexible for the provided rule combinations.

## Payment shape interpretation

The project supports three shapes, as permitted by the creditor rules.

### `even`

- All creditor payments are as equal as possible.
- If the total does not divide evenly, the remainder is distributed to later payments.
- This keeps the sequence non-decreasing and satisfies even-payment constraints.

### `balloon`

- Early payments are set as low as the rules allow.
- The final payment absorbs the remaining balance.
- This shape is only used when `is_ballooning_allowed` is true and it passes all floor and token-pay constraints.

### `staircase`

- Payments increase in steps, using at most `max_segments` distinct values.
- A flat payment sequence is allowed if it satisfies the rules.
- The implementation builds staircase candidates with floor-aware segments and rejects any that violate constraints.

## Fee timing objective

The core objective is feasibility plus fast fee collection. The solver does not simply select the first valid payment count; it ranks feasible candidates by how early program fees can be allocated.

That means:

- program fees are allocated on cadence dates starting with the earliest feasible date,
- bank fees are only applied on creditor payment dates,
- schedules are compared by fee timing first, then by shape.

## Assumptions and limitations

- The main objective is earliest program-fee collection for a feasible schedule, not minimum payment count.
- The staircase schedule generation is intentionally conservative and focused on valid, rule-abiding outcomes rather than enumerating every possible monotonic sequence.
- Recovery calculations use deterministic strategies to find a minimum lump sum or monthly increment rather than exploring every arbitrary funding pattern.
- The implementation assumes provided JSON inputs are structurally valid and the ledger entries are dated correctly.

## Test coverage

The repository includes tests for the four provided example cases and additional scenarios, including:

- even payment scheduling,
- balloon scheduling when allowed,
- staircase scheduling with segment limits,
- token-pay and tier floor enforcement,
- exact-sum and non-decreasing payment constraints,
- same-day credit-before-debit simulation,
- fee timing behavior,
- infeasible recovery calculations.

## Notes

- All monetary calculations use integer cents.
- Date cadence respects end-of-month versus fixed-day rules.
- The output format preserves the required structure: feasibility, `pay_shape_used`, schedule rows, and recovery details when infeasible.
