"""COLD-RELOAD halt probe (audit bench wrapper).

Usage: python3 scripts/pa_w_cold.py <home> <delete_flag: 0|1>

Loads state.json from disk in a FRESH process, feeds one crafted signal bar
(ES 2026-11-06 12:00 stamp, quote 6044) through the real bar_step live path,
prints ENTRY or BLOCKED, and saves the state back to disk.
"""
import sys
from datetime import datetime

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import post_audit_scen as scen

home = sys.argv[1]
ps.set_paths(home)
st = ps.load_state()
if len(sys.argv) > 2 and sys.argv[2] == "1":
    st.pop("halted_date", None)          # simulate lost flag; live condition must still block
    ps.atomic_json(ps.STATE_F, st)
    st = ps.load_state()

ts = datetime.fromisoformat("2026-11-06T12:00:00-05:00")
bar = (*scen.ES_FRI[2], ts)
now_ts = datetime.fromisoformat("2026-11-06T13:20:00-05:00")
quote = dict(px=6044.0, ts=ts, method="poll_quote")
ps.bar_step("ES", bar, st, "live", now_ts=now_ts, quote=quote)
d = st["day"]["ES"]
if d.get("pos"):
    print(f"RESULT: ENTRY opened at {d['pos']['entry_px']} (id {d['pos'].get('trade_id')})")
else:
    print(f"RESULT: BLOCKED (trades={d['trades']}, halted={d.get('halted')}, "
          f"halted_date={st.get('halted_date')})")
ps.atomic_json(ps.STATE_F, st)
