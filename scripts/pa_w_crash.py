"""Same as pa_w_poll but CRASHES right after the journal rows hit disk,
before the state save (audit bench wrapper for the crash-recovery probe).

Usage: python3 scripts/pa_w_crash.py <home> <poll_key>
"""
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import post_audit_scen as scen

home, key = sys.argv[1], sys.argv[2]
ps.set_paths(home)

real = ps.flush_journal_rows


def crashing(path, rows):
    r = real(path, rows)
    print(f"CRASH INJECTED: {r[0]} row(s) written to {path}; dying before state save")
    raise RuntimeError("INJECTED CRASH after journal write")


ps.flush_journal_rows = crashing
now, fetch = scen.get_poll(key)
try:
    ps.run_live(now=now, fetch=fetch)
except RuntimeError as e:
    if "INJECTED CRASH" not in str(e):
        raise
    print("process died as designed (journal durable, state stale)")
