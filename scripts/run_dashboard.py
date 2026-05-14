#!/usr/bin/env python3
"""
Streamlit dashboard to view onboarding analysis results.
Reads CSVs from output/analysis/ (or a custom path) and displays tables and charts.

Run from project root:
  streamlit run scripts/run_dashboard.py

Or with custom analysis folder:
  streamlit run scripts/run_dashboard.py -- --analysis-dir path/to/analysis
"""

import re
import sys
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Ensure project root is on sys.path so `src` can be imported when running via `streamlit run`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.property_config import load_property_inventory

# Default path to analysis CSVs (per-property; default to lafave_zion)
DEFAULT_PROPERTY_ID = "lafave_zion"
DEFAULT_ANALYSIS_DIR = PROJECT_ROOT / "output" / DEFAULT_PROPERTY_ID / "analysis"

ANALYSIS_FILES = [
    "overall_summary",
    "monthly_performance",
    "monthly_performance_combined",
    "channel_by_year",
    "channel_summary",
    "channel_by_listing",
    "by_day_of_week",
    "booking_window",
    "adr_by_listing_by_month",
    "listing_season_performance",
    "daily_tier_calendar",
    "listing_daily_tier_calendar",
    "tier_summary",
    "tier_blocks",
    "tier_diagnostics",
    "tier_leadtime_pricing_integrity",
    "tier_sensitivity_sweep",
    "tier_validation_summary",
]

# Pricing matrix filenames (per-property)
# - pricing_matrix_draft.csv: per-listing (unit_id) matrix
# - pricing_matrix_group_draft.csv: per-group (listing_group) matrix
PRICING_MATRIX_FILE = "pricing_matrix_draft.csv"
PRICING_MATRIX_GROUP_FILE = "pricing_matrix_group_draft.csv"
DAILY_MARKET_CONTEXT_FILE = "daily_market_context.csv"


def load_analysis_data(analysis_dir: Path) -> dict[str, pd.DataFrame]:
    """Load all analysis CSVs from the given directory. Returns dict of name -> DataFrame."""
    data = {}
    for name in ANALYSIS_FILES:
        path = analysis_dir / f"{name}.csv"
        if path.exists():
            data[name] = pd.read_csv(path)
        else:
            data[name] = pd.DataFrame()
    return data


# Column names that should display as USD ($)
USD_COLUMNS = {"revenue", "adr", "revpar", "draft_adr"}


def _format_usd(val):
    """Format number as USD (e.g. $1,234.56). None/NaN -> ''."""
    if pd.isna(val) or val is None:
        return ""
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return str(val)


def _format_pct(val):
    """Format number as percentage (e.g. 12.5% or -3.2%). None/NaN -> ''."""
    if pd.isna(val) or val is None:
        return ""
    try:
        return f"{float(val):.2f}%"
    except (TypeError, ValueError):
        return str(val)


