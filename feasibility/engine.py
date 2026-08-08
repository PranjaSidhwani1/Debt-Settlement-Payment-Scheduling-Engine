"""Candidate implementation goes here.

Implement ``evaluate_offer`` so that it satisfies the rules in ASSIGNMENT.md and
the example expectations in tests/test_cases.py. The dataclasses below define the
required OUTPUT shape (see ASSIGNMENT.md "Output"). You may add helpers, modules,
or rewrite internals freely, but keep ``evaluate_offer``'s signature and the
serialized shape of ``Result`` (so the runner and tests work).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from math import inf

from feasibility.models import (
    Client,
    CreditorRules,
    Offer,
    default_first_payment_date,
    end_of_month,
    is_end_of_month,
    load_case,
    monthly_payment_dates,
)


@dataclass
class ScheduleRow:
    date: date
    creditor_payment_cents: int
    program_fee_cents: int
    bank_fee_cents: int
    balance_cents: int


@dataclass
class FundsOption:
    amount_cents: int
    within_guardrail: bool
    reason: str
    # lump-sum only:
    date: date | None = None
    # monthly-increment only:
    num_drafts: int | None = None


@dataclass
class AdditionalFunds:
    lump_sum: FundsOption
    monthly_increment: FundsOption


@dataclass
class Result:
    feasible: bool
    # One of "even", "staircase", or "balloon" — the shape your solution produced
    # (driven by the creditor flags). None when infeasible.
    pay_shape_used: str | None = None
    schedule: list[ScheduleRow] | None = None
    additional_funds: AdditionalFunds | None = None

    def to_dict(self) -> dict:
        out: dict = {"feasible": self.feasible, "pay_shape_used": self.pay_shape_used}
        out["schedule"] = (
            [
                {
                    "date": r.date.isoformat(),
                    "creditor_payment_cents": r.creditor_payment_cents,
                    "program_fee_cents": r.program_fee_cents,
                    "bank_fee_cents": r.bank_fee_cents,
                    "balance_cents": r.balance_cents,
                }
                for r in self.schedule
            ]
            if self.schedule is not None
            else None
        )
        if self.additional_funds is None:
            out["additional_funds"] = None
        else:
            def opt(o: FundsOption) -> dict:
                d = {
                    "amount_cents": o.amount_cents,
                    "within_guardrail": o.within_guardrail,
                    "reason": o.reason,
                }
                if o.date is not None:
                    d["date"] = o.date.isoformat()
                if o.num_drafts is not None:
                    d["num_drafts"] = o.num_drafts
                return d

            out["additional_funds"] = {
                "lump_sum": opt(self.additional_funds.lump_sum),
                "monthly_increment": opt(self.additional_funds.monthly_increment),
            }
        return out


def _round_half_up(value: float | Decimal) -> int:
    d = Decimal(str(value)) if not isinstance(value, Decimal) else value
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _offer_total(offer: Offer) -> int:
    return _round_half_up(Decimal(str(offer.current_balance_cents)) * Decimal(str(offer.settlement_pct)))


def _program_fee_total(offer: Offer, rules: CreditorRules) -> int:
    return _round_half_up(Decimal(str(offer.original_balance_cents)) * Decimal(str(rules.program_fee_pct)))


def _cadence_dates(start: date, horizon: date) -> list[date]:
    if start > horizon:
        return []
    out: list[date] = []
    current = start
    while current <= horizon:
        out.append(current)
        if is_end_of_month(current):
            current = end_of_month(current.replace(day=1))
            current = current.replace(month=current.month + 1 if current.month < 12 else 1)
        else:
            current = current.replace(day=1)
        # Instead of custom month logic, reuse monthly_payment_dates by length 1
        current = monthly_payment_dates(current, 1)[0]
    return out


def _cadence_to_horizon(first_payment_date: date, horizon: date) -> list[date]:
    dates: list[date] = []
    current = first_payment_date
    while current <= horizon:
        dates.append(current)
        current = monthly_payment_dates(current, 2)[1]
    return dates


def _payment_floor(position: int, rules: CreditorRules) -> int:
    floor_amount = rules.min_payment_cents
    for tier_start, tier_min in rules.min_payment_tiers:
        if position >= tier_start:
            floor_amount = max(floor_amount, tier_min)
    return floor_amount


def _count_token_pays(payments: list[int], rules: CreditorRules) -> int:
    return sum(1 for p in payments if p == rules.min_payment_cents)


def _is_valid_payment_sequence(payments: list[int], rules: CreditorRules) -> bool:
    if any(p < 0 for p in payments):
        return False
    if any(payments[i] > payments[i + 1] for i in range(len(payments) - 1)):
        return False
    if _count_token_pays(payments, rules) > rules.max_token_pays:
        return False
    for i, p in enumerate(payments, start=1):
        if p < _payment_floor(i, rules):
            return False
    return True


def _generate_even_payments(total: int, k: int, rules: CreditorRules) -> list[int] | None:
    base = total // k
    remainder = total % k
    payments = [base] * k
    for idx in range(k - remainder, k):
        payments[idx] += 1
    if not _is_valid_payment_sequence(payments, rules):
        return None
    return payments


def _generate_balloon_payments(total: int, k: int, rules: CreditorRules) -> list[int] | None:
    if k < 1:
        return None
    floors = [_payment_floor(i + 1, rules) for i in range(k)]
    if k == 1:
        payments = [total]
        return payments if _is_valid_payment_sequence(payments, rules) else None
    payments = floors.copy()
    payments[-1] = total - sum(payments[:-1])
    if payments[-1] < floors[-1] or payments[-1] < payments[-2]:
        return None
    # Enforce token-pay rule by raising late minimum payments if needed.
    min_pay = rules.min_payment_cents
    token_pays = _count_token_pays(payments[:-1], rules)
    if token_pays > rules.max_token_pays:
        need = token_pays - rules.max_token_pays
        for idx in range(k - 2, -1, -1):
            if need <= 0:
                break
            if payments[idx] == min_pay:
                payments[idx] += 1
                payments[-1] -= 1
                need -= 1
        if need > 0:
            return None
        if payments[-1] < floors[-1] or payments[-1] < payments[-2]:
            return None
    if not _is_valid_payment_sequence(payments, rules):
        return None
    return payments


def _sequence_from_segments(total: int, floors: list[int], segment_bounds: list[int]) -> list[int] | None:
    segments: list[tuple[int, int]] = []
    start = 0
    for boundary in segment_bounds:
        segments.append((start, boundary))
        start = boundary
    segments.append((start, len(floors)))
    counts = [end - start for start, end in segments]
    base_values = [max(floors[start:end]) for start, end in segments]
    for i in range(1, len(base_values)):
        base_values[i] = max(base_values[i], base_values[i - 1])
    minimal_sum = sum(count * value for count, value in zip(counts, base_values))
    if minimal_sum > total:
        return None
    extra = total - minimal_sum
    increments: list[int] = [0] * len(counts)
    # Backtracking search from last segment backwards.
    def assign_segment(idx: int, remaining: int, next_value: int) -> bool:
        if idx < 0:
            return remaining == 0
        count = counts[idx]
        base = base_values[idx]
        max_add = remaining // count
        for add in range(max_add, -1, -1):
            value = base + add
            if value > next_value:
                continue
            if assign_segment(idx - 1, remaining - add * count, value):
                increments[idx] = add
                return True
        return False
    if not assign_segment(len(counts) - 1, extra, inf):
        return None
    values = [base + add for base, add in zip(base_values, increments)]
    payments = []
    for (start, end), value in zip(segments, values):
        payments.extend([value] * (end - start))
    return payments


def _generate_staircase_payments(total: int, k: int, rules: CreditorRules) -> list[int] | None:
    floors = [_payment_floor(i + 1, rules) for i in range(k)]
    if sum(floors) > total:
        return None
    # Try a flat sequence first if allowed.
    if total % k == 0:
        equal = [total // k] * k
        if _is_valid_payment_sequence(equal, rules):
            return equal
    # Try balloon-like staircase with low early payments.
    candidate = floors.copy()
    if k > 1:
        candidate[-1] = total - sum(candidate[:-1])
        if (candidate[-1] >= candidate[-2] and _is_valid_payment_sequence(candidate, rules)):
            distinct = len(set(candidate))
            if distinct <= rules.max_segments:
                return candidate
    # Try segment partitions up to rules.max_segments.
    for segments in range(1, max(1, rules.max_segments) + 1):
        if segments > k:
            break
        for boundaries in combinations(range(1, k), segments - 1):
            payments = _sequence_from_segments(total, floors, list(boundaries))
            if payments is None:
                continue
            if not _is_valid_payment_sequence(payments, rules):
                continue
            if len(set(payments)) <= rules.max_segments:
                return payments
    return None


def _build_fixed_balance_path(
    client: Client,
    payment_dates: list[date],
    payments: list[int],
    bank_fee_cents: int,
    extra_ledger: list[tuple[date, int]],
    starting_balance: int,
) -> tuple[dict[date, int], list[date]] | None:
    future_events: dict[date, dict[str, int]] = {}
    for entry in client.ledger:
        if entry.date <= client.as_of_date:
            continue
        bucket = future_events.setdefault(entry.date, {"credit": 0, "debit": 0})
        bucket[entry.type] += entry.amount_cents
    for d, amount in extra_ledger:
        if d <= client.as_of_date:
            starting_balance += amount
            continue
        bucket = future_events.setdefault(d, {"credit": 0, "debit": 0})
        bucket["credit"] += amount
    for idx, (d, p) in enumerate(zip(payment_dates, payments)):
        if p > 0:
            bucket = future_events.setdefault(d, {"credit": 0, "debit": 0})
            bucket["debit"] += p
            if bank_fee_cents > 0:
                bucket["debit"] += bank_fee_cents
    dates = sorted(set(future_events) | set(payment_dates))
    balance = starting_balance
    fixed_balance: dict[date, int] = {}
    for d in dates:
        events = future_events.get(d, {"credit": 0, "debit": 0})
        balance += events["credit"]
        balance -= events["debit"]
        if balance < 0:
            return None
        fixed_balance[d] = balance
    return fixed_balance, dates


def _allocate_program_fee(
    fixed_balance: dict[date, int],
    all_dates: list[date],
    fee_dates: list[date],
    total_fee: int,
) -> dict[date, int] | None:
    remaining = total_fee
    allocations: dict[date, int] = {}
    current_fee = 0
    sorted_all = sorted(set(all_dates) | set(fee_dates))
    filled_balance: dict[date, int] = {}
    last_balance = 0
    for d in sorted_all:
        if d in fixed_balance:
            last_balance = fixed_balance[d]
        filled_balance[d] = last_balance
    for fee_date in sorted(fee_dates):
        if remaining <= 0:
            allocations[fee_date] = 0
            continue
        available = min(
            filled_balance[d] - current_fee
            for d in sorted_all
            if d >= fee_date
        )
        take = max(0, min(remaining, available))
        allocations[fee_date] = take
        current_fee += take
        remaining -= take
    if remaining != 0:
        return None
    return allocations


def _simulate_schedule(
    client: Client,
    payment_dates: list[date],
    payments: list[int],
    fee_allocations: dict[date, int],
    bank_fee_cents: int,
    extra_ledger: list[tuple[date, int]],
    starting_balance: int,
) -> list[ScheduleRow] | None:
    future_events: dict[date, dict[str, int]] = {}
    for entry in client.ledger:
        if entry.date <= client.as_of_date:
            continue
        bucket = future_events.setdefault(entry.date, {"credit": 0, "debit": 0})
        bucket[entry.type] += entry.amount_cents
    for d, amount in extra_ledger:
        if d <= client.as_of_date:
            starting_balance += amount
            continue
        bucket = future_events.setdefault(d, {"credit": 0, "debit": 0})
        bucket["credit"] += amount
    schedule_dates = set()
    for idx, (d, p) in enumerate(zip(payment_dates, payments)):
        if p > 0:
            bucket = future_events.setdefault(d, {"credit": 0, "debit": 0})
            bucket["debit"] += p
            if bank_fee_cents > 0:
                bucket["debit"] += bank_fee_cents
            schedule_dates.add(d)
    for d, amount in fee_allocations.items():
        if amount <= 0:
            continue
        bucket = future_events.setdefault(d, {"credit": 0, "debit": 0})
        bucket["debit"] += amount
        schedule_dates.add(d)
    dates = sorted(set(future_events))
    balance = starting_balance
    rows: list[ScheduleRow] = []
    for d in dates:
        events = future_events.get(d, {"credit": 0, "debit": 0})
        balance += events["credit"]
        balance -= events["debit"]
        if balance < 0:
            return None
        if d in schedule_dates:
            payment = 0
            bank_fee = 0
            if d in payment_dates:
                idx = payment_dates.index(d)
                payment = payments[idx]
                if payment > 0:
                    bank_fee = bank_fee_cents
            fee = fee_allocations.get(d, 0)
            rows.append(
                ScheduleRow(
                    date=d,
                    creditor_payment_cents=payment,
                    program_fee_cents=fee,
                    bank_fee_cents=bank_fee if payment > 0 else 0,
                    balance_cents=balance,
                )
            )
    return rows


def _shape_rank(shape: str) -> int:
    return {"even": 0, "balloon": 1, "staircase": 2}.get(shape, 99)


def _try_schedule(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    payments: list[int],
    cadence: list[date],
    extra_ledger: list[tuple[date, int]],
    starting_balance: int,
) -> tuple[list[ScheduleRow], tuple, date] | None:
    payment_dates = cadence[: len(payments)]
    if len(payment_dates) != len(payments):
        return None
    fixed_path = _build_fixed_balance_path(
        client,
        payment_dates,
        payments,
        rules.bank_fee_cents,
        extra_ledger,
        starting_balance,
    )
    if fixed_path is None:
        return None
    fixed_balance, all_dates = fixed_path
    fee_dates = [d for d in cadence if d >= payment_dates[0]]
    allocations = _allocate_program_fee(fixed_balance, all_dates, fee_dates, _program_fee_total(offer, rules))
    if allocations is None:
        return None
    rows = _simulate_schedule(
        client,
        payment_dates,
        payments,
        allocations,
        rules.bank_fee_cents,
        extra_ledger,
        starting_balance,
    )
    if rows is None:
        return None
    fee_vector = tuple(-allocations[d] for d in fee_dates)
    metric = (fee_vector, len(payment_dates))
    return rows, metric, payment_dates[-1]


def _choose_best_schedule(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    cadence: list[date],
    extra_ledger: list[tuple[date, int]],
    starting_balance: int,
) -> tuple[list[ScheduleRow], str] | None:
    total = _offer_total(offer)
    max_k = min(rules.max_terms, rules.max_payments)
    best: tuple[list[int], list[ScheduleRow], str] | None = None
    for k in range(1, max_k + 1):
        if k > len(cadence):
            break
        payments_candidates: list[tuple[list[int] | None, str]] = []
        if rules.even_pays:
            payments_candidates.append((_generate_even_payments(total, k, rules), "even"))
        elif rules.is_ballooning_allowed:
            payments_candidates.append((_generate_balloon_payments(total, k, rules), "balloon"))
            payments_candidates.append((_generate_staircase_payments(total, k, rules), "staircase"))
        else:
            payments_candidates.append((_generate_staircase_payments(total, k, rules), "staircase"))
        for payments, shape in payments_candidates:
            if payments is None:
                continue
            result = _try_schedule(client, offer, rules, payments, cadence, extra_ledger, starting_balance)
            if result is None:
                continue
            rows, metric, _ = result
            shape_rank = _shape_rank(shape)
            full_metric = (metric, shape_rank)
            if best is None:
                best = (payments, rows, shape, full_metric)
                continue
            if full_metric < best[3]:
                best = (payments, rows, shape, full_metric)
    if best is None:
        return None
    return best[1], best[2]


def _draft_dates_after_as_of(client: Client) -> list[date]:
    dates: list[date] = []
    current = client.first_draft_date
    while current <= client.last_draft_date:
        if current > client.as_of_date:
            dates.append(current)
        current = monthly_payment_dates(current, 2)[1]
    return dates


def _is_schedule_feasible_with_extras(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    extra_ledger: list[tuple[date, int]],
    starting_balance: int,
) -> bool:
    first_payment = offer.first_payment_date or default_first_payment_date(client)
    cadence = _cadence_to_horizon(first_payment, client.last_draft_date)
    result = _choose_best_schedule(client, offer, rules, cadence, extra_ledger, starting_balance)
    return result is not None


def _find_minimum_lump_sum(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
) -> FundsOption:
    low = 0
    high = max(1, _offer_total(offer) * 2)
    extra_date = client.as_of_date if client.as_of_date <= client.last_draft_date else client.last_draft_date
    while not _is_schedule_feasible_with_extras(client, offer, rules, [(extra_date, high)], client.current_balance_cents):
        high *= 2
        if high > _offer_total(offer) * 10 + 100000:
            break
    while low < high:
        mid = (low + high) // 2
        if _is_schedule_feasible_with_extras(client, offer, rules, [(extra_date, mid)], client.current_balance_cents):
            high = mid
        else:
            low = mid + 1
    amount = low
    guardrail = _round_half_up(Decimal(str(0.65)) * Decimal(str(_offer_total(offer))))
    within = amount <= guardrail
    reason = "" if within else "exceeds guardrail"
    return FundsOption(amount_cents=amount, within_guardrail=within, reason=reason, date=extra_date)


def _find_minimum_monthly_increment(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
) -> FundsOption:
    draft_dates = _draft_dates_after_as_of(client)
    num_drafts = len(draft_dates)
    if num_drafts == 0:
        return FundsOption(
            amount_cents=0,
            within_guardrail=False,
            reason="no future drafts",
            num_drafts=0,
        )
    low = 0
    high = max(1, client.draft_amount_cents * 2)
    while not _is_schedule_feasible_with_extras(
        client,
        offer,
        rules,
        [(d, high) for d in draft_dates],
        client.current_balance_cents,
    ):
        high *= 2
        if high > max(client.draft_amount_cents * 10, 100000):
            break
    while low < high:
        mid = (low + high) // 2
        extra_ledger = [(d, mid) for d in draft_dates]
        if _is_schedule_feasible_with_extras(client, offer, rules, extra_ledger, client.current_balance_cents):
            high = mid
        else:
            low = mid + 1
    amount = low
    guardrail_limit = max(10000, _round_half_up(Decimal(str(0.40)) * Decimal(str(client.draft_amount_cents))))
    within = amount <= guardrail_limit
    reason = "" if within else "exceeds guardrail"
    return FundsOption(amount_cents=amount, within_guardrail=within, reason=reason, num_drafts=num_drafts)


def evaluate_offer(client: Client, offer: Offer, rules: CreditorRules) -> Result:
    first_payment = offer.first_payment_date or default_first_payment_date(client)
    cadence = _cadence_to_horizon(first_payment, client.last_draft_date)
    best = _choose_best_schedule(client, offer, rules, cadence, [], client.current_balance_cents)
    if best is not None:
        rows, shape = best
        return Result(feasible=True, pay_shape_used=shape, schedule=rows, additional_funds=None)
    lump = _find_minimum_lump_sum(client, offer, rules)
    monthly = _find_minimum_monthly_increment(client, offer, rules)
    return Result(
        feasible=False,
        pay_shape_used=None,
        schedule=None,
        additional_funds=AdditionalFunds(lump_sum=lump, monthly_increment=monthly),
    )
