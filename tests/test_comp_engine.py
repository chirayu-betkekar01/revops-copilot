"""Comp engine tests. Pure Python: no database, no network.

The SCOPE.md plan: $150k OTE, 50/50, $150k quarterly quota -> base rate 12.5%.
Tier 1 (0-100%) 12.5%, tier 2 (100-150%) 18.75%, tier 3 (150%+) 25%.
"""

import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from comp.engine import (
    CreditedDeal,
    PlanError,
    compute_payouts,
    formula_payout,
    load_plan,
    parse_plan,
    pay_rep_quarter,
    quarter_end,
    quarters_between,
)

Q3 = date(2026, 7, 1)
D = Decimal


def plan_data(*extra_versions: dict[str, Any]) -> dict[str, Any]:
    v1 = {
        "version": 1,
        "effective_from": date(2025, 7, 1),
        "ote": "150000",
        "base_salary": "75000",
        "annual_quota": "600000",
        "tiers": [
            {"floor": "0.0", "ceiling": "1.0", "multiplier": "1.0"},
            {"floor": "1.0", "ceiling": "1.5", "multiplier": "1.5"},
            {"floor": "1.5", "ceiling": None, "multiplier": "2.0"},
        ],
    }
    return {"plan_id": "test_plan", "name": "Test", "versions": [v1, *extra_versions]}


PLAN = parse_plan(plan_data())


def deal(amount: str, day: int = 15, deal_id: str | None = None, month: int = 8) -> CreditedDeal:
    return CreditedDeal(
        deal_id=deal_id or f"d-{amount}-{month}-{day}",
        rep_id="rep_01",
        amount=D(amount),
        close_date=date(2026, month, day),
    )


def pay(*deals: CreditedDeal) -> Any:
    return pay_rep_quarter("rep_01", Q3, deals, PLAN)


# ---------- The plan itself ----------


def test_plan_derives_rates_and_quota() -> None:
    v = PLAN.versions[0]
    assert v.variable == D("75000")
    assert v.quarterly_quota == D("150000")
    assert v.base_rate == D("0.125")


def test_real_plan_file_loads() -> None:
    plan = load_plan(Path("comp/plans/standard_ae.yaml"))
    assert plan.versions[0].base_rate == D("0.125")


def test_unquoted_decimal_in_yaml_is_rejected() -> None:
    data = plan_data()
    data["versions"][0]["tiers"][1]["multiplier"] = 1.5  # a float, as unquoted YAML would give
    with pytest.raises(PlanError, match="quoted string"):
        parse_plan(data)


def test_gap_between_tiers_is_rejected() -> None:
    data = plan_data()
    data["versions"][0]["tiers"][1]["floor"] = "1.1"  # 100% -> 110% isn't covered
    with pytest.raises(PlanError, match="contiguous"):
        parse_plan(data)


# ---------- Tier edges (SCOPE.md) ----------


@pytest.mark.parametrize(
    ("bookings", "expected"),
    [
        ("0", "0.00"),  # zero bookings
        ("75000", "9375.00"),  # 50%: all tier 1
        ("150000", "18750.00"),  # exactly 100%: tier 1 full, nothing in tier 2
        ("150001", "18750.19"),  # $1 into tier 2 at 18.75%
        ("225000", "32812.50"),  # exactly 150%: 18,750 + 75,000 x 18.75%
        ("300000", "51562.50"),  # 200%: + 75,000 x 25%
    ],
)
def test_payout_at_tier_edges(bookings: str, expected: str) -> None:
    result = pay(deal(bookings)) if bookings != "0" else pay()
    assert result.payout == D(expected)
    assert result.payout == formula_payout(D(bookings), PLAN.versions[0])


def test_zero_bookings_gives_zero_and_no_lines() -> None:
    result = pay()
    assert (result.payout, result.attainment, result.lines) == (D(0), D(0), ())


def test_exactly_100_percent_stays_in_tier_1() -> None:
    result = pay(deal("150000"))
    assert [line.tier for line in result.lines] == [1]
    assert result.attainment == D(1)


