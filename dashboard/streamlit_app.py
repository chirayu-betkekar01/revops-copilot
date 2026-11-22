"""RevOps Copilot dashboard: forecast, rep payouts (with the deals behind them) and a chat box.

Every number comes from the API, which serves stored runs (`just forecast`, `just comp`).
The dashboard never talks to the database and never computes a figure of its own beyond
simple totals of what the API returned.
"""

import os
from decimal import Decimal
from typing import Any

import httpx
import streamlit as st

# Inside Docker this is http://api:8000 (set in docker-compose.yml).
# Running Streamlit directly on your laptop, it falls back to localhost.
API_URL = os.environ.get("API_URL", "http://localhost:8000")
TIMEOUT_SECONDS = 5


class ApiError(Exception):
    """The API couldn't be reached or answered with an error."""


@st.cache_data(ttl=30, show_spinner=False)
def api_get(path: str, params: tuple[tuple[str, str], ...] = ()) -> Any:
    """GET from the API, cached for 30s. Returns None on 404 (nothing stored yet)."""
    try:
        resp = httpx.get(f"{API_URL}{path}", params=dict(params), timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise ApiError(f"Can't reach the API at {API_URL} ({type(exc).__name__}).") from exc
    if resp.status_code == 404:
        return None
    if resp.is_error:
        detail = resp.json().get("detail", resp.text) if resp.content else resp.reason_phrase
        raise ApiError(f"The API answered {resp.status_code} for {path}: {detail}")
    return resp.json()


def money(value: str | Decimal) -> str:
    """$1,234 / -$1,234. The minus goes first: Streamlit reads it to pick the delta arrow."""
    amount = Decimal(value)
    return f"{'-' if amount < 0 else ''}${abs(amount):,.0f}"


def quarter_label(iso_date: str) -> str:
    year, month = int(iso_date[:4]), int(iso_date[5:7])
    return f"{year} Q{(month - 1) // 3 + 1}"


# ---------- Page ----------

st.set_page_config(page_title="RevOps Copilot", page_icon=":bar_chart:", layout="wide")
st.title("RevOps Copilot")
st.caption("Bookings forecast and sales commissions, from HubSpot data.")

# Is the API (and its database) up? If not, say so clearly and stop: no half-drawn page.
try:
    health = httpx.get(f"{API_URL}/health", timeout=TIMEOUT_SECONDS).json()
except httpx.HTTPError as exc:
    st.error(f"Can't reach the API at {API_URL} ({type(exc).__name__}).")
    st.info("Start everything with `just up`, then refresh this page.")
    st.stop()
if health.get("database") != "ok":
    st.error("The API is up, but it can't reach the database.")
    st.info("Check the db container with `docker compose ps`, then refresh this page.")
    st.stop()

try:
    # ---------- Forecast ----------
    forecast = api_get("/forecast")
    if forecast is None:
        st.info("No forecast yet. Run `just forecast`.")
    else:
        st.header(f"Forecast: {quarter_label(forecast['quarter_start'])}")
        history = api_get("/forecast/runs", (("limit", "20"),)) or []
        earlier = [
            r
            for r in history
            if r["quarter_start"] == forecast["quarter_start"]
            and r["method"] == forecast["method"]
            and r["run_id"] < forecast["run_id"]
        ]
        delta = (
            money(Decimal(forecast["p50"]) - Decimal(earlier[0]["p50"])) + " vs previous run"
            if earlier
            else None
        )

        tiles = st.columns(4)
        tiles[0].metric("Forecast", money(forecast["p50"]), delta=delta)
        tiles[1].metric("Closed won so far", money(forecast["closed_won"]),
                        help=f"{forecast['n_won']} deals")  # fmt: skip
        tiles[2].metric("Weighted open pipeline", money(forecast["weighted_open"]),
                        help="Open amount x stage probability")  # fmt: skip
        tiles[3].metric("Open pipeline", money(forecast["open_pipeline"]),
                        help=f"{forecast['n_open']} open deals closing this quarter")  # fmt: skip
        st.caption(
            f"Weighted pipeline as of {forecast['as_of']} (run {forecast['run_id']}). "
            "Uses HubSpot's stage probabilities, which tend to be optimistic."
        )

        for count, text in (
            (forecast["n_missing_amount"], "open deals have no amount (counted as $0)"),
            (forecast["n_unknown_stage"], "open deals are in an unknown stage (counted at 0%)"),
            (forecast["n_overdue_prior"], "deals are still open but were due last quarter"),
        ):
            if count:
                st.warning(f"{count} {text}")

        with st.expander("Forecast history"):
            st.dataframe(
                [
                    {
                        "Run": r["run_id"],
                        "As of": r["as_of"],
                        "Quarter": quarter_label(r["quarter_start"]),
                        "Forecast": money(r["p50"]),
                        "Closed won": money(r["closed_won"]),
                        "Weighted open": money(r["weighted_open"]),
                    }
                    for r in history
                ],
                hide_index=True,
            )

    # ---------- Payouts ----------
    st.header("Commission payouts")
    comp_run = api_get("/comp/runs/latest")
    if comp_run is None:
        st.info("No commission run yet. Run `just comp`.")
    else:
        quarters: list[str] = comp_run["quarters"]
        last = quarters[-1]
        quarter = st.selectbox(
            "Quarter",
            quarters,
            index=max(0, len(quarters) - 2),  # default: the last complete quarter
            format_func=lambda q: quarter_label(q) + (" (to date)" if q == last else ""),
        )
        payouts = api_get("/comp/payouts", (("quarter", quarter),))
        reps = payouts["reps"]
        bookings, payout = Decimal(payouts["total_bookings"]), Decimal(payouts["total_payout"])
        at_quota = sum(1 for r in reps if Decimal(r["attainment"]) >= 1)

        tiles = st.columns(4)
        tiles[0].metric("Total payout", money(payout))
        tiles[1].metric("Total bookings", money(bookings))
        tiles[2].metric(
            "Cost of sales",
            f"{payout / bookings:.1%}" if bookings else "n/a",
            help="Commission paid / bookings",
        )
        tiles[3].metric("Reps at quota", f"{at_quota} of {len(reps)}")

        st.dataframe(
            [
                {
                    "Rep": r["rep_name"],
                    "Deals": r["n_deals"],
                    "Bookings": money(r["bookings"]),
                    "Quota": money(r["quota"]),
                    "Attainment": float(Decimal(r["attainment"]) * 100),  # display only
                    "Payout": f"${Decimal(r['payout']):,.2f}",
                }
                for r in reps
            ],
            hide_index=True,
            column_config={
                "Attainment": st.column_config.ProgressColumn(
                    "Attainment",
                    min_value=0,
                    max_value=200,
                    format="%.0f%%",
                    help="Bookings / quota. The bar is full at 200%.",
                ),
            },  # fmt: skip
        )
        status = "final" if payouts["is_complete"] else f"to date (as of {payouts['as_of']})"
        st.caption(f"Commission run {payouts['run_id']}, {status}.")

        # The deals behind one rep's payout: every dollar traced to a deal and a tier.
        names = {r["rep_name"]: r["rep_id"] for r in reps}
        chosen = st.selectbox("See the deals behind a payout", list(names))
        if chosen:
            lines = api_get(f"/comp/payouts/{names[chosen]}/lines", (("quarter", quarter),))
            if lines:
                st.dataframe(
                    [
                        {
                            "Deal": line["deal_name"] or line["deal_id"],
                            "Closed": line["close_date"],
                            "Tier": line["tier"],
                            "Credit": money(line["credit"]),
                            "Rate": f"{Decimal(line['rate']):.2%}",
                            "Payout": f"${Decimal(line['payout']):,.2f}",
                        }
                        for line in lines
                    ],
                    hide_index=True,
                )
                st.caption("A deal that crossed a tier boundary appears once per tier.")
            else:
                st.caption(f"{chosen} closed no deals in {quarter_label(quarter)}.")

except ApiError as exc:
    st.error(str(exc))
    st.info("Refresh the page to try again.")

# ---------- Chat ----------
st.header("Ask the analyst")
if "messages" not in st.session_state:
    st.session_state.messages = []
conversation = st.container()  # reserved above the input, filled in once any answer is in

# Inside a container the chat box sits inline at the end of the page, instead of being pinned
# over the bottom of the window (where it covered the payout tables).
with st.container():
    question = st.chat_input("Ask about the forecast or payouts", max_chars=2000, height=80)
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    try:
        resp = httpx.post(f"{API_URL}/agent/chat", json={"question": question}, timeout=60)
        if resp.status_code == 200:
            answer = resp.json()["answer"]
        else:
            answer = resp.json().get("detail", f"The API answered {resp.status_code}.")
    except httpx.HTTPError as exc:
        answer = f"Can't reach the API ({type(exc).__name__})."
    st.session_state.messages.append({"role": "assistant", "content": answer})

with conversation:
    for message in st.session_state.messages:
        st.chat_message(message["role"]).write(message["content"])
