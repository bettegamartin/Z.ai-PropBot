"""FRESH INDEPENDENT POST-REMEDIATION AUDIT BENCH (audit-only, 2026-11-05).

Second independent audit of the remediated VWAP-fade paper-trading engine
(scripts/paper_step.py v2). Written NEW for this audit; it does not reuse or
invoke the remediator's suite. Every probe drives the engine through its real
entry points (run_live / run_replay / bar_step / overlay_update) with isolated
homes under audit_workspace/post_audit/. Crafted data comes from
scripts/post_audit_scen.py (auditor-designed numbers).

Safety: no probe writes to the live home download/paper_trading/ (the only
touch is a read-only `--status` whose file hashes are verified unchanged);
no network calls; the production Tradovate/NT8 bot is unreachable and
untouched.
"""
import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/scripts")
import paper_step as ps
import post_audit_scen as scen

ROOT = "/home/z/my-project"
WS = f"{ROOT}/audit_workspace/post_audit"
LIVE = f"{ROOT}/download/paper_trading"
ET = scen.ET
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
    with open(path) as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    return lines[1:] if lines else []


def journal_dicts(path):
    """Journal rows as dicts, header taken from the file itself."""
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


def run_wrapper(script, *args, env_extra=None, timeout=180):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, f"{ROOT}/scripts/{script}", *map(str, args)],
        capture_output=True, text=True, timeout=timeout, cwd=ROOT, env=env)


def snap(home_):
    out = {}
    for f in sorted(os.listdir(home_)):
        if f == ".lock":
            continue
        p = f"{home_}/{f}"
        if os.path.isfile(p):
            out[f] = hashlib.md5(open(p, "rb").read()).hexdigest()
    return out


