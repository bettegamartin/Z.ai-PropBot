"""Prop-firm evaluation + payout Monte Carlo simulator (v3 - final).

Fixes vs v2:
  * Monthly-renewal fees accrued DURING the passing attempt (Topstep/TPT).
  * MFFU 100K = Rapid plan (Rapid EOD only exists 25K/50K); funded stage
    switches to intraday trailing (approximated at daily granularity).
  * Apex payout only above safety net (start+100).
  * TPT waits for >= $600 profit above buffer before withdrawing (80%).
  * Static consistency caps: Topstep best-day < 55% of target; MFFU Rapid EOD
    30% / Rapid 50% dynamic-with-floor; TPT 50% dynamic-with-floor.
  * Commission tally added (commissions are inside net P&L; reported for cost
    transparency).
Scenarios: base (haircut x0.60) and stress (haircut x0.35) on backtest day P&L
with block-bootstrap sampling and per-day execution noise.
"""
import json
import numpy as np
import pandas as pd

DATA = "/home/z/my-project/download/data"
rng = np.random.default_rng(7)

dp = pd.read_csv(f"{DATA}/daily_pnl.csv")
dp = dp[(dp["strat"] == "VWAPFD") & (dp["variant"] == 1)]
piv = dp.pivot_table(index="date", columns="sym", values="net", aggfunc="sum").fillna(0.0)
piv["tot"] = piv["ES"] + piv["NQ"]
day_pnl = piv["tot"].values
traded_days = (piv[["ES", "NQ"]].abs().sum(axis=1) > 0).values
n_trades_per_day = dp.groupby("date")["n"].sum().reindex(piv.index).fillna(0.0)
n_mean_traded = float(n_trades_per_day[traded_days].mean())
n_days = len(day_pnl)
COMM_PER_TRADE = 2.05  # avg MES/NQ per-side comm+slip already in P&L; tally for reporting
print(f"portfolio day P&L: n={n_days}, mean ${day_pnl.mean():.2f}, sd ${day_pnl.std():.2f}, "
      f"win-day {(day_pnl>0).mean()*100:.1f}%, trades/traded-day {n_mean_traded:.2f}")

def degraded_panel(haircut, noise_frac, noise_floor):
    noise_sd = np.maximum(noise_floor, noise_frac * np.abs(day_pnl))
    return day_pnl * haircut + rng.normal(0, noise_sd)

SCENARIOS = {
    "base":   dict(haircut=0.60, noise_frac=0.20, noise_floor=25.0),
    "stress": dict(haircut=0.35, noise_frac=0.30, noise_floor=40.0),
}

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
                    split=1.0, pmin=500, proc=3))
    F["Topstep 100K"] = dict(start=100_000, target=6000, dd=3000, min_days=2,
        eval_consistency=0.55, window=None, eval_fee=99.0, activation=149.0,
        monthly_fee=119.0, mode="monthly", dll=2000.0, eval_cap="static55",
        funded=dict(kind="topstep", caps=[3000, 6000], win=150, win_need=5,
                    split=1.0, pmin=500, proc=3))
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

def sim_eval(cfg, panel, base_c, scaled_c, dstop, scale_level):
    target, dd, start = cfg["target"], cfg["dd"], cfg["start"]
    floor = start - dd
    bal = start
    cum = 0.0
    tdays = 0
    cal = 0.0
    max_cal = cfg["window"] * 1.45 if cfg["window"] else 600
    while cal < max_cal:
        i = int(rng.integers(0, n_days))
        if not traded_days[i]:
            cal += 1
            continue
        n = base_c if cum < scale_level else scaled_c
        pnl = panel[i] * n
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
        floor = min(max(floor, bal - dd), start)  # EOD trail, locks at start
        if bal <= floor:
            return dict(passed=False, cal=cal, bust=True)
        if cum >= target and tdays >= cfg["min_days"]:
            return dict(passed=True, cal=cal, bust=False)
    return dict(passed=False, cal=cal, bust=False)

def sim_funded(cfg, panel, base_c, scaled_c, dstop, scale_level, max_cal=400):
    f = cfg["funded"]
    start, dd = cfg["start"], cfg["dd"]
    floor = start - dd
    bal = start
    cum = 0.0
    cal = 0.0
    tdays_since_payout = 0
    qual = 0
    wins = 0
    payouts = 0
    while cal < max_cal:
        i = int(rng.integers(0, n_days))
        if not traded_days[i]:
            cal += 1
            continue
        n = base_c if cum < scale_level else scaled_c
        pnl = panel[i] * n
        pnl = max(pnl, -dstop)
        if f["kind"] == "apex" and payouts >= 1 and pnl > 0:
            pnl = min(pnl, max(0.5 * cum, 0.2 * cfg["target"]))
        bal += pnl
        cum = bal - start
        cal += 1.45
        tdays_since_payout += 1
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
            return dict(cal=cal, cash=0.0, busted=True, payouts=payouts)
        can = False
        if f["kind"] == "apex":
            can = qual >= f["qual_need"] and tdays_since_payout >= f["gap_need"]
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
        elif f["kind"] == "mffu":
            amt = cum - f["buffer"]
        else:
            amt = cum - f["buffer"]
        if amt < f["pmin"]:
            continue
        cash = amt * f["split"]
        bal -= amt
        payouts += 1
        return dict(cal=cal + f["proc"], cash=cash, busted=False, payouts=payouts)
    return dict(cal=cal, cash=0.0, busted=False, payouts=payouts)

