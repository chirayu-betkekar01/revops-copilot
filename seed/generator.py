"""Synthetic pipeline for a typical B2B software company.

Originally a ✋ hand-written file; written by Claude at Chirayu's request (2026-11-22).

What makes it look like a real B2B tech pipeline:
- Three customer segments. Bigger customers = bigger deals, longer sales cycles, lower win rate.
- Each company belongs to one rep (a territory), and reps differ in skill.
- About 30% of deals are expansions (existing customers buying more): smaller, faster, likelier.
- Close dates bunch up at the end of each quarter (the "hockey stick").
- Some deals slip. Lost deals slip more often, which later becomes a risk signal.
- A few deals are past their close date but still open (reps forget to update the CRM).
"""

import math
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from seed.models import (
    LOST_STAGE,
    OPEN_STAGES,
    REP_IDS,
    WON_STAGE,
    Company,
    Deal,
    Note,
    SeedData,
)

AS_OF = date(2026, 11, 22)  # fixed "today", so the same seed always gives the same data
N_COMPANIES = 300
N_DEALS = 2_000

# The 6 quarters deals are planned to close in. Pipeline grows about 5% a quarter.
QUARTERS = [(2025, 3), (2025, 4), (2026, 1), (2026, 2), (2026, 3), (2026, 4)]
QUARTER_WEIGHTS = [1.00, 1.05, 1.10, 1.16, 1.22, 1.28]
# Within a quarter, most deals are planned for month 3 (the end-of-quarter push).
MONTH_WEIGHTS = [0.25, 0.30, 0.45]

EXPANSION_SHARE = 0.30
EXPANSION_WIN_BOOST = 0.12
MIN_DEAL = 1_500


@dataclass(frozen=True)
class Segment:
    name: str
    share: float  # share of companies in this segment
    employees: tuple[int, int]
    median_deal: int  # typical first-year contract value, USD
    win_rate: float
    cycle_days: tuple[int, int]  # sales cycle: created -> planned close
    deal_weight: float  # how often this segment's companies get deals, relative


# Deal sizes are tuned so reps land near the $150k/quarter quota in docs/SCOPE.md:
# 2,000 deals over 6 quarters and 8 reps is ~11 wins per rep per quarter, a high-velocity
# motion, so typical contracts are smaller than in a classic enterprise sales team.
SEGMENTS = [
    Segment("SMB", 0.50, (10, 200), 4_500, 0.26, (30, 60), 1.0),
    Segment("Mid-market", 0.35, (200, 2_000), 14_000, 0.22, (60, 120), 1.5),
    Segment("Enterprise", 0.15, (2_000, 20_000), 40_000, 0.16, (120, 240), 2.0),
]

# Rep skill multiplies the win rate: one star, one struggling rep, the rest in between.
REP_SKILL = dict(zip(REP_IDS, [1.25, 1.15, 1.05, 1.0, 1.0, 0.95, 0.85, 0.75], strict=True))

# Industry -> (weight, name suffixes). Tech-heavy, like a typical B2B software customer base.
INDUSTRY_MIX: dict[str, tuple[int, list[str]]] = {
    "COMPUTER_SOFTWARE": (30, ["Software", "Labs", "Systems", "Cloud"]),
    "INFORMATION_TECHNOLOGY_AND_SERVICES": (20, ["Networks", "Data", "Tech", "IT Services"]),
    "FINANCIAL_SERVICES": (18, ["Capital", "Financial", "Payments", "Lending"]),
    "HOSPITAL_HEALTH_CARE": (12, ["Health", "Medical", "Care", "Clinics"]),
    "RETAIL": (10, ["Retail", "Goods", "Outfitters", "Markets"]),
    "MARKETING_AND_ADVERTISING": (10, ["Media", "Marketing", "Creative", "Digital"]),
}
NAME_PREFIXES = [
    "Blue", "North", "Bright", "Iron", "Silver", "Summit", "Harbor", "Pine", "Vector", "Atlas",
    "Nimbus", "Cobalt", "Granite", "Maple", "Orbit", "Quartz", "Redwood", "Signal", "Tidal", "Echo",
    "Falcon", "Juniper", "Lumen", "Meridian", "Nova", "Onyx", "Prairie", "Ridge", "Sterling",
    "Willow",
]  # fmt: skip