def format_table_display(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a copy of the dataframe with USD and % columns formatted for display.
    - revenue, adr, revpar -> $X,XXX.XX
    - *_yoy_pct, occupancy_pct, share_of_*_pct -> X.XX%
    """
    if df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        if col in USD_COLUMNS and col in out.columns:
            out[col] = out[col].map(_format_usd)
        elif (
            "yoy_pct" in col
            or col == "occupancy_pct"
            or (col.startswith("share_of_") and col.endswith("_pct"))
        ) and col in out.columns:
            out[col] = out[col].map(_format_pct)
    return out


def main():
    st.set_page_config(
        page_title="Onboarding EDA Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Parse --property / --analysis-dir from argv if passed after --
    analysis_dir = DEFAULT_ANALYSIS_DIR
    selected_property_id = DEFAULT_PROPERTY_ID
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        rest = sys.argv[idx + 1:]
        for i, arg in enumerate(rest):
            if arg == "--property" and i + 1 < len(rest):
                selected_property_id = str(rest[i + 1]).strip()
                analysis_dir = PROJECT_ROOT / "output" / selected_property_id / "analysis"
            if arg == "--analysis-dir" and i + 1 < len(rest):
                analysis_dir = Path(rest[i + 1])
                break
    analysis_dir = Path(analysis_dir)

    with st.sidebar:
        st.title("Onboarding EDA")
        st.caption("View analysis results")
        output_root = PROJECT_ROOT / "output"
        available_properties = sorted(
            [p.name for p in output_root.iterdir() if p.is_dir() and (p / "analysis").exists()]
        ) if output_root.exists() else []
        if available_properties:
            if selected_property_id not in available_properties:
                selected_property_id = available_properties[0]
            selected_property_id = st.selectbox(
                "Property",
                options=available_properties,
                index=available_properties.index(selected_property_id),
                help="Switch between properties with analysis outputs.",
            )
            analysis_dir = PROJECT_ROOT / "output" / selected_property_id / "analysis"
        custom_dir = st.text_input(
            "Analysis folder",
            value=str(analysis_dir),
            help="Path to folder containing the analysis CSV files (e.g. output/analysis)",
        )
        analysis_dir = Path(custom_dir) if custom_dir else analysis_dir

    if not analysis_dir.exists():
        st.error(f"Analysis folder not found: {analysis_dir}")
        st.info("Run the analysis first: `python scripts/run_analysis.py --property <id>`")
        return

    # Load property config (for pricing events, etc.)
    property_id = analysis_dir.parent.name
    try:
        inventory = load_property_inventory(property_id)
    except Exception:
        inventory = {}
    # Listing groups are defined in the property config but not used as a UI filter here.
    selected_unit_ids = None

    data = load_analysis_data(analysis_dir)
    if data["overall_summary"].empty:
        st.warning("No data in overall_summary.csv. Run analysis first.")
        return

    # Try to locate pricing matrices next to analysis dir (../pricing)
    pricing_dir = analysis_dir.parent / "pricing"
    pricing_path = pricing_dir / PRICING_MATRIX_FILE
    pricing_df = pd.read_csv(pricing_path) if pricing_path.exists() else pd.DataFrame()
    pricing_group_path = pricing_dir / PRICING_MATRIX_GROUP_FILE
    pricing_group_df = (
        pd.read_csv(pricing_group_path) if pricing_group_path.exists() else pd.DataFrame()
    )
    benchmark_dir = analysis_dir.parent / "benchmark"
    market_ctx_path = benchmark_dir / DAILY_MARKET_CONTEXT_FILE
    market_ctx_df = pd.read_csv(market_ctx_path) if market_ctx_path.exists() else pd.DataFrame()

    # ----- Overview (property-level KPIs) -----
    overall = data["overall_summary"]
    property_row = overall[overall["unit_id"] == "PROPERTY"]
    if not property_row.empty:
        row = property_row.iloc[0]
        st.title("Property performance overview")
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("Revenue (2Y)", f"${row.get('revenue', 0):,.0f}", f"{row.get('revenue_yoy_pct') or 0:+.1f}% YoY")
        with c2:
            st.metric("ADR", f"${row.get('adr', 0):,.0f}", f"{row.get('adr_yoy_pct') or 0:+.1f}% YoY")
        with c3:
            st.metric("Bookings", f"{int(row.get('bookings', 0)):,}", f"{row.get('bookings_yoy_pct') or 0:+.1f}% YoY")
        with c4:
            st.metric("Occupancy %", f"{row.get('occupancy_pct') or 0:.1f}%", None)
        with c5:
            st.metric("RevPAR", f"${row.get('revpar') or 0:,.0f}", None)
        st.divider()

    # ----- Tabs for each analysis -----
    tab_findings, tab_overall, tab_monthly, tab_channel, tab_dow, tab_booking, tab_season, tab_adr, tab_tiers, tab_market_ctx, tab_pricing, tab_pricing_sheet = st.tabs([
        "Key findings",
        "Overall summary",
        "Monthly performance",
        "Channel",
        "Day of week",
        "Booking window",
        "Listing seasonality",
        "ADR by listing × month",
        "Tier calendar",
        "AirDNA context",
        "Pricing matrix",
        "Pricing sheet",
    ])

    # ----- Key findings (executive summary from analysis) -----
    with tab_findings:
        st.subheader("Portfolio snapshot")
        listing_df = overall[overall["unit_id"] != "PROPERTY"].copy()
        total_listings = int(len(listing_df))
        property_rev = float(row.get("revenue", 0) or 0) if not property_row.empty else 0.0
        property_bookings = int(row.get("bookings", 0) or 0) if not property_row.empty else 0
        property_nights = int(row.get("room_nights", 0) or 0) if not property_row.empty else 0
        property_adr = float(row.get("adr", 0) or 0) if not property_row.empty else 0.0
        property_occ = float(row.get("occupancy_pct", 0) or 0) if not property_row.empty else 0.0
        property_revpar = float(row.get("revpar", 0) or 0) if not property_row.empty else 0.0
        top_listing = (
            listing_df.sort_values("revenue", ascending=False).iloc[0]["unit_id"]
            if not listing_df.empty and "revenue" in listing_df.columns else "N/A"
        )
        top_month = (
            data["monthly_performance_combined"].sort_values("revenue", ascending=False).iloc[0]["month_name"]
            if not data["monthly_performance_combined"].empty and "revenue" in data["monthly_performance_combined"].columns else "N/A"
        )
        top_channel = (
            data["channel_summary"].sort_values("revenue", ascending=False).iloc[0]["channel"]
            if not data["channel_summary"].empty and "revenue" in data["channel_summary"].columns else "N/A"
        )
        st.markdown(
            f"- **Size:** {total_listings} active listings\n"
            f"- **2-year totals:** Revenue **${property_rev:,.0f}**, Bookings **{property_bookings:,}**, Room-nights **{property_nights:,}**\n"
            f"- **Quality:** ADR **${property_adr:,.0f}**, Occupancy **{property_occ:.1f}%**, RevPAR **${property_revpar:,.0f}**\n"
            f"- **Top drivers:** Listing **{top_listing}**, Month **{top_month}**, Channel **{top_channel}**\n"
        )

    def _filter_by_group(df: pd.DataFrame, unit_id_col: str = "unit_id") -> pd.DataFrame:
        """If a listing group is selected, filter to those unit_ids plus PROPERTY if present."""
        if selected_unit_ids is None or df.empty or unit_id_col not in df.columns:
            return df
        keep = df[unit_id_col].isin(selected_unit_ids) | (df[unit_id_col] == "PROPERTY")
        return df.loc[keep]

    with tab_overall:
        st.subheader("Listings + property summary")
        df = data["overall_summary"]
        df = _filter_by_group(df)
        st.caption("Values are summed over the two analysis years (24 months).")
        st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_monthly:
        st.subheader("Monthly performance (revenue, ADR, occupancy, RevPAR)")
        df = data["monthly_performance"]
        df_combined = data.get("monthly_performance_combined", pd.DataFrame())
        if not df.empty and "year_month" in df.columns:
            df = df.sort_values("year_month")
            col1, col2 = st.columns(2)
            with col1:
                if "revenue" in df.columns:
                    fig = px.line(df, x="year_month", y="revenue", title="Revenue by month", markers=True)
                    fig.update_layout(xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)
            with col2:
                if "occupancy_pct" in df.columns:
                    fig = px.line(df, x="year_month", y="occupancy_pct", title="Occupancy % by month", markers=True)
                    fig.update_layout(xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)
            st.caption("Values are summed for each year–month across the two analysis years.")
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
            if not df_combined.empty:
                st.subheader("Combined monthly performance (average across 2 years)")
                st.caption("Values are averaged per calendar month across the two analysis years (typical month view). Score (1–10) is only in this table.")
                with st.expander("How the monthly score (1–10) is calculated"):
                    st.markdown(
                        "Applies to the **combined monthly** table below (12 calendar months).\n\n"
                        "- **Inputs:** For each **calendar month** (Jan–Dec), we use metrics **averaged across the two years** "
                        "(total revenue and RevPAR per calendar month).\n"
                        "- We **rank the 12 calendar months** by revenue and by RevPAR (worst to best), convert each rank to 0–1,\n"
                        "  average those two scores **50/50**, then map to **1–10**.\n"
                        "- **Score 10** = strongest calendar month (high revenue and high RevPAR); **1** = weakest. "
                        "**Percentile** (if shown) is the same rank expressed as 0–100%."
                    )
                if "performance_score_1_10" in df_combined.columns:
                    fig = px.bar(
                        df_combined,
                        x="month_name",
                        y="performance_score_1_10",
                        title="Monthly performance score (1–10) by calendar month",
                    )
                    fig.update_layout(xaxis_tickangle=-45, yaxis=dict(dtick=1, range=[0.5, 10.5]))
                    st.plotly_chart(fig, use_container_width=True)
                st.dataframe(format_table_display(df_combined), use_container_width=True, hide_index=True)
        else:
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_channel:
        st.subheader("Channel distribution")
        summary = data["channel_summary"]
        by_year = data["channel_by_year"]
        if not summary.empty:
            col1, col2 = st.columns(2)
            with col1:
                if "share_of_revenue_pct" in summary.columns and "channel" in summary.columns:
                    fig = px.pie(
                        summary, values="share_of_revenue_pct", names="channel",
                        title="Share of revenue by channel",
                    )
                    st.plotly_chart(fig, use_container_width=True)
            with col2:
                if "share_of_bookings_pct" in summary.columns and "channel" in summary.columns:
                    fig = px.pie(
                        summary, values="share_of_bookings_pct", names="channel",
                        title="Share of bookings by channel",
                    )
                    st.plotly_chart(fig, use_container_width=True)
            st.caption("Values are summed over the two analysis years; shares are based on these totals.")
            st.dataframe(format_table_display(summary), use_container_width=True, hide_index=True)
        if not by_year.empty:
            st.subheader("Channel by year")
            st.caption("Values are summed within each year; each row is a single year’s totals.")
            st.dataframe(format_table_display(by_year), use_container_width=True, hide_index=True)
        st.subheader("Channel by listing")
        st.caption("Values are summed over the two analysis years for each listing × channel.")
        ch_by_listing = _filter_by_group(data["channel_by_listing"])
        st.dataframe(format_table_display(ch_by_listing), use_container_width=True, hide_index=True)

    with tab_dow:
        st.subheader("Night-of-week pricing signal")
        df = data["by_day_of_week"]
        if not df.empty:
            with st.expander("How the day-of-week score (1–10) is calculated"):
                st.markdown(
                    "- **Inputs:** ADR, share of revenue, and share of room nights for each stay-night weekday (Mon-Sun).\n"
                    "- Revenue is first split across occupied stay dates (`revenue / nights`) and then grouped by the actual night weekday.\n"
                    "- `check_ins` is shown as contextual arrival behavior only; it is not used in the score.\n"
                    "- For each metric we rank days from worst to best and convert ranks to a 0–1 scale.\n"
                    "- We then combine them with weights **40% ADR**, **40% revenue share**, and **20% room-night share**, "
                    "and map that combined score to a **continuous 1–10 scale** (no rounding).\n"
                    "- A day scores closer to **10** only if it is consistently **high-rate, high-revenue, and high-volume** "
                    "relative to the others; values near **1** are weakest across those dimensions."
                )
            col1, col2, col3 = st.columns(3)
            with col1:
                if "room_nights" in df.columns and "day_of_week" in df.columns:
                    fig = px.bar(df, x="day_of_week", y="room_nights", title="Room nights by stay weekday")
                    fig.update_layout(xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)
            with col2:
                if "share_of_revenue_pct" in df.columns and "day_of_week" in df.columns:
                    fig = px.bar(df, x="day_of_week", y="share_of_revenue_pct", title="Share of revenue by day")
                    fig.update_layout(xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)
            with col3:
                if "dow_score_1_10" in df.columns and "day_of_week" in df.columns:
                    fig = px.bar(
                        df,
                        x="day_of_week",
                        y="dow_score_1_10",
                        title="Day-of-week performance score (1–10)",
                    )
                    fig.update_layout(xaxis_tickangle=-45, yaxis=dict(dtick=1, range=[0.5, 10.5]))
                    st.plotly_chart(fig, use_container_width=True)
            st.caption("Room-night and revenue metrics are night-of-week values (from exploded stay dates) across the configured analysis window.")
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
        else:
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_season:
        st.subheader("Listing performance by season")
        df = data.get("listing_season_performance", pd.DataFrame())
        if df.empty:
            st.info("No listing season performance table found.")
        else:
            with st.expander("How the listing season score (1–10) is calculated"):
                if set(df.get("season", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()).issubset({"High", "Shoulder", "Low"}):
                    st.markdown(
                        "Used for the **listing season performance** table in this tab.\n\n"
                        "- **Inputs:** For each listing in a season we use **season_revenue** and **season_revpar** "
                        "(revenue and RevPAR over that season’s months, across both years).\n"
                        "- We **rank listings within that season** by revenue and by RevPAR (worst to best), "
                        "convert each rank to 0–1, then combine **70% revenue + 30% RevPAR** and map to **1–10**.\n"
                        "- **Score 10** = top performer in that season; **1** = weakest. "
                        "**Percentile** (if shown) is that rank within the season as 0–100%. "
                        "Scores are **not comparable across seasons** (each season is ranked separately)."
                    )
                else:
                    st.markdown(
                        "Used for the **listing tier-segment performance** table in this tab.\n\n"
                        "- Seasons in this property are mapped to **tier segments** (e.g., Soft, Low, Shoulder Low, ... Peak).\n"
                        "- For each listing in each segment, we compute `season_revenue`, `season_room_nights`, `season_bookings`, and `season_adr`.\n"
                        "- We rank listings within each segment by revenue and segment-level RevPAR (70/30 blend) and map to **1–10**.\n"
                        "- Scores are relative within each segment only; they are not directly comparable across different segments."
                    )
            if "season_order" in df.columns:
                season_options = (
                    df[["season", "season_order"]]
                    .dropna()
                    .drop_duplicates()
                    .sort_values(["season_order", "season"])
                    ["season"]
                    .tolist()
                )
            else:
                season_options = sorted(df["season"].dropna().astype(str).unique().tolist())
            view_mode = st.selectbox(
                "View mode",
                ["By listing", "By submarket"],
                index=0,
                help="Switch listing seasonality view between individual listings and submarket aggregates.",
            )
            season = st.selectbox("Season", season_options, index=0 if season_options else None)
            df_season = df[df["season"] == season].copy()
            if df_season.empty:
                st.warning(f"No data for season: {season}")
            else:
                if view_mode == "By submarket":
                    pulls = (inventory.get("airdna") or {}).get("submarket_pulls") or []
                    unit_to_submarket = {}
                    for pull in pulls:
                        subm = str(pull.get("submarket", "")).strip()
                        for uid in (pull.get("listings") or []):
                            unit_to_submarket[str(uid)] = subm
                    if "unit_id" in df_season.columns:
                        df_season["submarket"] = df_season["unit_id"].astype(str).map(unit_to_submarket).fillna("Unmapped")
                    grouped = (
                        df_season.groupby("submarket", as_index=False)
                        .agg(
                            season_revenue=("season_revenue", "sum"),
                            season_room_nights=("season_room_nights", "sum"),
                            season_bookings=("season_bookings", "sum"),
                        )
                    )
                    grouped["season_adr"] = grouped["season_revenue"] / grouped["season_room_nights"]
                    grouped.loc[grouped["season_room_nights"] == 0, "season_adr"] = None
                    n = len(grouped)
                    if n > 1:
                        rev_rank = grouped["season_revenue"].rank(method="min", ascending=True)
                        adr_rank = grouped["season_adr"].rank(method="min", ascending=True)
                        s_rev = (rev_rank - 1) / (n - 1)
                        s_adr = (adr_rank - 1) / (n - 1)
                        s_combined = 0.7 * s_rev + 0.3 * s_adr
                        grouped["season_score_1_10"] = 1 + 9 * s_combined
                    else:
                        grouped["season_score_1_10"] = 5.0
                    grouped = grouped.sort_values(["season_score_1_10", "submarket"], ascending=[False, True])
                    st.caption("Seasonal metrics are aggregated by submarket for the selected season. Score (1–10) is relative within this submarket view.")
                    st.info(
                        "Submarket aggregations group listings by location but tier assignments reflect portfolio-wide scoring, "
                        "not submarket-native performance bands. A Tier 6 day in Virginia Beach and a Tier 6 day in Downtown Baltimore "
                        "are both in the top ~17% of the full FLOHOM portfolio — they are not independently top-performing within their own local markets."
                    )
                    fig = px.bar(
                        grouped,
                        x="season_score_1_10",
                        y="submarket",
                        orientation="h",
                        title=f"Submarket performance – {season} season (score 1–10)",
                    )
                    fig.update_layout(yaxis=dict(autorange="reversed"), xaxis=dict(dtick=1, range=[0.5, 10.5]))
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(format_table_display(grouped), use_container_width=True, hide_index=True)
                else:
                    df_season = _filter_by_group(df_season)
                    # Sort by score descending, then by unit_id
                    df_season = df_season.sort_values(
                        by=["season_score_1_10", "unit_id"], ascending=[False, True]
                    )
                    st.caption("Seasonal metrics are summed over the two analysis years for each listing and season. Scores (1–10) are relative within each season.")
                    # Show only top N listings in chart to keep it readable
                    max_n = len(df_season)
                    if max_n <= 1:
                        n_to_show = max_n
                    elif max_n <= 5:
                        # For small sets, avoid invalid slider bounds.
                        n_to_show = st.slider(
                            "Number of listings to show in chart (sorted by score)",
                            min_value=1,
                            max_value=max_n,
                            value=max_n,
                            step=1,
                        )
                    else:
                        n_to_show = st.slider(
                            "Number of listings to show in chart (sorted by score)",
                            min_value=5,
                            max_value=max_n,
                            value=min(10, max_n),
                            step=1,
                        )
                    chart_df = df_season.head(n_to_show)
                    # Horizontal bar: score on x-axis, listings on y-axis
                    fig = px.bar(
                        chart_df,
                        x="season_score_1_10",
                        y="unit_id",
                        orientation="h",
                        title=f"Top {n_to_show} listings – {season} season (score 1–10)",
                    )
                    fig.update_layout(yaxis=dict(autorange="reversed"), xaxis=dict(dtick=1, range=[0.5, 10.5]))
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(format_table_display(df_season), use_container_width=True, hide_index=True)

    with tab_booking:
        st.subheader("Booking window (lead time)")
        df = data["booking_window"]
        if not df.empty and "booking_window" in df.columns:
            if "share_of_revenue_pct" in df.columns:
                fig = px.bar(
                    df, x="booking_window", y="share_of_revenue_pct",
                    title="Share of revenue by booking window",
                )
                fig.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)
            st.caption("Values are summed over the two analysis years for each booking window band.")
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
        else:
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_adr:
        st.subheader("ADR by listing and month (two years)")
        df = data["adr_by_listing_by_month"]
        if not df.empty and "unit_id" in df.columns:
            def _unit_sort_key(u):
                if u == "PROPERTY":
                    return (999999, u)
                m = re.match(r"^(\d+)", str(u))
                return (int(m.group(1)) if m else 0, u)
            all_units = sorted(df["unit_id"].unique().tolist(), key=_unit_sort_key)
            if selected_unit_ids is not None:
                all_units = [u for u in all_units if u in selected_unit_ids]
            unit_ids = ["All"] + all_units
            selected = st.selectbox("Filter by listing", unit_ids)
            if selected and selected != "All":
                df = df[df["unit_id"] == selected]
            if "year_month" in df.columns and "adr" in df.columns and len(df) > 0:
                plot_df = df.sort_values("year_month")
                fig = px.bar(plot_df, x="year_month", y="adr", title=f"ADR by month {f'({selected})' if selected != 'All' else ''}")
                fig.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)
            st.caption("Values are summed within each listing–month; ADR and min/max ADR are derived from these sums.")
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
        else:
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_tiers:
        st.subheader("Daily tier calendar (RevPAR-based)")
        with st.expander("How tier calendar analysis works"):
            st.markdown(
                "### 1) Scope and date window\n"
                "- The tier model uses the same two-year analysis window as core analysis (example: 2024-01-01 to 2025-12-31).\n"
                "- Days outside that window are not used for tier scoring.\n\n"
                "### 2) Listing-first daily build (start-date aware)\n"
                "- Each listing starts at its configured `listing_start_date`.\n"
                "- Reservations are expanded into **stay nights** (`arrival_date` to `departure_date - 1 day`).\n"
                "- Revenue is distributed per night (`revenue / nights`) before day-level aggregation.\n"
                "- Check-ins (`bookings`) remain arrival-day based.\n\n"
                "**Example:**\n"
                "- Reservation: Dec 5 -> Dec 7, revenue $963, nights 2.\n"
                "- Listing-day rows become:\n"
                "  - Dec 5: revenue $481.50, room_nights 1, bookings 1\n"
                "  - Dec 6: revenue $481.50, room_nights 1, bookings 0\n\n"
                "### 3) Day-of-year normalization per listing\n"
                "- For each listing and each month-day (`MM-DD`), the model averages that listing's RevPAR across contributing years.\n"
                "- This avoids penalizing newer listings for not existing in earlier years.\n\n"
                "**Example:**\n"
                "- Listing A active in 2024 + 2025:\n"
                "  - Jul 15 RevPAR values: $220 (2024), $260 (2025) -> DOY avg = $240.\n"
                "- Listing B active only in 2025:\n"
                "  - Jul 15 RevPAR value: $180 -> DOY avg = $180 (1-year evidence).\n\n"
                "### 4) Portfolio daily signal (with maturity weighting)\n"
                "- Portfolio daily RevPAR is built from eligible listing-day DOY averages.\n"
                "- Listings that have not yet met the minimum active-history threshold are excluded from the portfolio signal until that threshold is reached.\n"
                "- Remaining listings are combined using weighted mean:\n"
                "  - weight = `contributing_year_count`\n"
                "  - daily portfolio revpar = `sum(listing_doy_avg * weight) / sum(weight)`\n\n"
                "**Example:**\n"
                "- Listing A: doy avg $240, years=2 (weight 2)\n"
                "- Listing B: doy avg $180, years=1 (weight 1)\n"
                "- Portfolio revpar = `(240*2 + 180*1) / (2+1) = $220`.\n\n"
                "### 5) Smoothing and tier assignment\n"
                "- The daily series is smoothed with a centered 14-day rolling mean.\n"
                "- The model attempts natural gap detection first.\n"
                "- If no valid structure is found, it falls back to quantile tiers from property config.\n"
                "- Tier count selection should be validated per property by checking separability between adjacent tiers "
                "(e.g., step-size compression at higher tier counts indicates over-segmentation).\n\n"
                "### 6) How to interpret outputs\n"
                "- Use `tier_summary` to understand each tier's average level (most reliable discriminator).\n"
                "- Use `tier_blocks` to see contiguous regime periods.\n"
                "- Use `tier_diagnostics` to confirm method path and any composition-shift conditions.\n"
                "- Tier-seasons are quantile-derived regime buckets; they are not calendar-native submarket seasons.\n"
                "- `composition_shift_flag = True` means smoothing window includes denominator changes; interpret those spans with extra caution.\n"
                "- Listings whose start date falls entirely outside the analysis window are flagged as `out_of_window` and excluded from current tier modeling.\n"
                "- Days where `composition_shift_flag = True` or where market context is `market_reliability: low` should be treated as directional signals, not precise benchmarks."
            )
        daily = data.get("daily_tier_calendar", pd.DataFrame())
        listing_daily = data.get("listing_daily_tier_calendar", pd.DataFrame())
        summary = data.get("tier_summary", pd.DataFrame())
        blocks = data.get("tier_blocks", pd.DataFrame())
        diagnostics = data.get("tier_diagnostics", pd.DataFrame())
        tier_lead_integrity = data.get("tier_leadtime_pricing_integrity", pd.DataFrame())
        sensitivity = data.get("tier_sensitivity_sweep", pd.DataFrame())
        validation_summary = data.get("tier_validation_summary", pd.DataFrame())
        if daily.empty:
            st.info("No daily tier outputs found. Re-run analysis for this property.")
        else:
            method = daily["tier_method"].iloc[0] if "tier_method" in daily.columns and len(daily) > 0 else "unknown"
            st.caption(f"Tier method: {method}. Tiers are property-relative using smoothed daily RevPAR percentiles.")
            # Consistent cross-property color progression:
            # lowest tier -> red, highest tier -> green (by tier_id).
            palette = [
                "#d73027", "#e95b2b", "#f58634", "#fdbf6f", "#fee08b",
                "#e6f598", "#c7e9ad", "#a6dba0", "#80cdc1", "#66bd63",
                "#4daf4a", "#3b9d47", "#2a8a43", "#1f9e89", "#1a9850",
            ]
            if not summary.empty and "tier_id" in summary.columns and "tier_label" in summary.columns:
                sorted_tiers = (
                    summary[["tier_id", "tier_label"]]
                    .dropna()
                    .drop_duplicates()
                    .sort_values("tier_id")
                )
                tier_order = sorted_tiers["tier_label"].astype(str).tolist()
            elif "tier_label" in daily.columns and "tier_id" in daily.columns:
                sorted_tiers = (
                    daily[["tier_id", "tier_label"]]
                    .dropna()
                    .drop_duplicates()
                    .sort_values("tier_id")
                )
                tier_order = sorted_tiers["tier_label"].astype(str).tolist()
            else:
                tier_order = sorted(daily["tier_id"].dropna().astype(int).unique().tolist()) if "tier_id" in daily.columns else []
                tier_order = [f"Tier {t}" for t in tier_order]
            tier_color_map = {}
            n = len(tier_order)
            if n > 0:
                for idx, label in enumerate(tier_order):
                    pal_idx = int(round(idx * (len(palette) - 1) / max(1, n - 1)))
                    tier_color_map[str(label)] = palette[pal_idx]
            col1, col2 = st.columns(2)
            with col1:
                if "date" in daily.columns and "revpar_smoothed" in daily.columns and "tier_id" in daily.columns:
                    color_series = daily["tier_label"].astype(str) if "tier_label" in daily.columns else daily["tier_id"].astype(str)
                    fig = px.scatter(
                        daily,
                        x="date",
                        y="revpar_smoothed",
                        color=color_series,
                        title="Smoothed RevPAR by day colored by tier",
                        color_discrete_map=tier_color_map,
                        category_orders={"color": tier_order, "tier_label": tier_order},
                    )
                    fig.update_layout(xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)
            with col2:
                if not summary.empty and "tier_label" in summary.columns and "days" in summary.columns:
                    fig = px.bar(
                        summary.sort_values("tier_id"),
                        x="tier_label",
                        y="days",
                        title="Days per tier",
                        color="tier_label",
                        color_discrete_map=tier_color_map,
                        category_orders={"tier_label": tier_order},
                    )
                    fig.update_layout(showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
            # Month-of-year aggregate view (helpful when analysis window starts mid-year).
            if "date" in daily.columns and "revpar_smoothed" in daily.columns:
                md = daily.copy()
                md["date"] = pd.to_datetime(md["date"], errors="coerce")
                md = md.dropna(subset=["date"])
                if not md.empty:
                    md["month_index"] = md["date"].dt.month
                    month_map = {
                        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
                    }
                    month_order = [month_map[i] for i in range(1, 13)]
                    agg = (
                        md.groupby("month_index", as_index=False)
                        .agg(
                            avg_revpar_smoothed=("revpar_smoothed", "mean"),
                            n_days=("date", "count"),
                        )
                    )
                    if "tier_label" in md.columns:
                        dom = (
                            md.groupby(["month_index", "tier_label"], as_index=False)
                            .size()
                            .sort_values(["month_index", "size"], ascending=[True, False])
                            .drop_duplicates(subset=["month_index"])
                            .rename(columns={"tier_label": "dominant_tier_label"})
                        )
                        agg = agg.merge(dom[["month_index", "dominant_tier_label"]], on="month_index", how="left")
                    full = pd.DataFrame({"month_index": list(range(1, 13))})
                    agg = full.merge(agg, on="month_index", how="left")
                    agg["month_name"] = agg["month_index"].map(month_map)
                    st.subheader("Month-of-year tier profile (aggregated across analysis years)")
                    st.caption(
                        "For each calendar month (Jan-Dec), values are averaged across all years included in the analysis window. "
                        "This helps read seasonality when the window starts mid-year."
                    )
                    fig = px.bar(
                        agg,
                        x="month_name",
                        y="avg_revpar_smoothed",
                        color="dominant_tier_label" if "dominant_tier_label" in agg.columns else None,
                        color_discrete_map=tier_color_map,
                        category_orders={"month_name": month_order, "dominant_tier_label": tier_order},
                        title="Average smoothed RevPAR by calendar month (all years combined)",
                    )
                    fig.update_layout(xaxis={"categoryorder": "array", "categoryarray": month_order})
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(format_table_display(agg), use_container_width=True, hide_index=True)
            st.subheader("Tier summary")
            st.dataframe(format_table_display(summary), use_container_width=True, hide_index=True)
            if not diagnostics.empty:
                st.subheader("Tier diagnostics")
                st.dataframe(diagnostics, use_container_width=True, hide_index=True)
            if not validation_summary.empty:
                st.subheader("Tier validation summary")
                st.caption("Automated sweep-based recommendation from analysis run.")
                st.dataframe(validation_summary, use_container_width=True, hide_index=True)
            if not sensitivity.empty:
                st.subheader("Tier sensitivity sweep")
                st.caption(
                    "Validation sweep across gap thresholds and quantile tier counts. "
                    "Use this to confirm whether fallback tier count is defensible."
                )
                with st.expander("View full tier sensitivity sweep table"):
                    st.dataframe(sensitivity, use_container_width=True, hide_index=True)
            if not tier_lead_integrity.empty:
                st.subheader("Pricing integrity: tier x lead band")
                st.caption(
                    "Operational check of ADR and revenue mix by tier and booking lead-time band "
                    "(arrival-date join to tier calendar). `revenue_share_of_tier` is percent of tier revenue."
                )
                view = tier_lead_integrity.copy()
                if "tier_label" in view.columns:
                    tier_opts = ["All"] + sorted(view["tier_label"].dropna().astype(str).unique().tolist())
                    tier_sel = st.selectbox("Filter pricing integrity by tier", tier_opts, index=0, key="tier_lead_filter")
                    if tier_sel != "All":
                        view = view[view["tier_label"] == tier_sel]
                st.dataframe(format_table_display(view), use_container_width=True, hide_index=True)
            st.subheader("Tier blocks (contiguous runs)")
            st.dataframe(blocks, use_container_width=True, hide_index=True)
            st.subheader("Daily calendar table")
            st.dataframe(format_table_display(daily), use_container_width=True, hide_index=True)
            if not listing_daily.empty:
                st.subheader("Listing-day tier context (start-date aware)")
                st.caption("Listing-level history with active-window logic and day-of-year averaging context.")
                tier_view_mode = st.selectbox(
                    "Tier context view mode",
                    ["By listing", "By submarket"],
                    index=0,
                    key="tier_ctx_view_mode",
                )
                if tier_view_mode == "By submarket":
                    pulls = (inventory.get("airdna") or {}).get("submarket_pulls") or []
                    unit_to_submarket = {}
                    for pull in pulls:
                        subm = str(pull.get("submarket", "")).strip()
                        for uid in (pull.get("listings") or []):
                            unit_to_submarket[str(uid)] = subm
                    sub_df = listing_daily.copy()
                    if "unit_id" in sub_df.columns:
                        sub_df["submarket"] = sub_df["unit_id"].astype(str).map(unit_to_submarket).fillna("Unmapped")
                    tier_options = ["All"] + sorted(sub_df["tier_label"].dropna().astype(str).unique().tolist()) if "tier_label" in sub_df.columns else ["All"]
                    selected_tier = st.selectbox("Filter by tier label", tier_options, index=0, key="tier_ctx_submarket_tier")
                    if selected_tier != "All" and "tier_label" in sub_df.columns:
                        sub_df = sub_df[sub_df["tier_label"] == selected_tier]
                    grouped_cols = [c for c in ["submarket", "tier_label"] if c in sub_df.columns]
                    if not grouped_cols:
                        st.dataframe(format_table_display(sub_df), use_container_width=True, hide_index=True)
                    else:
                        g = (
                            sub_df.groupby(grouped_cols, as_index=False)
                            .agg(
                                rows=("unit_id", "count") if "unit_id" in sub_df.columns else ("date", "count"),
                                listings=("unit_id", "nunique") if "unit_id" in sub_df.columns else ("date", "count"),
                                avg_property_revpar=("property_revpar", "mean") if "property_revpar" in sub_df.columns else ("rows", "count"),
                                avg_listing_revpar_doy_avg=("listing_revpar_doy_avg", "mean") if "listing_revpar_doy_avg" in sub_df.columns else ("rows", "count"),
                                avg_contributing_years=("contributing_year_count", "mean") if "contributing_year_count" in sub_df.columns else ("rows", "count"),
                            )
                        )
                        for c in ["avg_property_revpar", "avg_listing_revpar_doy_avg", "avg_contributing_years"]:
                            if c in g.columns:
                                g[c] = pd.to_numeric(g[c], errors="coerce").round(2)
                        g = g.sort_values([c for c in ["tier_label", "avg_listing_revpar_doy_avg"] if c in g.columns], ascending=[True, False] if "tier_label" in g.columns else [False])
                        st.info(
                            "Submarket aggregations group listings by location but tier assignments reflect portfolio-wide scoring, "
                            "not submarket-native performance bands. A Tier 6 day in Virginia Beach and a Tier 6 day in Downtown Baltimore "
                            "are both in the top ~17% of the full FLOHOM portfolio — they are not independently top-performing within their own local markets."
                        )
                        if "submarket" in g.columns and "avg_listing_revpar_doy_avg" in g.columns:
                            plot_g = g.groupby("submarket", as_index=False).agg(avg_listing_revpar_doy_avg=("avg_listing_revpar_doy_avg", "mean"))
                            fig = px.bar(
                                plot_g.sort_values("avg_listing_revpar_doy_avg", ascending=False),
                                x="submarket",
                                y="avg_listing_revpar_doy_avg",
                                title="Average listing day-of-year RevPAR by submarket",
                            )
                            fig.update_layout(xaxis_tickangle=-45)
                            st.plotly_chart(fig, use_container_width=True)
                        st.dataframe(format_table_display(g), use_container_width=True, hide_index=True)
                else:
                    st.dataframe(format_table_display(listing_daily), use_container_width=True, hide_index=True)

    with tab_market_ctx:
        st.subheader("AirDNA daily market context")
        with st.expander("How AirDNA context is computed"):
            st.markdown(
                "### 1) Purpose and separation\n"
                "- This is an **extension layer**: it enriches listing-day rows with market benchmarks.\n"
                "- It does **not** alter tier assignment logic.\n\n"
                "### 2) Join grain and benchmark granularity\n"
                "- Property side is listing-day (`unit_id`, `date`).\n"
                "- AirDNA benchmark side is monthly (`submarket`, `year`, `month`).\n"
                "- Each listing-day row gets the benchmark for its submarket and month.\n\n"
                "**Important:**\n"
                "- RPI is daily-assigned but monthly-benchmarked.\n"
                "- Two different days in the same month use the same `market_revpar_monthly` reference.\n\n"
                "- Listing-days with no active property RevPAR produce null RPI and are not benchmark-enriched; `benchmark_warning` reflects this where applicable.\n\n"
                "- Tier-seasons are quantile-derived regime buckets; they are not calendar-native submarket seasons.\n\n"
                "### 3) RPI formula\n"
                "- `rpi = property_revpar / market_revpar_monthly`\n"
                "- Interpretation:\n"
                "  - `rpi > 1.0` -> listing outperformed submarket benchmark for that month\n"
                "  - `rpi < 1.0` -> listing underperformed benchmark\n\n"
                "**Example:**\n"
                "- Listing-day RevPAR = $240\n"
                "- Submarket monthly RevPAR = $180\n"
                "- `rpi = 240 / 180 = 1.33` (about 33% above benchmark)\n\n"
                "### 4) Market condition labeling\n"
                "- For each submarket independently, monthly RevPAR history is split into terciles:\n"
                "  - bottom third -> `weak`\n"
                "  - middle third -> `neutral`\n"
                "  - top third -> `strong`\n"
                "- This avoids cross-market scale distortion (e.g., Annapolis vs Virginia Beach absolute levels).\n\n"
                "### 5) Reliability and warning fields\n"
                "- `market_reliability` (reliable / low / pending) comes from property config mapping.\n"
                "- `pending` indicates the listing/submarket benchmark pull is not yet active (example: Myrtle Beach), so benchmark context may be incomplete.\n"
                "- `benchmark_warning` flags known caution states:\n"
                "  - `circular_market`\n"
                "  - `thin_market`\n"
                "  - `inactive_listing`\n"
                "- Treat high RPI values in low-reliability rows as directional, not definitive.\n\n"
                "### 6) Optional context fields\n"
                "- `percentile_band`: where listing revenue sits vs monthly submarket 25/50/75 percentile cutoffs.\n"
                "- `bedroom_revenue_benchmark`: populated only where bedroom-level AirDNA coverage is sufficient for that submarket; currently null where coverage is sparse or shifts mid-series.\n\n"
                "### 7) Interpreting mixed signals\n"
                "- `market_condition = strong` and `rpi < 1.0`: listing underperformed during a strong market period (investigate pricing/positioning/availability).\n"
                "- `market_condition = weak` and `rpi > 1.0`: listing outperformed a soft market (signal of relative pricing power)."
            )
        if market_ctx_df.empty:
            st.info(
                "No market context file found. Generate it with "
                "`python scripts/run_daily_market_context.py --property <id>`."
            )
        else:
            df = market_ctx_df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
            for c in ["rpi", "property_revpar", "market_revpar_monthly"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Rows", f"{len(df):,}")
            with c2:
                n_units = df["unit_id"].nunique() if "unit_id" in df.columns else 0
                st.metric("Listings", f"{n_units:,}")
            with c3:
                avg_rpi = pd.to_numeric(df.get("rpi"), errors="coerce").mean()
                st.metric("Avg RPI", f"{avg_rpi:.2f}" if pd.notna(avg_rpi) else "n/a")
            with c4:
                low_rel = (df.get("market_reliability") == "low").sum() if "market_reliability" in df.columns else 0
                st.metric("Low reliability rows", f"{int(low_rel):,}")

            st.subheader("Action-oriented interpretation")
            strong_under = (
                ((df.get("market_condition") == "strong") & (pd.to_numeric(df.get("rpi"), errors="coerce") < 1)).sum()
                if {"market_condition", "rpi"}.issubset(df.columns) else 0
            )
            weak_out = (
                ((df.get("market_condition") == "weak") & (pd.to_numeric(df.get("rpi"), errors="coerce") > 1)).sum()
                if {"market_condition", "rpi"}.issubset(df.columns) else 0
            )
            cc1, cc2 = st.columns(2)
            with cc1:
                st.metric("Strong market + RPI < 1", f"{int(strong_under):,}")
            with cc2:
                st.metric("Weak market + RPI > 1", f"{int(weak_out):,}")

            if {"unit_id", "submarket", "market_reliability", "rpi"}.issubset(df.columns):
                listing_summary = (
                    df.groupby(["unit_id", "submarket", "market_reliability"], as_index=False)
                    .agg(rows=("rpi", "size"), avg_rpi=("rpi", "mean"), med_rpi=("rpi", "median"))
                )
                reliable_recos = listing_summary[
                    (listing_summary["market_reliability"] == "reliable")
                    & (listing_summary["rows"] >= 60)
                    & (listing_summary["avg_rpi"] >= 2.0)
                ].sort_values("avg_rpi", ascending=False)
                caution_recos = listing_summary[
                    (listing_summary["market_reliability"] != "reliable")
                    & (listing_summary["rows"] >= 20)
                ].sort_values("avg_rpi", ascending=False)

                if not reliable_recos.empty:
                    st.markdown("**Reliable-market rate review candidates**")
                    st.dataframe(format_table_display(reliable_recos), use_container_width=True, hide_index=True)
                else:
                    st.info("No reliable-market listings met the current review thresholds.")

                if not caution_recos.empty:
                    st.markdown("**Low-confidence markets (do not change rates from RPI alone)**")
                    st.caption("These rows are shown for monitoring only; benchmark circularity/thinness can inflate signals.")
                    st.dataframe(format_table_display(caution_recos), use_container_width=True, hide_index=True)

                vb = listing_summary[listing_summary["submarket"].astype(str).str.contains("Virginia Beach", case=False, na=False)]
                if not vb.empty:
                    st.warning(
                        "Virginia Beach currently has low-confidence benchmark context. Treat high RPI as directional only until listings "
                        "have longer history and benchmark independence improves."
                    )

            if "submarket" in df.columns and "rpi" in df.columns:
                sub = (
                    df.groupby("submarket", as_index=False)
                    .agg(
                        avg_rpi=("rpi", "mean"),
                        rows=("submarket", "count"),
                    )
                    .sort_values("avg_rpi", ascending=False)
                )
                col_left, col_right = st.columns(2)
                with col_left:
                    fig = px.bar(
                        sub,
                        x="submarket",
                        y="avg_rpi",
                        title="Average RPI by submarket",
                    )
                    fig.update_layout(xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)
                with col_right:
                    if "benchmark_warning" in df.columns:
                        warn = (
                            df["benchmark_warning"]
                            .fillna("none")
                            .value_counts()
                            .reset_index()
                        )
                        warn.columns = ["benchmark_warning", "rows"]
                        fig = px.bar(
                            warn,
                            x="benchmark_warning",
                            y="rows",
                            title="Benchmark warning distribution",
                        )
                        fig.update_layout(xaxis_tickangle=-30)
                        st.plotly_chart(fig, use_container_width=True)

                st.subheader("Submarket summary")
                st.dataframe(format_table_display(sub), use_container_width=True, hide_index=True)

            # Filters for detailed exploration
            if "unit_id" in df.columns:
                units = ["All"] + sorted(df["unit_id"].dropna().unique().tolist())
                unit_sel = st.selectbox("Filter by listing", units, index=0, key="market_ctx_unit")
                if unit_sel != "All":
                    df = df[df["unit_id"] == unit_sel]
            if "market_reliability" in df.columns:
                rels = ["All"] + sorted(df["market_reliability"].dropna().unique().tolist())
                rel_sel = st.selectbox("Filter by reliability", rels, index=0, key="market_ctx_rel")
                if rel_sel != "All":
                    df = df[df["market_reliability"] == rel_sel]

            st.subheader("Detailed daily context")
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_pricing:
        st.subheader("Draft pricing matrix")
        if pricing_df.empty:
            st.info(
                "No pricing matrix found. Generate it with "
                "`python scripts/run_pricing_matrix.py --property <id>`."
            )
        else:
            st.caption(
                "Draft ADRs by listing × calendar month × day of week, built from analysis scores "
                "and constrained by historical ADR ranges and your configured weekday ladder."
            )
            df = pricing_df.copy()

            with st.expander("How these draft rates are calculated"):
                st.markdown(
                    "1. **Start from each listing’s long‑run level**  \n"
                    "   - Take the 2‑year ADR for each listing from the overall summary (`base_adr_anchor`). "
                    "This is the central price level the matrix revolves around.\n\n"
                    "2. **Adjust for listing strength (RevPAR)**  \n"
                    "   - Compute each listing’s RevPAR and rank it versus the portfolio (percentile).  \n"
                    "   - Map the percentile into a small multiplier (e.g. 1.10 for top performers, 0.90 for the weakest).  \n"
                    "   - This `listing_strength_factor` nudges strong listings slightly above their anchor and weak listings slightly below, "
                    "without forcing exact cross‑listing ordering.\n\n"
                    "3. **Add month seasonality via a 1–10 score**  \n"
                    "   - Use the combined monthly table (12 rows) to get a `month_score` (1–10) for each calendar month, "
                    "based on revenue + RevPAR.  \n"
                    "   - Convert that score into a month factor so strong months (spring/fall) sit above weak months (winter).  \n"
                    "   - Enforce a **month hierarchy**: for a fixed listing and weekday, higher‑scored months are never priced below lower‑scored months.\n\n"
                    "4. **Add day‑of‑week pattern from a 1–10 DOW score**  \n"
                    "   - Use the day‑of‑week table to get `dow_score` (1–10) from ADR + revenue share + check‑in share.  \n"
                    "   - Turn that into a weekday factor and apply your ladder "
                    "**Mon/Tue < Wed < Sun < Thu/Fri < Sat** for every listing × month, by raising lower days up where needed.\n\n"
                    "5. **Combine the three adjustments for a raw price**  \n"
                    "   - For each listing × month × weekday, we start with the base ADR and add three adjustments: "
                    "listing strength, month strength, and DOW strength.  \n"
                    "   - This gives a raw draft ADR that already reflects listing, season, and weekday demand.\n\n"
                    "6. **Constrain to realistic historical ADR ranges**  \n"
                    "   - For each listing × month we look at all historical ADRs for that listing and calendar month "
                    "(with fallbacks to same season, listing‑wide, and portfolio‑wide data if needed).  \n"
                    "   - We build a floor and ceiling around those historical values (weaker months have looser downside, "
                    "strong months allow more upside).  \n"
                    "   - If the whole week sits outside this band, we **scale the entire week up or down**, rather than clipping days individually, "
                    "so the weekday pattern is preserved.\n\n"
                    "7. **Re‑check the hierarchies and format the matrix**  \n"
                    "   - After bounds, we re‑enforce both the month hierarchy and the weekday ladder so they always hold.  \n"
                    "   - Finally we round all rates to whole dollars and pivot to this wide format: one row per listing × month, "
                    "and one column for each day of week showing the final draft ADR."
                )

            # Listing/group filter: matrix may be by listing (`unit_id`) or by group (`listing_group`)
            id_col = "unit_id" if "unit_id" in df.columns else ("listing_group" if "listing_group" in df.columns else None)
            if id_col:
                all_ids = sorted(df[id_col].dropna().unique().tolist())
                label = "Listing" if id_col == "unit_id" else "Listing group"
                options = ["All"] + all_ids
                selected_id = st.selectbox(label, options, index=0)
                if selected_id != "All":
                    df = df[df[id_col] == selected_id]

            # Month filter
            month_options = ["All months"] + sorted(df["month_index"].unique().tolist())
            selected_month = st.selectbox("Month (1–12)", month_options, index=0)
            if selected_month != "All months":
                df = df[df["month_index"] == selected_month]

            # Simple bar view for the current selection (if single listing & month)
            if (
                id_col
                and selected_id != "All"
                and selected_month != "All months"
                and not df.empty
            ):
                row = df.iloc[0]
                days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                plot_df = pd.DataFrame(
                    {
                        "day_of_week": days,
                        "rate": [row.get(d) for d in days],
                    }
                )
                fig = px.bar(
                    plot_df,
                    x="day_of_week",
                    y="rate",
                    title=f"Draft ADR by day of week – {selected_id}, month {selected_month}",
                )
                fig.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_pricing_sheet:
        st.subheader("Pricing sheet (daily rates)")
        if pricing_df.empty and pricing_group_df.empty:
            st.info(
                "Pricing matrices not found. Generate them first, then configure the pricing sheet here."
            )
        else:
            pricing_cfg = inventory.get("pricing") or {}

            matrix_choice = st.selectbox(
                "Pricing sheet base",
                ["Per listing", "Per group"],
                index=0 if not pricing_df.empty else 1,
                help="Choose whether daily columns represent each unit_id (per listing) or each listing_group.",
            )
            pricing_sheet_df = pricing_df if matrix_choice == "Per listing" else pricing_group_df

            # Default dates:
            # 1) property-specific pricing sheet defaults from config (if provided)
            # 2) otherwise use today -> one year horizon.
            today = date.today()
            default_start = today
            default_end = date(today.year + 1, today.month, today.day) if not (today.month == 2 and today.day == 29) else date(today.year + 1, 2, 28)
            cfg_start = pricing_cfg.get("pricing_sheet_start_date")
            cfg_end = pricing_cfg.get("pricing_sheet_end_date")
            try:
                if cfg_start:
                    default_start = datetime.strptime(str(cfg_start), "%Y-%m-%d").date()
                if cfg_end:
                    default_end = datetime.strptime(str(cfg_end), "%Y-%m-%d").date()
            except Exception:
                # Keep safe defaults if config dates are malformed.
                pass
            col_dates, col_events = st.columns([2, 3])
            with col_dates:
                start_date = st.date_input(
                    "Start date",
                    value=default_start,
                )
                end_date = st.date_input(
                    "End date",
                    value=default_end,
                )
                if end_date < start_date:
                    st.error("End date must be on or after start date.")
            with col_events:
                # Pull events across the full selected range (handles year boundaries).
                years = list(range(start_date.year, end_date.year + 1))
                raw_events = []
                for y in years:
                    raw_events.extend(pricing_cfg.get(f"events_{y}", []))
                # Sort events by start_date for a clean, chronological list
                def _parse_start(evt: dict) -> datetime:
                    try:
                        return datetime.strptime(str(evt.get("start_date")), "%Y-%m-%d")
                    except Exception:
                        return datetime.max

                events = sorted(raw_events, key=_parse_start)
                st.markdown(
                    f"**Event multipliers for selected range ({start_date} to {end_date})** "
                    "(editable for this session, ordered by date):"
                )
                event_rows = []
                for idx, evt in enumerate(events):
                    name = evt.get("name", f"Event {idx+1}")
                    s = evt.get("start_date", "")
                    e = evt.get("end_date", "")
                    default_mult = float(evt.get("multiplier", 1.0) or 1.0)
                    label = f"{name} ({s} – {e})" if s and e else name
                    new_mult = st.number_input(
                        label,
                        min_value=0.5,
                        max_value=3.0,
                        step=0.05,
                        value=default_mult,
                        key=f"evt_{idx}_{name}_{s}_{e}",
                    )
                    event_rows.append(
                        {
                            "name": name,
                            "start_date": s,
                            "end_date": e,
                            "multiplier": new_mult,
                        }
                    )

            generate = st.button("Generate pricing sheet")
            if generate and not pricing_sheet_df.empty and start_date <= end_date:
                # Build date->event lookup
                events_by_date: dict[date, dict] = {}
                for evt in event_rows:
                    try:
                        s = datetime.strptime(str(evt["start_date"]), "%Y-%m-%d").date()
                        e = datetime.strptime(str(evt["end_date"]), "%Y-%m-%d").date()
                    except Exception:
                        continue
                    mult = float(evt["multiplier"] or 1.0)
                    name = evt["name"]
                    cur = s
                    while cur <= e:
                        existing = events_by_date.get(cur)
                        if existing is None or mult > existing.get("multiplier", 1.0):
                            events_by_date[cur] = {"name": name, "multiplier": mult}
                        cur += timedelta(days=1)

                # Helper to iterate dates
                def _daterange(start: date, end: date):
                    cur = start
                    while cur <= end:
                        yield cur
                        cur += timedelta(days=1)

                # Pricing matrix may be keyed by `unit_id` (per-listing) or `listing_group` (group-level).
                id_col = (
                    "unit_id"
                    if "unit_id" in pricing_sheet_df.columns
                    else "listing_group"
                )
                unit_ids = pricing_sheet_df[id_col].unique().tolist()
                if selected_unit_ids is not None:
                    unit_ids = [u for u in unit_ids if u in selected_unit_ids]
                weekday_cols = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                rows = []
                for d in _daterange(start_date, end_date):
                    dow_name = d.strftime("%A")
                    if dow_name not in weekday_cols:
                        continue
                    month_index = d.month
                    row = {
                        "date": d.isoformat(),
                        "day_of_week": dow_name,
                    }
                    for unit in unit_ids:
                        match = pricing_sheet_df[
                            (pricing_sheet_df[id_col] == unit)
                            & (pricing_sheet_df["month_index"] == month_index)
                        ]
                        rate = None
                        if not match.empty:
                            rate = match.iloc[0].get(dow_name)
                        row[unit] = rate
                    evt = events_by_date.get(d)
                    if evt:
                        m = evt.get("multiplier", 1.0) or 1.0
                        for unit in unit_ids:
                            if row[unit] is not None:
                                try:
                                    row[unit] = round(float(row[unit]) * float(m))
                                except (TypeError, ValueError):
                                    pass
                        row["notes"] = evt.get("name", "")
                    else:
                        row["notes"] = ""
                    rows.append(row)

                sheet_df = pd.DataFrame(rows)
                st.success(f"Generated pricing sheet with {len(sheet_df)} days.")
                st.dataframe(sheet_df, use_container_width=True, hide_index=True)

                # Download button
                csv_buf = StringIO()
                sheet_df.to_csv(csv_buf, index=False)
                st.download_button(
                    "Download pricing sheet as CSV",
                    data=csv_buf.getvalue(),
                    file_name=f"pricing_sheet_{start_date.isoformat()}_{end_date.isoformat()}.csv",
                    mime="text/csv",
                )


if __name__ == "__main__":
    main()
