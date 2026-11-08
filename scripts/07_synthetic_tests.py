"""Synthetic-market stress tests (v4).

Pipeline: real Yahoo 1h RTH bars (ES=F, NQ=F, 6 clean bars/day) -> synthetic
worlds via day-donor bootstrap with per-bar vol noise, AR(1) return shaping
and day-drift regimes -> same 3 strategies x 2 variants run on synthetic bars
-> day-P&L panels -> prop-eval Monte Carlo (block-day sampling) for 8 firm
configs (50K/100K).  Synthetic worlds:

  neutral  vol x1.0 / x0.7 / x1.4   (calm, normal, wild)
  chop     vol x1.0, AR=-0.25, tiny drift
  trend    vol x1.0, AR=+0.22, fat drift, 25% trend-days
  meltup   trend + vol x1.4         (ORB paradise / fade killer)

MC applies the same base derate as the real-data study (haircut 0.60) so
numbers are comparable, plus a raw (x1.0) pass on the pooled panel.
"""
import os
import sys
import json
import importlib.util
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location(
    "bk", "/home/z/my-project/scripts/02_backtest.py")
bk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bk)

DATA = "/home/z/my-project/download/data"
rng = np.random.default_rng(11)

WORLDS = [
    dict(name="calm",    vol_scale=0.70, ar=0.00, drift_sd=0.0004, trend_p=0.05, range_p=0.30),
    dict(name="normal",  vol_scale=1.00, ar=0.00, drift_sd=0.0006, trend_p=0.10, range_p=0.15),
    dict(name="wild",    vol_scale=1.40, ar=0.00, drift_sd=0.0009, trend_p=0.12, range_p=0.10),
    dict(name="choppy",  vol_scale=1.00, ar=-0.25, drift_sd=0.0003, trend_p=0.03, range_p=0.45),
    dict(name="trendy",  vol_scale=1.00, ar=0.22, drift_sd=0.0011, trend_p=0.30, range_p=0.05),
    dict(name="meltup",  vol_scale=1.40, ar=0.22, drift_sd=0.0013, trend_p=0.35, range_p=0.05),
]

def load_real(sym):
    return bk.load_rth(sym)

def gen_world(real, seed, n_days=120, vol_scale=1.0, ar=0.0, drift_sd=0.0006,
              trend_p=0.10, range_p=0.15, phi=0.55):
    """Donor-day bootstrap at 1h level -> synthetic 6-bar sessions.

    Bar 0: open = prev close x empirical overnight gap (real gap pool),
           body = donor 10:00-bar open->close move.
    Bars 1-5: open = prev close (continuity), body = donor open->close move.
    Wicks/volume keep the donor bar's shape, rescaled by the synthetic move.
    """
    r = np.random.default_rng(seed)
    days = [g for _, g in real.groupby("date") if len(g) == 6]
    # real overnight gap pool: log(10:00 open / prev 15:55 close)
    gaps, prev_c = [], None
    for _, g in real.groupby("date"):
        if prev_c is not None:
            gaps.append(np.log(float(g["Open"].iloc[0]) / prev_c))
        prev_c = float(g["Close"].iloc[-1])
    gaps = np.array([g for g in gaps if np.isfinite(g)])
    donors = []
    for g in days:
        o, h, l, c, v = (g[x].values for x in ["Open", "High", "Low", "Close", "Volume"])
        donors.append(dict(
            omv=np.log(c / o),
            wu=(h - np.maximum(o, c)) / np.maximum(h - l, 1e-9),
            wd=(np.minimum(o, c) - l) / np.maximum(h - l, 1e-9),
            rr=(h - l) / np.maximum(np.abs(c - o), 1e-9),
            v=v))
    start_date = pd.Timestamp("2101-01-05")
    rows = []
    px = float(real["Close"].iloc[-1])
    drift = 0.0
    for k in range(n_days):
        d = donors[int(r.integers(len(donors)))]
        mult = float(r.lognormal(0, 0.30)) * vol_scale
        trend_day = r.random() < trend_p
        range_day = (not trend_day) and r.random() < range_p
        drift = phi * drift + r.normal(0, drift_sd)
        if trend_day:
            drift = (1.0 if drift >= 0 else -1.0) * r.uniform(0.003, 0.008)
            mult *= 1.12
        if range_day:
            drift *= 0.15
            mult *= 0.72
        body = d["omv"] * mult * r.lognormal(0, 0.28, 6)
        if ar != 0.0:
            out = np.empty(6)
            out[0] = body[0]
            for i in range(1, 6):
                out[i] = ar * out[i - 1] + np.sqrt(1 - ar * ar) * body[i]
            body = out
        body = body + drift / 6.0
        gap = float(r.choice(gaps)) * mult * float(r.lognormal(0, 0.2))
        date = start_date + pd.Timedelta(days=int(k * 1.45))  # ~5 sess/week
        level = px
        for i in range(6):
            o = level * np.exp(gap) if i == 0 else level
            c = o * np.exp(body[i])
            mv_eff = abs(c / o - 1)
            rr = d["rr"][i] * float(r.lognormal(0, 0.15))
            rr = min(max(rr, 0.5), 12.0)
            rng_i = mv_eff * o * rr
            h = max(o, c) + d["wu"][i] * rng_i
            l = min(o, c) - d["wd"][i] * rng_i
            vol = d["v"][i] * mult * float(r.lognormal(0, 0.20))
            ts = (date + pd.Timedelta(hours=10 + i)).tz_localize("America/New_York")
            rows.append((ts, o, max(h, o, c, l), min(l, o, c), c, vol, date.normalize()))
            level = c
        px = level
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close",
                                     "Volume", "date"]).set_index("ts")
    cl = df["Close"]
    df["ema9"] = cl.ewm(span=9, adjust=False).mean()
    df["ema21"] = cl.ewm(span=21, adjust=False).mean()
    pc = cl.shift(1)
    tr = np.maximum(df["High"] - df["Low"],
                    np.maximum((df["High"] - pc).abs(), (df["Low"] - pc).abs()))
    df["atr14"] = tr.ewm(span=14, adjust=False).mean()
    return df

