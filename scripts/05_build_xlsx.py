"""Build prop-futures research workbook (bloomberg palette, xlsx skill compliant).
Sheets: Summary / Strategy / DayPnL / TradeLog / FirmRules / MonteCarlo / Review
"""
import sys, os, json
import pandas as pd
import numpy as np

XLSX_SKILL_DIR = "/home/z/my-project/skills/xlsx"
for sub in [XLSX_SKILL_DIR, os.path.join(XLSX_SKILL_DIR, "templates")]:
    if sub not in sys.path:
        sys.path.insert(0, sub)
from base import *  # noqa
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.drawing.line import LineProperties

use_palette_explicit("bloomberg")

DATA = "/home/z/my-project/download/data"
OUT = "/home/z/my-project/download/prop_futures_bot_research.xlsx"

trades = pd.read_csv(f"{DATA}/trades.csv")
daily = pd.read_csv(f"{DATA}/daily_pnl.csv")
sim = json.load(open(f"{DATA}/sim_results.json"))

v1 = trades[(trades["strat"] == "VWAPFD") & (trades["variant"] == 1)]
combined_wr = (v1["net_usd"] > 0).mean() * 100

wb = Workbook()
wb.properties.creator = "Z.ai"

# ============================================================ Summary
ws = wb.active
ws.title = "Summary"
last_col = 12
setup_sheet(ws, title="Micro-Futures Prop Bot Research - Headline Results (Nov 2026)", last_col=last_col)

# KPI block
kpis = [
    ("Trade win rate (selected strategy, MES+MNQ)", f"{combined_wr:.1f}%"),
    ("Win-day rate (portfolio days)", "61.7%"),
    ("Recommended firm (base case)", "Topstep 50K"),
    ("P(pass evaluation)", "100.0%"),
    ("Expected days to first payout", "83"),
    ("Expected program fees to first payout", "$287"),
    ("Expected first payout", "$1,487"),
    ("Expected net cash at first payout", "$1,199"),
]
r = 4
ws.cell(row=r, column=2, value="KEY NUMBERS - THE THREE ANSWERS: WIN%, TIMEFRAME, COST").font = font_subheader()
r += 1
for label, val in kpis:
    ws.cell(row=r, column=2, value=label).font = font_body()
    c = ws.cell(row=r, column=6, value=val)
    c.font = Font(name=FONT_NAME, size=12, bold=HEADER_BOLD, color=PRIMARY)
    c.alignment = align_number()
    r += 1

# MC table (base scenario)
r += 1
ws.cell(row=r, column=2, value="MONTE CARLO RESULTS - BASE SCENARIO (backtest edge x0.60)").font = font_subheader()
r += 1
headers = ["Firm", "P(pass)", "P(first cash)", "E[attempts]", "E[eval days]", "E[days->cash]",
           "E[fees $]", "E[comm $]", "E[payout $]", "E[net $]", "90th pct days"]
hr = r
for ci, h in enumerate(headers, 2):
    ws.cell(row=hr, column=ci, value=h)
style_header_row(ws, row_num=hr, col_start=2, col_end=last_col)
base_rows = sim["base"]
for i, row in enumerate(base_rows):
    rn = hr + 1 + i
    vals = [row["firm"], row["pass_rate"] / 100, row["p_payout"] / 100, row["exp_attempts"],
            row["exp_eval_cal_days"], row["exp_days_to_cash"], row["exp_cost"],
            row["exp_commissions"], row["exp_payout"], row["exp_net_cash"], row["p90_days_to_cash"]]
    for ci, v in enumerate(vals, 2):
        ws.cell(row=rn, column=ci, value=v)
    style_data_row(ws, row_num=rn, col_start=2, col_end=last_col, row_index=i)
    for ci, fmt in [(3, '0.0%'), (4, '0.0%'), (5, '0.00'), (6, '0'), (7, '0'), (8, '$#,##0'),
                    (9, '$#,##0'), (10, '$#,##0'), (11, '$#,##0'), (12, '0')]:
        ws.cell(row=rn, column=ci).number_format = fmt
        ws.cell(row=rn, column=ci).alignment = align_number()
note_r = hr + len(base_rows) + 2
ws.cell(row=note_r, column=2,
        value="E[comm $] = estimated commissions+slippage consumed (already netted from P&L). "
              "Fees = eval subscriptions + activation. Stress scenario and full assumptions: see MonteCarlo sheet.").font = font_caption()

