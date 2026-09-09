"""Streamlit operational cockpit — talks to FastAPI only."""

from __future__ import annotations

import os
from datetime import datetime

import httpx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("API_KEY", "dev-api-key")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE_URL.rstrip("/"),
        headers={"X-API-Key": API_KEY},
        timeout=30.0,
    )


def _fetch_metrics(start: datetime | None = None, end: datetime | None = None) -> dict:
    params = {}
    if start is not None and end is not None:
        params = {"start": start.isoformat(), "end": end.isoformat()}
    with _client() as client:
        response = client.get("/performance-metrics", params=params)
        response.raise_for_status()
        return response.json()


def _fetch_alerts(status: str | None) -> list[dict]:
    params = {}
    if status and status != "all":
        params["status"] = status
    with _client() as client:
        response = client.get("/alerts", params=params)
        response.raise_for_status()
        return response.json()


def render_overview() -> None:
    st.title("GES plant health")
    st.caption("Actual vs expected production — hidden losses shown as cash at risk.")
    st.caption("Default window is the seeded telemetry span (capped at 31 days).")
    try:
        payload = _fetch_metrics()
    except httpx.HTTPError as exc:
        st.error(f"API unreachable at {API_BASE_URL}: {exc}")
        return

    plant = payload["plant"]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Plant health", f"{plant['health_pct']:.1f}%")
    col2.metric("Actual energy", f"{plant['actual_kwh']:.0f} kWh")
    col3.metric("Expected energy", f"{plant['expected_kwh']:.0f} kWh")
    col4.metric("Cash at risk", f"${plant['cash_loss_usd']:.2f}")
    st.metric("Open high-priority alerts", plant["open_alerts"])

    daily = pd.DataFrame(payload.get("daily") or [])
    if daily.empty:
        st.info("No telemetry in this window. Run `python -m ges_intel.cli` to seed.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily["date"], y=daily["actual_kwh"], name="Actual kWh", mode="lines"))
    fig.add_trace(go.Scatter(x=daily["date"], y=daily["expected_kwh"], name="Expected kWh", mode="lines"))
    fig.update_layout(
        title="Daily production vs expected",
        xaxis_title="Plant-local day",
        yaxis_title="kWh",
        legend=dict(orientation="h"),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)

    inv = pd.DataFrame(payload.get("inverters") or [])
    if not inv.empty:
        st.subheader("Per-inverter performance")
        st.dataframe(inv, use_container_width=True, hide_index=True)


def render_alerts() -> None:
    st.title("Alerts and work orders")
    st.caption("Sustained underperformance (≥10% for 1 hour). Cash loss = lost kWh × $0.10.")
    status = st.selectbox("Status", ["open", "resolved", "all"], index=0)
    try:
        alerts = _fetch_alerts(status)
    except httpx.HTTPError as exc:
        st.error(f"API unreachable at {API_BASE_URL}: {exc}")
        return
    if not alerts:
        st.success("No alerts in this filter.")
        return
    frame = pd.DataFrame(alerts)
    display = frame.rename(
        columns={
            "inverter_id": "Inverter",
            "status": "Status",
            "priority": "Priority",
            "window_start": "From (UTC)",
            "window_end": "To (UTC)",
            "lost_kwh": "Lost kWh",
            "cash_loss_usd": "Est. cash loss (USD)",
            "streak_periods": "Streak",
            "model_version": "Model",
        }
    )
    cols = [
        "Inverter",
        "Status",
        "Priority",
        "From (UTC)",
        "To (UTC)",
        "Lost kWh",
        "Est. cash loss (USD)",
        "Streak",
        "Model",
    ]
    st.dataframe(display[cols], use_container_width=True, hide_index=True)
    total = float(frame["cash_loss_usd"].sum())
    st.metric("Selected-filter cash loss", f"${total:.2f}")


def main() -> None:
    st.set_page_config(page_title="GES Intelligence Cockpit", layout="wide")
    page = st.sidebar.radio("Page", ["Overview", "Alerts and work orders"])
    st.sidebar.markdown(f"API: `{API_BASE_URL}`")
    if page == "Overview":
        render_overview()
    else:
        render_alerts()


main()