PRODUCTS = ["Core Platform", "Analytics Add-on", "Security Suite", "API Tier", "Enterprise Seats"]
NEXT_STEPS = [
    "Discovery call with ops lead",
    "Send proposal",
    "Technical review with IT",
    "Security questionnaire due",
    "Legal redlines on MSA",
    "Exec sponsor call",
    "Pricing approval from finance",
    "Procurement paperwork",
]
OPENING_NOTES = [
    "Intro call. Pain point: manual reporting takes their team days each month.",
    "Inbound demo request. Evaluating us against two other vendors.",
    "Referred by an existing customer. Strong initial interest.",
    "Met at a conference. Asked for a follow-up demo.",
]
EXPANSION_OPENING_NOTES = [
    "Existing customer asked about adding seats for a second team.",
    "QBR with the customer. They want to roll out to another department.",
    "Account review: usage is up 40%, they're hitting plan limits.",
    "Customer's admin asked for pricing on the add-on.",
]
PROGRESS_NOTES = [
    "Demo went well. Champion wants to loop in their manager.",
    "Technical deep dive with IT. Questions on SSO and data residency.",
    "Sent proposal. Some pushback on price per seat.",
    "Champion confirmed budget is approved for this fiscal year.",
    "Security review in progress, waiting on their questionnaire.",
    "Procurement involved. Asked for a multi-year discount.",
]
SLIP_NOTES = [
    "Decision delayed while their budget owner is out. Close date pushed out.",
    "Legal review is taking longer than expected. Close date moved.",
    "Champion went quiet for two weeks. Pushed the close date.",
]
WON_NOTES = [
    "Contract signed. Kickoff scheduled with customer success.",
    "Verbal yes from the exec sponsor. Paperwork signed this week.",
]
LOST_NOTES = [
    "Lost to a competitor on price.",
    "Prospect went dark after the proposal. Closing out.",
    "Budget frozen for the rest of the year. Closed lost.",
    "Decided to build in-house instead.",
]


