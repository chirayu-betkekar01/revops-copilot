"""Comp engine: tiered commission payouts as a pure function of (plan, quota, credited deals).

Originally a ✋ hand-written file; written by Claude at Chirayu's request (2026-11-22).

No database, no network, no floats. Money is Decimal end to end, so the same inputs always give
the same cents, and every payout is built from commission lines that each point at one deal.

How a quarter is paid:
1. A rep's closed-won deals in the quarter are taken in close-date order (ties: deal id).
2. Each deal fills the quota "bucket" from where the previous deal stopped. The tiers split the
   bucket by % of quota (0-100%, 100-150%, 150%+). A deal that crosses a tier boundary is split
   into one line per tier it touches.
3. Each slice is paid at base rate x that tier's multiplier, using the plan version in force on
   the day that deal closed (so a mid-quarter plan change only affects later deals).
4. Each line's payout is rounded to the cent (half up); the rep's payout is the sum of lines.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

CENT = Decimal("0.01")
QUARTERS_PER_YEAR = 4


# ---------- Plan definition ----------


@dataclass(frozen=True)
class Tier:
    floor: Decimal  # fraction of quarterly quota where this tier starts (0.0 = 0%)
    ceiling: Decimal | None  # where it ends (1.5 = 150%); None = no upper limit
    multiplier: Decimal  # x base rate


@dataclass(frozen=True)
class PlanVersion:
    plan_id: str
    version: int
    effective_from: date
    ote: Decimal
    base_salary: Decimal
    annual_quota: Decimal
    tiers: tuple[Tier, ...]

    @property
    def variable(self) -> Decimal:
        return self.ote - self.base_salary

    @property
    def quarterly_quota(self) -> Decimal:
        return self.annual_quota / QUARTERS_PER_YEAR

    @property
    def base_rate(self) -> Decimal:
        """Commission rate at 100% of quota: variable pay / annual quota (75k / 600k = 12.5%)."""
        return self.variable / self.annual_quota


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    versions: tuple[PlanVersion, ...]  # sorted by effective_from

    def version_on(self, day: date) -> PlanVersion:
        """The version in force on `day`: the latest one that started on or before it."""
        in_force = [v for v in self.versions if v.effective_from <= day]
        if not in_force:
            raise ValueError(f"Plan {self.plan_id} has no version in force on {day}")
        return in_force[-1]


class PlanError(ValueError):
    """The plan file is malformed or inconsistent."""


def to_decimal(value: Any, field: str) -> Decimal:
    # YAML turns unquoted 0.1 into a float, which can't hold 0.1 exactly. Refuse it.
    if isinstance(value, float):
        raise PlanError(f'{field}: write {value} as a quoted string, e.g. "{value}"')
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise PlanError(f"{field}: {value!r} is not a number") from exc


def parse_plan(data: dict[str, Any]) -> Plan:
    plan_id = str(data["plan_id"])
    versions = []
    for raw in data["versions"]:
        where = f"{plan_id} v{raw['version']}"
        tiers = tuple(
            Tier(
                floor=to_decimal(t["floor"], f"{where} tier floor"),
                ceiling=None if t["ceiling"] is None else to_decimal(t["ceiling"], f"{where} tier"),
                multiplier=to_decimal(t["multiplier"], f"{where} tier multiplier"),
            )
            for t in raw["tiers"]
        )
        version = PlanVersion(
            plan_id=plan_id,
            version=int(raw["version"]),
            effective_from=raw["effective_from"],
            ote=to_decimal(raw["ote"], f"{where} ote"),
            base_salary=to_decimal(raw["base_salary"], f"{where} base_salary"),
            annual_quota=to_decimal(raw["annual_quota"], f"{where} annual_quota"),
            tiers=tiers,
        )
        validate_version(version)
        versions.append(version)

    versions.sort(key=lambda v: v.effective_from)
    if len({v.effective_from for v in versions}) != len(versions):
        raise PlanError(f"{plan_id}: two versions start on the same day")
    return Plan(plan_id=plan_id, name=str(data.get("name", plan_id)), versions=tuple(versions))


def validate_version(v: PlanVersion) -> None:
    where = f"{v.plan_id} v{v.version}"
    if not isinstance(v.effective_from, date):
        raise PlanError(f"{where}: effective_from must be a date like 2025-07-01")
    if v.annual_quota <= 0 or v.base_salary < 0 or v.variable <= 0:
        raise PlanError(f"{where}: need annual_quota > 0 and base_salary < ote")
    if not v.tiers or v.tiers[0].floor != 0:
        raise PlanError(f"{where}: the first tier must start at 0")
    for lower, upper in zip(v.tiers, v.tiers[1:], strict=False):
        if lower.ceiling is None or lower.ceiling != upper.floor:
            raise PlanError(f"{where}: tiers must be contiguous (each ceiling = next floor)")
    if v.tiers[-1].ceiling is not None:
        raise PlanError(f"{where}: the last tier must have no ceiling (null)")
    if any(t.multiplier < 0 for t in v.tiers):
        raise PlanError(f"{where}: multipliers can't be negative")


def load_plan(path: Path) -> Plan:
    with path.open(encoding="utf-8") as f:
        return parse_plan(yaml.safe_load(f))


# ---------- Inputs and outputs ----------


@dataclass(frozen=True)
class CreditedDeal:
    deal_id: str
    rep_id: str  # credit rule: 100% to the deal owner
    amount: Decimal
    close_date: date  # ... on the closed-won date


@dataclass(frozen=True)
class CommissionLine:
    rep_id: str
    deal_id: str
    quarter_start: date
    close_date: date
    tier: int  # 1-based
    credit: Decimal  # the slice of this deal's amount that fell in this tier
    rate: Decimal
    payout: Decimal  # credit x rate, rounded to the cent
    plan_id: str
    plan_version: int


@dataclass(frozen=True)
class RepQuarterResult:
    rep_id: str
    quarter_start: date
    bookings: Decimal
    quota: Decimal
    attainment: Decimal  # bookings / quota (1.0 = 100%)
    payout: Decimal
    lines: tuple[CommissionLine, ...]


# ---------- The maths ----------


def quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def quarter_end(start: date) -> date:
    """Last day of the quarter starting on `start`."""
    month = start.month + 2
    next_month = date(start.year + month // 12, month % 12 + 1, 1)
    return date.fromordinal(next_month.toordinal() - 1)


def quarters_between(first: date, last: date) -> list[date]:
    """Quarter start dates from first's quarter to last's quarter, inclusive."""
    quarters, current = [], quarter_start(first)
    while current <= last:
        quarters.append(current)
        month = current.month + 3
        current = date(current.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
    return quarters


def tier_bounds(version: PlanVersion, tier: Tier) -> tuple[Decimal, Decimal | None]:
    """A tier's lower and upper bound in dollars for one quarter."""
    quota = version.quarterly_quota
    return tier.floor * quota, None if tier.ceiling is None else tier.ceiling * quota


