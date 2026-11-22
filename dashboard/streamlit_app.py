"""RevOps Copilot dashboard.

Step 1 only: a placeholder page that calls the API's /health endpoint.
The forecast, payout table and chat box arrive in Step 7.
"""

import os

import httpx
import streamlit as st

# Inside Docker this is http://api:8000 (set in docker-compose.yml).
# Running Streamlit directly on your laptop, it falls back to localhost.
API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="RevOps Copilot")
st.title("RevOps Copilot")
st.caption("Step 1 placeholder. Forecast, payouts and chat arrive in Step 7.")

try:
    resp = httpx.get(f"{API_URL}/health", timeout=3)
except httpx.HTTPError as exc:
    st.error(f"Can't reach the API at {API_URL}: {exc}")
else:
    if resp.status_code == 200:
        st.success("API and database are both up.")
    else:
        st.warning(f"API is up but returned {resp.status_code}.")
    st.json(resp.json())
