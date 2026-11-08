"""POST-AUDIT FIX VERIFICATION BENCH (fix stage, 2026-11-05).

Verifies the five residual findings of reports/vwap_fade_post_remediation_audit.md
(N1 bot-side daily profit cap, N2 day-scoped accounting, N3 idempotent EOD
diary write, N4 reachable replay abort guard, N5 remediate_data re-run safety)
plus the cosmetic items, and re-runs both earlier suites as regression.

Every probe drives the engine through its real entry points (run_live /
bar_step / overlay_update / eod_summary / run_replay) with isolated homes
under fixes_workspace/. Crafted data: scripts/fix_scen.py (numbers chosen
independently of both earlier benches). The live home download/paper_trading/
is NEVER written by this bench (F5 uses byte-identical COPIES of it).

Safety: no network calls; the production Tradovate/NT8 bot is unreachable
and untouched.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import fix_scen as scen

ROOT = "/home/z/my-project"
WS = f"{ROOT}/fixes_workspace"
LIVE = f"{ROOT}/download/paper_trading"
RESULTS = {}
ORDER = []
CUR = None


def check(name, cond, detail=""):
    RESULTS.setdefault(CUR, {"status": "PASS", "items": []})
    RESULTS[CUR]["items"].append([bool(cond), name, str(detail)])
    if not cond:
        RESULTS[CUR]["status"] = "FAIL"
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))


def home(name):
    h = f"{WS}/{name}"
    shutil.rmtree(h, ignore_errors=True)
    os.makedirs(h, exist_ok=True)
    return h


def rows_of(path):
    if not os.path.exists(path):
        return []
    lines = [l for l in open(path).read().splitlines() if l.strip()]
    return lines[1:] if lines else []


def journal_dicts(path):
    if not os.path.exists(path):
        return []
    lines = [l for l in open(path).read().splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    header = lines[0].split(",")
    return [dict(zip(header, l.split(","))) for l in lines[1:]]


def diary(home_):
    p = f"{home_}/log.md"
    return open(p).read() if os.path.exists(p) else ""


def state_of(home_):
    with open(f"{home_}/state.json") as fh:
        return json.load(fh)


def run_wrapper(script, *args, env_extra=None, timeout=300):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, f"{ROOT}/scripts/{script}", *map(str, args)],
        capture_output=True, text=True, timeout=timeout, cwd=ROOT, env=env)


def snap(home_):
    out = {}
    if not os.path.isdir(home_):
        return out
    for f in sorted(os.listdir(home_)):
        if f == ".lock":
            continue
        p = f"{home_}/{f}"
        if os.path.isfile(p):
            out[f] = hashlib.md5(open(p, "rb").read()).hexdigest()
    return out


def close_float(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


def seed_state(h, *, today, day, cum=0.0, traded=True, cap_date=None, cap_reason=None,
               overlays_extra=None):
    """Write a seeded v2 state.json into home h."""
    ps.set_paths(h)
    st = ps.fresh_state()
    st["today"] = today
    st["day"] = day
    st["cum"] = cum
    st["traded_today"] = traded
    st["cap_blocked_date"] = cap_date
    st["cap_blocked_reason"] = cap_reason
    if overlays_extra:
        for k, upd in overlays_extra.items():
            st["overlays"][k].update(upd)
    ps.atomic_json(ps.STATE_F, st)
    return st


# ---------------------------------------------------------------- tests

def f1_n1_daily_profit_cap():
    """N1: the research's bot-side daily profit cap (Table 8, $1,650 Topstep)."""
    # --- organic: a real close pushes the day past the cap; gate fires same run
    h = home("f1_organic")
    r1 = run_wrapper("fix_w_poll.py", h, "P1")
    check("P1 poll exits 0", r1.returncode == 0, r1.stderr[-200:] if r1.returncode else "")
    st = state_of(h)
    pos = st["day"]["ES"].get("pos")
    check("P1: SHORT entered at the observable price 6220.00",
          pos and close_float(pos["entry_px"], 6220.0), pos and pos["entry_px"])
    check("P1: band price recorded separately as signal_px (never the fill)",
          pos and close_float(pos["signal_px"], scen.ES_SIG12["signal_px"], 0.02),
          pos and pos["signal_px"])
    check("P1: stop recomputed from the fill (fill + 1.2*sigma, independent math)",
          pos and close_float(pos["stop_px"], 6220.0 + 1.2 * scen.ES_SIG12["sd"], 0.02),
          pos and pos["stop_px"])
    check("P1: no cap flag yet", st.get("cap_blocked_date") is None, st.get("cap_blocked_date"))

    r2 = run_wrapper("fix_w_poll.py", h, "P2")
    check("P2 poll exits 0", r2.returncode == 0, r2.stderr[-200:] if r2.returncode else "")
    st = state_of(h)
    d_es = st["day"]["ES"]
    check("P2: ES short closed at the target (session VWAP, independent math)",
          not d_es.get("pos") and close_float(d_es["day_realized"], scen.ES_NET_EXPECT, 0.02),
          d_es["day_realized"])
    check(f"P2: day P&L {scen.ES_NET_EXPECT:+.2f} >= cap ${scen.CAP_USD:.0f}",
          scen.ES_NET_EXPECT >= scen.CAP_USD, scen.ES_NET_EXPECT)
    check("P2: CAP FIRED -- cap_blocked_date set in state (persists)",
          st.get("cap_blocked_date") == "2026-11-06", st.get("cap_blocked_date"))
    check("P2: per-day-entry cap_blocked flag set", d_es.get("cap_blocked") is True)
    check("P2: diary announces the DAILY PROFIT CAP",
          "**DAILY PROFIT CAP 2026-11-06" in diary(h) and "no further" in diary(h))
    nq = st["day"].get("NQ", {})
    check("P2: NQ signal the SAME poll is BLOCKED (account-level cap, organic)",
          nq.get("trades", 0) == 0 and not nq.get("pos"), nq)
    check("P2: journal has exactly 1 row (no second entry anywhere)",
          len(rows_of(f"{h}/trades.csv")) == 1)
    check("P2: n_entries still 1 (the NQ signal never became an entry)",
          st["n_entries"] == 1, st["n_entries"])

    r3 = run_wrapper("fix_w_poll.py", h, "P3")
    st = state_of(h)
    check("P3: still exactly 1 journal row at 14:40 (no same-day re-entry)",
          len(rows_of(f"{h}/trades.csv")) == 1)
    check("P3: cap flag survives further polls",
          st.get("cap_blocked_date") == "2026-11-06", st.get("cap_blocked_date"))

    r4 = run_wrapper("fix_w_poll.py", h, "P4")
    check("P4 EOD poll exits 0", r4.returncode == 0, r4.stderr[-200:] if r4.returncode else "")
    st = state_of(h)
    txt = r4.stdout
    check("P4: DAY SUMMARY written for 2026-11-06", "DAY SUMMARY 2026-11-06" in txt, txt[:160])
    check(f"P4: summary shows the capped day P&L ${scen.ES_NET_EXPECT:+.2f}",
          f"${scen.ES_NET_EXPECT:+.2f}" in txt, [l for l in txt.splitlines() if "DAY SUMMARY" in l])
    check("P4: summary carries the DAILY PROFIT CAP line",
          "DAILY PROFIT CAP active today" in txt)
    check("P4: trading_days == 1, topstep tdays == 1 (one real trading day)",
          st["trading_days"] == 1 and st["overlays"]["topstep"]["tdays"] == 1,
          (st["trading_days"], st["overlays"]["topstep"]["tdays"]))
    check("P4: best_day tracked truthfully at the real closed day P&L (PASS gate stays strict)",
          close_float(st["overlays"]["topstep"]["best_day"], scen.ES_NET_EXPECT, 0.02),
          st["overlays"]["topstep"]["best_day"])
    check("P4: PASS gate blocks this attempt (best day >= 55% cap)",
          not st["overlays"]["topstep"].get("done"), st["overlays"]["topstep"].get("done"))

    r5 = run_wrapper("fix_w_poll.py", h, "P5")
    check("P5 poll exits 0", r5.returncode == 0, r5.stderr[-200:] if r5.returncode else "")
    st = state_of(h)
    mon = st["day"].get("ES", {})
    check("P5: NEXT trading day (Nov-9) entry allowed again (cap reset)",
          mon.get("pos") is not None and mon.get("pos", {}).get("trade_id") == "2026-11-09_ES_01",
          mon.get("pos", {}).get("trade_id"))
    check("P5: stale cap_blocked_date (Nov-6) inert on the new day",
          st.get("cap_blocked_date") == "2026-11-06", st.get("cap_blocked_date"))

    # --- seeded cold-reload gate variants (fresh processes, real bar_step path)
    gate_day = ps.new_day("2026-11-09")
    snap_state = None
    for variant, expect_entry, why in (
            ("cond", False, "flag deleted -> live condition (day 1700 >= 1650) blocks"),
            ("flag", False, "flag alone blocks although day P&L is only 1000"),
            ("reset", True, "stale past-day flag + day P&L 200 -> entry allowed"),
            ("apex", True, "topstep overlay done -> no consistency cap applies")):
        h2 = home(f"f1_gate_{variant}")
        day = ps.new_day("2026-11-09")
        day["day_realized"] = {"cond": 1700.0, "flag": 1000.0,
                               "reset": 200.0, "apex": 1700.0}[variant]
        cap = None
        if variant == "flag":
            cap = "2026-11-09"
        extra = {"topstep": {"done": True}} if variant == "apex" else None
        seed_state(h2, today="2026-11-09", day={"ES": day}, cum=day["day_realized"],
                   cap_date=cap, overlays_extra=extra)
        r = run_wrapper("fix_w_gate.py", h2, variant)
        got_entry = "RESULT: ENTRY" in r.stdout
        check(f"gate[{variant}]: {'ENTRY' if expect_entry else 'BLOCKED'} ({why})",
              got_entry == expect_entry, r.stdout.strip().splitlines()[-1:] if r.stdout else r.stderr[-200:])
        if snap_state is None:
            snap_state = True


