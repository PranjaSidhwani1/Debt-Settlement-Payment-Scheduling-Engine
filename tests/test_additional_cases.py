"""Additional targeted tests for the feasibility scheduler."""

from __future__ import annotations

from datetime import date

from feasibility.engine import evaluate_offer
from feasibility.models import Client, CreditorRules, LedgerEntry, Offer


def test_even_payment_schedule_is_valid_and_exact_sum() -> None:
    client = Client(
        draft_amount_cents=15000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 4, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[
            LedgerEntry(date(2026, 1, 1), 15000, "credit"),
            LedgerEntry(date(2026, 2, 1), 15000, "credit"),
            LedgerEntry(date(2026, 3, 1), 15000, "credit"),
            LedgerEntry(date(2026, 4, 1), 15000, "credit"),
        ],
    )
    offer = Offer(
        creditor="EvenMoreCo",
        current_balance_cents=10001,
        original_balance_cents=10001,
        settlement_pct=1.0,
        first_payment_date=date(2026, 1, 15),
    )
    rules = CreditorRules(
        max_terms=3,
        max_payments=3,
        min_payment_cents=2500,
        max_token_pays=3,
        min_payment_tiers=[],
        even_pays=True,
        is_ballooning_allowed=False,
        max_segments=1,
        bank_fee_cents=0,
        program_fee_pct=0.0,
    )

    result = evaluate_offer(client, offer, rules)

    assert result.feasible is True
    assert result.pay_shape_used == "even"
    assert result.schedule is not None
    payments = [row.creditor_payment_cents for row in result.schedule]
    assert sum(payments) == 10001
    assert payments == sorted(payments)


def test_balloon_shape_when_ballooning_allowed() -> None:
    client = Client(
        draft_amount_cents=10000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 6, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[
            LedgerEntry(date(2026, 1, 1), 10000, "credit"),
            LedgerEntry(date(2026, 2, 1), 10000, "credit"),
            LedgerEntry(date(2026, 3, 1), 10000, "credit"),
            LedgerEntry(date(2026, 4, 1), 10000, "credit"),
            LedgerEntry(date(2026, 5, 1), 10000, "credit"),
            LedgerEntry(date(2026, 6, 1), 10000, "credit"),
        ],
    )
    offer = Offer(
        creditor="BalloonPreferredCo",
        current_balance_cents=60000,
        original_balance_cents=60000,
        settlement_pct=0.5,
        first_payment_date=date(2026, 1, 31),
    )
    rules = CreditorRules(
        max_terms=6,
        max_payments=6,
        min_payment_cents=2500,
        max_token_pays=6,
        min_payment_tiers=[],
        even_pays=False,
        is_ballooning_allowed=True,
        max_segments=4,
        bank_fee_cents=0,
        program_fee_pct=0.0,
    )

    result = evaluate_offer(client, offer, rules)

    assert result.feasible is True
    assert result.pay_shape_used == "balloon"
    assert result.schedule is not None
    payments = [row.creditor_payment_cents for row in result.schedule]
    assert payments == sorted(payments)
    assert payments[-1] >= payments[0]


def test_staircase_shape_respects_max_segments() -> None:
    client = Client(
        draft_amount_cents=12000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 6, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[
            LedgerEntry(date(2026, 1, 1), 12000, "credit"),
            LedgerEntry(date(2026, 2, 1), 12000, "credit"),
            LedgerEntry(date(2026, 3, 1), 12000, "credit"),
            LedgerEntry(date(2026, 4, 1), 12000, "credit"),
            LedgerEntry(date(2026, 5, 1), 12000, "credit"),
            LedgerEntry(date(2026, 6, 1), 12000, "credit"),
        ],
    )
    offer = Offer(
        creditor="StaircaseCo",
        current_balance_cents=36000,
        original_balance_cents=36000,
        settlement_pct=1.0,
        first_payment_date=date(2026, 1, 31),
    )
    rules = CreditorRules(
        max_terms=3,
        max_payments=3,
        min_payment_cents=5000,
        max_token_pays=3,
        min_payment_tiers=[],
        even_pays=False,
        is_ballooning_allowed=False,
        max_segments=2,
        bank_fee_cents=0,
        program_fee_pct=0.0,
    )

    result = evaluate_offer(client, offer, rules)

    assert result.feasible is True
    assert result.pay_shape_used == "staircase"
    assert result.schedule is not None
    payments = [row.creditor_payment_cents for row in result.schedule]
    assert len(set(payments)) <= 2
    assert sum(payments) == 36000


def test_program_fee_is_not_collected_before_first_payment_date() -> None:
    client = Client(
        draft_amount_cents=20000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 5, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[
            LedgerEntry(date(2026, 1, 1), 20000, "credit"),
            LedgerEntry(date(2026, 2, 1), 20000, "credit"),
            LedgerEntry(date(2026, 3, 1), 20000, "credit"),
            LedgerEntry(date(2026, 4, 1), 20000, "credit"),
            LedgerEntry(date(2026, 5, 1), 20000, "credit"),
        ],
    )
    offer = Offer(
        creditor="FeeTimingCo",
        current_balance_cents=40000,
        original_balance_cents=40000,
        settlement_pct=0.5,
        first_payment_date=date(2026, 2, 15),
    )
    rules = CreditorRules(
        max_terms=4,
        max_payments=4,
        min_payment_cents=2500,
        max_token_pays=4,
        min_payment_tiers=[],
        even_pays=True,
        is_ballooning_allowed=False,
        max_segments=1,
        bank_fee_cents=500,
        program_fee_pct=0.25,
    )

    result = evaluate_offer(client, offer, rules)

    assert result.feasible is True
    assert result.schedule is not None
    assert all(row.date >= offer.first_payment_date for row in result.schedule)
    assert any(row.program_fee_cents > 0 for row in result.schedule)
