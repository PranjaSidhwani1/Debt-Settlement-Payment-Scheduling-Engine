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

This solution is implemented as a deterministic pipeline in `feasibility/engine.py` with data models in `feasibility/models.py`.

1. Input parsing
   - `run.py` loads the case JSON files and constructs typed objects: `Client`, `Offer`, and `CreditorRules`.
   - `models.py` provides JSON deserialization, date helpers, and `default_first_payment_date()`.

2. Offer and fee computation
   - `_offer_total()` computes the creditor payment total from `offer.current_balance_cents` and `offer.settlement_pct`.
   - `_program_fee_total()` computes the total program fee from `offer.original_balance_cents` and `rules.program_fee_pct`.
   - Both use `_round_half_up()` with `Decimal` to ensure exact round-half-up behavior.

3. Cadence generation
   - `_cadence_to_horizon()` builds the payment cadence up to `client.last_draft_date`.
   - It preserves true end-of-month recurrence when the first payment date is month-end, otherwise it uses month-day clamping.

4. Candidate schedule generation
   - `_choose_best_schedule()` iterates `k` from 1 to `min(rules.max_terms, rules.max_payments)`.
   - For each `k`, it generates candidates using:
     - `_generate_even_payments()` for `even_pays`,
     - `_generate_balloon_payments()` when ballooning is permitted,
     - `_generate_staircase_payments()` otherwise.
   - Each generator returns either a valid payment vector or `None`.

5. Rule validation
   - `_is_valid_payment_sequence()` checks non-negative payments, non-decreasing order, token-pay limits, and position-based floor rules.
   - `_payment_floor()` computes the applicable floor from `min_payment_cents` and `min_payment_tiers`.
   - `_count_token_pays()` enforces `max_token_pays`.

6. Balance path construction
   - `_build_fixed_balance_path()` merges future ledger entries, extra funding, and creditor payments with bank fees.
   - It simulates balance progression over sorted dates and returns `fixed_balance` only if the balance never goes negative.

7. Fee allocation and schedule simulation
   - `_allocate_program_fee()` greedily assigns program fees to earliest allowable dates while preserving the future balance path.
   - `_simulate_schedule()` rebuilds the date-by-date ledger including fee allocations, and returns `ScheduleRow` output if the balance remains non-negative.

8. Schedule ranking
   - `_try_schedule()` evaluates a candidate schedule and returns a metric tuple containing a negative fee allocation vector and the payment count.
   - `_choose_best_schedule()` picks the lowest metric, so schedules with earlier fee collection are preferred, with shape rank as a final tie-breaker.

9. Recovery calculation
   - If no feasible schedule is found, `evaluate_offer()` calls `_find_minimum_lump_sum()` and `_find_minimum_monthly_increment()`.
   - These use binary search over candidate extra funding amounts and `_is_schedule_feasible_with_extras()` to test feasibility.
   - Guardrail limits are applied to lump sum and monthly increment results.

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