def f2_n2_day_scoped_accounting():
    """N2: one-symbol feed outage cannot pollute EOD accounting."""
    # S1: ES traded today (+200); NQ's YESTERDAY entry (realized -300, open pos) lingers
    h = home("f2_s1")
    d_es = ps.new_day("2026-11-06")
    d_es["trades"] = 1
    d_es["day_realized"] = 200.0
    d_nq = ps.new_day("2026-11-05")            # yesterday: feed dead all day today
    d_nq["trades"] = 2
    d_nq["day_realized"] = -300.0
    d_nq["pos"] = dict(side=1, entry_px=25000.0, stop_px=24900.0, tp_px=25100.0,
                       entry_ts="2026-11-05T12:20:00-05:00", contracts=2,
                       fill_method="poll_quote", exec_lag_min=0, trade_id="2026-11-05_NQ_01")
    d_nq["last_close"] = 25050.0
    seed_state(h, today="2026-11-06", day={"ES": d_es, "NQ": d_nq}, cum=200.0)
    r = run_wrapper("fix_w_poll.py", h, "S1")
    check("S1 poll exits 0", r.returncode == 0, r.stderr[-200:] if r.returncode else "")
    txt = r.stdout
    check("S1: EOD day P&L shows TODAY's +$200.00 (yesterday's -300 filtered out)",
          "$+200.00" in txt and "$-100.00" not in txt,
          [l for l in txt.splitlines() if "DAY SUMMARY" in l])
    st = state_of(h)
    check("S1: trading_days == 1 (ES really traded today)",
          st["trading_days"] == 1, st["trading_days"])
    check("S1: topstep tdays == 1, best_day == +200 (not -100)",
          st["overlays"]["topstep"]["tdays"] == 1
          and close_float(st["overlays"]["topstep"]["best_day"], 200.0, 0.01),
          (st["overlays"]["topstep"]["tdays"], st["overlays"]["topstep"]["best_day"]))
    check("S1: the open NQ position is preserved (feed outage must not close it)",
          st["day"]["NQ"].get("pos") is not None)
    eq_rows = rows_of(f"{h}/equity.csv")
    check("S1: equity row day mark = +200 (filtered)",
          eq_rows and close_float(eq_rows[-1].split(",")[2], 200.0, 0.01), eq_rows[-1:])

    # S2: NOTHING traded today (ES day entry zero trades; NQ stale with pos)
    h = home("f2_s2")
    d_es = ps.new_day("2026-11-06")
    d_nq = ps.new_day("2026-11-05")
    d_nq["trades"] = 2
    d_nq["day_realized"] = -300.0
    d_nq["pos"] = dict(side=1, entry_px=25000.0, stop_px=24900.0, tp_px=25100.0,
                       entry_ts="2026-11-05T12:20:00-05:00", contracts=2,
                       fill_method="poll_quote", exec_lag_min=0, trade_id="2026-11-05_NQ_02")
    d_nq["last_close"] = 25050.0
    seed_state(h, today="2026-11-06", day={"ES": d_es, "NQ": d_nq}, cum=-300.0, traded=False)
    r = run_wrapper("fix_w_poll.py", h, "S2")
    check("S2 poll exits 0", r.returncode == 0, r.stderr[-200:] if r.returncode else "")
    txt = r.stdout
    st = state_of(h)
    check("S2: EOD summary written with trades: 0", "trades: 0" in txt,
          [l for l in txt.splitlines() if "DAY SUMMARY" in l])
    check("S2: day P&L shows $+0.00 (stale -300 filtered out)",
          "day P&L: $+0.00" in txt, [l for l in txt.splitlines() if "DAY SUMMARY" in l])
    check("S2: trading_days NOT incremented (no trade happened today)",
          st["trading_days"] == 0, st["trading_days"])
    check("S2: topstep tdays NOT incremented (min-days progress not inflated)",
          st["overlays"]["topstep"]["tdays"] == 0, st["overlays"]["topstep"]["tdays"])
    check("S2: best_day stays 0.0", close_float(st["overlays"]["topstep"]["best_day"], 0.0, 1e-9),
          st["overlays"]["topstep"]["best_day"])

    # replay-mode regression: date=None keeps summing the whole (single) replay day
    ps.set_paths(home("f2_replay"))
    ps.pending_clear()
    st = ps.fresh_state()
    st["today"] = "replay"
    d1 = ps.new_day("2026-11-06")
    d1["day_realized"] = 400.0
    d2 = ps.new_day("2026-11-05")
    d2["day_realized"] = -100.0
    st["day"] = {"ES": d1, "NQ": d2}
    st["traded_today"] = True
    ps.overlay_update(st, eod=True)            # replay caller: no date argument
    check("replay regression: date=None keeps the pre-fix semantics "
          "(sums ALL day entries: 400 + (-100) = 300 -> best_day 300)",
          close_float(st["overlays"]["topstep"]["best_day"], 300.0, 0.01),
          st["overlays"]["topstep"]["best_day"])
    ps.pending_clear()


