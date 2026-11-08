"""PAPER10 final report compiler (READ-ONLY on the live paper home).

Compiles the approved 10-session paper-trading test into
reports/vwap_fade_paper10_report.md (+ copy in download/reports/):
  * per-session table from reports/paper10/session_*.json (or derived live)
  * executable performance: trades, win%, avg win/loss, day P&L distribution
  * cost tally (commissions+slippage), halt/cap events, overlay paths
  * missed-session coverage vs the 10-session target

It NEVER writes to the paper-trading home and never touches the production
Tradovate/NT8 environment. Env overrides for testing: P10_HOME, P10_OUT.
No performance claims beyond what the live journal itself records.
"""
import glob
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = "/home/z/my-project"
HOME = os.environ.get("P10_HOME", f"{ROOT}/download/paper_trading")
OUT = os.environ.get("P10_OUT", f"{ROOT}/reports")
START = date(2026, 11, 5)
TARGET = 10
TARGET_USD = 3000.0


def sessions_expected(upto):
    out, d = [], START
    while d <= upto:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def load_all():
    diary = open(f"{HOME}/log.md").read() if os.path.exists(f"{HOME}/log.md") else ""
    marked = sorted(set(re.findall(r"--- live run (\d{4}-\d{2}-\d{2})T", diary)))
    rows = []
    p = f"{HOME}/trades.csv"
    if os.path.exists(p):
        lines = [l for l in open(p).read().splitlines() if l.strip()]
        if len(lines) > 1:
            header = lines[0].split(",")
            for l in lines[1:]:
                parts = l.split(",")
                if len(parts) == len(header):
                    d = dict(zip(header, parts))
                    if d.get("mode") == "live":
                        rows.append(d)
    state = {}
    if os.path.exists(f"{HOME}/state.json"):
        with open(f"{HOME}/state.json") as fh:
            state = json.load(fh)
    return marked, rows, state, diary