def run_backtest(bars, sym, strat, variant, p_over=None):
    """Returns {date: net_usd} for one strategy variant on a 6-bar session frame."""
    spec = bk.SPECS[sym]
    p = dict(bk.GRIDS[strat][variant])
    if strat == "ORB10":
        p["max_or_pts"] = float(bars["Close"].iloc[-1] * 0.004)
    if p_over:
        p.update(p_over)
    out = {}
    for d, g in bars.groupby("date"):
        g = g.copy()
        if strat == "ORB10":
            sigs = bk.sig_orb(g, p)
        elif strat == "VWAPFD":
            sigs = bk.sig_vwap(g, p)
        else:
            sigs = bk.sig_emapb(g, p)
        net = 0.0
        for t in bk.run_trades(g, sigs):
            gross, costs = bk.trade_pnl(t, spec)
            net += gross - costs
        out[d] = net
    return out

# ---------------- evaluation Monte Carlo (block-day sampling) ----------------

def firms():
    F = {}
    F["Apex 50K"] = dict(start=50_000, target=3000, dd=2500, min_days=7,
        eval_consistency=None, window=30, eval_fee=33.0, activation=99.0,
        monthly_fee=0.0, mode="onetime", dll=None, eval_cap=None,
        funded=dict(kind="apex", caps=[1500, 2000, 2500, 2500, 3000], qual=50,
                    qual_need=5, gap_need=8, split=1.0, pmin=500, proc=5))
    F["Apex 100K"] = dict(start=100_000, target=6000, dd=3000, min_days=7,
        eval_consistency=None, window=30, eval_fee=67.0, activation=137.0,
        monthly_fee=0.0, mode="onetime", dll=None, eval_cap=None,
        funded=dict(kind="apex", caps=[2000, 2500, 3000, 3000, 4000], qual=50,
                    qual_need=5, gap_need=8, split=1.0, pmin=500, proc=5))
    F["Topstep 50K"] = dict(start=50_000, target=3000, dd=2000, min_days=2,
        eval_consistency=0.55, window=None, eval_fee=49.0, activation=149.0,
        monthly_fee=75.0, mode="monthly", dll=1000.0, eval_cap="static55",
        funded=dict(kind="topstep", caps=[2000, 4000], win=150, win_need=5,
                    split=0.9, pmin=500, proc=3))
    F["Topstep 100K"] = dict(start=100_000, target=6000, dd=3000, min_days=2,
        eval_consistency=0.55, window=None, eval_fee=99.0, activation=149.0,
        monthly_fee=119.0, mode="monthly", dll=2000.0, eval_cap="static55",
        funded=dict(kind="topstep", caps=[3000, 6000], win=150, win_need=5,
                    split=0.9, pmin=500, proc=3))
    F["MFFU RapidEOD 50K"] = dict(start=50_000, target=3000, dd=2000, min_days=4,
        eval_consistency=0.30, window=30, eval_fee=125.0, activation=0.0,
        monthly_fee=0.0, mode="onetime", dll=None, eval_cap="dyn30",
        funded=dict(kind="mffu", buffer=2100, split=0.9, pmin=500, proc=1))
    F["MFFU Rapid 100K"] = dict(start=100_000, target=6000, dd=3000, min_days=2,
        eval_consistency=0.50, window=30, eval_fee=214.0, activation=0.0,
        monthly_fee=0.0, mode="onetime", dll=None, eval_cap="dyn50",
        funded=dict(kind="mffu", buffer=3100, split=0.9, pmin=500, proc=1))
    F["TPT 50K"] = dict(start=50_000, target=3000, dd=2000, min_days=5,
        eval_consistency=0.50, window=None, eval_fee=170.0, activation=130.0,
        monthly_fee=170.0, mode="monthly", dll=None, eval_cap="dyn50",
        funded=dict(kind="tpt", buffer=2000, extra=600, split=0.8, pmin=100, proc=1))
    F["TPT 100K"] = dict(start=100_000, target=6000, dd=3000, min_days=5,
        eval_consistency=0.50, window=None, eval_fee=330.0, activation=130.0,
        monthly_fee=330.0, mode="monthly", dll=None, eval_cap="dyn50",
        funded=dict(kind="tpt", buffer=3000, extra=600, split=0.8, pmin=100, proc=1))
    return F