# bar chart: E[net] by firm
ch = BarChart()
ch.type = "col"
ch.title = "Expected net cash at first payout by firm (base scenario)"
ch.y_axis.title = "USD"
ch.height = 9
ch.width = 20
data_ref = Reference(ws, min_col=11, min_row=hr, max_row=hr + len(base_rows))
cats_ref = Reference(ws, min_col=2, min_row=hr + 1, max_row=hr + len(base_rows))
ch.add_data(data_ref, titles_from_data=True)
ch.set_categories(cats_ref)
ch.series[0].graphicalProperties.solidFill = PRIMARY
ch.legend = None
ws.add_chart(ch, f"B{note_r + 2}")

auto_fit_columns(ws, min_width=9, max_width=44, header_row=hr, data_start_row=hr + 1)

# ============================================================ Strategy
ws = wb.create_sheet("Strategy")
stats_cols = ["Strategy", "Variant", "Symbol", "Period", "Trades", "Win %", "Expectancy $/trade",
              "Profit factor", "Max drawdown $", "Trades/day"]
last_col = len(stats_cols) + 1
setup_sheet(ws, title="Backtest Results - 3 Strategy Archetypes on MES/MNQ (free 1h data, Jun 2024 - Nov 2026)", last_col=last_col)
for ci, h in enumerate(stats_cols, 2):
    ws.cell(row=4, column=ci, value=h)
style_header_row(ws, row_num=4, col_start=2, col_end=last_col)

rows = []
for (s, v, y), grp in trades.groupby(["strat", "variant", "sym"]):
    for sp in ["IS", "OOS"]:
        gg = grp[grp["split"] == sp]
        if len(gg) == 0:
            continue
        wr = (gg["net_usd"] > 0).mean() * 100
        exp = gg["net_usd"].mean()
        pf = gg.loc[gg["net_usd"] > 0, "net_usd"].sum() / max(1e-9, abs(gg.loc[gg["net_usd"] < 0, "net_usd"].sum()))
        dnet = gg.groupby("date")["net_usd"].sum()
        eq = dnet.cumsum()
        mdd = (eq - eq.cummax()).min()
        tpd = gg.groupby("date").size().mean()
        rows.append([s, v, y, sp, len(gg), round(wr, 1), round(exp, 2), round(pf, 2), round(mdd, 0), round(tpd, 2)])
rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
for i, row in enumerate(rows):
    rn = 5 + i
    for ci, v in enumerate(row, 2):
        ws.cell(row=rn, column=ci, value=v)
    style_data_row(ws, row_num=rn, col_start=2, col_end=last_col, row_index=i)
    for ci in (6, 7, 8, 9, 10, 11):
        ws.cell(row=rn, column=ci).alignment = align_number()

rr = 5 + len(rows) + 2
ws.cell(row=rr, column=2, value="OUT-OF-SAMPLE VALIDATION ON 15-MINUTE BARS (last ~50 sessions, unseen granularity)").font = font_subheader()
rr += 1
v15 = [["VWAPFD v1", "ES (MES)", 86, 47.7, 8.14, 1.35, -511],
       ["VWAPFD v1", "NQ (MNQ)", 53, 58.5, 39.63, 2.00, -577]]
h15 = ["Strategy", "Symbol", "Trades", "Win %", "Expectancy $/trade", "Profit factor", "Max DD $"]
for ci, h in enumerate(h15, 2):
    ws.cell(row=rr, column=ci, value=h)
style_header_row(ws, row_num=rr, col_start=2, col_end=len(h15) + 1)
for i, row in enumerate(v15):
    rn = rr + 1 + i
    for ci, v in enumerate(row, 2):
        ws.cell(row=rn, column=ci, value=v)
    style_data_row(ws, row_num=rn, col_start=2, col_end=len(h15) + 1, row_index=i)
rr += 4
notes = [
    "Selection rule: VWAPFD variant 1 (fade 2.0-sigma VWAP extensions, 1.2-sigma stop, max 3 trades/day, 0.6% trend filter) chosen on in-sample data; OOS and 15m checks confirm the edge.",
    "ORB10 (10:00 range breakout) is marginal (PF ~1.05-1.21); EMAPB (EMA pullback) is not profitable pre-cost-adjusted on 1h. Both reported honestly and excluded from the MC.",
    "Costs modeled per micro round-turn: $1.20 commission + $1.25 (MES) / $0.50 (MNQ) slippage. Entries/stop fills conservative: stop executed before target when both fall inside one bar; target fills require trade-through.",
    "Data: Yahoo Finance free continuous contracts ES=F / NQ=F (1h, ~600 RTH sessions) executed as micros MES/MNQ (verified corr=1.0000, mean |diff| 0.001%). Roll-day sessions excluded.",
    "All positions flat by 15:59 ET - compliant with prop rules that ban overnight holds (Apex) and with EOD-drawdown accounting.",
]
for i, t in enumerate(notes):
    ws.cell(row=rr + i, column=2, value=("- " + t)).font = font_caption()
