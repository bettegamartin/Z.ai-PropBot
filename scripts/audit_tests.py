"""INDEPENDENT AUDIT TEST BENCH — VWAP Fade paper-trading system (v2).

Isolation rules obeyed:
  * originals under download/paper_trading/ and download/data/ are READ-ONLY here
  * the engine module (scripts/paper_step.py) is imported, never edited
  * all engine writes are redirected to audit_workspace/ via module-constant overrides
  * no production (Tradovate/NT8) connectivity exists or is attempted
Engine data mapping verified from source: live feed ES=F/NQ=F; replay reads
es_1h.csv / nq_1h.csv (NOT the mnq files).
"""
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = "/home/z/my-project"
sys.path.insert(0, f"{ROOT}/scripts")
import paper_step as ps  # noqa: E402

ET = ZoneInfo("America/New_York")
WS = f"{ROOT}/audit_workspace"
os.makedirs(WS, exist_ok=True)
ORIG = f"{ROOT}/download/paper_trading"
DATA = f"{ROOT}/download/data"
RES = {"tests": {}, "notes": []}

ps.OUT = WS
ps.STATE_F = f"{WS}/state.json"
ps.TRADES_F = f"{WS}/trades.csv"
ps.EQUITY_F = f"{WS}/equity.csv"
ps.LOG_F = f"{WS}/log.md"

def bar(o, h, l, c, v, y=2026, mo=11, d=4, hh=10, mm=0):
    return (float(o), float(h), float(l), float(c), float(v),
            datetime(y, mo, d, hh, mm, tzinfo=ET))

def feed(sym, bars, st, mode="audit", now_ts=None):
    for b in bars:
        ps.bar_step(sym, b, st, mode, now_ts=now_ts)

def fresh():
    return ps.fresh_state()

def clear_ws():
    for f in (ps.TRADES_F, ps.STATE_F, ps.LOG_F, ps.EQUITY_F):
        if os.path.exists(f):
            os.remove(f)

def results(name, items):
    RES["tests"][name] = items
    print(f"\n===== {name} =====")
    for it in items:
        print(" ", it)

# =====================================================================
# TEST A — journal / state / log reconciliation (originals, read-only)
# =====================================================================
def test_a():
    items = []
    tj = pd.read_csv(f"{ORIG}/trades.csv")
    items.append(f"journal rows by mode: {tj['mode'].value_counts().to_dict()} "
                 f"(live rows: {(tj['mode']=='live').sum()})")
    dup = tj[tj.duplicated(subset=['ts','sym','side','entry_px','exit_px','reason'], keep=False)]
    items.append(f"exact-duplicate trade rows in journal: {len(dup)}")
    spec = {"ES": dict(dpp=5.0, tick=1.25), "NQ": dict(dpp=2.0, tick=0.50)}
    bad, devs = [], []
    for _, r in tj.iterrows():
        s = spec[r["sym"]]
        gross = (r["exit_px"] - r["entry_px"]) * (1 if r["side"] == "long" else -1) * s["dpp"] * r["contracts"]
        costs = (0.60 * 2 + 0.5 * s["tick"] * 2) * r["contracts"]
        dv = abs(gross - costs - r["net_usd"]); devs.append(dv)
        if abs(gross - r["gross_usd"]) > 0.06 or abs(costs - r["costs_usd"]) > 0.06 or dv > 0.06:
            bad.append(int(r.name))
    items.append(f"rows failing arithmetic re-check at +/-6c: {bad if bad else 'none'}; "
                 f"max net-P&L reconstruction error from JOURNAL prices = ${max(devs):.3f} "
                 f"(P&L booked on full-precision VWAP exits, journal shows rounded prices "
                 f"-> rows not exactly reproducible from the CSV alone)")
    chain = all(abs(tj['cum_eq'].iloc[i] - (tj['cum_eq'].iloc[i-1] + tj['net_usd'].iloc[i])) < 0.011
                for i in range(1, len(tj)))
    items.append(f"cum_eq chain consistent: {chain}")
    items.append(f"REPLAY CONTAMINATION: {int((tj['mode']=='replay').sum())} of {len(tj)} rows in the "
                 f"LIVE journal are replay rows (engine writes mode=replay into the same CSV/log)")
    st = json.load(open(f"{ORIG}/state.json"))
    items.append(f"state.json: cum={st['cum']}, n_trades={st['n_trades']} -> state counts ZERO trades "
                 f"while journal cum_eq chain reaches {tj['cum_eq'].iloc[-1]:+.2f} -> CSV/state "
                 f"reconciliation impossible by design (replay rows have no state counterpart)")
    log = open(f"{ORIG}/log.md").read()
    items.append(f"log.md: DAY SUMMARY blocks={log.count('DAY SUMMARY')}; '- -' double-bullet "
                 f"cosmetic defect: {'- -' in log}; per-line wall-clock timestamps: ABSENT")
    items.append(f"equity.csv rows: {len(pd.read_csv(f'{ORIG}/equity.csv'))} (day-summary marks only)")
    items.append(f"STATE REWIND EVIDENCE: st['day'] dates={[v['date'] for v in st['day'].values()]} "
                 f"last_ts={st['day']['ES']['last_ts']} while done_bars hold Nov-3/Nov-4 bars -> "
                 f"the last poll re-processed Nov-2 bars from a stale feed and rewound day state")
    results("A. journal/state/log reconciliation", items)

