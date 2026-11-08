"""Backtest v2 - 3 intraday strategies on free 1h futures data (ES=F, NQ=F),
executed as micros MES ($5/pt) / MNQ ($2/pt).

Data notes (verified vs 5m): Yahoo 1h bars are top-of-hour aligned; the 09:00
bar mixes premarket with the first 30min of RTH. RTH sessions are therefore
built from bars 10:00..15:59 (6 clean RTH bars/day). Strategies:

  ORB10  - 10:00-11:00 range breakout (classic ORB shifted to clean data)
  VWAPFD - fade k*sigma extensions of session VWAP (from 10:00)
  EMAPB  - EMA9/21 pullback with CROSS-SESSION continuous EMA state

Executor: conservative bar-level fills. Stop checked before TP inside a bar;
TP fills require trade-through (+1 tick). Costs: $0.60/side commission +
0.5 tick slippage per side. Flat by end of session (prop-compliant).
"""
import os
import numpy as np
import pandas as pd

DATA = "/home/z/my-project/download/data"
OUT = DATA

SPECS = {
    "ES": {"dpp": 5.0, "tick": 0.25, "tick_usd": 1.25, "comm_side": 0.60, "slip_ticks": 0.5},
    "NQ": {"dpp": 2.0, "tick": 0.25, "tick_usd": 0.50, "comm_side": 0.60, "slip_ticks": 0.5},
}

def third_fridays(index, start="2024-01-01"):
    out = []
    for y in range(pd.Timestamp(start).year, index[-1].year + 1):
        for m in (3, 6, 9, 12):
            d = pd.Timestamp(year=y, month=m, day=1)
            ff = d + pd.offsets.Week(weekday=4)
            if ff.month != m:
                ff += pd.Timedelta(days=7)
            out.append(ff + pd.Timedelta(days=14))
    return pd.DatetimeIndex(out).tz_localize("America/New_York")

