"""Fetch free futures data via yfinance: ES, NQ, MES, MNQ.
Daily (max history) + 1h (730d) + 15m/5m (60d). Saves CSVs, prints quality report.
All free of charge (Yahoo Finance public data).
"""
import os
import pandas as pd
import yfinance as yf

OUT = "/home/z/my-project/download/data"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["ES=F", "NQ=F", "MES=F", "MNQ=F"]
JOBS = [
    ("1d", "max"),
    ("1h", "730d"),
    ("15m", "60d"),
    ("5m", "60d"),
]

report = []
for sym in SYMBOLS:
    tkr = yf.Ticker(sym)
    for iv, per in JOBS:
        try:
            df = tkr.history(period=per, interval=iv, auto_adjust=False)
        except Exception as e:
            report.append((sym, iv, "ERROR", str(e)[:80]))
            continue
        if df is None or len(df) == 0:
            report.append((sym, iv, "EMPTY", "no rows"))
            continue
        df.index = pd.to_datetime(df.index)
        # Convert to New York time for session logic
        try:
            df.index = df.index.tz_convert("America/New_York")
        except Exception:
            df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()
        fname = f"{sym.replace('=F','').lower()}_{iv}.csv"
        df.to_csv(os.path.join(OUT, fname))
        report.append((sym, iv, len(df), f"{df.index[0]} -> {df.index[-1]}"))

print(f"{'SYM':6} {'IV':5} {'ROWS':>8}  RANGE")
for r in report:
    print(f"{str(r[0]):6} {str(r[1]):5} {str(r[2]):>8}  {r[3]}")

# Quality checks on 1h series (the core backtest data)
print("\n--- 1h quality checks ---")
for sym in SYMBOLS:
    tag = sym.replace("=F", "").lower()
    p = os.path.join(OUT, f"{tag}_1h.csv")
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    rth = df.between_time("09:00", "16:00")
    # RTH sessions = days with bars in 9:30-16:00 window
    sess = rth.between_time("09:30", "15:59")
    n_days = sess.index.normalize().nunique()
    bad = df[(df["Close"] <= 0) | (df["High"] < df["Low"])]
    # daily close-to-close jumps (roll detection)
    d = pd.read_csv(os.path.join(OUT, f"{tag}_1d.csv"), index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index, utc=True).tz_convert("America/New_York")
    ret = d["Close"].pct_change().dropna()
    big = (ret.abs() > 0.035).sum()
    print(f"{sym}: 1h rows={len(df)}, RTH trading days={n_days}, bad ticks={len(bad)}, daily |ret|>3.5% days={big}")