def random_close_date(rng: random.Random, year: int, quarter: int) -> date:
    """A date in the quarter, skewed towards the end of the month and of the quarter."""
    month = (quarter - 1) * 3 + 1 + rng.choices(range(3), weights=MONTH_WEIGHTS)[0]
    first = date(year, month, 1)
    next_month = date(year + month // 12, month % 12 + 1, 1)
    days_in_month = (next_month - first).days
    # random() ** 0.6 pushes values towards 1, i.e. towards the end of the month
    return first + timedelta(days=int((days_in_month - 1) * rng.random() ** 0.6))


def random_timestamp(rng: random.Random, start: date, end: date) -> datetime:
    """A working-hours moment between start and end (inclusive), in UTC."""
    day = start + timedelta(days=rng.randint(0, (end - start).days))
    return datetime.combine(day, time(rng.randint(9, 17), rng.choice([0, 15, 30, 45])), UTC)


def make_companies(rng: random.Random) -> tuple[list[Company], dict[str, Segment]]:
    companies: list[Company] = []
    segment_of: dict[str, Segment] = {}
    used_names: set[str] = set()
    industries = list(INDUSTRY_MIX)
    industry_weights = [INDUSTRY_MIX[i][0] for i in industries]

    for i in range(1, N_COMPANIES + 1):
        segment = rng.choices(SEGMENTS, weights=[s.share for s in SEGMENTS])[0]
        industry = rng.choices(industries, weights=industry_weights)[0]

        name = ""
        while not name or name in used_names:
            name = f"{rng.choice(NAME_PREFIXES)} {rng.choice(INDUSTRY_MIX[industry][1])}"
        used_names.add(name)

        # Log-uniform: as many companies with 10-100 staff as with 100-1,000 within a segment.
        low, high = segment.employees
        employees = int(math.exp(rng.uniform(math.log(low), math.log(high))))
        revenue = round(employees * rng.uniform(120_000, 350_000), -5)  # nearest $100k

        ref = f"co_{i:04d}"
        companies.append(
            Company(
                ref=ref,
                name=name,
                domain=name.lower().replace(" ", "") + ".example",  # .example is never real
                industry=industry,
                number_of_employees=employees,
                annual_revenue=Decimal(int(revenue)),
            )
        )
        segment_of[ref] = segment
    return companies, segment_of


def assign_territories(rng: random.Random, companies: list[Company]) -> dict[str, str]:
    """Each company gets one owning rep, spread evenly."""
    refs = [c.ref for c in companies]
    rng.shuffle(refs)
    return {ref: REP_IDS[i % len(REP_IDS)] for i, ref in enumerate(refs)}


def make_notes(
    rng: random.Random,
    deal: Deal,
    *,
    won: bool,
    slipped: bool,
    closed: bool,
    is_expansion: bool,
) -> list[Note]:
    # Notes written along the way, in time order, between creation and today/close.
    bodies = [rng.choice(EXPANSION_OPENING_NOTES if is_expansion else OPENING_NOTES)]
    bodies += rng.sample(PROGRESS_NOTES, k=rng.randint(0, 2))
    if slipped:
        bodies.append(rng.choice(SLIP_NOTES))
    end = min(deal.close_date, AS_OF)
    timestamps = sorted(random_timestamp(rng, deal.create_date, end) for _ in bodies)
    notes = [Note(deal.ref, body, ts) for body, ts in zip(bodies, timestamps, strict=True)]

    # The won/lost note is written on the close date itself, after the day's other notes.
    if closed:
        closed_at = datetime.combine(deal.close_date, time(18, 0), UTC)
        notes.append(Note(deal.ref, rng.choice(WON_NOTES if won else LOST_NOTES), closed_at))
    return notes


def generate(rng: random.Random) -> SeedData:
    companies, segment_of = make_companies(rng)
    owner = assign_territories(rng, companies)
    company_weights = [segment_of[c.ref].deal_weight for c in companies]

    deals: list[Deal] = []
    notes: list[Note] = []

    for i in range(1, N_DEALS + 1):
        company = rng.choices(companies, weights=company_weights)[0]
        segment = segment_of[company.ref]
        rep = owner[company.ref]
        is_expansion = rng.random() < EXPANSION_SHARE

        # Deal size: lopsided (lognormal) around the segment's typical size, nearest $500.
        raw_amount = segment.median_deal * rng.lognormvariate(0, 0.5)
        if is_expansion:
            raw_amount *= 0.6
        amount = Decimal(max(MIN_DEAL, round(raw_amount / 500) * 500))

        # Decide the outcome up front. Open deals keep this hidden; it only shapes their notes
        # and slips, the way a deal that's going to be lost often shows warning signs early.
        win_rate = segment.win_rate * REP_SKILL[rep] + (EXPANSION_WIN_BOOST if is_expansion else 0)
        will_win = rng.random() < min(win_rate, 0.9)

        year, quarter = rng.choices(QUARTERS, weights=QUARTER_WEIGHTS)[0]
        planned_close = random_close_date(rng, year, quarter)
        cycle = rng.randint(*segment.cycle_days)
        if is_expansion:
            cycle = int(cycle * 0.6)
        create = planned_close - timedelta(days=cycle)
        if create > AS_OF:
            # A deal can't be created in the future: it was opened recently instead.
            create = AS_OF - timedelta(days=rng.randint(0, 14))

        # Slips: each one pushes the close date 3-6 weeks. Losing deals slip twice as often.
        slip_chance = 0.20 if will_win else 0.40
        close = planned_close
        slips = 0
        while slips < 3 and rng.random() < slip_chance:
            close += timedelta(days=rng.randint(21, 45))
            slips += 1
        if not will_win and slips == 0:
            # Lost deals often die before the planned date.
            close = create + timedelta(
                days=int((planned_close - create).days * rng.uniform(0.4, 1))
            )

        if close < AS_OF:
            stage = WON_STAGE if will_win else LOST_STAGE
            if (AS_OF - close).days <= 21 and rng.random() < 0.15:
                # Past its close date but never updated: still shows as late-stage open.
                stage = rng.choice(OPEN_STAGES[3:])
        else:
            # Open: how far through its cycle the deal is decides its stage (with some noise).
            progress = (AS_OF - create).days / max(1, (close - create).days)
            progress += rng.uniform(-0.1, 0.1)
            stage = OPEN_STAGES[min(len(OPEN_STAGES) - 1, max(0, int(progress * 5)))]

        is_open = stage in OPEN_STAGES
        product = rng.choice(PRODUCTS)
        deal = Deal(
            ref=f"deal_{i:04d}",
            company_ref=company.ref,
            rep_id=rep,
            name=f"{company.name} - {product}" + (" (Expansion)" if is_expansion else ""),
            amount=amount,
            stage=stage,
            create_date=create,
            close_date=close,
            next_step=rng.choice(NEXT_STEPS) if is_open else None,
        )
        deals.append(deal)
        notes += make_notes(
            rng,
            deal,
            won=will_win,
            slipped=slips > 0,
            closed=not is_open,
            is_expansion=is_expansion,
        )

    return SeedData(companies=companies, deals=deals, notes=notes)
