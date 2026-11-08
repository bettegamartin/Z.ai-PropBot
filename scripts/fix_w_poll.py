"""Run ONE canned live poll in a fresh process (fix-verification bench wrapper).

Usage: python3 scripts/fix_w_poll.py <home> <poll_key>
Exercises the REAL run_live path: flock, state load/save, journal flush,
EOD summary at >=16:00 polls.
"""
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import fix_scen as scen

home, key = sys.argv[1], sys.argv[2]
ps.set_paths(home)
now, fetch = scen.get_poll(key)
ps.run_live(now=now, fetch=fetch)
