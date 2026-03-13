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
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
]


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
USD_COLUMNS = {"revenue", "adr", "revpar"}


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

    # Parse --analysis-dir from argv if passed after --
    analysis_dir = DEFAULT_ANALYSIS_DIR
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        rest = sys.argv[idx + 1:]
        for i, arg in enumerate(rest):
            if arg == "--analysis-dir" and i + 1 < len(rest):
                analysis_dir = Path(rest[i + 1])
                break
    analysis_dir = Path(analysis_dir)

    with st.sidebar:
        st.title("Onboarding EDA")
        st.caption("View analysis results")
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

    data = load_analysis_data(analysis_dir)
    if data["overall_summary"].empty:
        st.warning("No data in overall_summary.csv. Run analysis first.")
        return

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
    tab_findings, tab_overall, tab_monthly, tab_channel, tab_dow, tab_booking, tab_season, tab_adr = st.tabs([
        "Key findings",
        "Overall summary",
        "Monthly performance",
        "Channel",
        "Day of week",
        "Booking window",
        "Listing seasonality",
        "ADR by listing × month",
    ])

    # ----- Key findings (executive summary from analysis) -----
    with tab_findings:
        st.subheader("Portfolio overview")
        st.markdown("""
        **32 listings** with consistent patterns across size, seasonality, pricing, demand timing, channel mix, and listing dispersion.
        """)
        st.markdown("**Portfolio totals (2 years combined):** Revenue **$7.37M** · Bookings **3,793** · Room-nights **11,521** · ADR **$639** · Occupancy **49.3%** · RevPAR **$315** · Revenue YoY **-0.86%** · Bookings YoY **-0.66%** · ADR YoY **-0.20%**.")

        with st.expander("Listing-level spread and structure", expanded=True):
            st.markdown("""
            - **Typical unit (median):** ~\$232k revenue, ~\$645 ADR, ~50% occupancy, ~\$312 RevPAR.
            - **Spread:** Revenue \$99k–\$364k · ADR \$345–\$1,307 · Occupancy 18%–76% · RevPAR \$135–\$498.
            - **Top revenue:** Angels Landing, The Gallery House, Cathedral Mountain, The Narrows, Johnson Mountain, Virgin River.
            - **Top ADR:** The Gallery House **\$1,307** (outlier); then premium band \$825–\$880 (The Narrows, Angels Landing, Cathedral Mountain, Virgin River, West Temple, Johnson Mountain); core mid-\$600s–low-\$700s.
            - **Top RevPAR:** Angels Landing (\$498), The Gallery House (\$464), Cathedral Mountain (\$452), The Narrows (\$446), Johnson Mountain (\$439).
            - **Outlier:** 103 The Zion Suite — lowest revenue (\$98.8k), -94.6% revenue YoY, -93.3% bookings YoY.
            """)

        with st.expander("Seasonality"):
            st.markdown("""
            - **Strongest months:** April, May, June, September, October.
            - **Weakest months:** January, February, December.
            - **ADR pattern:** Highest March–June and September–October; **summer (Jul–Aug) is not the peak** — ADR softens (e.g. Jul \$594, Aug \$563 vs May \$747). Lowest ADR in Jan–Feb.
            - **Annual:** 2024 ~\$3.70M revenue, 2025 ~\$3.67M; 2025 slightly lower but close.
            """)

        with st.expander("Booking window (lead time)"):
            st.markdown("""
            - **~59% of revenue** booked **15–120 days** before arrival; **~56%** booked **61–270 days** out.
            - **Only ~9%** booked inside **0–14 days** (last-minute).
            - Largest single windows: 121–180 days (15.9%), 91–120 days (12.2%), 181–270 days (10.5%), 31–45 days (10.2%), 46–60 days (9.9%).
            """)

        with st.expander("Check-in day of week"):
            st.markdown("""
            - Check-ins **spread across the week**; not only weekends.
            - **Saturday** leads check-ins (16.4%) and revenue share (17.8%); **Tuesday** lowest (11.0% check-ins, 10.5% revenue).
            - **Highest ADR by arrival day:** Saturday (\$658); **lowest:** Friday (\$616).
            """)

        with st.expander("Channel mix"):
            st.markdown("""
            - **Booked Online:** ~70% of revenue, ~69% of bookings, \$648 ADR.
            - **Direct Connect:** ~20% of revenue, ~22% of bookings, **\$601 ADR (lowest of the three)**.
            - **Other:** ~9% of revenue and bookings, \$663 ADR.
            - Channel mix stable YoY; slight shift toward Direct Connect in 2025.
            - Many listings are **OTA-heavy** (e.g. The Sentinel, Checkerboard Mesa, Temple of Sinawava, Mountain of the Sun, Emerald Pools at 81–83% Booked Online).
            """)

        st.subheader("Combined picture")
        st.markdown("""
        - 32-listing portfolio, **\$7.37M** revenue, **49.3%** occupancy, **\$639** ADR.
        - **Wide performance gap** between listings; one high-rate outlier (The Gallery House) and a handful of premium units driving top revenue.
        - **Strong spring and fall** seasonality; weaker winter; **softer mid-summer ADR** than spring/fall.
        - Demand **concentrated 1–6 months before arrival**; limited last-minute share.
        - Check-ins **spread across the week**, with Saturday leading.
        - **~70% of revenue** from Booked Online; Direct Connect ~20% with the **lowest ADR** of the three channels.
        """)

    with tab_overall:
        st.subheader("Listings + property summary")
        df = data["overall_summary"]
        st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_monthly:
        st.subheader("Monthly performance (revenue, ADR, occupancy, RevPAR)")
        df = data["monthly_performance"]
        df_combined = data.get("monthly_performance_combined", pd.DataFrame())
        if not df.empty and "year_month" in df.columns:
            df = df.sort_values("year_month")
            with st.expander("How the monthly score (1–10) is calculated"):
                st.markdown(
                    "- **Inputs:** total revenue and RevPAR for each month over the two years.\n"
                    "- We **rank months by revenue** and by **RevPAR** (worst to best), convert each rank to a 0–1 scale,\n"
                    "  average those two scores 50/50, then map the result to **1–10**.\n"
                    "- So a month scores **10** only if it is both **big (high revenue)** and **strong (high RevPAR)** "
                    "relative to the other months in this dataset; **1** is the weakest on that combined view."
                )
            col1, col2, col3 = st.columns(3)
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
            with col3:
                if "performance_score_1_10" in df.columns:
                    fig = px.bar(
                        df,
                        x="year_month",
                        y="performance_score_1_10",
                        title="Monthly performance score (1–10)",
                    )
                    fig.update_layout(xaxis_tickangle=-45, yaxis=dict(dtick=1, range=[0.5, 10.5]))
                    st.plotly_chart(fig, use_container_width=True)
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
            if not df_combined.empty:
                st.subheader("Combined monthly performance (average across 2 years)")
                st.caption("One row per calendar month (Jan–Dec), using averages across 2024 and 2025.")
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
            st.dataframe(format_table_display(summary), use_container_width=True, hide_index=True)
        if not by_year.empty:
            st.subheader("Channel by year")
            st.dataframe(format_table_display(by_year), use_container_width=True, hide_index=True)
        st.subheader("Channel by listing")
        st.dataframe(format_table_display(data["channel_by_listing"]), use_container_width=True, hide_index=True)

    with tab_dow:
        st.subheader("Check-ins and revenue by day of week")
        df = data["by_day_of_week"]
        if not df.empty:
            with st.expander("How the day-of-week score (1–10) is calculated"):
                st.markdown(
                    "- **Inputs:** ADR, share of revenue, and share of check-ins for each arrival day (Mon–Sun).\n"
                    "- For each of the three metrics we rank days from worst to best, convert to 0–1, "
                    "then average the three scores equally and map to **1–10**.\n"
                    "- A day scores **10** only if it is a **high-rate, high-revenue, high-volume** day relative to the others; "
                    "**1** is the weakest across those three dimensions."
                )
            col1, col2, col3 = st.columns(3)
            with col1:
                if "check_ins" in df.columns and "day_of_week" in df.columns:
                    fig = px.bar(df, x="day_of_week", y="check_ins", title="Check-ins by day of week")
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
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
        else:
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)

    with tab_season:
        st.subheader("Listing performance by season")
        df = data.get("listing_season_performance", pd.DataFrame())
        if df.empty:
            st.info("No listing season performance table found.")
        else:
            season_options = ["High", "Shoulder", "Low"]
            season = st.selectbox("Season", season_options, index=0)
            df_season = df[df["season"] == season].copy()
            if df_season.empty:
                st.warning(f"No data for season: {season}")
            else:
                # Sort by score descending, then by unit_id
                df_season = df_season.sort_values(
                    by=["season_score_1_10", "unit_id"], ascending=[False, True]
                )
                st.caption(
                    "Scores (1–10) are relative **within each season**, combining season revenue and ADR for each listing."
                )
                # Show only top N listings in chart to keep it readable
                max_n = len(df_season)
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
            unit_ids = ["All"] + sorted(df["unit_id"].unique().tolist(), key=_unit_sort_key)
            selected = st.selectbox("Filter by listing", unit_ids)
            if selected and selected != "All":
                df = df[df["unit_id"] == selected]
            if "year_month" in df.columns and "adr" in df.columns and len(df) > 0:
                plot_df = df.sort_values("year_month")
                fig = px.bar(plot_df, x="year_month", y="adr", title=f"ADR by month {f'({selected})' if selected != 'All' else ''}")
                fig.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)
        else:
            st.dataframe(format_table_display(df), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
