"""Request and response shapes. Pydantic validates input (bad input -> 422) and documents
every field at /docs. Decimals are sent as JSON strings ("18750.00") so no cent is ever lost
to floating point on the way to the client."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field


def must_be_quarter_start(value: date) -> date:
    if value.day != 1 or value.month not in (1, 4, 7, 10):
        raise ValueError("must be the first day of a quarter, e.g. 2026-07-01")
    return value


QuarterStart = Annotated[date, AfterValidator(must_be_quarter_start)]


class ForecastRun(BaseModel):
    run_id: int
    created_at: datetime
    method: str
    as_of: date
    quarter_start: date
    p10: Decimal | None = Field(description="Low end of the range (range methods only)")
    p50: Decimal = Field(description="The forecast")
    p90: Decimal | None = Field(description="High end of the range (range methods only)")
    closed_won: Decimal = Field(description="Already won this quarter")
    open_pipeline: Decimal = Field(description="Open amount closing this quarter")
    weighted_open: Decimal = Field(description="Open amount x stage probability")
    n_won: int
    n_open: int
    n_missing_amount: int = Field(description="Open deals with no amount (counted as $0)")
    n_unknown_stage: int = Field(description="Open deals in an unknown stage (counted at 0%)")
    n_overdue_prior: int = Field(description="Open deals due in an earlier quarter (excluded)")


class CompRun(BaseModel):
    run_id: int
    created_at: datetime
    as_of: date
    plan_id: str
    first_quarter: date
    last_quarter: date
    total_payout: Decimal
    quarters: list[date] = Field(description="Every quarter this run covers, oldest first")


class RepPayout(BaseModel):
    rep_id: str
    rep_name: str
    n_deals: int
    bookings: Decimal
    quota: Decimal
    attainment: Decimal = Field(description="bookings / quota; 1.0 = 100%")
    payout: Decimal


class PayoutsResponse(BaseModel):
    run_id: int
    as_of: date
    quarter_start: date
    is_complete: bool = Field(description="False while the quarter is still in progress")
    total_bookings: Decimal
    total_payout: Decimal
    reps: list[RepPayout]


class CommissionLine(BaseModel):
    deal_id: str
    deal_name: str | None
    close_date: date
    tier: int
    credit: Decimal = Field(description="Part of the deal's amount that fell in this tier")
    rate: Decimal
    payout: Decimal
    plan_id: str
    plan_version: int


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ToolCallOut(BaseModel):
    name: str
    input: dict[str, Any] = Field(description="Arguments the model sent, e.g. the SQL query")
    ok: bool
    error: str | None = None


class ChatResponse(BaseModel):
    answer: str
    status: str = Field(description="answered | max_turns | stopped")
    model_id: str
    turns: int = Field(description="Model calls made in the loop")
    tool_calls: list[ToolCallOut]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    log_id: int | None = Field(description="Row in ops.agent_logs (None if logging failed)")


class ErrorResponse(BaseModel):
    detail: str
