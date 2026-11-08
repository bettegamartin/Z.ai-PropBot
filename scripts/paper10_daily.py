"""PAPER10 daily session summarizer (READ-ONLY on the live paper home).

Part of the approved 10-session paper-trading test (sessions = ET trading
days, Thu 2026-11-05 .. Wed 2026-11-18).

For one session day (default: today ET) this script:
  * counts the live-run markers in the diary (ground truth: did the engine poll?)
  * extracts that day's closed trades from the live journal (trade_id prefix)
  * reads the DAY SUMMARY block + state.json for day P&L / halts / overlays
  * writes reports/paper10/session_<date>.json and refreshes the day's row in
    reports/paper10_sessions.md (idempotent; safe to re-run)
  * prints a compact summary block for the scheduler agent to relay

It NEVER writes to the paper-trading home (download/paper_trading/) and never
touches the production Tradovate/NT8 environment. Env overrides for testing:
P10_HOME (source home), P10_OUT (output dir).
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = "/home/z/my-project"
HOME = os.environ.get("P10_HOME", f"{ROOT}/download/paper_trading")
OUT = os.environ.get("P10_OUT", f"{ROOT}/reports")
START = date(2026, 11, 5)          # first approved session
TARGET_SESSIONS = 10

OVERLAY_NAMES = {"topstep": "Topstep", "apex": "Apex"}


def sessions_expected(upto):
    """Approved session dates from START up to and including `upto` (weekdays)."""
    out, d = [], START
    while d <= upto:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def read_diary():
    p = f"{HOME}/log.md"
    return open(p).read() if os.path.exists(p) else ""


def journal_rows():
    p = f"{HOME}/trades.csv"
    if not os.path.exists(p):
        return []
    lines = [l for l in open(p).read().splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    header = lines[0].split(",")
    rows = []
    for l in lines[1:]:
        parts = l.split(",")
        if len(parts) == len(header):
            rows.append(dict(zip(header, parts)))
    return [r for r in rows if r.get("mode") == "live"]


def summarize(day_iso):
    diary = read_diary()
    polls = len(re.findall(rf"--- live run {day_iso}T", diary))
    state = {}
    if os.path.exists(f"{HOME}/state.json"):
        with open(f"{HOME}/state.json") as fh:
            state = json.load(fh)
    rows = [r for r in journal_rows() if r.get("trade_id", "").startswith(day_iso)]
    day_pnl = sum(float(r["net_usd"]) for r in rows if r.get("net_usd"))
    wins = sum(1 for r in rows if float(r.get("net_usd", 0)) > 0)
    costs = sum(float(r.get("costs_usd", 0)) for r in rows)
    # DAY SUMMARY block for the day
    m = re.search(rf"\*\*DAY SUMMARY {day_iso}\*\* — (.*)", diary)
    summary_line = m.group(1) if m else None
    halted = state.get("halted_date") == day_iso
    capped = state.get("cap_blocked_date") == day_iso
    ov = state.get("overlays", {})
    ov_desc = "; ".join(
        f"{OVERLAY_NAMES.get(k, k)} floor {o.get('floor', 0):+.0f} "
        f"tdays {o.get('tdays', 0)} best ${o.get('best_day', 0.0):+.0f}"
        + (" PASSED" if o.get("done") else "")
        for k, o in ov.items())
    return dict(date=day_iso, polls=polls, trades=len(rows),
                by_sym={s: sum(1 for r in rows if r["sym"] == s) for s in ("ES", "NQ")},
                wins=wins, day_pnl=round(day_pnl, 2), costs=round(costs, 2),
                cum=round(float(state.get("cum", 0.0)), 2),
                n_trades_total=state.get("n_trades", 0),
                trading_days=state.get("trading_days", 0),
                halted=halted, capped=capped,
                feed_status=state.get("feed_status", {}),
                overlays=ov_desc, summary_line=summary_line,
                rows=[{k: r.get(k, "") for k in ("trade_id", "sym", "side", "contracts",
                                                 "entry_px", "exit_px", "reason",
                                                 "fill_method", "net_usd", "cum_eq")} for r in rows])


def missed_sessions(today_d):
    """Approved session dates with no engine polls in the diary."""
    diary = read_diary()
    marked = set(re.findall(r"--- live run (\d{4}-\d{2}-\d{2})T", diary))
    return [d for d in sessions_expected(today_d) if d not in marked]


def fmt_report(s, idx, missed):
    wd = datetime.fromisoformat(s["date"] + "T12:00:00").strftime("%a")
    lines = [f"PAPER10 SESSION {idx}/{TARGET_SESSIONS} — {wd} {s['date']}"]
    if s["polls"] == 0:
        lines.append(f"  NO ENGINE POLLS RECORDED for this session — MISSED?"
                     + (f" (missed so far: {', '.join(missed)})" if missed else ""))
        lines.append(f"  journal totals so far: {s['n_trades_total']} trades, "
                     f"cum ${s['cum']:+.2f}, {s['trading_days']} trading days")
        return "\n".join(lines)
    lines.append(f"  polls: {s['polls']} | trades: {s['trades']} "
                 f"(ES {s['by_sym']['ES']}, NQ {s['by_sym']['NQ']}) | "
                 f"day P&L: ${s['day_pnl']:+.2f} | cum: ${s['cum']:+.2f} "
                 f"({s['cum'] / 3000 * 100:+.0f}% of $3,000 target)")
    lines.append(f"  wins {s['wins']}/{s['trades']} | costs today ${s['costs']:.2f} | "
                 f"halt: {'YES' if s['halted'] else 'no'} | profit-cap: {'YES' if s['capped'] else 'no'}")
    lines.append(f"  feeds: {s['feed_status'] or 'n/a'}")
    lines.append(f"  overlays: {s['overlays']}")
    if s["rows"]:
        for r in s["rows"]:
            lines.append(f"    {r['trade_id']} {r['sym']} {r['side']} x{r['contracts']} "
                         f"{r['entry_px']}->{r['exit_px']} ({r['reason']}, {r['fill_method']}) "
                         f"net ${float(r['net_usd']):+.2f} cum ${r['cum_eq']}")
    if missed:
        lines.append(f"  NOTE missed sessions so far: {', '.join(missed)}")
    return "\n".join(lines)


def write_files(s, idx, missed):
    os.makedirs(f"{OUT}/paper10", exist_ok=True)
    with open(f"{OUT}/paper10/session_{s['date']}.json", "w") as fh:
        json.dump({"session_index": idx, "target_sessions": TARGET_SESSIONS,
                   "missed_so_far": missed, **s}, fh, indent=1, default=str)
    # refresh the markdown ledger: one line per session (deduped by date)
    ledger = f"{OUT}/paper10_sessions.md"
    done = {}
    if os.path.exists(ledger):
        for l in open(ledger).read().splitlines():
            p = l.split("|")
            if len(p) > 4 and re.match(r"\s*\d{4}-\d{2}-\d{2}", p[1]):
                done[p[1].strip()] = l
    if s["polls"] > 0:
        wd = datetime.fromisoformat(s["date"] + "T12:00:00").strftime("%a")
        row = (f"| {s['date']} | {wd} | {s['polls']} | {s['trades']} | {s['wins']}/"
               f"{max(s['trades'], 1)} | ${s['day_pnl']:+.2f} | ${s['cum']:+.2f} | "
               f"{'HALT' if s['halted'] else ('CAP' if s['capped'] else '-')} | "
               f"{s['overlays']} |")
        done[s["date"]] = row
    head_default = ("# Paper10 session ledger (VWAP Fade, live paper journal)\n\n"
                    "| date | day | polls | trades | wins | day P&L | cum | flags | overlays |\n"
                    "|---|---|---|---|---|---|---|---|---|")
    lines = open(ledger).read().splitlines() if os.path.exists(ledger) else []
    head = [l for l in lines if not re.match(r"\s*\|?\s*\d{4}-\d{2}-\d{2}", l) and l.strip()]
    if not head:
        head = head_default.splitlines()
    with open(ledger, "w") as fh:
        fh.write("\n".join(head + [done[d] for d in sorted(done)]) + "\n")


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).date().isoformat()
    today_d = datetime.now(ET).date()
    if len(sys.argv) > 1:
        today_d = date.fromisoformat(day)
    s = summarize(day)
    approved = sessions_expected(today_d)
    idx = approved.index(day) + 1 if day in approved else len(approved) + 1
    missed = missed_sessions(min(today_d, date.fromisoformat(day)) if day <= today_d.isoformat()
                             else today_d)
    missed = [d for d in missed if d != day or s["polls"] == 0]
    print(fmt_report(s, idx, missed))
    if s["polls"] > 0:
        write_files(s, idx, missed)
    # machine-readable tail for the relaying agent
    print(f"SESSION_STATUS: date={day} index={idx}/{TARGET_SESSIONS} polls={s['polls']} "
          f"trades={s['trades']} day_pnl={s['day_pnl']:+.2f} cum={s['cum']:+.2f} "
          f"missed={len([d for d in missed if d < day])}")


if __name__ == "__main__":
    main()