SIZING = {50_000: (2, 4, 400.0), 100_000: (3, 6, 600.0)}

class Panel:
    def __init__(self, arr, traded):
        self.p = arr
        self.traded = traded
        self.n = len(arr)

def sim_eval(cfg, panel, base_c, scaled_c, dstop, scale_level, r):
    target, dd, start = cfg["target"], cfg["dd"], cfg["start"]
    floor = start - dd
    bal = start
    cum = 0.0
    tdays = 0
    cal = 0.0
    max_cal = cfg["window"] * 1.45 if cfg["window"] else 600
    pos = int(r.integers(0, panel.n))
    block = 0
    while cal < max_cal:
        if block <= 0:
            block = int(r.integers(3, 10))
            pos = int(r.integers(0, panel.n))
        i = pos % panel.n
        pos += 1
        block -= 1
        if not panel.traded[i]:
            cal += 1
            continue
        n = base_c if cum < scale_level else scaled_c
        pnl = panel.p[i] * n
        pnl = max(pnl, -dstop)
        if cfg["dll"]:
            pnl = max(pnl, -cfg["dll"])
        cap = cfg["eval_cap"]
        if cap == "static55":
            pnl = min(pnl, 0.55 * target)
        elif cap == "dyn30":
            pnl = min(pnl, max(0.30 * cum if cum > 0 else 0.0, 0.2 * target))
        elif cap == "dyn50":
            pnl = min(pnl, max(0.50 * cum if cum > 0 else 0.0, 0.25 * target))
        bal += pnl
        cum = bal - start
        tdays += 1
        cal += 1.45
        floor = min(max(floor, bal - dd), start)
        if bal <= floor:
            return dict(passed=False, cal=cal)
        if cum >= target and tdays >= cfg["min_days"]:
            return dict(passed=True, cal=cal)
    return dict(passed=False, cal=cal)