def f3_n3_eod_diary_idempotent():
    """N3: crash inside the EOD window no longer duplicates the DAY SUMMARY block."""
    h = home("f3")
    ps.set_paths(h)
    ps.pending_clear()
    st = ps.fresh_state()
    st["today"] = "2026-11-06"
    d = ps.new_day("2026-11-06")
    d["trades"] = 1
    d["day_realized"] = 100.0
    st["day"] = {"ES": d}
    st["traded_today"] = True
    ps.atomic_json(ps.STATE_F, st)
    ps.eod_summary(st)                        # writes diary DIRECTLY; mutates in memory only
    ps.pending_clear()                        # <- process "dies": pendings and mutations lost
    st2 = state_of(h)                         # disk state predates the summary
    st2["today"] = "2026-11-06"
    st2["traded_today"] = True
    out2 = ps.eod_summary(st2)                # recovery run: marker must prevent the rewrite
    ps.flush_pending(st2)
    txt = diary(h)
    n_sum = txt.count("**DAY SUMMARY 2026-11-06**")
    eq_rows = rows_of(f"{h}/equity.csv")
    st3 = state_of(h)
    check("recovery run still RETURNS the summary text (run_live prints it)",
          out2 is not None and "DAY SUMMARY 2026-11-06" in out2)
    check("N3 FIXED: exactly ONE DAY SUMMARY block after the crash+recovery",
          n_sum == 1, n_sum)
    check("accounting recovers single-count (tdays 1, trading_days 1, 1 equity row)",
          st3["overlays"]["apex"]["tdays"] == 1 and st3["trading_days"] == 1
          and len(eq_rows) == 1,
          (st3["overlays"]["apex"]["tdays"], st3["trading_days"], len(eq_rows)))
    # normal path regression: second summary the same day is a no-op
    out3 = ps.eod_summary(st3)
    check("normal path: eod_summary returns None once last_summary is set",
          out3 is None and txt.count("**DAY SUMMARY 2026-11-06**") == 1)