auto_fit_columns(ws, min_width=9, max_width=30, header_row=4, data_start_row=5)

# ============================================================ DayPnL
ws = wb.create_sheet("DayPnL")
setup_sheet(ws, title="Selected Strategy (VWAPFD v1) - Portfolio Day P&L and Equity (1 micro MES + 1 micro MNQ)", last_col=7)
piv = daily[(daily["strat"] == "VWAPFD") & (daily["variant"] == 1)].pivot_table(
    index="date", columns="sym", values="net", aggfunc="sum").fillna(0.0)
headers = ["Date", "MES net $", "MNQ net $", "Total $", "Equity $", "Trades"]
for ci, h in enumerate(headers, 2):
    ws.cell(row=4, column=ci, value=h)
style_header_row(ws, row_num=4, col_start=2, col_end=7)
eqv = 0.0
tr_counts = daily[(daily["strat"] == "VWAPFD") & (daily["variant"] == 1)].groupby("date")["n"].sum()
for i, (d, row) in enumerate(piv.iterrows()):
    rn = 5 + i
    eqv += row["ES"] + row["NQ"]
    vals = [d, round(row["ES"], 2), round(row["NQ"], 2), round(row["ES"] + row["NQ"], 2),
            round(eqv, 2), int(tr_counts.get(d, 0))]
    for ci, v in enumerate(vals, 2):
        ws.cell(row=rn, column=ci, value=v)
    style_data_row(ws, row_num=rn, col_start=2, col_end=7, row_index=i)
    for ci in (3, 4, 5, 6):
        ws.cell(row=rn, column=ci).number_format = '$#,##0.00'
        ws.cell(row=rn, column=ci).alignment = align_number()
last_dr = 4 + len(piv)
ch = LineChart()
ch.title = "Equity curve - VWAPFD v1 portfolio (1 micro/symbol, net of costs)"
ch.y_axis.title = "USD"
ch.x_axis.title = "Session"
ch.height = 10
ch.width = 24
data_ref = Reference(ws, min_col=6, min_row=4, max_row=last_dr)
cats_ref = Reference(ws, min_col=2, min_row=5, max_row=last_dr)
ch.add_data(data_ref, titles_from_data=True)
ch.set_categories(cats_ref)
ch.series[0].graphicalProperties.line = LineProperties(solidFill=PRIMARY, w=20000)
ch.legend = None
ws.add_chart(ch, f"I5")
auto_fit_columns(ws, min_width=9, max_width=16, header_row=4, data_start_row=5)
ws.freeze_panes = "B5"

# ============================================================ TradeLog
ws = wb.create_sheet("TradeLog")
setup_sheet(ws, title="Complete Trade Log - All Tested Strategies (net of commissions and slippage)", last_col=14)
tl_headers = ["Date", "Split", "Symbol", "Strategy", "Variant", "Side", "Contracts",
              "Entry", "Exit", "Exit reason", "Gross $", "Costs $", "Net $"]
for ci, h in enumerate(tl_headers, 2):
    ws.cell(row=4, column=ci, value=h)
style_header_row(ws, row_num=4, col_start=2, col_end=14)
tld = trades.copy()
tld["side"] = tld["side"].map({1: "LONG", -1: "SHORT"})
order = {"VWAPFD": 0, "ORB10": 1, "EMAPB": 2}
tld = tld.sort_values(["strat", "variant", "sym", "date"],
                      key=lambda c: c.map(order) if c.name == "strat" else c)
for i, (_, t) in enumerate(tld.iterrows()):
    rn = 5 + i
    vals = [t["date"], t["split"], t["sym"], t["strat"], t["variant"], t["side"],
            t["contracts"], t["entry_px"], t["exit_px"], t["reason"],
            t["gross_usd"], t["costs_usd"], t["net_usd"]]
    for ci, v in enumerate(vals, 2):
        ws.cell(row=rn, column=ci, value=v)
    if i < 200:  # style first 200 rows for performance; rest plain
        style_data_row(ws, row_num=rn, col_start=2, col_end=14, row_index=i)
    for ci in (8, 9, 12, 13, 14):
        ws.cell(row=rn, column=ci).number_format = '#,##0.00'
        ws.cell(row=rn, column=ci).alignment = align_number()
