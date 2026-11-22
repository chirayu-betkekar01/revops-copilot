"""Dashboard tests with Streamlit's AppTest and a fake API (httpx.get / httpx.post swapped)."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "dashboard" / "streamlit_app.py")

FORECAST = {
    "run_id": 2, "created_at": "2026-11-22T12:00:00Z", "method": "weighted_pipeline",
    "as_of": "2026-11-22", "quarter_start": "2026-10-01", "p10": None, "p50": "1631300.00",
    "p90": None, "closed_won": "496000.00", "open_pipeline": "1542500.00",
    "weighted_open": "1135300.00", "n_won": 46, "n_open": 125, "n_missing_amount": 0,
    "n_unknown_stage": 2, "n_overdue_prior": 0,
}  # fmt: skip
OLDER = {**FORECAST, "run_id": 1, "p50": "2194250.00"}
COMP_RUN = {
    "run_id": 1, "created_at": "2026-11-22T12:00:00Z", "as_of": "2026-11-22",
    "plan_id": "standard_ae", "first_quarter": "2026-07-01", "last_quarter": "2026-10-01",
    "total_payout": "196687.50", "quarters": ["2026-07-01", "2026-10-01"],
}  # fmt: skip
PAYOUTS = {
    "run_id": 1, "as_of": "2026-11-22", "quarter_start": "2026-07-01", "is_complete": True,
    "total_bookings": "321500.00", "total_payout": "56937.50",
    "reps": [
        {"rep_id": "rep_01", "rep_name": "Priya Shah", "n_deals": 9, "bookings": "321500.00",
         "quota": "150000.00", "attainment": "2.1433", "payout": "56937.50"},
    ],
}  # fmt: skip
LINES = [
    {"deal_id": "1", "deal_name": "Granite Systems - API Tier", "close_date": "2026-07-03",
     "tier": 1, "credit": "150000.00", "rate": "0.125", "payout": "18750.00",
     "plan_id": "standard_ae", "plan_version": 1},
]  # fmt: skip

Routes = dict[str, tuple[int, Any]]
HEALTHY: Routes = {
    "/health": (200, {"api": "ok", "database": "ok"}),
    "/forecast": (200, FORECAST),
    "/forecast/runs": (200, [FORECAST, OLDER]),
    "/comp/runs/latest": (200, COMP_RUN),
    "/comp/payouts": (200, PAYOUTS),
    "/comp/payouts/rep_01/lines": (200, LINES),
}


def fake_get(routes: Routes) -> Callable[..., httpx.Response]:
    def get(url: str, **kwargs: Any) -> httpx.Response:
        path = "/" + url.split("/", 3)[3]  # drop scheme://host
        status, body = routes[path]
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return get


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    st.cache_data.clear()  # api_get results are cached; don't leak between tests
    yield
    st.cache_data.clear()


def run_app(monkeypatch: pytest.MonkeyPatch, routes: Routes | None = None) -> AppTest:
    if routes is not None:
        monkeypatch.setattr(httpx, "get", fake_get(routes))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def test_api_down_shows_a_clear_error_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", down)
    at = run_app(monkeypatch)

    assert not at.exception
    assert "Can't reach the API" in at.error[0].value
    assert "just up" in at.info[0].value
    assert not at.metric  # nothing half-drawn


def test_database_down_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    at = run_app(monkeypatch, {"/health": (503, {"api": "ok", "database": "unreachable"})})

    assert "can't reach the database" in at.error[0].value


def test_full_page_renders_from_api_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    at = run_app(monkeypatch, HEALTHY)

    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Forecast"] == "$1,631,300"
    assert metrics["Total payout"] == "$56,938"
    assert metrics["Reps at quota"] == "1 of 1"
    forecast_tile = at.metric[0].proto
    assert forecast_tile.delta == "-$562,950 vs previous run"
    assert forecast_tile.direction == forecast_tile.DOWN  # a drop must not show a green up arrow
    assert any("unknown stage" in w.value for w in at.warning)  # data problems surfaced
    assert len(at.dataframe) == 3  # history, payouts, deal lines


def test_an_api_error_mid_page_is_shown_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {**HEALTHY, "/comp/runs/latest": (503, {"detail": "Database unavailable"})}
    at = run_app(monkeypatch, routes)

    assert not at.exception
    assert "Database unavailable" in at.error[0].value


def test_chat_says_the_agent_is_not_built_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    def post(url: str, **kwargs: Any) -> httpx.Response:
        body = {"detail": "The analyst agent isn't built yet (Step 6, needs AWS Bedrock)."}
        return httpx.Response(501, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    at = run_app(monkeypatch, HEALTHY)
    at.chat_input[0].set_value("What's our P50?").run()

    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert "Step 6" in at.chat_message[1].markdown[0].value