def close_float(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------- tests

def b01_daily_stop():
    """P0.1 with auditor-designed data: one -$537.57 stop-out must halt the day."""
    h = home("b01")
    r1 = run_wrapper("pa_w_poll.py", h, "A1")
    check("A1 poll exits 0", r1.returncode == 0, r1.stderr[-200:] if r1.returncode else "")
    st = state_of(h)
    d = st["day"]["ES"]
    pos = d.get("pos")
    check("entry opened at the poll's observable price 6044.00",
          pos and close_float(pos["entry_px"], 6044.0), pos and pos["entry_px"])
    check("signal price recorded separately (6036.11 band, never used as fill)",
          pos and close_float(pos["signal_px"], scen.ES_SIG["signal_px"], 0.02),
          pos and pos["signal_px"])
    check("stop recomputed from the FILL = fill + 1.2*sigma (auditor's sigma)",
          pos and close_float(pos["stop_px"], 6044.0 + 1.2 * scen.ES_SIG["sd"], 0.02),
          pos and pos["stop_px"])
    check("target = signal-bar VWAP (auditor's recompute)",
          pos and close_float(pos["tp_px"], scen.ES_SIG["vwap"], 0.02), pos and pos["tp_px"])
    check("trade_id deterministic format", pos and pos["trade_id"] == "2026-11-06_ES_01",
          pos and pos["trade_id"])
    check("exec lag recorded = 20 min", pos and int(pos["exec_lag_min"]) == 20,
          pos and pos["exec_lag_min"])
    check("no journal row while the position is open", rows_of(f"{h}/trades.csv") == [])

    r2 = run_wrapper("pa_w_poll.py", h, "A2")
    check("A2 poll exits 0", r2.returncode == 0, r2.stderr[-200:] if r2.returncode else "")
    st = state_of(h)
    d = st["day"]["ES"]
    check("stop-out closed at the stop level 6097.27 (auditor's 6044+1.2sigma)",
          not d.get("pos") and close_float(d["day_realized"], -537.57, 0.02),
          d["day_realized"])
    check("HALT FIRED: state.halted_date == 2026-11-06", st.get("halted_date") == "2026-11-06",
          st.get("halted_date"))
    check("per-symbol halted flag set", d.get("halted") is True)
    check("diary announces the account-level daily stop",
          "**DAILY STOP 2026-11-06" in diary(h) and "no further entries" in diary(h))
    row = journal_dicts(f"{h}/trades.csv")
    check("journal has exactly 1 row", len(row) == 1, len(row))
    f = row[0] if row else {}
    check("row: reason=stop, net -537.57, cum -537.57",
          f.get("reason") == "stop" and close_float(f.get("net_usd", 1), -537.57, 0.02)
          and close_float(f.get("cum_eq", 1), -537.57, 0.02), f)
    check("row: signal 6036.11 vs entry 6044.00 recorded apart",
          close_float(f.get("signal_px", 0), 6036.11, 0.02) and close_float(f.get("entry_px", 0), 6044.0, 0.02))
    shutil.copy2(f"{h}/state.json", f"{WS}/b01_halted_state_snapshot.json")

    r3 = run_wrapper("pa_w_poll.py", h, "A3")
    st = state_of(h)
    nq = st["day"].get("NQ", {})
    check("A3: NQ signal the SAME day is blocked (account-level, NQ day flag not set)",
          nq.get("trades", 0) == 0 and not nq.get("pos"), nq)
    check("A3: no re-entry on ES either (journal unchanged at exactly 1 row)",
          len(rows_of(f"{h}/trades.csv")) == 1)
    # note: the ES day entry itself may be pruned at poll start by migrate_state,
    # which compares against WALL-CLOCK today -- a test-environment artifact here
    # (injected poll date 2026-11-06 vs sandbox clock 2026-11-04); in production
    # poll time == wall clock, so same-day entries are never pruned.

    r4 = run_wrapper("pa_w_poll.py", h, "A4")
    st = state_of(h)
    mon = st["day"].get("ES", {})
    check("A4: NEXT trading day (Nov-9) entries re-enabled and trade opens",
          mon.get("pos") is not None and mon.get("pos", {}).get("trade_id") == "2026-11-09_ES_01",
          mon.get("pos", {}).get("trade_id"))
    check("A4: stale halted_date (Nov-6) is inert on the new day",
          st.get("halted_date") == "2026-11-06", st.get("halted_date"))


def b02_halt_survives_restart():
    """P0.1: cold process reload blocks; the flag alone AND the condition alone block."""
    snap = f"{WS}/b01_halted_state_snapshot.json"
    for tag, del_flag in (("b02a", "0"), ("b02b", "1")):
        h = home(tag)
        shutil.copy2(snap, f"{h}/state.json")
        r = run_wrapper("pa_w_cold.py", h, del_flag)
        check(f"cold reload (flag {'kept' if del_flag == '0' else 'DELETED'}): signal BLOCKED",
              "RESULT: BLOCKED" in r.stdout, r.stdout.strip().splitlines()[-1:] if r.stdout else r.stderr[-200:])
    h = home("b02c")
    st = json.load(open(snap))
    st["day"]["ES"]["day_realized"] = -100.0        # condition no longer breached
    st["halted_date"] = "2026-11-06"                # but the persisted flag remains
    os.makedirs(h, exist_ok=True)
    json.dump(st, open(f"{h}/state.json", "w"), default=str)
    r = run_wrapper("pa_w_cold.py", h, "0")
    check("persisted flag blocks even when the live condition is back above -$400",
          "RESULT: BLOCKED" in r.stdout, r.stdout.strip().splitlines()[-1:] if r.stdout else "")


def b03_observable_fills():
    """P0.2 variants: worse fill, instant-target (friction only), no-quote skip, late quote."""
    # worse fill (quote below band for a short)
    h = home("b03a")
    r1 = run_wrapper("pa_w_poll.py", h, "B1")
    st = state_of(h)
    pos = st["day"]["ES"].get("pos")
    check("B1: fill booked at observable 6020.00, signal 6036.11 recorded apart",
          pos and close_float(pos["entry_px"], 6020.0) and close_float(pos["signal_px"], 6036.11, 0.02),
          pos and (pos["entry_px"], pos["signal_px"]))
    check("B1: stop anchored to the FILL (6020+1.2sigma), not to the signal",
          pos and close_float(pos["stop_px"], 6020.0 + 1.2 * scen.ES_SIG["sd"], 0.02),
          pos and pos["stop_px"])
    r2 = run_wrapper("pa_w_poll.py", h, "B2")
    f = (journal_dicts(f"{h}/trades.csv") or [{}])[0]
    check("B2: closed row keeps signal/execution apart with method+lag",
          f.get("fill_method") == "poll_quote" and f.get("exec_lag_min") == "20"
          and close_float(f.get("signal_px", 0), 6036.11, 0.02)
          and close_float(f.get("entry_px", 0), 6020.0, 0.02), f)
    # instant-target: observable already through the target -> friction-only exit
    h = home("b03b")
    run_wrapper("pa_w_poll.py", h, "C1")
    f = (journal_dicts(f"{h}/trades.csv") or [{}])[0]
    check("C1: target pre-traded-through -> immediate exit AT the observable price (tp_poll)",
          f.get("reason") == "tp_poll" and close_float(f.get("entry_px", 1), 5940.0)
          and close_float(f.get("exit_px", 1), 5940.0)
          and close_float(f.get("gross_usd", 1), 0.0) and close_float(f.get("net_usd", 1), -4.90, 0.01), f)
    st = state_of(h)
    check("C1: friction-only loss does NOT trip the daily stop",
          st.get("halted_date") is None and close_float(st["cum"], -4.90, 0.01),
          (st.get("halted_date"), st["cum"]))
    # no observable price AT ALL -> no entry, explicit note (bar_step-level probe:
    # run_live's last_completed_close fallback always supplies a price whenever a
    # signal bar is still fresh, so the skip-note is only reachable below run_live)
    h = home("b03c")
    ps.set_paths(h)
    ps.pending_clear()
    st = ps.fresh_state()
    st["today"] = "2026-11-06"
    now = datetime.fromisoformat("2026-11-06T13:20:00-05:00")
    for i in (0, 1):
        ps.bar_step("ES", (*scen.ES_FRI[i], datetime.fromisoformat(scen.ES_FRI_STAMPS[i])),
                    st, "live", now_ts=now, quote=dict(px=6010.0, method="poll_quote"))
    ps.bar_step("ES", (*scen.ES_FRI[2], datetime.fromisoformat(scen.ES_FRI_STAMPS[2])),
                st, "live", now_ts=now, quote=None)
    notes = " | ".join(ps.PENDING_LOGS)
    check("no-price poll takes NO entry and logs the explicit skip note",
          not st["day"]["ES"].get("pos") and "NOT taken" in notes
          and "no fresh observable price" in notes, notes[:160])
    ps.pending_clear()


def b04_no_backfill_exits():
    """P0.2: bars that closed before the entry action cannot exit the position."""
    h = home("b04")
    ps.set_paths(h)
    ps.pending_clear()
    st = ps.fresh_state()
    st["today"] = "2026-11-06"
    now = datetime.fromisoformat("2026-11-06T12:20:00-05:00")
    q = dict(px=6010.0, method="poll_quote")
    for i in (0, 1):
        ps.bar_step("ES", (*scen.ES_FRI[i], datetime.fromisoformat(scen.ES_FRI_STAMPS[i])),
                    st, "live", now_ts=now, quote=q)
    now2 = datetime.fromisoformat("2026-11-06T13:20:00-05:00")
    ps.bar_step("ES", (*scen.ES_FRI[2], datetime.fromisoformat(scen.ES_FRI_STAMPS[2])),
                st, "live", now_ts=now2, quote=dict(px=6044.0, method="poll_quote"))
    pos = st["day"]["ES"]["pos"]
    check("short opened at 6044 with stop 6097.27 / target 5947.33",
          pos and close_float(pos["entry_px"], 6044.0)
          and close_float(pos["stop_px"], 6044.0 + 1.2 * scen.ES_SIG["sd"], 0.02)
          and close_float(pos["tp_px"], scen.ES_SIG["vwap"], 0.02))
    # a duplicate 11:00-stamped bar (closed 12:00, BEFORE the 13:20 action) that
    # spans BOTH the stop and the target must be ignored for exits
    old = (6000., 6300., 5700., 6020., 1000.)
    ps.bar_step("ES", (*old, datetime.fromisoformat("2026-11-06T11:00:00-05:00")),
                st, "live", now_ts=now2, quote=q)
    check("pre-entry bar that spans stop AND target did NOT close the position",
          st["day"]["ES"].get("pos") is not None)
    # a post-entry bar that trades through the stop does close it
    ps.bar_step("ES", (*scen.ES_FRI[3], datetime.fromisoformat(scen.ES_FRI_STAMPS[3])),
                st, "live", now_ts=now2, quote=q)
    check("post-entry bar closes at the stop level (reason stop)",
          not st["day"]["ES"].get("pos")
          and close_float(st["day"]["ES"]["day_realized"], -537.57, 0.02),
          st["day"]["ES"]["day_realized"])


def b05_flock():
    """P1.1: overlapping runs are serialised; the loser writes nothing."""
    import time
    h = home("b05a")
    ps.set_paths(h)
    lock = open(ps.LOCK_F, "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = snap(h)
    out = io.StringIO()
    now, fetch = scen.get_poll("A1")
    with contextlib.redirect_stdout(out):
        ps.run_live(now=now, fetch=fetch)
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    check("lock holder: second run prints SKIP", "SKIP: another paper_step --live run holds the lock" in out.getvalue(),
          out.getvalue().strip()[:120])
    check("lock holder: SKIP run wrote NOTHING (no state, no diary, no journal)",
          snap(h) == before, sorted(set(snap(h)) ^ set(before)))
    # true concurrent pair in separate processes
    h = home("b05b")
    p1 = subprocess.Popen([sys.executable, f"{ROOT}/scripts/pa_w_poll.py", h, "A1"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT,
                          env={**os.environ, "PA_FETCH_DELAY": "1.5"})
    time.sleep(0.6)
    p2 = subprocess.Popen([sys.executable, f"{ROOT}/scripts/pa_w_poll.py", h, "A1"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
    o1, _ = p1.communicate(timeout=120)
    o2, _ = p2.communicate(timeout=120)
    skips = sum("SKIP" in o for o in (o1, o2))
    lives = sum("LIVE " in o for o in (o1, o2))
    check("concurrent pair: exactly one SKIP and one processed run", skips == 1 and lives == 1,
          (skips, lives, o1[:60], o2[:60]))
    st = state_of(h)
    check("concurrent pair: exactly one entry recorded", st["n_entries"] == 1, st["n_entries"])


def b06_crash_recovery():
    """P1.1: crash after journal write, before state save -> recovery dedups."""
    h = home("b06")
    r1 = run_wrapper("pa_w_crash.py", h, "X1")
    check("crash injected after the journal write", "CRASH INJECTED" in r1.stdout, r1.stdout[-160:])
    check("crashed run: journal row durable on disk", len(rows_of(f"{h}/trades.csv")) == 1)
    check("crashed run: state.json never saved", not os.path.exists(f"{h}/state.json"))
    r2 = run_wrapper("pa_w_poll.py", h, "X1")
    check("recovery run reports JOURNAL DEDUP (same regenerated trade_id skipped)",
          "JOURNAL DEDUP" in r2.stdout, r2.stdout.strip()[:160])
    rows = rows_of(f"{h}/trades.csv")
    st = state_of(h)
    f = (journal_dicts(f"{h}/trades.csv") or [{}])[0]
    check("recovery: journal still exactly 1 row (no duplicate)", len(rows) == 1, len(rows))
    check("recovery: state reconciles with the journal (n_trades, cum == cum_eq)",
          st["n_trades"] == 1 and close_float(st["cum"], float(f.get("cum_eq", 9)), 0.01),
          (st["n_trades"], st["cum"], f.get("cum_eq")))
    # truncated tail + a second day's trade (seq numbering + repair)
    with open(f"{h}/trades.csv", "a") as fh:
        fh.write("live,2026-11-09_ES")           # simulated torn write
    r3 = run_wrapper("pa_w_poll.py", h, "X2")
    rows = rows_of(f"{h}/trades.csv")
    st = state_of(h)
    check("torn tail line detected and repaired (JOURNAL REPAIR)",
          "JOURNAL REPAIR" in r3.stdout, r3.stdout.strip()[:120])
    check("second day's trade appended with a fresh per-day sequence id",
          len(rows) == 2 and "2026-11-09_ES_01" in rows[1], [r[:80] for r in rows])
    f2 = (journal_dicts(f"{h}/trades.csv") or [{}, {}])[1]
    check("state reconciles again after repair (2 trades, cum matches last row)",
          st["n_trades"] == 2 and close_float(st["cum"], float(f2.get("cum_eq", 9)), 0.01),
          (st["n_trades"], st["cum"]))


def b07_replay_isolation():
    """P1.2: CLI replay writes only <home>/replay; live files untouched; injected rows inert."""
    h = home("b07")
    env = {**os.environ, "PAPER_HOME": h}
    r = subprocess.run([sys.executable, "scripts/paper_step.py", "--replay", "3"],
                       capture_output=True, text=True, cwd=ROOT, env=env, timeout=300)
    check("CLI --replay exits 0", r.returncode == 0, r.stderr[-200:])
    check("replay wrote its own journal under <home>/replay/",
          os.path.exists(f"{h}/replay/trades.csv") and len(rows_of(f"{h}/replay/trades.csv")) > 0)
    check("replay left NO live files in the home root",
          not os.path.exists(f"{h}/trades.csv") and not os.path.exists(f"{h}/state.json")
          and not os.path.exists(f"{h}/log.md"))
    check("replay stdout declares isolation", "ISOLATED journal" in r.stdout and "live journal/state untouched" in r.stdout)
    replay_rows = rows_of(f"{h}/replay/trades.csv")
    check("replay rows are tagged mode=replay", all(l.split(",")[0] == "replay" for l in replay_rows),
          replay_rows[0][:60] if replay_rows else "")
    # the abort guard is structurally unreachable (rdir is always OUT + "/replay") --
    # verify the property it is meant to guarantee instead: replay never targets live files
    for cand in (ps.ROOT_HOME, "/tmp", WS, f"{WS}/x/replay"):
        rdir = ps.replay_dir_for(cand)
        check(f"replay_dir_for({cand}) never resolves to a live root",
              rdir != os.path.abspath(cand) and rdir != os.path.abspath(ps.ROOT_HOME),
              rdir)
    # injected replay row in the LIVE journal is inert for live accounting
    h = home("b07c")
    os.makedirs(h, exist_ok=True)
    cols = ps.JOURNAL_COLS
    injected = ",".join(["replay", "2026-11-01_NQ_99", "2026-11-01T15:00:00-05:00", "NQ", "long",
                         "2", "25000.0", "2026-11-01T13:00:00-05:00", "25000.0",
                         "2026-11-01T13:00:00-05:00", "2026-11-01T14:00:00-05:00", "25050.0",
                         "tp", "bar_fill", "0", "100.00", "3.40", "96.60", "96.60"])
    with open(f"{h}/trades.csv", "w") as fh:
        fh.write(",".join(cols) + "\n" + injected + "\n")
    st = ps.fresh_state()
    st["today"] = "2026-11-06"
    ps.atomic_json(f"{h}/state.json", st)
    r = run_wrapper("pa_w_poll.py", h, "N1")
    st = state_of(h)
    live_now = rows_of(f"{h}/trades.csv")
    check("injected mode=replay row left untouched and ignored by live accounting",
          len(live_now) == 1 and live_now[0] == injected
          and st["cum"] == 0.0 and st["n_trades"] == 0, (st["cum"], st["n_trades"]))


def _ov_state(cum, tdays, best_day, key="topstep"):
    st = ps.fresh_state()
    st["today"] = "2026-11-06"
    st["cum"] = cum
    st["overlays"] = {k: dict(base_cum=0.0, hwm_eod=0.0, floor=-ps.OVERLAYS[k]["dd"],
                              tdays=0, fees=0.0, attempts=1, best_day=0.0,
                              start="2026-11-06") for k in ps.OVERLAYS}
    st["overlays"][key].update(tdays=tdays, best_day=best_day)
    return st


def b08_consistency():
    """P1.3: Topstep 55% pass gate at the exact boundary; Apex uncapped by design."""
    check("cap math: round(0.55*3000,2) == 1650.00 exactly", round(0.55 * ps.TARGET, 2) == 1650.0)
    st = _ov_state(3100.0, 2, 1650.00)
    _, msgs = ps.overlay_update(st, eod=False, write=False)
    check("best day exactly $1,650 (=55% of target) BLOCKS the pass",
          not st["overlays"]["topstep"].get("done") and any("consistency cap blocks" in m for m in msgs),
          msgs)
    st = _ov_state(3100.0, 2, 1600.00)
    _, msgs = ps.overlay_update(st, eod=False, write=False)
    check("best day $1,600 (<55%) PASSES and charges the $149 activation",
          st["overlays"]["topstep"].get("done") and st["overlays"]["topstep"]["fees"] == 149.0,
          st["overlays"]["topstep"])
    st = _ov_state(3100.0, 7, 2999.00, key="apex")
    ps.overlay_update(st, eod=False, write=False)
    check("Apex has NO consistency cap (best day $2,999 still passes) -- per research Table 4",
          st["overlays"]["apex"].get("done") and st["overlays"]["apex"]["fees"] == 99.0,
          st["overlays"]["apex"])


def b09_eod_trail():
    """P1.3: both floors trail EOD closed equity only; intraday spikes never raise them."""
    st = _ov_state(1000.0, 0, 0.0)
    d = ps.new_day("2026-11-06")
    d["pos"] = dict(side=-1, entry_px=6000.0, contracts=2)
    d["mark_px"] = 5762.0                       # open pnl = (5762-6000)*(-1)*5*2 = +2380
    st["day"] = {"ES": d}
    ps.overlay_update(st, eod=False, write=False)
    check("intraday equity +2380 does NOT raise either floor (hwm_eod stays 0)",
          st["overlays"]["apex"]["hwm_eod"] == 0.0 and st["overlays"]["topstep"]["hwm_eod"] == 0.0
          and st["overlays"]["apex"]["floor"] == -2500.0 and st["overlays"]["topstep"]["floor"] == -2000.0,
          (st["overlays"]["apex"]["floor"], st["overlays"]["topstep"]["floor"]))
    d["pos"] = None
    st["traded_today"] = True
    ps.overlay_update(st, eod=True, write=False)
    check("EOD closed equity +1000 sets hwm_eod=1000 -> Apex floor -1500, Topstep -1000 (capped <0)",
          st["overlays"]["apex"]["hwm_eod"] == 1000.0 and st["overlays"]["apex"]["floor"] == -1500.0
          and st["overlays"]["topstep"]["floor"] == -1000.0 and st["overlays"]["apex"]["tdays"] == 1,
          (st["overlays"]["apex"]["floor"], st["overlays"]["topstep"]["floor"]))
    d["pos"] = dict(side=-1, entry_px=6000.0, contracts=2)
    d["mark_px"] = 5262.0                       # another intraday spike: open pnl +7380
    ps.overlay_update(st, eod=False, write=False)
    check("later intraday spike still leaves both floors untouched",
          st["overlays"]["apex"]["floor"] == -1500.0 and st["overlays"]["topstep"]["floor"] == -1000.0)
    # cap at breakeven (Topstep) vs uncapped (Apex)
    for ov in st["overlays"].values():
        ov["hwm_eod"] = 3000.0
    ps.overlay_update(st, eod=False, write=False)
    check("Topstep floor capped at breakeven (0); Apex floor uncapped (+500)",
          st["overlays"]["topstep"]["floor"] == 0.0 and st["overlays"]["apex"]["floor"] == 500.0,
          (st["overlays"]["topstep"]["floor"], st["overlays"]["apex"]["floor"]))
    # bust evaluated at CURRENT equity
    st = _ov_state(-1501.0, 1, 0.0, key="apex")
    st["overlays"]["apex"]["hwm_eod"] = 1000.0   # floor -1500
    n_before = st["overlays"]["apex"]["attempts"]
    ps.overlay_update(st, eod=False, write=False)
    ap = st["overlays"]["apex"]
    check("Apex bust fires on current equity (-1501 <= -1500): new attempt, +$33 eval fee",
          ap["attempts"] == n_before + 1 and ap["fees"] == 33.0 and ap["base_cum"] == -1501.0
          and ap["hwm_eod"] == 0.0, ap)


def b10_no_rewind():
    """P1.4: old bars cannot rewind state; rejections are counted and warned."""
    h = home("b10")
    st = ps.fresh_state()
    st["today"] = "2026-11-04"
    old = [f"2026-11-03T{h_:02d}:00:00-05:00" for h_ in range(10, 16)] + \
          [f"2026-11-04T{h_:02d}:00:00-05:00" for h_ in range(10, 16)]
    st["done_bars"] = {"ES": list(old), "NQ": list(old)}
    st["last_bar_ts"] = {"ES": old[-1], "NQ": old[-1]}
    ps.set_paths(h)
    ps.atomic_json(ps.STATE_F, st)
    r = run_wrapper("pa_w_poll.py", h, "H")
    st = state_of(h)
    rej = st["rejected_bars"].get("ES", [])
    check("2 re-offered old bars rejected (Nov-2 x2, outside the done-bars window)",
          len(rej) == 2 and all(k.startswith("2026-11-02") for k in rej), rej)
    check("re-offered bar INSIDE done_bars dedups silently (not counted as rejection)",
          "2026-11-03T10:00:00-05:00" in st["done_bars"]["ES"]
          and "2026-11-03T10:00:00-05:00" not in rej)
    check("rejection warned in the status line", "2 stale/out-of-order bar(s) rejected" in r.stdout,
          r.stdout.strip()[:200])
    check("state moved FORWARD only: day is Nov-5, last_bar_ts Nov-5 11:00",
          st["day"]["ES"]["date"] == "2026-11-05"
          and st["last_bar_ts"]["ES"] == "2026-11-05T11:00:00-05:00",
          st["last_bar_ts"])
    exp = scen.indep_session([(6300., 6310., 6290., 6305., 1000.), (6305., 6320., 6300., 6315., 900.)])
    vn = st["day"]["ES"]["vwap_n"] / st["day"]["ES"]["vwap_d"]
    check("VWAP state built ONLY from the new bars (auditor recompute: %.3f)" % exp[-1]["vwap"],
          close_float(vn, exp[-1]["vwap"], 0.01), vn)
    check("no trades, cum untouched", st["cum"] == 0.0 and st["n_trades"] == 0)


def b11_feed_health():
    """P1.4: per-symbol derived health; stale never reported healthy; gating on observable prices."""
    now = datetime.fromisoformat("2026-11-06T13:20:00-05:00")
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    old = scen.bars_df(["2026-11-04T10:00:00-05:00"], [(6100., 6110., 6090., 6105., 1000.)])
    today = scen.bars_df(["2026-11-06T12:00:00-05:00"], [(6020., 6050., 5682., 6034., 1000.)])
    q = dict(px=6034.0, ts=now, method="poll_quote")
    cases = [
        ("no data -> unavailable", ps.classify_feed(None, None, now) == "unavailable"),
        ("empty frame -> unavailable", ps.classify_feed(empty, None, now) == "unavailable"),
        ("no bar today, 11:30 RTH -> stale", "stale" in ps.classify_feed(old, None, datetime.fromisoformat("2026-11-06T11:30:00-05:00"))),
        ("no bar today, 10:30 RTH -> awaiting", ps.classify_feed(old, None, datetime.fromisoformat("2026-11-06T10:30:00-05:00")) == "awaiting first completed bar"),
        ("forming quote -> ok", ps.classify_feed(today, q, now) == "ok"),
        ("today bar 30 min old, no quote -> ok (bar current)", ps.classify_feed(today, None, now) == "ok"),
        ("today bar 150 min old, no quote -> stale", "stale" in ps.classify_feed(today, None, datetime.fromisoformat("2026-11-06T15:50:00-05:00"))),
    ]
    for name, cond in cases:
        check("classify: " + name, cond)
    h = home("b11")
    r = run_wrapper("pa_w_poll.py", h, "G1")
    st = state_of(h)
    fs = st.get("feed_status", {})
    check("run-level: per-symbol feed status recorded (ES stale, NQ ok)",
          str(fs.get("ES", "")).startswith("stale") and fs.get("NQ") == "ok", fs)
    check("run-level: status line shows both symbols' health",
          "feed ES:stale NQ:ok" in r.stdout, r.stdout.strip()[:160])
    check("run-level: healthy symbol still trades while the stale one is flagged",
          st["day"].get("NQ", {}).get("pos") is not None)
    # fallback observable = newest completed close
    h = home("b11f")
    run_wrapper("pa_w_poll.py", h, "F1")
    st = state_of(h)
    pos = st["day"]["ES"].get("pos")
    check("fallback entry booked at the newest completed close 6034.00",
          pos and close_float(pos["entry_px"], 6034.0) and pos.get("fill_method") == "last_completed_close",
          pos and (pos.get("entry_px"), pos.get("fill_method")))
    run_wrapper("pa_w_poll.py", h, "F2")
    f = (journal_dicts(f"{h}/trades.csv") or [{}])[0]
    check("fallback method carried into the journal row",
          f.get("fill_method") == "last_completed_close" and close_float(f.get("entry_px", 0), 6034.0, 0.01), f)


def b12_parameters():
    """Strategy parameters preserved: engine == backtest variant 1 == research Tables 2/4/8."""
    check("P == researched config", ps.P == dict(k=2.0, stop_k=1.2, trend=0.006, max_trades=3,
          cutoff_h=14, base_c=2, scaled_c=4, scale_level=1500.0, dstop=400.0, dll=1000.0), ps.P)
    bt = dict(k=2.0, stop_k=1.2, trend_filter_pct=0.006, max_trades=3, entry_cutoff_h=14)
    check("backtest VWAPFD variant-1 constants match engine (02_backtest.py L192)",
          ps.P["k"] == bt["k"] and ps.P["stop_k"] == bt["stop_k"]
          and ps.P["trend"] == bt["trend_filter_pct"] and ps.P["max_trades"] == bt["max_trades"]
          and ps.P["cutoff_h"] == bt["entry_cutoff_h"])
    check("SPEC matches research Table 2 (dpp/tick/comm/slip)",
          ps.SPEC["ES"] == dict(yh="ES=F", dpp=5.0, tick_usd=1.25, comm=0.60, slip=0.5)
          and ps.SPEC["NQ"] == dict(yh="NQ=F", dpp=2.0, tick_usd=0.50, comm=0.60, slip=0.5), ps.SPEC)
    check("round-turn friction: MES $2.45, MNQ $1.70 per micro (Table 2)",
          close_float(0.60 * 2 + 0.5 * 1.25 * 2, 2.45) and close_float(0.60 * 2 + 0.5 * 0.50 * 2, 1.70))
    check("Topstep overlay: $2,000 EOD trail, 55% consistency, 2 min-days, $149/$49",
          ps.OVERLAYS["topstep"] == dict(name="Topstep 50K", dd=2000.0, eval_fee=49.0, act=149.0,
                                         min_days=2, consistency=0.55, floor_cap_be=True), ps.OVERLAYS["topstep"])
    check("Apex overlay: $2,500 EOD trail, no consistency, 7 min-days, $99/$33",
          ps.OVERLAYS["apex"] == dict(name="Apex 50K", dd=2500.0, eval_fee=33.0, act=99.0,
                                      min_days=7, consistency=None, floor_cap_be=False), ps.OVERLAYS["apex"])
    check("journal schema carries the P0.2/P1.1 fields (19 columns)",
          len(ps.JOURNAL_COLS) == 19 and all(c in ps.JOURNAL_COLS for c in
          ("trade_id", "signal_px", "signal_ts", "fill_method", "exec_lag_min", "mode")),
          ps.JOURNAL_COLS)
    check("retention/freshness constants: 200-key history, 40-min freshness, 100-min stale line",
          ps.MAX_KEY_HIST == 200 and ps.FRESH_MIN == 40 and ps.STALE_LAG_MIN == 100)


def _indep_replay_trades(n_days=5):
    """Auditor's OWN simulator of the backtest semantics over the stored data."""
    def sessions(sym):
        df = pd.read_csv(f"{ps.DATA}/{sym.lower()}_1h.csv", index_col=0)
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(ps.ET)
        rth = df.between_time("10:00", "15:59")
        rth = rth[rth["Volume"] > 0]
        days = [g for _, g in rth.groupby(rth.index.normalize()) if len(g) >= 5]
        return days[-n_days:]

    trades, cum = [], 0.0
    es_days = sessions("ES")
    dates = [g.index[0].date() for g in es_days]
    tick = 0.25
    for di, date in enumerate(dates):
        for sym in ("ES", "NQ"):
            days = sessions(sym)
            g = days[di]
            if g.index[0].date().isoformat() != date.isoformat():
                continue
            spec = ps.SPEC[sym]
            vn = vd = 0.0
            devs = []
            n = 0
            pos = None
            closed_this_bar = False
            day_open = None

            def close_pos(px, reason, ts):
                nonlocal cum, pos, closed_this_bar
                gross = (px - pos["entry"]) * pos["side"] * spec["dpp"] * pos["contracts"]
                costs = (spec["comm"] * 2 + spec["slip"] * spec["tick_usd"] * 2) * pos["contracts"]
                cum += gross - costs
                trades.append(dict(sym=sym, side="short" if pos["side"] < 0 else "long",
                                   entry_px=round(pos["entry"], 2), exit_px=round(px, 2),
                                   reason=reason, net_usd=round(gross - costs, 2)))
                pos = None
                closed_this_bar = True

            for ts, row in g.iterrows():
                o, h, l, c, v = (float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume"))
                closed_this_bar = False
                if day_open is None:
                    day_open = o
                vn += (h + l + c) / 3 * v
                vd += v
                vwap = vn / vd
                devs.append(c - vwap)
                sd = float(np.std(devs, ddof=1)) if len(devs) >= 2 else 0.0
                if pos:
                    if pos["side"] > 0 and l <= pos["stop"]:
                        close_pos(pos["stop"], "stop", ts)
                    elif pos["side"] < 0 and h >= pos["stop"]:
                        close_pos(pos["stop"], "stop", ts)
                    elif pos["side"] > 0 and h >= pos["tp"] + tick:
                        close_pos(pos["tp"], "tp", ts)
                    elif pos["side"] < 0 and l <= pos["tp"] - tick:
                        close_pos(pos["tp"], "tp", ts)
                if (not pos and not closed_this_bar and ts.hour in (12, 13) and n < 3
                        and sd > 0 and abs(c - day_open) <= 0.006 * day_open):
                    up, dn = vwap + 2.0 * sd, vwap - 2.0 * sd
                    side = -1 if h >= up else (1 if l <= dn else None)
                    if side is not None:
                        sig = max(up, o) if side < 0 else min(dn, o)
                        pos = dict(side=side, entry=sig,
                                   stop=sig + 1.2 * sd if side < 0 else sig - 1.2 * sd,
                                   tp=vwap, contracts=4 if cum >= 1500.0 else 2)
                        n += 1
                        if pos["side"] < 0 and h >= pos["stop"]:
                            close_pos(pos["stop"], "stop", ts)
                        elif pos["side"] > 0 and l <= pos["stop"]:
                            close_pos(pos["stop"], "stop", ts)
                        elif pos["side"] < 0 and l <= pos["tp"] - tick:
                            close_pos(pos["tp"], "tp", ts)
                        elif pos["side"] > 0 and h >= pos["tp"] + tick:
                            close_pos(pos["tp"], "tp", ts)
                if pos and ts.hour == 15:
                    close_pos(c, "eod", ts)
    return trades, cum


def b13_replay_equivalence():
    """Core regression: engine replay == auditor's own simulator == the 8 archived trades."""
    h = home("b13")
    env = {**os.environ, "PAPER_HOME": h}
    r = subprocess.run([sys.executable, "scripts/paper_step.py", "--replay", "5"],
                       capture_output=True, text=True, cwd=ROOT, env=env, timeout=300)
    check("engine --replay 5 exits 0", r.returncode == 0, r.stderr[-200:])
    cols = ps.JOURNAL_COLS
    eng = [dict(zip(cols, l.split(","))) for l in rows_of(f"{h}/replay/trades.csv")]
    arch_cols = ["mode", "ts", "sym", "side", "contracts", "entry_px", "entry_ts",
                 "exit_ts", "exit_px", "reason", "gross_usd", "costs_usd", "net_usd", "cum_eq"]
    arch = [dict(zip(arch_cols, l.split(","))) for l in rows_of(
        f"{LIVE}/archive/pre_remediation_2026-11-05/trades.csv")]
    check("engine replay reproduces all 8 audit-recorded trades (count)", len(eng) == len(arch) == 8,
          (len(eng), len(arch)))
    key = lambda t: (t["sym"], t["side"], float(t["entry_px"]), float(t["exit_px"]), t["reason"])
    mism = [i for i, (e, a) in enumerate(zip(eng, arch)) if key(e) != key(a)]
    check("engine replay == archived trades field-by-field (sym/side/entry/exit/reason)",
          not mism, mism)
    check("engine replay == archived trades net P&L to the cent",
          all(close_float(e["net_usd"], a["net_usd"], 0.005) for e, a in zip(eng, arch)))
    check("engine replay final cumulative equity == archived final cum_eq",
          close_float(eng[-1]["cum_eq"], arch[-1]["cum_eq"], 0.005),
          (eng[-1]["cum_eq"], arch[-1]["cum_eq"]))
    sim, sim_cum = _indep_replay_trades(5)
    check("auditor's OWN simulator reproduces the same 8 trades (count)", len(sim) == 8, len(sim))
    sim_mism = [i for i, (s, e) in enumerate(zip(sim, eng))
                if (s["sym"], s["side"], s["entry_px"], s["exit_px"], s["reason"]) !=
                   (e["sym"], e["side"], float(e["entry_px"]), float(e["exit_px"]), e["reason"])]
    check("auditor's simulator == engine replay (entry/exit/reason, order preserved)", not sim_mism, sim_mism)
    check("auditor's simulator == engine replay (net P&L to the cent)",
          all(close_float(s["net_usd"], e["net_usd"], 0.005) for s, e in zip(sim, eng)))
    check("simulator final cum == engine replay final cum_eq",
          close_float(sim_cum, float(eng[-1]["cum_eq"]), 0.01), (sim_cum, eng[-1]["cum_eq"]))


def b14_new_defect_probes():
    """Fresh-audit defect probes, UPDATED BY THE FIX STAGE (2026-11-05).

    The original probes demonstrated three residual defects (N1/N2/N3 of
    reports/vwap_fade_post_remediation_audit.md). The approved fix stage
    resolved them (engine v2.1), so this group now asserts the FIXED
    behaviours: no duplicated EOD diary block (N3), day-scoped EOD
    accounting (N2), and the still-strict overlay PASS gate (N1's entry
    gate is verified organically by fixes_workspace/fixver_tests.py F1).
    """
    # (a) eod_summary crash window
    h = home("b14a")
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
    ps.eod_summary(st)                    # writes diary DIRECTLY; mutates in-memory only
    ps.pending_clear()                    # <- process "dies": pendings and mutations lost
    st2 = state_of(h)                     # disk state predates the summary
    st2["today"] = "2026-11-06"
    st2["traded_today"] = True
    ps.eod_summary(st2)
    ps.flush_pending(st2)
    txt = diary(h)
    n_sum = txt.count(f"**DAY SUMMARY {st2['today']}**")
    eq_rows = rows_of(f"{h}/equity.csv")
    st3 = state_of(h)
    check("(a) FIXED (N3): crash in the eod window no longer duplicates the DAY SUMMARY block",
          n_sum == 1, n_sum)
    check("(a) accounting recovers single-count (tdays 1, trading_days 1, 1 equity row)",
          st3["overlays"]["apex"]["tdays"] == 1 and st3["trading_days"] == 1 and len(eq_rows) == 1,
          (st3["overlays"]["apex"]["tdays"], st3["trading_days"], len(eq_rows)))
    # (b) stale day entry must NOT contaminate eod accounting (one symbol feed-dead)
    h = home("b14b")
    ps.set_paths(h)
    ps.pending_clear()
    st = ps.fresh_state()
    st["today"] = "2026-11-06"
    d_es = ps.new_day("2026-11-06")
    d_es["trades"] = 1
    d_es["day_realized"] = 200.0
    d_nq = ps.new_day("2026-11-05")       # NQ feed dead all day: YESTERDAY's entry lingers
    d_nq["trades"] = 2
    d_nq["day_realized"] = -300.0
    st["day"] = {"ES": d_es, "NQ": d_nq}
    st["traded_today"] = True
    ps.atomic_json(ps.STATE_F, st)
    summary = ps.eod_summary(st)
    ps.flush_pending(st)
    check("(b) FIXED (N2): eod day P&L shows today's +$200.00 (yesterday's -300 filtered out)",
          "$+200.00" in summary and "$-100.00" not in summary, summary[:120])
    check("(b) tdays/trading_days count the real ES trading day",
          st["overlays"]["apex"]["tdays"] == 1 and st["trading_days"] == 1,
          (st["overlays"]["apex"]["tdays"], st["trading_days"]))
    check("(b) best_day tracks today's true P&L (+200)",
          abs(st["overlays"]["topstep"]["best_day"] - 200.0) < 0.01,
          st["overlays"]["topstep"]["best_day"])
    # (c) overlay PASS gate stays strict (N1 complement)
    h = home("b14c")
    ps.set_paths(h)
    ps.pending_clear()
    st = _ov_state(1700.0, 0, 0.0)
    d = ps.new_day("2026-11-06")
    d["trades"] = 2
    d["day_realized"] = 1700.0
    st["day"] = {"ES": d}
    st["traded_today"] = True
    ps.overlay_update(st, eod=True, write=False)
    check("(c) setup: best_day tracked at EOD = +$1,700", st["overlays"]["topstep"]["best_day"] == 1700.0,
          st["overlays"]["topstep"]["best_day"])
    st = _ov_state(3200.0, 2, 1700.0)
    _, msgs = ps.overlay_update(st, eod=False, write=False)
    blocked1 = any("consistency cap blocks" in m for m in msgs)
    st = _ov_state(5000.0, 2, 1700.0)
    _, msgs = ps.overlay_update(st, eod=False, write=False)
    blocked2 = not st["overlays"]["topstep"].get("done")
    check("(c) PASS gate REMAINS STRICT: an over-cap day blocks the attempt's PASS even at +$5,000 "
          "(N1's entry gate now prevents FRESH over-cap days; an open position riding over is "
          "reported truthfully, never truncated)",
          blocked1 and blocked2, (blocked1, blocked2))


def b15_live_home_readonly():
    """The bench and --status never modified the live home (hash proof)."""
    import hashlib
    def hashes():
        out = {}
        for f in ("state.json", "trades.csv", "equity.csv", "log.md"):
            p = f"{LIVE}/{f}"
            out[f] = hashlib.md5(open(p, "rb").read()).hexdigest()
        return out
    before = hashes()
    r = subprocess.run([sys.executable, "scripts/paper_step.py", "--status"],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    after = hashes()
    check("--status on the REAL home is read-only (all four files hash-unchanged)",
          before == after and r.returncode == 0, r.stdout.strip()[:160])
    check("real home journal still header-only (0 live trades)",
          len(rows_of(f"{LIVE}/trades.csv")) == 0)
    st = json.load(open(f"{LIVE}/state.json"))
    check("real home state: schema v2, cum 0, no halt, no replay contamination",
          st.get("schema_version") == 2 and st["cum"] == 0.0 and st["n_trades"] == 0
          and st.get("halted_date") is None, (st.get("schema_version"), st["cum"]))


TESTS = [
    ("B01 daily stop blocks + account-level + reset", b01_daily_stop),
    ("B02 halt survives restart (cold reload, flag/condition independence)", b02_halt_survives_restart),
    ("B03 fills at observable prices; variants", b03_observable_fills),
    ("B04 no backfilled exits (predates guard)", b04_no_backfill_exits),
    ("B05 flock serialises overlapping runs", b05_flock),
    ("B06 crash recovery without duplicates + repair", b06_crash_recovery),
    ("B07 replay isolation + abort-guard property + inert injected rows", b07_replay_isolation),
    ("B08 Topstep consistency boundary; Apex uncapped", b08_consistency),
    ("B09 EOD trails for both overlays; bust at current equity", b09_eod_trail),
    ("B10 stale bars rejected, no rewind", b10_no_rewind),
    ("B11 feed health classification + gating + fallback", b11_feed_health),
    ("B12 strategy parameters preserved", b12_parameters),
    ("B13 replay equivalence vs archive + auditor simulator", b13_replay_equivalence),
    ("B14 new-defect probes", b14_new_defect_probes),
    ("B15 live home untouched (hash proof)", b15_live_home_readonly),
]


def main():
    os.makedirs(WS, exist_ok=True)
    for name, fn in TESTS:
        CUR_NAME = name
        globals()["CUR"] = name
        print(f"\n== {name} ==")
        ORDER.append(name)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- audit bench must record, not crash
            import traceback
            check("test raised", False, f"{e.__class__.__name__}: {e}")
            traceback.print_exc()
            RESULTS.setdefault(name, {"status": "FAIL", "items": []})
    globals()["CUR"] = "__meta__"
    RESULTS["__meta__"] = {
        "status": "INFO",
        "items": [[True, "bench", "fresh post-remediation audit bench (second auditor)"],
                  [True, "engine", f"scripts/paper_step.py v2 (schema {ps.OVERLAYS and 'v2'})"],
                  [True, "workspace", WS],
                  [True, "live home touched?", "read-only --status only, hash-verified"]],
    }
    n_pass = sum(1 for k, v in RESULTS.items() if v["status"] == "PASS")
    n_fail = sum(1 for k, v in RESULTS.items() if v["status"] == "FAIL")
    with open(f"{WS}/post_audit_results.json", "w") as fh:
        json.dump(RESULTS, fh, indent=1, default=str)
    print(f"\n==== POST-AUDIT BENCH: {n_pass} test groups PASS, {n_fail} FAIL "
          f"(details: {WS}/post_audit_results.json) ====")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