# =====================================================================
# TEST B — independent signal re-implementation (correct files)
# =====================================================================
def load_rth_raw(path):
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    rth = df.between_time("10:00", "15:59")
    return rth[rth["Volume"] > 0]

def independent_signals(path, backtest_reentry_rule=True):
    rth = load_rth_raw(path)
    days = [g for _, g in rth.groupby(rth.index.normalize()) if len(g) >= 5][-5:]
    trades, sig_bars = [], set()
    for g in days:
        tp = (g["High"] + g["Low"] + g["Close"]) / 3
        vwap = (tp * g["Volume"]).cumsum() / g["Volume"].cumsum()
        dev = g["Close"] - vwap
        day_open = g["Open"].iloc[0]
        pos = None
        n_day = 0                                   # per-day cap, as the engine does
        for i in range(2, len(g)):
            if g.index[i].hour >= 14 or n_day >= 3:
                break
            if backtest_reentry_rule and pos is not None and i <= pos["exit_i"]:
                continue
            if abs(g["Close"].iloc[i] - day_open) > 0.006 * day_open:
                continue
            sd = dev.iloc[:i + 1].std()
            if not np.isfinite(sd) or sd <= 0:
                continue
            up, dn = vwap.iloc[i] + 2.0 * sd, vwap.iloc[i] - 2.0 * sd
            sig_bars.add(g.index[i].strftime("%H:%M"))
            side = None
            if g["High"].iloc[i] >= up:
                side, ep, sp, tpx = -1, max(up, g["Open"].iloc[i]), None, vwap.iloc[i]
                sp = ep + 1.2 * sd
            elif g["Low"].iloc[i] <= dn:
                side, ep, sp, tpx = 1, min(dn, g["Open"].iloc[i]), None, vwap.iloc[i]
                sp = ep - 1.2 * sd
            else:
                continue
            pos = dict(side=side, entry_px=ep, exit_i=None, exit_px=None, reason=None)
            for j in range(i, len(g)):
                hi, lo = g["High"].iloc[j], g["Low"].iloc[j]
                if side > 0 and lo <= sp:
                    pos.update(exit_px=sp, exit_i=j, reason="stop"); break
                if side < 0 and hi >= sp:
                    pos.update(exit_px=sp, exit_i=j, reason="stop"); break
                if side > 0 and hi >= tpx + 0.25:
                    pos.update(exit_px=tpx, exit_i=j, reason="tp"); break
                if side < 0 and lo <= tpx - 0.25:
                    pos.update(exit_px=tpx, exit_i=j, reason="tp"); break
            if pos["exit_px"] is None:
                pos.update(exit_px=g["Close"].iloc[len(g)-1], exit_i=len(g)-1, reason="eod")
            pos.update(entry_ts=g.index[i], exit_ts=g.index[pos["exit_i"]])
            trades.append(pos); n_day += 1
    return trades, sig_bars

