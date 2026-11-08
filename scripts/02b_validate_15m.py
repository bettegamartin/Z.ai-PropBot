"""Robustness checks:
1) VWAP-fade (production variant) re-run on REAL 15m bars, most recent ~50 sessions
   (unseen fine-grained data, closest to live fills).
2) ES<->MES and NQ<->MNQ price agreement (justifies trading micros off mini data).
"""
import numpy as np
import pandas as pd

DATA = "/home/z/my-project/download/data"

def load(sym, iv):
    df = pd.read_csv(f"{DATA}/{sym.lower()}_{iv}.csv", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    return df.sort_index()

def vwap_fade_15m(sym):
    df = load(sym, "15m")
    hours = sorted(set(df.between_time("09:00", "16:00").index.hour))
    start_h = 9 if 9 in hours else 10
    rth = df.between_time(f"{start_h:02d}:00", "15:59").copy()
    rth = rth[["Open", "High", "Low", "Close", "Volume"]]
    rth = rth[rth["Volume"] > 0]
    rth["date"] = rth.index.normalize()
    counts = rth.groupby("date").size()
    rth = rth[rth["date"].isin(counts[counts >= 20].index)]
    print(f"{sym} 15m: session start hour {start_h}, bars/day median {counts.median():.0f}, "
          f"sessions {rth['date'].nunique()}")
    trades = []
    for d, g in rth.groupby("date"):
        tp = (g["High"] + g["Low"] + g["Close"]) / 3
        v = g["Volume"].replace(0, 1)
        vwap = (tp * v).cumsum() / v.cumsum()
        dev = g["Close"] - vwap
        day_open = g["Open"].iloc[0]
        n = len(g)
        open_trades = []
        for i in range(8, n):
            ts = g.index[i]
            if ts.hour >= 14 and len(open_trades) >= 3:
                break
            # manage open trades first (conservative: stop before tp within bar)
            still = []
            for t in open_trades:
                hi, lo = g["High"].iloc[i], g["Low"].iloc[i]
                if t["side"] > 0:
                    if lo <= t["stop"]:
                        t["exit"], t["why"] = t["stop"], "stop"
                    elif hi >= t["tp"] + 0.25:
                        t["exit"], t["why"] = t["tp"], "tp"
                else:
                    if hi >= t["stop"]:
                        t["exit"], t["why"] = t["stop"], "stop"
                    elif lo <= t["tp"] - 0.25:
                        t["exit"], t["why"] = t["tp"], "tp"
                if "exit" in t:
                    trades.append(t | {"exit_date": d})
                else:
                    still.append(t)
            open_trades = still
            if ts.hour >= 14 or len(open_trades) + sum(1 for _ in trades if _["date"] == d) >= 3:
                continue
            if abs(g["Close"].iloc[i] - day_open) > 0.006 * day_open:
                continue
            sd = dev.iloc[:i + 1].std()
            if not np.isfinite(sd) or sd <= 0:
                continue
            b = g.iloc[i]
            up_b = vwap.iloc[i] + 2.0 * sd
            dn_b = vwap.iloc[i] - 2.0 * sd
            stop_d = 1.2 * sd
            if b["High"] >= up_b and not open_trades:
                open_trades.append(dict(date=d, side=-1, entry=max(up_b, b["Open"]),
                                        stop=max(up_b, b["Open"]) + stop_d, tp=vwap.iloc[i]))
            elif b["Low"] <= dn_b and not open_trades:
                open_trades.append(dict(date=d, side=1, entry=min(dn_b, b["Open"]),
                                        stop=min(dn_b, b["Open"]) - stop_d, tp=vwap.iloc[i]))
        # force EOD flat
        for t in open_trades:
            t["exit"], t["why"] = g["Close"].iloc[-1], "eod"
            trades.append(t | {"exit_date": d})
    tdf = pd.DataFrame(trades)
    dpp = 5.0 if sym == "ES" else 2.0
    cost = (1.25 if sym == "ES" else 0.50) + 1.20  # slip RT + comm RT
    tdf["net"] = (tdf["exit"] - tdf["entry"]) * tdf["side"] * dpp - cost
    wr = (tdf["net"] > 0).mean() * 100
    exp = tdf["net"].mean()
    pf = tdf.loc[tdf["net"] > 0, "net"].sum() / max(1e-9, abs(tdf.loc[tdf["net"] < 0, "net"].sum()))
    dd = tdf.groupby("date")["net"].sum()
    eq = dd.cumsum()
    mdd = (eq - eq.cummax()).min()
    print(f"{sym} 15m VWAPFD k=2.0: n={len(tdf)}, win%={wr:.1f}, exp=${exp:.2f}/trade, "
          f"PF={pf:.2f}, MaxDD=${mdd:.0f}, trades/day {len(tdf)/tdf['date'].nunique():.2f}")
    return dict(sym=sym, n=len(tdf), wr=round(wr, 1), exp=round(exp, 2), pf=round(pf, 2),
                mdd=round(mdd, 0))

def agree(a, b):
    da = load(a, "1d")["Close"]
    db = load(b, "1d")["Close"].reindex(da.index)
    m = pd.concat([da, db], axis=1).dropna()
    m.columns = ["a", "b"]
    corr = m["a"].corr(m["b"])
    mad = (m["a"] - m["b"]).abs().mean()
    print(f"{a} vs {b}: corr={corr:.5f}, mean abs diff={mad:.2f} pts ({mad/m['a'].mean()*100:.3f}%)")

if __name__ == "__main__":
    agree("ES", "MES")
    agree("NQ", "MNQ")
    vwap_fade_15m("ES")
    vwap_fade_15m("NQ")