def sim_funded(cfg, panel, base_c, scaled_c, dstop, scale_level, r, max_cal=400):
    f = cfg["funded"]
    start, dd = cfg["start"], cfg["dd"]
    floor = start - dd
    bal = start
    cum = 0.0
    cal = 0.0
    tdays_sp = 0
    qual = 0
    wins = 0
    payouts = 0
    pos = int(r.integers(0, panel.n))
    block = 0
    while cal < max_cal:
        if block <= 0:
            block = int(r.integers(3, 10))
            pos = int(r.integers(0, panel.n))
        i = pos % panel.n
        pos += 1
        block -= 1
        if not panel.traded[i]:
            cal += 1
            continue
        n = base_c if cum < scale_level else scaled_c
        pnl = panel.p[i] * n
        pnl = max(pnl, -dstop)
        if f["kind"] == "apex" and payouts >= 1 and pnl > 0:
            pnl = min(pnl, max(0.5 * cum, 0.2 * cfg["target"]))
        bal += pnl
        cum = bal - start
        cal += 1.45
        tdays_sp += 1
        if pnl >= 50:
            qual += 1
        if pnl >= f.get("win", 10 ** 9):
            wins += 1
        if f["kind"] in ("apex", "mffu"):
            floor = min(max(floor, bal - dd), start + 100)
        elif f["kind"] == "tpt":
            floor = min(max(floor, bal - dd), start)
        elif f["kind"] == "topstep":
            floor = start if payouts >= 1 else min(max(floor, bal - dd), start)
        if bal <= floor:
            return dict(cal=cal, cash=0.0, busted=True)
        can = False
        if f["kind"] == "apex":
            can = qual >= f["qual_need"] and tdays_sp >= f["gap_need"]
        elif f["kind"] == "topstep":
            can = wins >= f["win_need"] and (cum >= 0.9 * f["caps"][0] or cal >= 30)
        elif f["kind"] == "mffu":
            can = cum >= f["buffer"]
        elif f["kind"] == "tpt":
            can = cum >= f["buffer"] + f["extra"]
        if not can:
            continue
        if f["kind"] in ("apex", "topstep"):
            cap_ = f["caps"][min(payouts, len(f["caps"]) - 1)]
            amt = (cum - 100) if f["kind"] == "apex" else cum
            amt = min(amt, cap_)
        else:
            amt = cum - f["buffer"]
        if amt < f["pmin"]:
            continue
        cash = amt * f["split"]
        payouts += 1
        return dict(cal=cal + f["proc"], cash=cash, busted=False)
    return dict(cal=cal, cash=0.0, busted=False)

def run_config(cfg, panel, n_trials=4000, seed=0):
    r = np.random.default_rng(seed)
    base_c, scaled_c, dstop = SIZING[cfg["start"]]
    scale_level = 0.5 * cfg["target"]
    passed = got_cash = 0
    att, ev_cal, tot_cal, costs, payouts_ = [], [], [], [], []
    for t in range(n_trials):
        fees = 0.0
        attempts = 0
        ok = False
        ec = 0.0
        for attempt in range(1, 13):
            attempts += 1
            fees += cfg["eval_fee"] if (cfg["mode"] == "onetime" or attempt == 1) \
                else cfg["monthly_fee"]
            res = sim_eval(cfg, panel, base_c, scaled_c, dstop, scale_level, r)
            if res["passed"]:
                ok = True
                ec = res["cal"]
                break
        if not ok:
            costs.append(fees)
            tot_cal.append(np.nan)
            payouts_.append(0.0)
            continue
        passed += 1
        fees += cfg["activation"]
        if cfg["mode"] == "monthly":
            fees += cfg["monthly_fee"] * max(0, int(np.ceil(ec / 30.4)) - 1)
        fr = sim_funded(cfg, panel, base_c, scaled_c, dstop, scale_level, r)
        passed_fee = fees
        costs.append(passed_fee)
        got_cash += (not fr["busted"] and fr["cash"] > 0)
        tot_cal.append(ec + fr["cal"] if not fr["busted"] else np.nan)
        payouts_.append(fr["cash"])
        att.append(attempts)
        ev_cal.append(ec)
    return dict(
        pass_rate=round(passed / n_trials * 100, 1),
        p_payout=round(got_cash / n_trials * 100, 1),
        exp_attempts=round(float(np.mean(att)) if att else 0, 2),
        exp_eval_cal_days=round(float(np.mean(ev_cal)) if ev_cal else 0, 0),
        med_days_to_cash=round(float(np.nanmedian(tot_cal)), 0),
        exp_cost=round(float(np.mean(costs)), 0),
        exp_payout=round(float(np.mean(payouts_)), 0),
    )

# ---------------- main ----------------