def f4_n4_replay_guard():
    """N4: the replay abort guard is real and reachable."""
    h = home("f4")
    ps.set_paths(h)
    before = snap(h)
    orig = ps.replay_dir_for
    try:
        ps.replay_dir_for = lambda out: out     # simulate a broken path resolver
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ps.run_replay(3)
        out = buf.getvalue()
    finally:
        ps.replay_dir_for = orig
    check("broken resolver -> replay ABORTS with a clear message",
          "ABORT" in out, out.strip()[:120])
    check("aborted replay wrote NOTHING into the home",
          snap(h) == before, snap(h))
    # normal replay still runs (guard must not false-positive)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ps.run_replay(2)
    out = buf.getvalue()
    check("normal replay still runs under the new guard",
          "REPLAY of last 2" in out and "ABORT" not in out, out.strip()[:160])
    check("normal replay wrote only under replay/ (no live files in the home root)",
          not os.path.exists(f"{h}/trades.csv") and os.path.isdir(f"{h}/replay"))
    ps.pending_clear()


def f5_n5_remediate_rerun_safe():
    """N5: remediate_data.py re-run preserves the archive; first-run path still works."""
    # (a) re-run on a byte-identical COPY of the already-remediated live home
    h = home("f5_rerun")
    shutil.rmtree(h, ignore_errors=True)
    shutil.copytree(LIVE, h)
    arch = f"{h}/archive/pre_remediation_2026-11-05"
    before_arch = snap(arch)
    before_tc = snap(h)
    r = run_wrapper("remediate_data.py", env_extra={
        "REM_HOME": h, "REM_BACKUP": f"{ROOT}/remediation_workspace/backup_live_data_2026-11-05",
        "REM_ARCHIVE": arch, "REM_TODAY": "2026-11-05"})
    check("re-run exits 0", r.returncode == 0, r.stderr[-300:] if r.returncode else "")
    check("re-run: archive NOT overwritten (all archived files byte-identical)",
          snap(arch) == before_arch, (before_arch, snap(arch)))
    check("re-run: journal and diary untouched (byte-identical)",
          all(before_tc[f] == snap(h).get(f) for f in ("trades.csv", "log.md", "equity.csv")))
    check("re-run: output reports the skips",
          "NOT overwritten" in r.stdout and "restart skipped" in r.stdout
          and "rewrite skipped" in r.stdout, r.stdout.strip()[:300])

    # (b) first-run path on a copy of the REAL pre-remediation v1 backup
    h2 = home("f5_first")
    shutil.rmtree(h2, ignore_errors=True)
    shutil.copytree(f"{ROOT}/remediation_workspace/backup_live_data_2026-11-05", h2)
    arch2 = f"{h2}/archive/pre_remediation_2026-11-05"
    r2 = run_wrapper("remediate_data.py", env_extra={
        "REM_HOME": h2, "REM_BACKUP": f"{ROOT}/remediation_workspace/backup_live_data_2026-11-05",
        "REM_ARCHIVE": arch2, "REM_TODAY": "2026-11-05"})
    check("first run (v1-like home) exits 0", r2.returncode == 0,
          r2.stderr[-300:] if r2.returncode else "")
    lines = open(f"{h2}/trades.csv").read().splitlines()
    check("first run: journal is v2-header-only (0 live rows)",
          lines[0] == ",".join(ps.JOURNAL_COLS) and len(lines) == 1, lines[:2])
    check("first run: diary restarted with the v2 header",
          open(f"{h2}/log.md").readline().startswith("# VWAP Fade paper-trading diary (engine v2)"))
    st2 = state_of(h2)
    check("first run: state v2, cum 0, 0 trades", st2["schema_version"] == 2
          and st2["cum"] == 0.0 and st2["n_trades"] == 0)
    arch_rows = open(f"{arch2}/trades.csv").read().splitlines()
    check("first run: contaminated v1 journal archived (9 lines: header + 8 replay rows)",
          len(arch_rows) == 9, len(arch_rows))
    # (c) a second run on the SAME first-run home: archive still preserved
    arch2_snap = snap(arch2)
    r3 = run_wrapper("remediate_data.py", env_extra={
        "REM_HOME": h2, "REM_BACKUP": f"{ROOT}/remediation_workspace/backup_live_data_2026-11-05",
        "REM_ARCHIVE": arch2, "REM_TODAY": "2026-11-05"})
    check("second run on the remediated home also exits 0", r3.returncode == 0,
          r3.stderr[-300:] if r3.returncode else "")
    check("second run: archive STILL byte-identical (the N5 destruction cannot recur)",
          snap(arch2) == arch2_snap)