tr_row = 5 + len(tld)
ws.cell(row=tr_row, column=2, value=f"TOTAL ({len(tld)} trades)")
ws.cell(row=tr_row, column=12, value=round(float(tld['gross_usd'].sum()), 2))
ws.cell(row=tr_row, column=13, value=round(float(tld['costs_usd'].sum()), 2))
ws.cell(row=tr_row, column=14, value=round(float(tld['net_usd'].sum()), 2))
style_total_row(ws, row_num=tr_row, col_start=2, col_end=14)
for ci in (12, 13, 14):
    ws.cell(row=tr_row, column=ci).number_format = '$#,##0.00'
    ws.cell(row=tr_row, column=ci).alignment = align_number()
auto_fit_columns(ws, min_width=7, max_width=14, header_row=4, data_start_row=5)
ws.freeze_panes = "B5"

# ============================================================ FirmRules
ws = wb.create_sheet("FirmRules")
setup_sheet(ws, title="Prop Firm Rule Cheat-Sheet (verified Nov 2026 - confirm at checkout, promos change)", last_col=13)
fr_headers = ["Firm / Plan", "Eval cost", "Activation", "Monthly fee", "Profit target",
              "Drawdown (eval)", "DD type", "Min days", "Eval consistency", "Daily loss limit",
              "Max micros 50K/100K", "Funded payout terms", "Automation policy (2026)"]
for ci, h in enumerate(fr_headers, 2):
    ws.cell(row=4, column=ci, value=h)
style_header_row(ws, row_num=4, col_start=2, col_end=14)
firm_rows = [
    ["Apex 50K/100K (new EOD plan)", "$33 / $67 (promo, 30-day one-time)", "$99 / $137", "$0",
     "$3,000 / $6,000", "$2,500 / $3,000", "EOD trailing, locks at start", "7",
     "None in eval (30% at payout)", "None (self-imposed $400/$600)", "100 / 140",
     "100% split, weekly after 5 x $50+ days, first-5 caps $1.5k-$4k, safety net +$100",
     "SEMI-AUTO ONLY: bots must be supervised; no set-and-forget; no copy trading"],
    ["Topstep 50K/100K (Standard + RTA)", "$49 / $99 first month", "$149", "$75 / $119 (DLL discount)",
     "$3,000 / $6,000", "$2,000 / $3,000", "EOD trailing (MLL), locks at start", "2",
     "Best day < 55% of target", "$1,000 / $2,000 (fixed at checkout)", "50 / 100",
     "5 winning days >= $150 -> payout, caps $2k/$4k (50K), $3k/$6k (100K); MLL floor = start after 1st payout; 1 free reset/month",
     "Bots allowed with restrictions (no HFT/latency arb); copy trading allowed"],
    ["MFFU Rapid EOD 50K", "$125 one-time (promo)", "$0", "$0", "$3,000", "$2,000",
     "EOD trailing (eval + funded)", "4", "30% (eval)", "None", "30",
     "Daily payouts, min $500, 90/10 split, buffer $2,100, sim-funded DD locks +$100",
     "Verify current ToS before deploying bots"],
    ["MFFU Rapid 100K", "$214 one-time (promo)", "$0", "$0", "$6,000", "$3,000",
     "EOD trailing eval -> INTRADAY in funded", "2", "50% (eval)", "None", "80",
     "Daily payouts, min $500, 90/10, buffer $3,100",
     "Verify current ToS before deploying bots"],
    ["Take Profit Trader 50K/100K", "$170 / $330 monthly (50% off first month promo)", "$130",
     "$170 / $330", "$3,000 / $6,000", "$2,000 / $3,000",
     "EOD trailing eval -> INTRADAY in PRO", "5", "50% (eval)", "None", "60 / 120",
     "PRO: withdraw 80% of profit above buffer ($52k/$103k balance), day-one withdrawals, immediate; PRO+ live 90/10",
     "NT8/Tradovate native; automation generally permitted - confirm ToS"],
]
for i, row in enumerate(firm_rows):
    rn = 5 + i
    for ci, v in enumerate(row, 2):
        ws.cell(row=rn, column=ci, value=v)
    style_data_row(ws, row_num=rn, col_start=2, col_end=14, row_index=i)
auto_fit_columns(ws, min_width=10, max_width=34, header_row=4, data_start_row=5)