def main():
    F = firms()
    real = {sym: load_real(sym) for sym in ["ES", "NQ"]}

    # realism check: real vs neutral synthetic
    print("=== realism check (daily stats, 6-bar sessions) ===")
    for sym in ["ES", "NQ"]:
        rth = real[sym]
        g = rth.groupby("date")
        rr = (g["Close"].last() / g["Open"].first() - 1).dropna()
        rng_d = (g["High"].max() - g["Low"].min())
        synth = gen_world(rth, seed=101, n_days=120,
                          **{k: v for k, v in WORLDS[1].items() if k != "name"})
        gs = synth.groupby("date")
        rr_s = (gs["Close"].last() / gs["Open"].first() - 1).dropna()
        rng_s = (gs["High"].max() - gs["Low"].min())
        cl = rth["Close"]; cls = synth["Close"]
        ac_r = cl.pct_change().autocorr(lag=1)
        ac_s = cls.pct_change().autocorr(lag=1)
        print(f"{sym}: real sd={rr.std()*100:.3f}% range={rng_d.mean():.1f} ac1={ac_r:+.3f} up%={(rr>0).mean()*100:.0f} | "
              f"synth sd={rr_s.std()*100:.3f}% range={rng_s.mean():.1f} ac1={ac_s:+.3f} up%={(rr_s>0).mean()*100:.0f}")

    # real-data strategy reference stats (same executor, chronological data)
    dp = pd.read_csv(f"{DATA}/daily_pnl.csv")
    dpiv = dp.pivot_table(index=["strat", "variant", "date"], columns="sym",
                          values="net", aggfunc="sum").fillna(0.0)
    dpiv["tot"] = dpiv.get("ES", 0.0) + dpiv.get("NQ", 0.0)
    print("\n=== real-data reference ($/day @1 micro, ES+NQ, IS+OOS pooled) ===")
    for (strat, variant), g in dpiv.groupby(["strat", "variant"]):
        act = g["tot"]
        print(f"real    {strat:7} {variant:<2} {act.mean():>10.1f} {(act > 0).mean()*100:>8.1f}% {len(act):>5}")

    # strategy panels per world (cached: generation is the expensive part)
    cache_path = f"{DATA}/synth_panels_cache.npz"
    panels = {}
    strat_stats = []
    if os.path.exists(cache_path):
        z = np.load(cache_path, allow_pickle=True)
        for k in z.files:
            if k.endswith("|p"):
                base = k[:-2]
                w, s, v = base.split("|")
                panels[(w, s, int(v))] = Panel(z[k], z[base + "|t"])
        print(f"(panels loaded from cache: {len(panels)})")
    else:
        for w in WORLDS:
            wname = w["name"]
            kwargs = {k: v for k, v in w.items() if k != "name"}
            per_strat = {}
            for sym in ["ES", "NQ"]:
                bars = gen_world(real[sym], seed=hash(wname + sym) % 2**31,
                                 n_days=120, **kwargs)
                for strat in ["ORB10", "VWAPFD", "EMAPB"]:
                    for variant in [0, 1]:
                        res = run_backtest(bars, sym, strat, variant)
                        for d, net in res.items():
                            key = (strat, variant, d)
                            es, nq, n = per_strat.get(key, (0.0, 0.0, 0))
                            if sym == "ES":
                                es += net
                            else:
                                nq += net
                            per_strat[key] = (es, nq, n + 1)
            for strat in ["ORB10", "VWAPFD", "EMAPB"]:
                for variant in [0, 1]:
                    keys = sorted(k for k in per_strat
                                  if k[0] == strat and k[1] == variant)
                    vals = np.array([per_strat[k][0] + per_strat[k][1] for k in keys])
                    traded = np.array([per_strat[k][2] > 0 for k in keys])
                    panels[(wname, strat, variant)] = Panel(vals, traded)
        with open(cache_path, "wb") as fh:
            np.savez_compressed(
                fh,
                **{f"{k[0]}|{k[1]}|{k[2]}|p": v.p for k, v in panels.items()},
                **{f"{k[0]}|{k[1]}|{k[2]}|t": v.traded for k, v in panels.items()})
        print(f"(panels generated and cached: {len(panels)})")
    for (wname, strat, variant), panel in sorted(panels.items()):
        act = panel.p[panel.traded]
        if len(act):
            strat_stats.append(dict(
                world=wname, strat=strat, variant=variant,
                mean_day=round(float(act.mean()), 1),
                win_day=round(float((act > 0).mean() * 100), 1),
                n_traded=int(panel.traded.sum())))

    ss = pd.DataFrame(strat_stats)
    ss.to_csv(f"{DATA}/synthetic_strategy_stats.csv", index=False)
    print("\n=== strategy raw performance on synthetic worlds ($/day @1 micro, ES+NQ) ===")
    print(f"{'world':8} {'strat':7} {'v':2} {'mean$/day':>10} {'win-day%':>9} {'days':>5}")
    for _, row in ss.iterrows():
        print(f"{row['world']:8} {row['strat']:7} {row['variant']:<2} {row['mean_day']:>10.1f} "
              f"{row['win_day']:>8.1f}% {row['n_traded']:>5}")

    # pick best variant per strategy by pooled mean across worlds
    best_variant = {}
    for strat in ["ORB10", "VWAPFD", "EMAPB"]:
        scores = []
        for variant in [0, 1]:
            means = [panels[(w["name"], strat, variant)].p[panels[(w["name"], strat, variant)].traded].mean()
                     if panels[(w["name"], strat, variant)].traded.any() else -1e9
                     for w in WORLDS]
            scores.append(float(np.mean(means)))
        best_variant[strat] = int(np.argmax(scores))
    print("\nbest variant per strategy:", best_variant)

    # pooled panel across worlds (sequence bootstrap across regimes)
    pooled = {}
    for strat in ["ORB10", "VWAPFD", "EMAPB"]:
        v = best_variant[strat]
        vals = np.concatenate([panels[(w["name"], strat, v)].p for w in WORLDS])
        trd = np.concatenate([panels[(w["name"], strat, v)].traded for w in WORLDS])
        pooled[strat] = Panel(vals, trd)

    # MC: per world x firm x strategy (base derate 0.60, comparable to real-data study)
    out = {"per_world": {}, "pooled": {}, "pooled_raw": {}}
    print("\n=== eval MC on synthetic worlds (derate x0.60) ===")
    hdr = f"{'world':8} {'strat':7} {'firm':20} {'pass%':>6} {'cash%':>6} {'att':>5} {'evalD':>6} {'medD':>5} {'fees$':>7} {'payout$':>8}"
    print(hdr)
    for w in WORLDS:
        wname = w["name"]
        for strat in ["ORB10", "VWAPFD", "EMAPB"]:
            v = best_variant[strat]
            panel_raw = panels[(wname, strat, v)]
            panel = Panel(panel_raw.p * 0.60, panel_raw.traded)
            for name, cfg in F.items():
                cfg["_name"] = name
                seed = abs(hash((wname, strat, name))) % 2**31
                res = run_config(cfg, panel, n_trials=3000, seed=seed)
                res.update(world=wname, strat=strat, firm=name)
                out["per_world"].setdefault(strat, []).append(res)
                print(f"{wname:8} {strat:7} {name:20} {res['pass_rate']:>5.1f} {res['p_payout']:>5.1f} "
                      f"{res['exp_attempts']:>5.2f} {res['exp_eval_cal_days']:>6.0f} {res['med_days_to_cash']:>5.0f} "
                      f"{res['exp_cost']:>7.0f} {res['exp_payout']:>8.0f}")

    # pooled MC (derated + raw) with more trials
    for tag, derate, ntr in [("pooled", 0.60, 8000), ("pooled_raw", 1.00, 8000)]:
        print(f"\n=== pooled MC across all 6 synthetic worlds (derate x{derate}) ===")
        print(f"{'strat':7} {'firm':20} {'pass%':>6} {'cash%':>6} {'att':>5} {'evalD':>6} {'medD':>5} {'fees$':>7} {'payout$':>8}")
        for strat in ["ORB10", "VWAPFD", "EMAPB"]:
            v = best_variant[strat]
            pr = pooled[strat]
            panel = Panel(pr.p * derate, pr.traded)
            for name, cfg in F.items():
                seed = abs(hash((tag, strat, name))) % 2**31
                res = run_config(cfg, panel, n_trials=ntr, seed=seed)
                res.update(strat=strat, firm=name)
                out[tag].setdefault(strat, []).append(res)
                print(f"{strat:7} {name:20} {res['pass_rate']:>5.1f} {res['p_payout']:>5.1f} "
                      f"{res['exp_attempts']:>5.2f} {res['exp_eval_cal_days']:>6.0f} {res['med_days_to_cash']:>5.0f} "
                      f"{res['exp_cost']:>7.0f} {res['exp_payout']:>8.0f}")

    with open(f"{DATA}/synthetic_results.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nsaved synthetic_results.json")

if __name__ == "__main__":
    main()