def f6_cosmetics():
    """Cosmetic findings (i)-(iii) from the fresh audit."""
    src = open(f"{ROOT}/scripts/paper_step.py").read()
    check("(i) journal clock semantics documented in the engine source",
          "different" in src and "clock" in src and "exit_ts" in src
          and "NOTE on clock semantics" in src)
    check("(ii) no-quote branch marked as defence-in-depth",
          "Defence-in-depth" in src)
    # (iii) load_state prunes against the POLL date, not the wall clock
    h = home("f6")
    ps.set_paths(h)
    st = ps.fresh_state()
    d5 = ps.new_day("2026-11-05")
    d6 = ps.new_day("2026-11-06")
    st["day"] = {"ES": d5, "NQ": d6}
    ps.atomic_json(ps.STATE_F, st)
    st2 = ps.load_state(today="2026-11-06")
    check("(iii) load_state(today=...) prunes AGAINST THE GIVEN DATE: Nov-5 kept? no -- "
          "Nov-5 entry pruned, Nov-6 entry kept",
          "ES" not in st2["day"] and "NQ" in st2["day"], list(st2["day"].keys()))
    st3 = ps.load_state(today="2026-11-05")
    check("(iii) pruning follows the caller's date (Nov-5): opposite selection",
          "ES" in st3["day"] and "NQ" not in st3["day"], list(st3["day"].keys()))