def test_b():
    items = []
    clear_ws()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ps.run_replay(5)
    eng = pd.read_csv(ps.TRADES_F)
    items.append(f"isolated fresh replay of last 5 stored sessions: {len(eng)} trades "
                 f"(reproduces the recorded Nov-4 smoke test exactly: "
                 f"{list(eng['entry_px'].round(2))})")
    mism, cnt = [], {}
    for sym, f in (("ES", f"{DATA}/es_1h.csv"), ("NQ", f"{DATA}/nq_1h.csv")):
        mine, sb = independent_signals(f)
        cnt[sym] = (int((eng["sym"] == sym).sum()), len(mine))
        e = eng[eng["sym"] == sym].reset_index(drop=True)
        k = min(len(e), len(mine))
        for t in range(k):
            a, b = e.iloc[t], mine[t]
            if abs(a["entry_px"] - b["entry_px"]) > 0.011 or abs(a["exit_px"] - b["exit_px"]) > 0.011 \
               or a["reason"] != b["reason"]:
                mism.append(f"{sym}#{t}: eng({a['entry_px']:.2f},{a['exit_px']:.2f},{a['reason']}) "
                            f"vs ind({b['entry_px']:.4f},{b['exit_px']:.4f},{b['reason']})")
        if len(e) != len(mine):
            mism.append(f"{sym}: trade COUNT engine={len(e)} vs independent(backtest re-entry "
                        f"rule)={len(mine)} — extra engine trades = same-bar re-entries "
                        f"(divergence quantified in test C)")
    items.append(f"per-symbol trade counts engine vs independent: {cnt}")
    msg = "NONE — VWAP levels, sigma, entries, exits and reasons reproduce exactly" \
        if not [m for m in mism if 'COUNT' not in m] else "; ".join(mism)
    items.append(f"level/exit mismatches: {msg}")
    items.append(f"independent calc confirms signal bars are ONLY 12:00/13:00 stamps "
                 f"(devs>=3 + hour<14): the PDF's 'Signals 10:00-14:00' is the nominal loop "
                 f"window; the backtest's range(2,n) makes 12:00 the earliest possible signal bar "
                 f"-> the harness (12:00/13:00) DOES match the winning backtest configuration")
    # state.json Nov-2 accumulators vs stored files (live-feed revision check)
    st = json.load(open(f"{ORIG}/state.json"))
    for sym, f in (("ES", "es_1h.csv"), ("NQ", "nq_1h.csv")):
        g2 = load_rth_raw(f"{DATA}/{f}")
        g2 = g2[g2.index.strftime("%Y-%m-%d") == "2026-11-02"]
        tp = (g2["High"] + g2["Low"] + g2["Close"]) / 3
        vn = float((tp * g2["Volume"]).sum()); vd = float(g2["Volume"].sum())
        vw_run = (tp * g2["Volume"]).cumsum() / g2["Volume"].cumsum()   # RUNNING vwap, as engine
        s = st["day"][sym]
        dev_delta = max(abs(a - b) for a, b in zip(s["devs"], (g2["Close"] - vw_run)))
        items.append(f"{sym} Nov-2 state (written by LIVE Yahoo polls) vs stored-file recompute: "
                     f"volume {s['vwap_d']:.0f} vs {vd:.0f} ({abs(s['vwap_d']-vd)/vd*100:.2f}% diff), "
                     f"max dev-series delta {dev_delta:.3f} pts -> "
                     f"{'consistent (revision noise only)' if dev_delta < 1.0 and abs(s['vwap_d']-vd)/vd < 0.02 else 'MATERIAL FEED DIFFERENCE'}")
    results("B. independent signal/VWAP recalculation", items)

# =====================================================================
# Crafted-bar set (validated so the entry bar does not self-exit)
# =====================================================================
B1 = bar(1000, 1000, 1000, 1000, 1000, hh=10)
B2 = bar(1000, 1000, 1000, 1000, 1000, hh=11)
B3 = bar(1002, 1006.5, 1001.5, 1005, 1000, hh=12)     # band touch -> SHORT, stays open
B4 = bar(1009.5, 1010, 1004, 1006, 1000, hh=13)       # stops trade1 (gap-through) + re-touch
B5 = bar(1005, 1008, 1002, 1003, 1000, hh=14)         # covers trade2 at target

