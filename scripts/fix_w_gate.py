"""COLD-RELOAD cap-gate probe (fix-verification bench wrapper).

Usage: python3 scripts/fix_w_gate.py <home> <variant>

Variants (state.json must already be seeded by the bench):
  cond   -- cap_blocked_date DELETED, day P&L 1700  -> live condition must block
  flag   -- cap_blocked_date = today, day P&L 1000  -> persisted flag must block
  reset  -- cap_blocked_date = 2026-11-06 (past), day P&L 200 -> entry allowed
  apex   -- topstep overlay done, day P&L 1700      -> no cap, entry allowed

Loads state with the POLL date pinned (as run_live now does), feeds the full
three-bar Nov-9 setup (10:00, 11:00, 12:00 -- so len(devs)>=3 holds and the
12:00 bar genuinely signals) through the real bar_step live path, prints
RESULT: ENTRY / RESULT: BLOCKED, and saves the state back to disk.

The reset/apex variants entering while cond/flag block (identical bars) is
the differential proof that the CAP GATE -- not some other condition -- is
the blocker.
"""
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import fix_scen as scen

home, variant = sys.argv[1], sys.argv[2]
ps.set_paths(home)
POLL_DATE = "2026-11-09"
st = ps.load_state(today=POLL_DATE)
if variant == "cond":
    st.pop("cap_blocked_date", None)      # simulate lost flag; condition must block
elif variant == "flag":
    st["cap_blocked_date"] = POLL_DATE    # flag alone blocks (day P&L 1000 < cap)
elif variant == "reset":
    st["cap_blocked_date"] = "2026-11-06" # stale flag from a past day is inert
elif variant == "apex":
    st["overlays"]["topstep"]["done"] = True
ps.atomic_json(ps.STATE_F, st)
st = ps.load_state(today=POLL_DATE)

for i, ts_iso in enumerate(scen.ES_MON_STAMPS):
    from datetime import datetime
    ts = datetime.fromisoformat(ts_iso)
    ps.bar_step("ES", (*scen.ES_MON[i], ts), st, "live", now_ts=scen.GATE_NOW,
                quote=dict(px=scen.GATE_QUOTE_PX, ts=ts, method="poll_quote"))
d = st["day"]["ES"]
if d.get("pos"):
    print(f"RESULT: ENTRY opened at {d['pos']['entry_px']} (id {d['pos'].get('trade_id')})")
else:
    print(f"RESULT: BLOCKED (trades={d['trades']}, cap_date={st.get('cap_blocked_date')}, "
          f"halted_date={st.get('halted_date')}, cap={ps.consistency_cap_usd(st)})")
ps.atomic_json(ps.STATE_F, st)