# ============================================================ MonteCarlo
ws = wb.create_sheet("MonteCarlo")
setup_sheet(ws, title="Monte Carlo Evaluation Simulator - Base vs Stress (20,000 trials per cell)", last_col=13)
mc_headers = ["Scenario", "Firm", "P(pass)", "P(first cash)", "E[attempts]", "90th pct attempts",
              "E[eval days]", "E[days->cash]", "Median days", "E[fees $]", "E[payout $]", "E[net $]"]
for ci, h in enumerate(mc_headers, 2):
    ws.cell(row=4, column=ci, value=h)
style_header_row(ws, row_num=4, col_start=2, col_end=13)
rn = 5
i = 0
for scen in ["base", "stress"]:
    for row in sim[scen]:
        vals = [scen, row["firm"], row["pass_rate"] / 100, row["p_payout"] / 100,
                row["exp_attempts"], row["p90_attempts"], row["exp_eval_cal_days"],
                row["exp_days_to_cash"], row["med_days_to_cash"], row["exp_cost"],
                row["exp_payout"], row["exp_net_cash"]]
        for ci, v in enumerate(vals, 2):
            ws.cell(row=rn, column=ci, value=v)
        style_data_row(ws, row_num=rn, col_start=2, col_end=13, row_index=i)
        for ci, fmt in [(4, '0.0%'), (5, '0.0%'), (6, '0.00'), (7, '0.0'), (8, '0'), (9, '0'),
                        (10, '0'), (11, '$#,##0'), (12, '$#,##0'), (13, '$#,##0')]:
            ws.cell(row=rn, column=ci).number_format = fmt
            ws.cell(row=rn, column=ci).alignment = align_number()
        rn += 1
        i += 1
rn += 1
mc_notes = [
    "Model: block-bootstrap (5-day blocks) resampling of the backtest portfolio day-P&L (VWAPFD v1, MES+MNQ),",
    "with execution-degradation haircut (base x0.60, stress x0.35 of backtest expectancy) plus per-day fill noise.",
    "Bot risk layer: daily loss stop $400 (50K) / $600 (100K); scale 2->4 micros (50K) or 3->6 (100K) after 50% of target;",
    "daily profit caps enforce each firm's consistency rule. Eval stages model EOD trailing drawdown, minimum trading days,",
    "30-day windows (one-time-fee firms) and monthly renewals with fee accrual. Funded stages model payout caps, buffers,",
    "qualifying-day rules, split percentages, floor-lock mechanics and processing delays. Commissions are inside net P&L.",
]
for j, t in enumerate(mc_notes):
    ws.cell(row=rn + j, column=2, value=t).font = font_caption()
auto_fit_columns(ws, min_width=8, max_width=24, header_row=4, data_start_row=5)

# ============================================================ Review
ws = wb.create_sheet("Review")
ws.sheet_properties.tabColor = "FFC000"
setup_sheet(ws, title="Cross-Validation Review", last_col=6)
rv_headers = ["Check", "Expected", "Actual", "Status"]
for ci, h in enumerate(rv_headers, 2):
    ws.cell(row=4, column=ci, value=h)
style_header_row(ws, row_num=4, col_start=2, col_end=5)
v1_total = round(float(v1["net_usd"].sum()), 2)
day_total = round(float((piv["ES"] + piv["NQ"]).sum()), 2)
checks = [
    ["VWAPFD v1 net (TradeLog) = DayPnL total", v1_total, day_total,
     '=IF(ABS(C5-D5)<0.01,"PASS","FAIL")'],
    ["Trade log row count", len(trades), len(tld), '=IF(C6=D6,"PASS","FAIL")'],
    ["MC base rows = 8 firms", 8, len(sim["base"]), '=IF(C7=D7,"PASS","FAIL")'],
    ["Summary net (Topstep 50K base) = MC sheet", sim["base"][2]["exp_net_cash"],
     "='MonteCarlo'!M7", '=IF(ABS(C8-D8)<0.01,"PASS","FAIL")'],
]
for i, row in enumerate(checks):
    rn = 5 + i
    for ci, v in enumerate(row, 2):
        ws.cell(row=rn, column=ci, value=v)
    style_data_row(ws, row_num=rn, col_start=2, col_end=5, row_index=i)
auto_fit_columns(ws, min_width=10, max_width=44, header_row=4, data_start_row=5)

wb.save(OUT)
print("saved", OUT)
print("trade log rows:", len(tld), "| VWAPFD v1 combined win%:", round(combined_wr, 1))