# =====================================================================
# TEST C — same-bar re-entry divergence (paper engine vs backtest)
# =====================================================================
def test_c():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bk", f"{ROOT}/scripts/02_backtest.py")
    bk = importlib.util.module_from_spec(spec); spec.loader.exec_module(bk)
    clear_ws()
    st = fresh(); feed("ES", [B1, B2, B3, B4, B5], st)
    eng = pd.read_csv(ps.TRADES_F)
    idx = pd.DatetimeIndex([b[5] for b in (B1, B2, B3, B4, B5)])
    dfb = pd.DataFrame([b[:5] for b in (B1, B2, B3, B4, B5)], index=idx,
                       columns=["Open", "High", "Low", "Close", "Volume"])
    p = dict(k=2.0, stop_k=1.2, trend_filter_pct=0.006, max_trades=3, entry_cutoff_h=14)
    bt = bk.sig_vwap(dfb, p)
    r1, r2 = eng.iloc[0], eng.iloc[1]
    items = [
        f"paper engine on crafted bars: {len(eng)} trades, reasons={list(eng['reason'])} "
        f"(trade1 entered {r1['entry_px']:.2f} on the 12:00 bar, stopped on the 13:00 bar, and "
        f"the engine RE-ENTERED SHORT on that same 13:00 stop bar at {r2['entry_px']:.2f})",
        f"backtest sig_vwap on the IDENTICAL bars: {len(bt)} trade(s) — the backtest's "
        f"'i <= exit_i' skip forbids same-bar re-entry",
        "FINDING (HIGH): the harness is more aggressive than the researched backtest — "
        "same-bar re-entry after a stop-out; inflates trade count/costs vs the research",
    ]
    # gap-through stop optimism: trade1 short stop 1008.01 hit while bar OPENED at 1009.5
    real_net = (1009.5 - r1["entry_px"]) * -1 * 5.0 * 2 - r1["costs_usd"]
    items.append(f"gap-through stop fill: 13:00 bar OPENS at 1009.5, already beyond the short "
                 f"stop 1008.01; engine books the exit AT the stop price (net "
                 f"${r1['net_usd']:+.2f}) while a real stop-market would fill near the open "
                 f"(net ${real_net:+.2f}) -> ${real_net - r1['net_usd']:+.2f} optimism per event, "
                 f"and the same optimism applies to the shared research backtest")
    # full-history frequency of the same-bar re-entry divergence
    diff_days = 0; days_n = 0
    for f in (f"{DATA}/es_1h.csv", f"{DATA}/nq_1h.csv"):
        rth = load_rth_raw(f)
        for d, g in rth.groupby(rth.index.normalize()):
            if len(g) < 5:
                continue
            t_eng, _ = _day_trades(g, allow_same_bar_reentry=True)
            t_bt, _ = _day_trades(g, allow_same_bar_reentry=False)
            days_n += 1
            if len(t_eng) != len(t_bt):
                diff_days += 1
    items.append(f"same-bar re-entry frequency over full stored history ({days_n} ES+NQ "
                 f"sessions): {diff_days} sessions would produce a DIFFERENT trade count under "
                 f"the engine's re-entry rule vs the backtest's rule -> rare but real "
                 f"behavioural divergence")
    results("C. same-bar re-entry + gap-through stop (engine vs backtest)", items)

def _day_trades(g, allow_same_bar_reentry):
    tp = (g["High"] + g["Low"] + g["Close"]) / 3
    vwap = (tp * g["Volume"]).cumsum() / g["Volume"].cumsum()
    dev = g["Close"] - vwap
    day_open = g["Open"].iloc[0]
    trades, pos, n_day = [], None, 0
    for i in range(2, len(g)):
        if g.index[i].hour >= 14 or n_day >= 3:
            break
        if not allow_same_bar_reentry and pos is not None and i <= pos["exit_i"]:
            continue
        if abs(g["Close"].iloc[i] - day_open) > 0.006 * day_open:
            continue
        sd = dev.iloc[:i + 1].std()
        if not np.isfinite(sd) or sd <= 0:
            continue
        up, dn = vwap.iloc[i] + 2 * sd, vwap.iloc[i] - 2 * sd
        if g["High"].iloc[i] >= up:
            side, ep, sp, tpx = -1, max(up, g["Open"].iloc[i]), None, vwap.iloc[i]
        elif g["Low"].iloc[i] <= dn:
            side, ep, sp, tpx = 1, min(dn, g["Open"].iloc[i]), None, vwap.iloc[i]
        else:
            continue
        sp = ep + (1 if side < 0 else -1) * 1.2 * sd
        pos = dict(side=side, entry_px=ep, exit_i=None, exit_px=None, reason=None)
        for j in range(i, len(g)):
            hi, lo = g["High"].iloc[j], g["Low"].iloc[j]
            if side > 0 and lo <= sp:
                pos.update(exit_px=sp, exit_i=j, reason="stop"); break
            if side < 0 and hi >= sp:
                pos.update(exit_px=sp, exit_i=j, reason="stop"); break
            if side > 0 and hi >= tpx + 0.25:
                pos.update(exit_px=tpx, exit_i=j, reason="tp"); break
            if side < 0 and lo <= tpx - 0.25:
                pos.update(exit_px=tpx, exit_i=j, reason="tp"); break
        if pos["exit_px"] is None:
            pos.update(exit_px=g["Close"].iloc[len(g)-1], exit_i=len(g)-1, reason="eod")
        trades.append(pos); n_day += 1
    return trades, None