def f7_regression_suites():
    """Both earlier suites must still pass against the fixed engine."""
    r = run_wrapper("remediation_tests.py", timeout=600)
    check("remediation suite (16 tests) passes", r.returncode == 0,
          r.stdout.strip().splitlines()[-3:] if r.stdout else r.stderr[-300:])
    r2 = run_wrapper("post_audit_tests.py", timeout=900)
    check("post-audit suite (15 groups) passes", r2.returncode == 0,
          r2.stdout.strip().splitlines()[-3:] if r2.stdout else r2.stderr[-300:])
    # the archived 8-trade replay equivalence is asserted inside both suites


TESTS = [
    ("F1 N1 bot-side daily profit cap (organic + cold gates)", f1_n1_daily_profit_cap),
    ("F2 N2 day-scoped accounting (outage days + replay regression)", f2_n2_day_scoped_accounting),
    ("F3 N3 idempotent EOD diary write", f3_n3_eod_diary_idempotent),
    ("F4 N4 reachable replay abort guard", f4_n4_replay_guard),
    ("F5 N5 remediate_data re-run safety", f5_n5_remediate_rerun_safe),
    ("F6 cosmetic items", f6_cosmetics),
    ("F7 regression: both earlier suites", f7_regression_suites),
]


def main():
    os.makedirs(WS, exist_ok=True)
    for name, fn in TESTS:
        CUR = name
        globals()["CUR"] = name
        print(f"\n== {name} ==")
        ORDER.append(name)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- bench must record, not crash
            import traceback
            check("test raised", False, f"{e.__class__.__name__}: {e}")
            traceback.print_exc()
    n_pass = sum(1 for r in RESULTS.values() if r["status"] == "PASS")
    n_fail = len(RESULTS) - n_pass
    n_assert = sum(len(r["items"]) for r in RESULTS.values())
    n_assert_pass = sum(1 for r in RESULTS.values() for it in r["items"] if it[0])
    print("\n" + "=" * 74)
    print(f"RESULT: {n_pass}/{len(RESULTS)} groups passed, "
          f"{n_assert_pass}/{n_assert} assertions ok")
    with open(f"{WS}/fixver_results.json", "w") as fh:
        json.dump({"order": ORDER, "results": RESULTS}, fh, indent=1, default=str)
    print("saved ->", f"{WS}/fixver_results.json")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