def main():
    marked, rows, state, diary = load_all()
    today_d = datetime.now(ET).date()
    approved = sessions_expected(min(today_d, date(2026, 11, 18)))
    missed = [d for d in approved if d not in marked]
    done_sessions = [d for d in approved if d in marked]
    coverage = f"{len(done_sessions)}/{TARGET}"

    nets = [float(r["net_usd"]) for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    win_pct = (len(wins) / len(nets) * 100) if nets else 0.0
    costs = sum(float(r.get("costs_usd", 0)) for r in rows)
    cum = float(state.get("cum", sum(nets)))

    day_pnl = {}
    for r in rows:
        d = r["trade_id"].split("_")[0]
        day_pnl[d] = day_pnl.get(d, 0.0) + float(r["net_usd"])
    worst = min(day_pnl.values()) if day_pnl else 0.0
    best = max(day_pnl.values()) if day_pnl else 0.0

    reasons = {}
    for r in rows:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    fills = {}
    for r in rows:
        fills[r.get("fill_method", "?")] = fills.get(r.get("fill_method", "?"), 0) + 1

    ov = state.get("overlays", {})
    halted_days = state.get("halted_date")
    capped_days = state.get("cap_blocked_date")
    halt_events = len(re.findall(r"\*\*DAILY STOP \d{4}-\d{2}-\d{2}", diary))
    cap_events = len(re.findall(r"\*\*DAILY PROFIT CAP \d{4}-\d{2}-\d{2}", diary))
    stale_notes = len(re.findall(r"stale/out-of-order bar", diary))

    L = []
    L.append("# VWAP Fade — 10-session paper-trading test report")
    L.append("")
    L.append(f"Generated {datetime.now(ET).isoformat(timespec='seconds')} from the live paper journal "
             f"(read-only). Approved window: Thu 2026-11-05 .. Wed 2026-11-18 (ET trading days).")
    L.append("")
    L.append(f"## Coverage: {coverage} sessions polled" + (f" — missed: {', '.join(missed)}" if missed else ""))
    L.append("")
    L.append("| date | polls | trades | wins | day P&L |")
    L.append("|---|---|---|---|---|")
    for d in approved:
        if d in marked:
            t = [r for r in rows if r["trade_id"].startswith(d)]
            dp = sum(float(r["net_usd"]) for r in t)
            w = sum(1 for r in t if float(r["net_usd"]) > 0)
            polls = len(re.findall(rf"--- live run {d}T", diary))
            L.append(f"| {d} | {polls} | {len(t)} | {w} | ${dp:+.2f} |")
        else:
            L.append(f"| {d} | 0 | — | — | MISSED |")
    L.append("")
    L.append("## Executable performance (live journal, observable-price fills)")
    L.append("")
    if rows:
        L.append(f"- closed trades: **{len(rows)}** over {len(done_sessions)} polled sessions")
        L.append(f"- win rate: **{win_pct:.1f}%** ({len(wins)}W/{len(losses)}L)")
        avg_w = sum(wins) / len(wins) if wins else 0.0
        avg_l = sum(losses) / len(losses) if losses else 0.0
        L.append(f"- avg win ${avg_w:+.2f} / avg loss ${avg_l:+.2f} "
                 f"(payoff {abs(avg_w / avg_l):.2f}:1)" if losses else
                 f"- avg win ${avg_w:+.2f} (no losses yet)")
        L.append(f"- best day ${best:+.2f}, worst day ${worst:+.2f} "
                 f"(daily stop fires at -$400; profit cap at +$1,650 Topstep)")
        L.append(f"- exit reasons: {reasons}")
        L.append(f"- fill methods: {fills} (Yahoo fills are delayed/indicative last-trade prices)")
    else:
        L.append("- no closed trades recorded yet")
    L.append(f"- costs paid (commission+slippage): **${costs:.2f}**")
    L.append(f"- cumulative P&L: **${cum:+.2f}** ({cum / TARGET_USD * 100:+.1f}% of the $3,000 Topstep target)")
    L.append("")
    L.append("## Bot layer / overlays")
    L.append("")
    L.append(f"- daily-loss-stop events: {halt_events} (state halt day: {halted_days})")
    L.append(f"- daily-profit-cap events: {cap_events} (state cap day: {capped_days})")
    L.append(f"- stale/out-of-order bar rejections: {stale_notes}")
    for k, o in ov.items():
        nm = "Topstep 50K" if k == "topstep" else "Apex 50K"
        if o.get("done"):
            L.append(f"- {nm}: **WOULD HAVE PASSED** on {o.get('passed_on')} "
                     f"(fees ${o.get('fees', 0):.0f}, attempts {o.get('attempts', 1)})")
        else:
            L.append(f"- {nm}: profit {cum - o.get('base_cum', 0):+.0f}/{TARGET_USD:.0f}, "
                     f"floor {o.get('floor', 0):+.0f}, tdays {o.get('tdays', 0)}, "
                     f"best day ${o.get('best_day', 0):+.0f}, fees ${o.get('fees', 0):.0f}, "
                     f"attempts {o.get('attempts', 1)}")
    L.append("")
    L.append("## Caveats")
    L.append("")
    L.append("- Engine v2.1 with the five post-audit fixes (N1-N5) applied before session 1.")
    L.append("- Strategy parameters frozen (VWAP Fade v1); no optimization was performed.")
    L.append("- Fills are delayed/indicative Yahoo last-trade prices with 0.5 tick/side slippage"
             " modeled in costs; `fill_method` tags preserve signal-vs-executable separation.")
    L.append("- This report is evidence for the engine's own behaviour only; production"
             " (Tradovate/NT8) was never contacted.")
    report = "\n".join(L) + "\n"

    os.makedirs(OUT, exist_ok=True)
    path = f"{OUT}/vwap_fade_paper10_report.md"
    with open(path, "w") as fh:
        fh.write(report)
    try:
        os.makedirs(f"{ROOT}/download/reports", exist_ok=True)
        with open(f"{ROOT}/download/reports/vwap_fade_paper10_report.md", "w") as fh:
            fh.write(report)
    except OSError:
        pass
    print(report)
    print(f"REPORT_STATUS: coverage={coverage} trades={len(rows)} win_pct={win_pct:.1f} "
          f"cum={cum:+.2f} costs={costs:.2f} -> {path}")


if __name__ == "__main__":
    main()