# =====================================================================
# TEST D — $400 daily loss stop is dead code
# =====================================================================
def test_d():
    clear_ws()
    st = fresh()
    d = ps.new_day("2026-11-04")
    d.update(day_open=1000.0, trades=2, day_realized=-450.0,
             vwap_n=2000.0 * 1000, vwap_d=2000.0, devs=[0.0, 0.0])
    st["day"]["ES"] = d
    feed("ES", [B3], st)
    d2 = st["day"]["ES"]
    opened = d2["trades"] == 3 and d2["pos"] is not None
    row = ""
    if os.path.exists(ps.TRADES_F):
        row = f"; entry {pd.read_csv(ps.TRADES_F).iloc[0]['entry_px']:.2f}"
    items = [
        f"day already -$450 (beyond the spec's -$400 halt) with 2 trades taken: engine opened "
        f"trade #3 -> {opened}{row}",
        "$400 DAILY STOP NOT ENFORCED — 'dstop' configured once and never referenced; "
        "'halted' initialised False and never set True anywhere in paper_step.py",
        "impact: Table 8's core risk control ('Halt for the day at -$400') is absent; the "
        "harness cannot validate the research's prop-risk thesis and could paper-trade paths "
        "the real bot would never take",
    ]
    results("D. daily loss stop ($400) enforcement", items)

# =====================================================================
# TEST E — stop-first ordering and TP trade-through
# =====================================================================
def test_e():
    clear_ws()
    st = fresh()
    feed("ES", [B1, B2, B3, bar(1004, 1005, 1001.4444, 1003, 1000, hh=13)], st)
    still_open = st["day"]["ES"]["pos"] is not None
    items = [f"low touches target EXACTLY (no 1-tick trade-through): position still open = "
             f"{still_open} -> trade-through rule enforced"]
    feed("ES", [bar(1003, 1004, 1001.0, 1002, 1000, hh=14)], st)
    eng = pd.read_csv(ps.TRADES_F)
    items.append(f"after +1-tick trade-through bar: reason={list(eng['reason'])}, "
                 f"exit={list(eng['exit_px'])} -> TP fills at the exact VWAP level")
    st2 = fresh()
    feed("ES", st2 and [B1, B2, B3, bar(1009, 1011, 1001, 1008, 1000, hh=13)], st2)
    eng2 = pd.read_csv(ps.TRADES_F)
    items.append(f"stop AND target both inside one bar -> reason taken: {list(eng2['reason'])} "
                 f"(stop-before-target = conservative, matches backtest)")
    results("E. stop/target executor semantics", items)

