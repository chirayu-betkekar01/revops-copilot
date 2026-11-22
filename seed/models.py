"""Data shapes shared by the generator (hand-written) and the HubSpot sender.

The generator returns these plain Python objects; seed_hubspot.py turns them into API calls.
Keeping the two apart means the generator can be built and checked with no network at all.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# Written to every seeded company and deal, so seeded data can be found (and deleted) later.
SEED_BATCH = "seed-v1"


@dataclass(frozen=True)
class Rep:
    id: str  # stable key stored on every deal; never changes
    name: str  # display label only; free to change


# The 8 synthetic reps (made-up names: no real employee data in this project).
# HubSpot owners are real users, so deals carry a custom `rep_id` dropdown instead:
# HubSpot stores the id and shows the name.
REPS = (
    Rep("rep_01", "Priya Shah"),
    Rep("rep_02", "Marcus Reed"),
    Rep("rep_03", "Elena Torres"),
    Rep("rep_04", "James Okafor"),
    Rep("rep_05", "Hannah Becker"),
    Rep("rep_06", "Daniel Kim"),
    Rep("rep_07", "Sofia Rossi"),
    Rep("rep_08", "Liam Walsh"),
)
REP_IDS = tuple(rep.id for rep in REPS)

# Internal stage IDs of HubSpot's default "Sales Pipeline", in order.
# seed_hubspot.py checks these against your account before sending anything.
OPEN_STAGES = (
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
)
WON_STAGE = "closedwon"
LOST_STAGE = "closedlost"
ALL_STAGES = (*OPEN_STAGES, WON_STAGE, LOST_STAGE)

# A few of HubSpot's built-in `industry` option values (it rejects anything not on its list).
# seed_hubspot.py checks these against your account too.
INDUSTRIES = (
    "COMPUTER_SOFTWARE",
    "INFORMATION_TECHNOLOGY_AND_SERVICES",
    "FINANCIAL_SERVICES",
    "HOSPITAL_HEALTH_CARE",
    "RETAIL",
    "MARKETING_AND_ADVERTISING",
)


@dataclass(frozen=True)
class Company:
    ref: str  # your own unique ID, e.g. "co_0001". Deals point at it via company_ref.
    name: str
    domain: str
    industry: str  # one of INDUSTRIES
    number_of_employees: int
    annual_revenue: Decimal


@dataclass(frozen=True)
class Deal:
    ref: str  # your own unique ID, e.g. "deal_0001". Notes point at it via deal_ref.
    company_ref: str  # must match a Company.ref
    rep_id: str  # one of REP_IDS
    name: str
    amount: Decimal  # money is Decimal, never float
    stage: str  # one of ALL_STAGES
    create_date: date
    close_date: date
    next_step: str | None = None


@dataclass(frozen=True)
class Note:
    deal_ref: str  # must match a Deal.ref
    body: str
    timestamp: datetime  # timezone-aware (e.g. tzinfo=UTC)


@dataclass(frozen=True)
class SeedData:
    companies: list[Company]
    deals: list[Deal]
    notes: list[Note]
