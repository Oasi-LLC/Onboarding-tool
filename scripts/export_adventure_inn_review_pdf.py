#!/usr/bin/env python3
"""Export Adventure Inn Durango summer review as a PDF."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROP = "adventure_inn_durango"
BASE = PROJECT_ROOT / "output" / PROP
OUT_PATH = BASE / "Adventure_Inn_Durango_Summer_Review.pdf"


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="CoverTitle", parent=s["Title"], fontSize=20, spaceAfter=8, alignment=TA_CENTER))
    s.add(ParagraphStyle(name="CoverSub", parent=s["Normal"], fontSize=11, alignment=TA_CENTER, textColor=colors.HexColor("#444444"), spaceAfter=6))
    s.add(ParagraphStyle(name="H1Custom", parent=s["Heading1"], fontSize=14, spaceBefore=14, spaceAfter=8, textColor=colors.HexColor("#1a1a1a")))
    s.add(ParagraphStyle(name="H2Custom", parent=s["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#333333")))
    s.add(ParagraphStyle(name="BodyCustom", parent=s["Normal"], fontSize=9, leading=12, spaceAfter=6))
    s.add(ParagraphStyle(name="Note", parent=s["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#555555"), spaceAfter=6))
    s.add(ParagraphStyle(name="Cell", parent=s["Normal"], fontSize=8, leading=10))
    return s


def _fmt_money(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v, digits: int = 0) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        return f"{float(v):.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v, digits: int = 0) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _table(data: list[list], col_widths=None, header=True) -> Table:
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f7f9fc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f7f9fc"), colors.white]),
        ]
    t.setStyle(TableStyle(style))
    return t


def build_pdf(path: Path = OUT_PATH) -> Path:
    styles = _styles()
    summer = BASE / "analysis" / "summer"
    pricing = BASE / "pricing"
    bench = BASE / "benchmark"

    kpis = pd.read_csv(summer / "summer_year_month_kpis.csv")
    ww = pd.read_csv(summer / "summer_weekday_weekend.csv")
    pace = pd.read_csv(summer / "summer_pace_asof.csv")
    channel = pd.read_csv(summer / "summer_channel.csv")
    room = pd.read_csv(summer / "summer_room_type.csv")
    matrix = pd.read_csv(pricing / "pricing_matrix_summer_jul_sep_labeled.csv")
    market = pd.read_csv(bench / "market_summer_compare.csv") if (bench / "market_summer_compare.csv").exists() else pd.DataFrame()
    fut_mon = pd.read_csv(bench / "market_future_rates_by_month.csv") if (bench / "market_future_rates_by_month.csv").exists() else pd.DataFrame()

    story = []
    story.append(Spacer(1, 0.6 * inch))
    story.append(Paragraph("Adventure Inn Durango", styles["CoverTitle"]))
    story.append(Paragraph("Summer Performance & Rate Review", styles["CoverSub"]))
    story.append(Paragraph(f"Prepared {date.today().isoformat()} · As-of pace date 2026-07-24", styles["CoverSub"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(
        Paragraph(
            "Property-first analysis. 2025 Jul–Sep is the rate anchor (post-renovation). "
            "AirDNA STR comps are market context only and do not override property ADR/occupancy.",
            styles["Note"],
        )
    )

    # Context
    story.append(Paragraph("1. Property context", styles["H1Custom"]))
    story.append(
        Paragraph(
            "• PMS: unknown export (not Cloudbeds)<br/>"
            "• Ownership: Aug 2024 · Renovation Nov 2024–May 2025 · Capacity 25→27 rooms ~Apr 2026<br/>"
            "• Client concern: revenue / occ / ADR down after tool-driven pricing that ignored short booking windows<br/>"
            "• Weekend definition: Fri–Sat · Thu = Sun on recommended matrix (policy)",
            styles["BodyCustom"],
        )
    )

    # KPIs
    story.append(Paragraph("2. Summer KPIs (stay-month, not combined)", styles["H1Custom"]))
    kpi_rows = [["Year", "Month", "ADR", "Occ %", "Revenue", "Room-nights", "Median lead", "RevPAR"]]
    for _, r in kpis[kpis["year"].isin([2025, 2026])].sort_values(["year", "month"]).iterrows():
        kpi_rows.append([
            str(int(r["year"])),
            str(r.get("month_name", r["month"])),
            _fmt_money(r.get("adr")),
            _fmt_pct(r.get("occupancy_pct")),
            _fmt_money(r.get("revenue")),
            _fmt_num(r.get("room_nights")),
            _fmt_num(r.get("median_lead"), 0),
            _fmt_money(r.get("revpar")),
        ])
    story.append(_table(kpi_rows, col_widths=[0.6*inch, 0.9*inch, 0.7*inch, 0.7*inch, 0.95*inch, 0.9*inch, 0.85*inch, 0.7*inch]))
    story.append(
        Paragraph(
            "Note: Aug/Sep 2026 occupancy and Sep ADR are on-books / incomplete, not final month results.",
            styles["Note"],
        )
    )
    story.append(
        Paragraph(
            "<b>Read:</b> 2025 summer held ~$173 ADR at 90–95% occ. 2026 Jul is mildly soft on rate ($165) and softer on occ (73%). "
            "2026 Aug on-books ADR (~$143) is the main positioning miss versus a historically late-booking, high-ADR August.",
            styles["BodyCustom"],
        )
    )

    # Weekday / weekend
    story.append(Paragraph("3. Weekday vs weekend (Fri–Sat)", styles["H1Custom"]))
    ww_rows = [["Year", "Month", "Day group", "ADR", "Median lead", "RN share"]]
    for _, r in ww[ww["year"].isin([2025, 2026])].sort_values(["year", "month", "day_group"]).iterrows():
        ww_rows.append([
            str(int(r["year"])),
            str(r.get("month_name", r["month"])),
            str(r["day_group"]).replace("weekend_fri_sat", "Weekend (Fri–Sat)").replace("weekday", "Weekday"),
            _fmt_money(r.get("adr")),
            _fmt_num(r.get("median_lead_days"), 0) + "d",
            _fmt_pct(r.get("rn_share_pct")),
        ])
    story.append(_table(ww_rows, col_widths=[0.6*inch, 0.9*inch, 1.6*inch, 0.8*inch, 1.0*inch, 0.9*inch]))
    story.append(
        Paragraph(
            "2025 August weekend premium was ~+28% vs MTW (Fri–Sat ~$197 vs weekday ~$162) with median leads of only ~6–8 days. "
            "Short booking windows are structural — not a reason to clear rates early.",
            styles["BodyCustom"],
        )
    )

    # Pace
    story.append(Paragraph("4. Pace as-of 2026-07-24", styles["H1Custom"]))
    pace_rows = [["Stay year", "Month", "On-books RN", "On-books ADR", "vs LY on-books"]]
    for _, r in pace[pace["stay_year"].isin([2025, 2026])].sort_values(["stay_year", "month"]).iterrows():
        pace_rows.append([
            str(int(r["stay_year"])),
            str(r.get("month_name", r["month"])),
            _fmt_num(r.get("onbooks_room_nights")),
            _fmt_money(r.get("onbooks_adr")),
            _fmt_pct(r.get("onbooks_vs_ly_onbooks_pct")),
        ])
    story.append(_table(pace_rows, col_widths=[1.0*inch, 1.1*inch, 1.2*inch, 1.2*inch, 1.3*inch]))
    story.append(
        Paragraph(
            "Aug 2026 is at ~72% of LY on-books RN with soft ADR. Thin pace alone is expected for August; thin pace + soft ADR is the risk.",
            styles["BodyCustom"],
        )
    )

    # Channel
    story.append(Paragraph("5. Channel mix (Jul–Sep combined by year)", styles["H1Custom"]))
    ch = channel[channel["year"].isin([2025, 2026])].copy()
    ch_agg = (
        ch.groupby(["year", "channel"], as_index=False)
        .agg(revenue=("revenue", "sum"), room_nights=("room_nights", "sum"))
    )
    ch_agg["adr"] = ch_agg["revenue"] / ch_agg["room_nights"]
    tot = ch_agg.groupby("year")["revenue"].transform("sum")
    ch_agg["share"] = 100 * ch_agg["revenue"] / tot
    ch_rows = [["Year", "Channel", "Rev share", "ADR", "Revenue"]]
    for y in [2025, 2026]:
        sub = ch_agg[ch_agg["year"] == y].sort_values("revenue", ascending=False).head(6)
        for _, r in sub.iterrows():
            ch_rows.append([
                str(int(r["year"])),
                str(r["channel"])[:22],
                _fmt_pct(r["share"]),
                _fmt_money(r["adr"]),
                _fmt_money(r["revenue"]),
            ])
    story.append(_table(ch_rows, col_widths=[0.7*inch, 1.8*inch, 0.9*inch, 0.8*inch, 1.1*inch]))
    story.append(
        Paragraph(
            "Expedia share fell (~49% → ~33%) while Direct rose (~14% → ~25%). Channel insight is separate from rate-band recommendations.",
            styles["Note"],
        )
    )

    story.append(PageBreak())

    # Market
    story.append(Paragraph("6. AirDNA market context (secondary)", styles["H1Custom"]))
    story.append(
        Paragraph(
            "Filter set: 0–1 BR, 1–2 bath, 1–4 guests, entire/private, economy–upscale. STR comps — not a hotel ADR target.",
            styles["Note"],
        )
    )
    if not market.empty:
        mkt_rows = [["Year", "Month", "Mkt occ", "Mkt ADR", "Prop occ", "Prop ADR", "Occ gap", "ADR gap"]]
        sub = market[(market["year"] == 2025) & (market["month"].isin([7, 8, 9]))]
        for _, r in sub.sort_values("month").iterrows():
            mkt_rows.append([
                "2025",
                str(r.get("month_name", r["month"])),
                _fmt_pct(r.get("occupancy_pct")),
                _fmt_money(r.get("adr")),
                _fmt_pct(r.get("property_occupancy_pct")),
                _fmt_money(r.get("property_adr")),
                f"{float(r['occ_gap_ppt']):+.1f} ppt" if pd.notna(r.get("occ_gap_ppt")) else "—",
                (f"${float(r['adr_gap']):+.0f}" if pd.notna(r.get("adr_gap")) else "—"),
            ])
        story.append(_table(mkt_rows, col_widths=[0.55*inch, 0.8*inch, 0.7*inch, 0.7*inch, 0.75*inch, 0.75*inch, 0.85*inch, 0.75*inch]))
        story.append(
            Paragraph(
                "2025 property ran ~15–17 ppt above this STR set on occupancy with ADR roughly in line — softness looks more property/pricing than market collapse.",
                styles["BodyCustom"],
            )
        )

    if not fut_mon.empty:
        story.append(Paragraph("Forward market rates (next ~180 days)", styles["H2Custom"]))
        f_rows = [["Month", "Avg booked listings", "Avg ADR", "Min", "Max"]]
        for _, r in fut_mon.sort_values("month").iterrows():
            f_rows.append([
                str(r.get("month_name", r["month"])),
                _fmt_num(r.get("avg_booked"), 0),
                _fmt_money(r.get("avg_adr")),
                _fmt_money(r.get("min_adr")),
                _fmt_money(r.get("max_adr")),
            ])
        story.append(_table(f_rows, col_widths=[1.2*inch, 1.5*inch, 1.0*inch, 0.9*inch, 0.9*inch]))
        story.append(
            Paragraph(
                "Near-term pickup: ~31% of next-30-day on-books arrived in the last 30 days; only ~42% were booked >60 days out. "
                "August market Fri–Sat forward ADR is already ~$185–197.",
                styles["BodyCustom"],
            )
        )

    # Rate matrix
    story.append(Paragraph("7. Recommended rate matrix (Jul–Sep)", styles["H1Custom"]))
    story.append(
        Paragraph(
            "Base = Standard Queen midpoints of agreed bands. Room offsets from 2025 summer ADR vs Queen "
            "(Small −$20, King +$7, Double Queen +$18, Kitchen +$10). Thu = Sun by policy.",
            styles["Note"],
        )
    )
    mat_rows = [["Room", "Month", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
    for _, r in matrix.sort_values(["month_index", "unit_id"]).iterrows():
        mat_rows.append([
            str(r["unit_id"])[:22],
            str(r["month_name"])[:3],
            _fmt_num(r["Monday"]),
            _fmt_num(r["Tuesday"]),
            _fmt_num(r["Wednesday"]),
            _fmt_num(r["Thursday"]),
            _fmt_num(r["Friday"]),
            _fmt_num(r["Saturday"]),
            _fmt_num(r["Sunday"]),
        ])
    story.append(_table(mat_rows, col_widths=[1.55*inch, 0.55*inch, 0.5*inch, 0.5*inch, 0.5*inch, 0.5*inch, 0.5*inch, 0.5*inch, 0.5*inch]))

    # Room ladder reference
    story.append(Paragraph("8. Appendix — 2025 room-type ADR ladder", styles["H1Custom"]))
    rt = room[room["year"] == 2025].sort_values(["month", "adr"], ascending=[True, False])
    rt_rows = [["Month", "Room type", "ADR", "Room-nights"]]
    for _, r in rt.iterrows():
        rt_rows.append([
            str(r.get("month_name", r["month"])),
            str(r["room_type"]),
            _fmt_money(r.get("adr")),
            _fmt_num(r.get("room_nights")),
        ])
    story.append(_table(rt_rows, col_widths=[1.0*inch, 2.4*inch, 1.0*inch, 1.2*inch]))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="Adventure Inn Durango Summer Review",
        author="Onboarding EDA",
    )
    doc.build(story)
    return path


def main() -> None:
    path = build_pdf()
    # Also copy to Downloads for easy access
    downloads = Path.home() / "Downloads" / path.name
    downloads.write_bytes(path.read_bytes())
    print(f"Wrote PDF: {path}")
    print(f"Copied to: {downloads}")


if __name__ == "__main__":
    main()