def test_deal_bigger_than_a_whole_tier_is_split_across_all_three() -> None:
    result = pay(deal("300000"))
    assert [(line.tier, line.credit, line.rate) for line in result.lines] == [
        (1, D("150000"), D("0.125")),
        (2, D("75000"), D("0.1875")),
        (3, D("75000"), D("0.25")),
    ]


def test_deal_crossing_a_boundary_is_split_where_it_crosses() -> None:
    result = pay(deal("100000", day=1), deal("100000", day=20))
    second = [line for line in result.lines if line.close_date.day == 20]
    assert [(line.tier, line.credit) for line in second] == [(1, D("50000")), (2, D("50000"))]


def test_deals_fill_tiers_in_close_date_order_whatever_the_input_order() -> None:
    deals = [deal("90000", day=5), deal("60000", day=25), deal("40000", day=12)]
    forward, shuffled = pay(*deals), pay(*reversed(deals))
    assert forward == shuffled
    # Day 5 and 12 fill 130k of tier 1; day 25's deal is split 20k tier 1 / 40k tier 2.
    last = [(line.tier, line.credit) for line in forward.lines if line.close_date.day == 25]
    assert last == [(1, D("20000")), (2, D("40000"))]


def test_lines_always_add_up_to_the_formula() -> None:
    rng = random.Random(3)
    for _ in range(200):
        deals = [
            deal(str(rng.randint(1, 120) * 500), day=rng.randint(1, 28), deal_id=f"d{i}")
            for i in range(rng.randint(0, 12))
        ]
        result = pay(*deals)
        # Each line is rounded on its own, so allow a cent of rounding per line.
        assert abs(result.payout - formula_payout(result.bookings, PLAN.versions[0])) <= D(
            "0.01"
        ) * max(1, len(result.lines))


def test_payout_rounds_half_up_to_the_cent() -> None:
    # $0.04 x 12.5% = $0.005 -> rounds up to $0.01
    assert pay(deal("0.04")).payout == D("0.01")


# ---------- Mid-quarter plan change (SCOPE.md) ----------


def test_mid_quarter_plan_change_only_affects_later_deals() -> None:
    v2 = {
        **plan_data()["versions"][0],
        "version": 2,
        "effective_from": date(2026, 8, 15),
        "tiers": [
            {"floor": "0.0", "ceiling": "1.0", "multiplier": "1.0"},
            {"floor": "1.0", "ceiling": "1.5", "multiplier": "1.75"},  # tier 2 raised
            {"floor": "1.5", "ceiling": None, "multiplier": "2.0"},
        ],
    }
    plan = parse_plan(plan_data(v2))
    before = deal("150000", day=1)  # v1: fills tier 1
    after = deal("50000", day=20)  # v2: lands in tier 2 at 12.5% x 1.75
    result = pay_rep_quarter("rep_01", Q3, [before, after], plan)

    assert [(line.plan_version, line.tier) for line in result.lines] == [(1, 1), (2, 2)]
    assert result.lines[1].rate == D("0.21875")
    assert result.payout == D("18750.00") + D("10937.50")


def test_deal_before_any_plan_version_is_an_error() -> None:
    with pytest.raises(ValueError, match="no version in force"):
        pay_rep_quarter("rep_01", date(2025, 4, 1), [deal("100", month=5)], PLAN)


# ---------- Whole runs ----------


def test_every_rep_gets_a_row_for_every_quarter_even_with_no_deals() -> None:
    quarters = quarters_between(date(2026, 1, 1), date(2026, 9, 30))
    results = compute_payouts([deal("1000")], PLAN, ["rep_01", "rep_02"], quarters)

    assert len(results) == 3 * 2
    assert sum(r.payout for r in results) == D("125.00")  # only rep_01's Q3 deal pays


def test_quarter_helpers() -> None:
    assert quarters_between(date(2025, 8, 3), date(2026, 1, 2)) == [
        date(2025, 7, 1),
        date(2025, 10, 1),
        date(2026, 1, 1),
    ]
    assert quarter_end(date(2026, 10, 1)) == date(2026, 12, 31)
    assert quarter_end(date(2026, 7, 1)) == date(2026, 9, 30)