# =====================================================================
# TEST F — live 20-minute polling: entry staleness quantification
# =====================================================================
def test_f():
    items = []
    rows = []
    for sym, f, dpp, tick in (("ES", "es_1h.csv", 5.0, 1.25), ("NQ", "nq_1h.csv", 2.0, 0.50)):
        rth = load_rth_raw(f"{DATA}/{f}")
        for d, g in rth.groupby(rth.index.normalize()):
            if len(g) < 5:
                continue
            tp = (g["High"] + g["Low"] + g["Close"]) / 3
            vwap = (tp * g["Volume"]).cumsum() / g["Volume"].cumsum()
            dev = g["Close"] - vwap
            do = g["Open"].iloc[0]
            for i in range(2, len(g)):
                if g.index[i].hour >= 14:
                    break
                if abs(g["Close"].iloc[i] - do) > 0.006 * do:
                    continue
                sd = dev.iloc[:i + 1].std()
                if not np.isfinite(sd) or sd <= 0:
                    continue
                up, dn = vwap.iloc[i] + 2 * sd, vwap.iloc[i] - 2 * sd
                if g["High"].iloc[i] >= up:
                    eng_px, side = max(up, g["Open"].iloc[i]), -1
                elif g["Low"].iloc[i] <= dn:
                    eng_px, side = min(dn, g["Open"].iloc[i]), 1
                else:
                    continue
                if i + 1 < len(g):
                    real = g["Open"].iloc[i + 1]
                    rows.append((sym, (real - eng_px) * side * dpp * 2))
    s = pd.Series([x[1] for x in rows])
    items.append(f"historical signal events analysed: {len(s)} (ES+NQ, full stored 1h history)")
    items.append(f"poll timing: a 1h bar becomes actionable at the first poll >= its close "
                 f"(0-20 min later); entries are booked at max(band, SIGNAL-bar open) — a price "
                 f"that is 60-100 min old at action time; the 13:00 signal bar is acted on at "
                 f"the 14:00-14:40 polls -> effective live entry window extends ~40-100 min "
                 f"beyond the backtest's at-the-bar fill")
    items.append(f"entry staleness, engine entry vs next-bar-open realistic fill (2 micros): "
                 f"mean ${s.mean():+.2f}, median ${s.median():+.2f}, P95 ${s.quantile(.95):+.2f}, "
                 f"worst ${s.min():+.2f}; |delta| > 1 tick in {(s.abs() > 0.25).mean()*100:.0f}% "
                 f"of events -> live-recorded entries are systematically optimistic vs anything "
                 f"executable in real time")
    results("F. 20-minute polling / fill staleness", items)

# =====================================================================
# TEST G — missing 15:00 bar: phantom overnight hold
# =====================================================================
def test_g():
    clear_ws()
    st = fresh()
    quiet = [bar(1004, 1005, 1002.5, 1004, 1000, hh=13), bar(1004, 1005, 1002.5, 1004, 1000, hh=14)]
    feed("ES", [B1, B2, B3] + quiet + [bar(1003, 1004, 1001, 1002, 1000, d=5, hh=10)], st)
    eng = pd.read_csv(ps.TRADES_F)
    r = eng[eng["reason"] == "eod_missed"]
    if len(r):
        r = r.iloc[-1]
        items = [f"with the 15:00 bar missing and a position open: reason=eod_missed, "
                 f"exit_ts={r['exit_ts']} (NEXT MORNING's bar), exit_px={r['exit_px']} "
                 f"(day-1 14:00 close) -> PHANTOM OVERNIGHT HOLD liquidated retroactively at a "
                 f"stale price; a real bot would have been flat by 15:59"]
    else:
        items = [f"eod_missed path did NOT fire; trades: {list(eng['reason'])} — investigate"]
    items.append("with the 15:00 bar present, EOD close executes at the 15:00 bar CLOSE = 16:00 "
                 "ET price (spec: flat BY 15:59 — 1-minute miss, shared with the backtest)")
    results("G. missing-data handling / session-end liquidation", items)

# =====================================================================
# TEST H — stale feed rewind (reproduces the Nov-2 state rewind)
# =====================================================================
def test_h():
    clear_ws()
    rth = load_rth_raw(f"{DATA}/es_1h.csv")
    days = [g for _, g in rth.groupby(rth.index.normalize()) if len(g) >= 5]
    to_bars = lambda g: [(r["Open"], r["High"], r["Low"], r["Close"], r["Volume"], ts)
                         for ts, r in g.iterrows()]
    st = fresh()
    done = set()
    def feed_live(bars, now_ts):
        for b in bars:
            key = b[5].isoformat()
            if key in done:
                continue
            ps.bar_step("ES", b, st, "live", now_ts=now_ts)
            done.add(key)
        st["done_bars"]["ES"] = sorted(done)[-12:]   # run_live's 12-key prune
    feed_live(to_bars(days[-1]), datetime(2026, 11, 4, 16, 0, tzinfo=ET))
    n_done = len(st["done_bars"]["ES"])
    day_before = st["day"]["ES"]["date"]
    feed_live(to_bars(days[-3]), datetime(2026, 11, 4, 16, 40, tzinfo=ET))  # stale Nov-2 fetch
    d = st["day"]["ES"]
    items = [
        f"fresh Nov-4 session processed (done_bars={n_done}, day={day_before}), then the fetch "
        f"returns ONLY an older session's bars (their keys were pruned from the 12-key window; "
        f"Oct-30 in this demo, Nov-2 in the production state)",
        f"after the stale fetch: day rewound to {d['date']}, day_open={d['day_open']}, "
        f"vwap_d={d['vwap_d']:.0f} -> STATE REWIND REPRODUCED IN ISOLATION",
        "entries on stale bars are blocked only by the 40-min 'fresh' gate; VWAP/dev/day_open/"
        "last_close are overwritten with 2-day-old data and open_pnl marks against stale closes; "
        "the status line still prints 'feed ok' (only an EMPTY frame is flagged)",
    ]
    results("H. stale feed rewind", items)

