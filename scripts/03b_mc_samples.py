"""Export Monte Carlo raw samples (base scenario) for distribution charts."""
import sys, json
import numpy as np

sys.path.insert(0, "/home/z/my-project/scripts")
from importlib import import_module
sim = import_module("03_sim_eval")

DATA = "/home/z/my-project/download/data"
panel = sim.degraded_panel(0.60, 0.20, 25.0)
F = sim.firms()

out = {}
for name in ["Topstep 50K", "Apex 50K", "MFFU RapidEOD 50K", "Topstep 100K"]:
    cfg = F[name]
    cfg["_name"] = name
    days, cash, fees = [], [], []
    for _ in range(12000):
        base_c, scaled_c, dstop = sim.SIZING[cfg["start"]]
        scale_level = 0.5 * cfg["target"]
        fees_tot, ok, eval_cal = 0.0, False, 0.0
        for attempt in range(1, 13):
            fees_tot += cfg["eval_fee"] if cfg["mode"] == "onetime" or attempt == 1 else cfg["monthly_fee"]
            r = sim.sim_eval(cfg, panel, base_c, scaled_c, dstop, scale_level)
            if r["passed"]:
                ok, eval_cal = True, r["cal"]
                break
        if not ok:
            continue
        fees_tot += cfg["activation"]
        if cfg["mode"] == "monthly":
            fees_tot += cfg["monthly_fee"] * max(0, int(np.ceil(eval_cal / 30.4)) - 1)
        fr = sim.sim_funded(cfg, panel, base_c, scaled_c, dstop, scale_level)
        if fr["busted"] or fr["cash"] <= 0:
            continue
        days.append(eval_cal + fr["cal"])
        cash.append(fr["cash"])
        fees.append(fees_tot)
    out[name] = dict(days=[round(d) for d in days], cash=[round(c, 2) for c in cash],
                     fees=[round(f, 2) for f in fees])
    print(f"{name}: n={len(days)}, med days={np.median(days):.0f}, med cash=${np.median(cash):.0f}")

json.dump(out, open(f"{DATA}/mc_samples.json", "w"))
print("saved mc_samples.json")