def formula_payout(bookings: Decimal, version: PlanVersion) -> Decimal:
    """SCOPE.md's formula, used to cross-check the line-by-line result:
    payout = sum over tiers of min(max(B - L, 0), U - L) x rate."""
    total = Decimal(0)
    for tier in version.tiers:
        low, high = tier_bounds(version, tier)
        in_tier = max(bookings - low, Decimal(0))
        if high is not None:
            in_tier = min(in_tier, high - low)
        total += in_tier * version.base_rate * tier.multiplier
    return total.quantize(CENT, ROUND_HALF_UP)


def pay_rep_quarter(
    rep_id: str, quarter: date, deals: Iterable[CreditedDeal], plan: Plan
) -> RepQuarterResult:
    """Pay one rep for one quarter. `deals` must all belong to that rep and quarter."""
    ordered = sorted(deals, key=lambda d: (d.close_date, d.deal_id))
    lines: list[CommissionLine] = []
    filled = Decimal(0)  # bookings counted so far this quarter

    for deal in ordered:
        version = plan.version_on(deal.close_date)
        start, end = filled, filled + deal.amount
        for number, tier in enumerate(version.tiers, start=1):
            low, high = tier_bounds(version, tier)
            top = end if high is None else min(end, high)
            credit = top - max(start, low)
            if credit <= 0:
                continue  # this deal doesn't reach (or already passed) this tier
            rate = version.base_rate * tier.multiplier
            lines.append(
                CommissionLine(
                    rep_id=rep_id,
                    deal_id=deal.deal_id,
                    quarter_start=quarter,
                    close_date=deal.close_date,
                    tier=number,
                    credit=credit,
                    rate=rate,
                    payout=(credit * rate).quantize(CENT, ROUND_HALF_UP),
                    plan_id=version.plan_id,
                    plan_version=version.version,
                )
            )
        filled = end

    # Attainment is reported against the quota in force on the quarter's last day.
    quota = plan.version_on(quarter_end(quarter)).quarterly_quota
    return RepQuarterResult(
        rep_id=rep_id,
        quarter_start=quarter,
        bookings=filled,
        quota=quota,
        attainment=filled / quota,
        payout=sum((line.payout for line in lines), Decimal(0)),
        lines=tuple(lines),
    )


def compute_payouts(
    deals: Sequence[CreditedDeal], plan: Plan, rep_ids: Sequence[str], quarters: Sequence[date]
) -> list[RepQuarterResult]:
    """Every rep x every quarter, including reps who booked nothing (they get a $0 row)."""
    by_rep_quarter: dict[tuple[str, date], list[CreditedDeal]] = defaultdict(list)
    for deal in deals:
        if deal.amount > 0:  # no clawbacks or negative credit in this plan
            by_rep_quarter[(deal.rep_id, quarter_start(deal.close_date))].append(deal)

    return [
        pay_rep_quarter(rep, q, by_rep_quarter.get((rep, q), []), plan)
        for q in quarters
        for rep in rep_ids
    ]