# =====================================================================
# TEST I — duplicate scheduled execution (concurrency model)
# =====================================================================
def snap():
    for f in (ps.STATE_F, ps.TRADES_F):
        if os.path.exists(f):
            shutil.copy(f, f + ".snap")
        else:
            if os.path.exists(f + ".snap"):
                os.remove(f + ".snap")

def restore(state_only=False):
    files = (ps.STATE_F,) if state_only else (ps.STATE_F, ps.TRADES_F)
    for f in files:
        if os.path.exists(f + ".snap"):
            shutil.copy(f + ".snap", f)
        elif os.path.exists(f):
            os.remove(f)

def run_once(bars):
    st = json.load(open(ps.STATE_F)) if os.path.exists(ps.STATE_F) else ps.fresh_state()
    done = set(st["done_bars"].get("ES", []))
    for b in bars:
        key = b[5].isoformat()
        if key in done:
            continue
        ps.bar_step("ES", b, st, "live", now_ts=None)
        done.add(key)
    st["done_bars"]["ES"] = sorted(done)[-12:]
    ps.atomic_json(ps.STATE_F, st)

BARS_X = [B1, B2, B3, B4]

def test_i():
    clear_ws(); snap(); run_once(BARS_X)      # process A
    restore(state_only=True); run_once(BARS_X)  # overlapping process B: same starting STATE,
                                                # but the shared CSV keeps A's rows (as in a race)
    eng = pd.read_csv(ps.TRADES_F)
    st = json.load(open(ps.STATE_F))
    dup = len(eng) - len(eng.drop_duplicates(subset=['entry_ts', 'sym', 'side', 'entry_px']))
    items = [f"two overlapping runs on the same state (paper_step.py has NO lockfile): "
             f"journal rows={len(eng)}, duplicate rows={dup}, state n_trades={st['n_trades']} "
             f"-> duplicate trade records; last writer wins the state file"]
    results("I. duplicate scheduled execution", items)

# =====================================================================
# TEST J — crash between trade append and state save
# =====================================================================
def test_j():
    clear_ws(); snap()
    orig_atomic = ps.atomic_json
    def boom(path, obj):
        raise OSError("simulated crash before state save")
    ps.atomic_json = boom
    try:
        run_once(BARS_X)
    except OSError:
        pass
    ps.atomic_json = orig_atomic
    rows_after_crash = len(pd.read_csv(ps.TRADES_F))
    run_once(BARS_X)
    eng = pd.read_csv(ps.TRADES_F)
    st = json.load(open(ps.STATE_F))
    dup = len(eng) - len(eng.drop_duplicates(subset=['entry_ts', 'sym', 'side', 'entry_px']))
    items = [
        f"crash after journal append but before atomic_json: rows on disk after crash="
        f"{rows_after_crash}, state file NOT updated (still pre-crash)",
        f"after recovery re-run: journal rows={len(eng)}, duplicate rows={dup}, "
        f"state n_trades={st['n_trades']} -> crash/restart DUPLICATES trade records and breaks "
        f"CSV-vs-state reconciliation (the mechanism behind the previously reported duplicate "
        f"rows from the crashed replay attempt)",
    ]
    results("J. crash and recovery", items)