def run_config(cfg, panel, n_trials=20_000):
    base_c, scaled_c, dstop = SIZING[cfg["start"]]
    scale_level = 0.5 * cfg["target"]
    passed = 0
    got_cash = 0
    att, ev_cal, tot_cal, costs, payouts_, nets, comm_ = [], [], [], [], [], [], []
    for _ in range(n_trials):
        fees = 0.0
        attempts = 0
        ok = False
        eval_cal = 0.0
        for attempt in range(1, 13):
            attempts += 1
            if cfg["mode"] == "onetime":
                fees += cfg["eval_fee"]
            else:
                fees += cfg["eval_fee"] if attempt == 1 else cfg["monthly_fee"]
            r = sim_eval(cfg, panel, base_c, scaled_c, dstop, scale_level)
            if r["passed"]:
                ok = True
                eval_cal = r["cal"]
                break
        if not ok:
            costs.append(fees)
            tot_cal.append(np.nan)
            payouts_.append(0.0)
            nets.append(np.nan)
            comm_.append(0.0)
            continue
        passed += 1
        fees += cfg["activation"]
        if cfg["mode"] == "monthly":
            extra_months = max(0, int(np.ceil(eval_cal / 30.4)) - 1)
            fees += cfg["monthly_fee"] * extra_months
        fr = sim_funded(cfg, panel, base_c, scaled_c, dstop, scale_level)
        est_comm = ((eval_cal + fr["cal"]) / 1.45) * n_mean_traded * COMM_PER_TRADE
        costs.append(fees)
        got_cash += (not fr["busted"] and fr["cash"] > 0)
        tot_cal.append(eval_cal + fr["cal"] if not fr["busted"] else np.nan)
        payouts_.append(fr["cash"])
        nets.append(fr["cash"] - fees if not fr["busted"] else np.nan)
        att.append(attempts)
        ev_cal.append(eval_cal)
        comm_.append(est_comm)
    res = dict(
        firm=cfg["_name"],
        pass_rate=round(passed / n_trials * 100, 1),
        p_payout=round(got_cash / n_trials * 100, 1),
        exp_attempts=round(float(np.mean(att)), 2) if att else None,
        p90_attempts=round(float(np.percentile(att, 90)), 1) if att else None,
        exp_eval_cal_days=round(float(np.mean(ev_cal)), 0) if ev_cal else None,
        exp_days_to_cash=round(float(np.nanmean(tot_cal)), 0),
        med_days_to_cash=round(float(np.nanmedian(tot_cal)), 0),
        p90_days_to_cash=round(float(np.nanpercentile(tot_cal, 90)), 0),
        exp_cost=round(float(np.mean(costs)), 0),
        exp_commissions=round(float(np.mean(comm_)), 0),
        exp_payout=round(float(np.mean(payouts_)), 0),
        exp_net_cash=round(float(np.nanmean(nets)), 0),
        med_net_cash=round(float(np.nanmedian(nets)), 0),
    )
    return res

if __name__ == "__main__":
    F = firms()
    out = {}
    for scen, sc in SCENARIOS.items():
        panel = degraded_panel(sc["haircut"], sc["noise_frac"], sc["noise_floor"])
        rows = []
        for name, cfg in F.items():
            cfg["_name"] = name
            rows.append(run_config(cfg, panel))
        out[scen] = rows
        print(f"\n===== SCENARIO: {scen} (haircut x{sc['haircut']}) =====")
        print(f"{'FIRM':22} {'P(pass)':>8} {'P(cash)':>8} {'E[att]':>7} {'E[evalD]':>9} "
              f"{'E[D->cash]':>11} {'med':>5} {'p90':>5} {'E[fees]':>8} {'E[comm]':>8} {'E[payout]':>10} {'E[net]':>8}")
        for r in rows:
            print(f"{r['firm']:22} {r['pass_rate']:>7.1f}% {r['p_payout']:>7.1f}% "
                  f"{r['exp_attempts']:>7} {r['exp_eval_cal_days']:>9.0f} "
                  f"{r['exp_days_to_cash']:>11.0f} {r['med_days_to_cash']:>5.0f} {r['p90_days_to_cash']:>5.0f} "
                  f"${r['exp_cost']:>7.0f} ${r['exp_commissions']:>7.0f} ${r['exp_payout']:>9.0f} ${r['exp_net_cash']:>7.0f}")
    with open(f"{DATA}/sim_results.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nsaved sim_results.json")
