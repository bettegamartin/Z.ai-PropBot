"""Run ONE canned live poll in a fresh process (audit bench wrapper).

Usage: python3 scripts/pa_w_poll.py <home> <poll_key> [sleep_s]
Exercises the REAL run_live path: flock, state load/save, journal flush.
"""
import os
import sys
import time

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import post_audit_scen as scen

home, key = sys.argv[1], sys.argv[2]
if len(sys.argv) > 3:
    time.sleep(float(sys.argv[3]))
ps.set_paths(home)
now, fetch = scen.get_poll(key)
delay = float(os.environ.get("PA_FETCH_DELAY", "0"))
if delay:
    base = fetch
    def slow_fetch(yh, want_quote=False):
        time.sleep(delay)          # sleeps INSIDE run_live's locked region
        return base(yh, want_quote=want_quote)
    fetch = slow_fetch
ps.run_live(now=now, fetch=fetch)