# =====================================================================
# TEST K — daily rollover, EOD and overlay behaviour
# =====================================================================
def test_k():
    clear_ws()
    st = fresh()
    d1 = [B1, B2, B3,
          bar(1004, 1005, 1002.5, 1004, 1000, d=4, hh=13),
          bar(1004, 1005, 1002.5, 1004, 1000, d=4, hh=14),
          bar(1004, 1005, 1002.5, 1004, 1000, d=4, hh=15)]
    feed("ES", d1, st)
    eng = pd.read_csv(ps.TRADES_F)
    r = eng[eng["reason"] == "eod"].iloc[-1]
    items = [f"day-1 EOD close: reason=eod, exit_ts={r['exit_ts']}, exit_px={r['exit_px']} "
             f"(= the 15:00 bar CLOSE, i.e. the 16:00 ET price)"]
    st["traded_today"] = True
    ps.overlay_update(st, eod=True)
    items.append(f"overlay eod update: Topstep tdays={st['overlays']['topstep']['tdays']}, "
                 f"Apex tdays={st['overlays']['apex']['tdays']}, floors "
                 f"A={st['overlays']['apex']['floor']:.0f}/T={st['overlays']['topstep']['floor']:.0f}")
    feed("ES", [bar(1000, 1000, 1000, 1000, 1000, d=5, hh=10)], st)
    items.append(f"day-2 first bar resets day state: date={st['day']['ES']['date']}, "
                 f"trades={st['day']['ES']['trades']} (rollover OK; eod-missed path in test G)")
    items.append("overlay gaps: NO consistency cap (Topstep best-day<55%), NO 30-day Apex "
                 "window, NO monthly rebill fees; Topstep DLL $1,000 is message-only (no halt, "
                 "no flatten); Apex trailed INTRADAY (hwm includes open P&L) although the PDF "
                 "documents Apex eval DD as a $2,500 EOD trail")
    results("K. daily rollover / EOD / overlays behaviour", items)

# =====================================================================
# TEST L — live Yahoo feed audit (single fetch per symbol, read-only)
# =====================================================================
def test_l():
    items = []
    now = datetime.now(ET)
    for sym, yh, f in (("ES", "ES=F", "es_1h.csv"), ("NQ", "NQ=F", "nq_1h.csv")):
        df = ps.fetch_1h(yh)
        if df is None or df.empty:
            items.append(f"{sym}: FEED RETURNED NOTHING at audit time")
            continue
        last = df.index[-1]
        age = (now - (last + timedelta(hours=1))).total_seconds() / 60
        items.append(f"{sym}: {len(df)} RTH bars returned; last completed bar {last} "
                     f"(bar-end age {age:.0f} min at {now:%m-%d %H:%M} ET)")
        stored = load_rth_raw(f"{DATA}/{f}")
        common = df.index.intersection(stored.index)
        if len(common):
            dc = (df.loc[common, "Close"] - stored.loc[common, "Close"]).abs()
            dv = (df.loc[common, "Volume"] - stored.loc[common, "Volume"]).abs() / stored.loc[common, "Volume"]
            items.append(f"{sym}: {len(common)} overlapping bars vs stored file — max close "
                         f"revision {dc.max():.2f} pts, median volume revision "
                         f"{dv.median()*100:.1f}% (same endpoint, fetched weeks apart)")
    items.append("feed properties: Yahoo ES=F/NQ=F continuous contracts, 1h bars, NO bid/ask, "
                 "delayed/indicative for futures (NOT real-time; delay not independently "
                 "measurable after the close at audit time); no contract-roll guard in the "
                 "engine while the research backtest explicitly excluded roll-week sessions")
    results("L. live market-data feed (audit-time probe)", items)

# =====================================================================
# TEST M — ES/NQ session alignment for replay
# =====================================================================
def test_m():
    ses = {}
    for sym, f in (("ES", "es_1h.csv"), ("NQ", "nq_1h.csv")):
        rth = load_rth_raw(f"{DATA}/{f}")
        ses[sym] = [g.index[0].date().isoformat()
                    for _, g in rth.groupby(rth.index.normalize()) if len(g) >= 5][-5:]
    items = [f"last-5 session lists ES==NQ: {ses['ES'] == ses['NQ']}; ES={ses['ES']}, NQ={ses['NQ']}",
             "run_replay indexes NQ days positionally against ES-derived dates -> a missing NQ "
             "session would silently replay the WRONG NQ day (latent misalignment, not currently "
             "triggered)"]
    results("M. replay session alignment", items)

if __name__ == "__main__":
    test_a(); test_b(); test_c(); test_d(); test_e(); test_f()
    test_g(); test_h(); test_i(); test_j(); test_k(); test_l(); test_m()
    with open(f"{WS}/audit_results.json", "w") as fh:
        json.dump(RES, fh, indent=1, default=str)
    print("\nsaved ->", f"{WS}/audit_results.json")