def load_rth(sym):
    """Sessions of 6 clean RTH bars (10:00..15:59) + continuous indicators."""
    df = pd.read_csv(f"{DATA}/{sym.lower()}_1h.csv", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    df = df.sort_index()
    rth = df.between_time("10:00", "15:59").copy()
    rth = rth[["Open", "High", "Low", "Close", "Volume"]]
    rth = rth[rth["Volume"] > 0]
    rth["date"] = rth.index.normalize()
    counts = rth.groupby("date").size()
    keep = counts[counts >= 5].index  # allow 5-bar early closes
    rth = rth[rth["date"].isin(keep)]
    # roll-day filter near quarterly expiries
    exp = third_fridays(rth.index, start="2024-06-01")
    g = rth.groupby("date")
    o = g["Open"].first(); c = g["Close"].last()
    gap = (o - c.shift(1)).abs()
    atr = g.apply(lambda x: (x["High"] - x["Low"]).mean())
    bad = set()
    for d in gap.index:
        near_exp = any(abs((d - e).days) <= 6 for e in exp)
        if near_exp and gap[d] > 2.5 * max(atr[d], 1e-9) * np.sqrt(6):
            bad.add(d)
    rth = rth[~rth["date"].isin(bad)]
    # continuous indicators across sessions (no lookahead: shifted by construction)
    closes = rth["Close"]
    rth["ema9"] = closes.ewm(span=9, adjust=False).mean()
    rth["ema21"] = closes.ewm(span=21, adjust=False).mean()
    prev_c = closes.shift(1)
    tr = np.maximum(rth["High"] - rth["Low"],
                    np.maximum((rth["High"] - prev_c).abs(), (rth["Low"] - prev_c).abs()))
    rth["atr14"] = tr.ewm(span=14, adjust=False).mean()
    return rth

def session_vwap(g):
    tp = (g["High"] + g["Low"] + g["Close"]) / 3
    v = g["Volume"].replace(0, 1)
    return (tp * v).cumsum() / v.cumsum()

def run_trades(bars, sigs):
    """Walk bars; conservative: stop first if both levels in bar; TP needs trade-through."""
    out = []
    n = len(bars)
    tick = 0.25
    for t in sigs:
        side, i = t["side"], t["entry_i"]
        ep, sp, tp = t["entry_px"], t["stop_px"], t["tp_px"]
        exit_px, exit_j, reason = None, None, None
        for j in range(i, n):
            hi, lo = bars["High"].iloc[j], bars["Low"].iloc[j]
            if side > 0:
                if lo <= sp:
                    exit_px, exit_j, reason = sp, j, "stop"; break
                if hi >= tp + tick:  # trade-through required for TP
                    exit_px, exit_j, reason = tp, j, "tp"; break
            else:
                if hi >= sp:
                    exit_px, exit_j, reason = sp, j, "stop"; break
                if lo <= tp - tick:
                    exit_px, exit_j, reason = tp, j, "tp"; break
        if exit_px is None:
            j = n - 1
            exit_px, reason, exit_j = bars["Close"].iloc[j], "eod", j
        out.append(dict(t, exit_px=exit_px, exit_i=exit_j, reason=reason, contracts=1))
    return out

def trade_pnl(t, spec):
    per_ct = (t["exit_px"] - t["entry_px"]) * t["side"] * spec["dpp"]
    slip = spec["slip_ticks"] * spec["tick_usd"] * 2
    comm = spec["comm_side"] * 2
    gross = per_ct * t["contracts"]
    costs = (slip + comm) * t["contracts"]
    return gross, costs

def sig_orb(bars, p):
    if len(bars) < 4: return []
    orh, orl = bars["High"].iloc[0], bars["Low"].iloc[0]
    w = orh - orl
    if w <= 0 or w > p["max_or_pts"]: return []
    for i in range(1, len(bars)):
        if bars.index[i].hour >= p["entry_cutoff_h"]: break
        b = bars.iloc[i]
        if b["High"] > orh:
            ep = b["Open"] if b["Open"] > orh else orh + 0.25
            return [{"side": 1, "entry_i": i, "entry_px": ep,
                     "stop_px": ep - w, "tp_px": ep + p["tp_mult"] * w}]
        if b["Low"] < orl:
            ep = b["Open"] if b["Open"] < orl else orl - 0.25
            return [{"side": -1, "entry_i": i, "entry_px": ep,
                     "stop_px": ep + w, "tp_px": ep - p["tp_mult"] * w}]
    return []

def sig_vwap(bars, p):
    if len(bars) < 5: return []
    vwap = session_vwap(bars)
    dev = bars["Close"] - vwap
    trades, n = [], len(bars)
    day_open = bars["Open"].iloc[0]
    for i in range(2, n):
        if bars.index[i].hour >= p["entry_cutoff_h"] or len(trades) >= p["max_trades"]: break
        if trades and i <= trades[-1]["exit_i"]: continue
        if abs(bars["Close"].iloc[i] - day_open) > p["trend_filter_pct"] * day_open: continue
        sd = dev.iloc[:i + 1].std()
        if not np.isfinite(sd) or sd <= 0: continue
        b = bars.iloc[i]
        up_b = vwap.iloc[i] + p["k"] * sd
        dn_b = vwap.iloc[i] - p["k"] * sd
        stop_d = p["stop_k"] * sd
        if b["High"] >= up_b:
            ep = max(up_b, b["Open"])
            t = {"side": -1, "entry_i": i, "entry_px": ep,
                 "stop_px": ep + stop_d, "tp_px": vwap.iloc[i]}
        elif b["Low"] <= dn_b:
            ep = min(dn_b, b["Open"])
            t = {"side": 1, "entry_i": i, "entry_px": ep,
                 "stop_px": ep - stop_d, "tp_px": vwap.iloc[i]}
        else:
            continue
        (trades.append(t))
        closed = run_trades(bars, [t])
        trades[-1].update(closed[0])
    return trades

def sig_emapb(bars, p):
    if len(bars) < 4: return []
    trades, n = [], len(bars)
    for i in range(1, n):
        if bars.index[i].hour >= p["entry_cutoff_h"] or len(trades) >= p["max_trades"]: break
        if trades and i <= trades[-1]["exit_i"]: continue
        b = bars.iloc[i]
        a = b["atr14"]
        if not np.isfinite(a) or a <= 0: continue
        up = b["ema9"] > b["ema21"] and b["Close"] > b["ema21"]
        dn = b["ema9"] < b["ema21"] and b["Close"] < b["ema21"]
        if up and b["Low"] <= b["ema9"] + 0.25:
            ep = b["Close"]
            t = {"side": 1, "entry_i": i, "entry_px": ep,
                 "stop_px": ep - p["atr_stop"] * a, "tp_px": ep + p["atr_tp"] * a}
        elif dn and b["High"] >= b["ema9"] - 0.25:
            ep = b["Close"]
            t = {"side": -1, "entry_i": i, "entry_px": ep,
                 "stop_px": ep + p["atr_stop"] * a, "tp_px": ep - p["atr_tp"] * a}
        else:
            continue
        trades.append(t)
        closed = run_trades(bars, [t])
        trades[-1].update(closed[0])
    return trades

GRIDS = {
    "ORB10": [dict(tp_mult=1.5, entry_cutoff_h=14),
              dict(tp_mult=2.0, entry_cutoff_h=14)],
    "VWAPFD": [dict(k=1.5, stop_k=1.0, trend_filter_pct=0.006, max_trades=3, entry_cutoff_h=14),
               dict(k=2.0, stop_k=1.2, trend_filter_pct=0.006, max_trades=3, entry_cutoff_h=14)],
    "EMAPB": [dict(atr_stop=1.0, atr_tp=2.0, max_trades=2, entry_cutoff_h=14),
              dict(atr_stop=1.2, atr_tp=2.5, max_trades=2, entry_cutoff_h=14)],
}

def main():
    all_trades = []
    is_end = pd.Timestamp("2026-03-01", tz="America/New_York")  # IS/OOS split
    for sym in ["ES", "NQ"]:
        rth = load_rth(sym)
        spec = SPECS[sym]
        max_or = rth["Close"].iloc[-1] * 0.004
        n_days = rth["date"].nunique()
        for strat, grid in GRIDS.items():
            for gi, p in enumerate(grid):
                if strat == "ORB10":
                    p = dict(p, max_or_pts=max_or)
                for d, g in rth.groupby("date"):
                    bars = g.copy()
                    if strat == "ORB10":
                        sigs = sig_orb(bars, p)
                    elif strat == "VWAPFD":
                        sigs = sig_vwap(bars, p)
                    else:
                        sigs = sig_emapb(bars, p)
                    for t in run_trades(bars, sigs):
                        gross, costs = trade_pnl(t, spec)
                        t.update({"date": d.date().isoformat(), "sym": sym, "strat": strat,
                                  "variant": gi, "gross_usd": round(gross, 2),
                                  "costs_usd": round(costs, 2), "net_usd": round(gross - costs, 2),
                                  "split": "IS" if d.tz_convert("America/New_York") < is_end else "OOS"})
                        all_trades.append(t)
    cols = ["date", "split", "sym", "strat", "variant", "side", "contracts",
            "entry_px", "exit_px", "stop_px", "tp_px", "reason",
            "gross_usd", "costs_usd", "net_usd"]
    tdf = pd.DataFrame(all_trades)[cols].sort_values(["strat", "variant", "sym", "date"])
    tdf.to_csv(f"{OUT}/trades.csv", index=False)
    daily = tdf.groupby(["strat", "variant", "sym", "split", "date"]).agg(
        net=("net_usd", "sum"), n=("net_usd", "size")).reset_index()
    daily["traded"] = daily["net"].notna()
    daily.to_csv(f"{OUT}/daily_pnl.csv", index=False)

    print(f"{'STRAT':7} {'V':2} {'SYM':4} {'S':3} {'N':>5} {'WIN%':>6} {'EXP$':>7} {'PF':>6} {'MaxDD$':>8} {'t/day':>5} {'stop%':>6}")
    for (s, v, y), grp in tdf.groupby(["strat", "variant", "sym"]):
        for sp in ["IS", "OOS"]:
            gg = grp[grp["split"] == sp]
            if len(gg) == 0: continue
            wr = (gg["net_usd"] > 0).mean() * 100
            exp = gg["net_usd"].mean()
            pf = gg.loc[gg["net_usd"] > 0, "net_usd"].sum() / max(1e-9, abs(gg.loc[gg["net_usd"] < 0, "net_usd"].sum()))
            dd = gg.groupby("date")["net_usd"].sum()
            eq = dd.cumsum(); mdd = (eq - eq.cummax()).min()
            tpd = gg.groupby("date").size().mean()
            sr = (gg["reason"] == "stop").mean() * 100
            print(f"{s:7} {v:<2} {y:4} {sp:3} {len(gg):>5} {wr:>6.1f} {exp:>7.2f} {pf:>6.2f} {mdd:>8.0f} {tpd:>5.2f} {sr:>6.1f}")

if __name__ == "__main__":
    main()
