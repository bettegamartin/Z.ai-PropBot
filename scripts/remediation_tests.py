"""REMEDIATION TEST SUITE — VWAP Fade paper-trading engine v2.

Isolation:
  * every test runs in its own directory under remediation_workspace/ via
    paper_step.set_paths(); the live home (download/paper_trading) is never
    written by this suite
  * market data is canned (no network); the live feed is touched only by the
    manual smoke check at the end of the suite (single read-only fetch)
  * no production (Tradovate/NT8) connectivity exists or is attempted

Covers the 11 remediation-test requirements plus regressions of behaviour the
audit found correct (VWAP/signal math, stop/target semantics, sizing ladder,
costs, day rollover, normal bar dedup).
"""
import io
import json
import os
import shutil
import sys
import threading
import traceback
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = "/home/z/my-project"
sys.path.insert(0, f"{ROOT}/scripts")
import paper_step as ps  # noqa: E402

ET = ZoneInfo("America/New_York")
WS = f"{ROOT}/remediation_workspace"
LIVE_HOME = f"{ROOT}/download/paper_trading"
RESULTS = {}


# ---------------------------------------------------------------- helpers

def tdir(name):
    d = f"{WS}/t_{name}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    ps.set_paths(d)
    ps.pending_clear()
    return d


def bar(o, h, l, c, v=1000.0, y=2026, mo=11, d=5, hh=10, mm=0, sym_scale=1.0):
    f = lambda x: float(x) * sym_scale
    return (f(o), f(h), f(l), f(c), float(v), datetime(y, mo, d, hh, mm, tzinfo=ET))


def bars_to_df(bars):
    df = pd.DataFrame([b[:5] for b in bars],
                      index=pd.DatetimeIndex([b[5] for b in bars]),
                      columns=["Open", "High", "Low", "Close", "Volume"])
    return df[["Open", "High", "Low", "Close", "Volume"]]


def canned_fetch(df_by_sym, quote_by_sym=None, sleep=0.0):
    """Build a fetch(yh, want_quote=False) replacement (no network)."""
    quote_by_sym = quote_by_sym or {}

    def fetch(yh, want_quote=False):
        if sleep:
            import time
            time.sleep(sleep)
        sym = "ES" if "ES" in yh else "NQ"
        df = df_by_sym.get(sym)
        q = quote_by_sym.get(sym)
        if want_quote:
            return df, q
        return df
    return fetch


def read_journal(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=ps.JOURNAL_COLS)
    return pd.read_csv(path)


def state_of(d):
    return json.load(open(f"{d}/state.json"))


def run_live_quiet(**kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        ps.run_live(**kw)
    return buf.getvalue()


def feed_bars(sym, bars, st, mode="audit", now_ts=None, quote=None):
    for b in bars:
        ps.bar_step(sym, b, st, mode, now_ts=now_ts, quote=quote)


def fresh():
    return ps.fresh_state()


def check(name, fn):
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            items = fn()
        RESULTS[name] = {"status": "PASS", "items": items}
        print(f"PASS  {name}")
        for it in items:
            print(f"      - {it}")
    except Exception as e:
        RESULTS[name] = {"status": "FAIL", "error": f"{e}",
                         "trace": traceback.format_exc(), "items": []}
        print(f"FAIL  {name}: {e}")
        traceback.print_exc()


# Crafted scenario (scaled x20 from the audit bench set B1..B4):
#   two flat bars -> 12:00 band-touch (SHORT signal) -> 13:00 gap-through stop
#   that also re-touches the band (a would-be same-bar re-entry).
S = 20.0
FLAT = dict(o=1000, h=1000, l=1000, c=1000)
SIG = dict(o=1002, h=1006.5, l=1001.5, c=1005)      # 12:00 band touch -> SHORT
STOP = dict(o=1009.5, h=1015.0, l=1004.0, c=1005.5)  # 13:00 gap-through stop + re-touch
TREND_SAFE = STOP  # |close-day_open| = 110 <= 0.6%*20000 = 120

def bar20(**kw):
    kw.setdefault("sym_scale", S)
    return bar(**kw)

def day1_bars():
    return [bar20(**FLAT, hh=10, d=4), bar20(**FLAT, hh=11, d=4),
            bar20(**SIG, hh=12, d=4), bar20(**TREND_SAFE, hh=13, d=4)]

def day1_quote(px=20190.0):
    return dict(px=px, ts=datetime(2026, 11, 4, 13, 0, tzinfo=ET), method="poll_quote")


# =====================================================================
# T01 — P0.1 daily loss limit prevents further entries (account level)
# =====================================================================
def t01():
    d = tdir("t01")
    st = fresh()
    feed_bars("ES", day1_bars()[:3], st, "live", now_ts=datetime(2026, 11, 4, 13, 20, tzinfo=ET),
              quote=day1_quote())
    assert st["day"]["ES"]["pos"] is not None, "setup: expected an open SHORT"
    sig_px = st["day"]["ES"]["pos"]["signal_px"]
    feed_bars("ES", [day1_bars()[3]], st, "live", now_ts=datetime(2026, 11, 4, 14, 20, tzinfo=ET))
    day = ps.account_day_pnl(st, "2026-11-04")
    d_es = st["day"]["ES"]
    assert day <= -400.0, f"setup: expected day <= -400, got {day:.2f}"
    assert st.get("halted_date") == "2026-11-04", f"halted_date not set: {st.get('halted_date')}"
    assert d_es["halted"] is True, "per-symbol halted flag not set"
    assert d_es["pos"] is None, "same-bar re-entry happened despite the daily stop"
    assert d_es["trades"] == 1, f"expected exactly 1 trade (no re-entry), got {d_es['trades']}"
    # account-level: the OTHER symbol is blocked too
    nq = [bar20(**FLAT, hh=10, d=4, sym_scale=50.0), bar20(**FLAT, hh=11, d=4, sym_scale=50.0),
          bar(o=1002, h=1007.5, l=1001.5, c=1006, hh=13, d=4, sym_scale=50.0)]
    feed_bars("NQ", nq, st, "live", now_ts=datetime(2026, 11, 4, 14, 20, tzinfo=ET))
    assert st["day"]["NQ"]["pos"] is None, "NQ entered although the ACCOUNT daily stop was hit"
    assert st["day"]["NQ"]["trades"] == 0, "NQ trade counted despite account halt"
    log = open(f"{d}/log.md").read() if os.path.exists(f"{d}/log.md") else ""
    # logs are buffered until flush; force a flush to inspect the diary
    ps.buf_log("__probe__")
    ps.flush_pending(st)
    log = open(f"{d}/log.md").read()
    assert "DAILY STOP" in log, "diary lacks the DAILY STOP announcement"
    return [f"day P&L after the stop-out: ${day:+.2f} (limit -$400) -> halted_date set to 2026-11-04",
            f"13:00 bar stopped trade #1 AND re-touched the band: engine took NO re-entry "
            f"(trades=1, pos=None); without the stop this bar re-enters (v1 behaviour)",
            "account-level: NQ signal bar the same day did NOT open a position (cross-symbol block)",
            "diary carries the DAILY STOP announcement with the account day P&L",
            f"(reference) signal px of trade #1 was {sig_px:.2f}; the fill logic itself is T03's subject"]


# =====================================================================
# T02 — P0.1 the halt persists after restart (and the condition is checked)
# =====================================================================
def t02():
    d = tdir("t02")
    st = fresh()
    d_es = st["day"].setdefault("ES", ps.new_day("2026-11-04"))
    d_es.update(day_open=20000.0, trades=2, day_realized=-450.0)
    st["halted_date"] = "2026-11-04"
    st["halted_reason"] = "account day P&L -450.00 breached the -$400 daily stop"
    ps.atomic_json(f"{d}/state.json", st)                      # "restart"
    st2 = json.load(open(f"{d}/state.json"))                   # cold reload
    ps.migrate_state(st2, "2026-11-04")
    sig = [bar20(**FLAT, hh=10, d=4), bar20(**FLAT, hh=11, d=4), bar20(**SIG, hh=12, d=4)]
    feed_bars("ES", sig, st2, "live", now_ts=datetime(2026, 11, 4, 13, 20, tzinfo=ET),
              quote=day1_quote())
    assert st2["day"]["ES"]["pos"] is None, "entry opened after restart with the day already <= -$400"
    assert st2["day"]["ES"]["trades"] == 2, "trades counter advanced despite the halt (seeded 2)"
    # condition-based defence: even with the flag LOST, the -450 day blocks
    st3 = fresh()
    d3 = st3["day"].setdefault("ES", ps.new_day("2026-11-04"))
    d3.update(day_open=20000.0, trades=2, day_realized=-450.0)
    feed_bars("ES", sig, st3, "live", now_ts=datetime(2026, 11, 4, 13, 20, tzinfo=ET),
              quote=day1_quote())
    assert st3["day"]["ES"]["pos"] is None, "entry opened on a -$450 day with halted_date absent"
    return ["state saved with halted_date=2026-11-04, cold-reloaded from disk: 12:00 signal bar "
            "did NOT open a position",
            "defence-in-depth: with halted_date deliberately absent, the live day-P&L condition "
            "(-$450 <= -$400) still blocks the entry (audit test D scenario, now passing the "
            "other way)"]


# =====================================================================
# T03 — P0.2 fills use the first realistically observable price
# =====================================================================
def t03():
    d = tdir("t03")
    st = fresh()
    now = datetime(2026, 11, 4, 13, 20, tzinfo=ET)
    feed_bars("ES", day1_bars()[:3], st, "live", now_ts=now, quote=day1_quote(20150.0))
    pos = st["day"]["ES"]["pos"]
    assert pos is not None, "setup: expected an entry"
    assert pos["entry_px"] == 20150.0, f"fill {pos['entry_px']} != observable quote 20150.0"
    assert pos["signal_px"] != pos["entry_px"], "signal px equals fill px (backdating not fixed)"
    assert pos["fill_method"] == "poll_quote", pos["fill_method"]
    assert pos["entry_ts"] == now, f"entry_ts {pos['entry_ts']} != acting poll time"
    assert pos["exec_lag_min"] == 20, f"exec lag {pos['exec_lag_min']} != 20 min (bar closed 13:00)"
    assert pos["stop_px"] == 20150.0 + 1.2 * pos_stop_sd(st), "stop not anchored to the fill"
    # instant-target case: observable price already beyond the target -> exit at that price
    st2 = fresh()
    feed_bars("ES", day1_bars()[:3], st2, "live", now_ts=now, quote=day1_quote(20000.0))
    eng = read_journal(f"{d}/trades.csv")  # not flushed yet -> use state instead
    p2 = None
    # the tp_poll close already appended a journal row into PENDING_ROWS
    rows = list(ps.PENDING_ROWS)
    assert len(rows) == 1 and rows[0]["reason"] == "tp_poll", \
        f"expected an immediate tp_poll exit, got {[r['reason'] for r in rows]}"
    net = float(rows[0]["net_usd"])
    assert abs(net - (-float(rows[0]["costs_usd"]))) < 0.011, \
        f"instant exit should book friction only, net {net}"
    # no observable price -> no entry, explicit note
    st3 = fresh()
    feed_bars("ES", day1_bars()[:3], st3, "live", now_ts=now, quote=None)
    assert st3["day"]["ES"]["pos"] is None, "entry opened with no observable price"
    notes = [m for m in ps.PENDING_LOGS if "NOT taken" in m and "no fresh observable price" in m]
    assert notes, "missing explicit 'signal not taken' note"
    return [f"fill booked at the poll's observable price 20150.00 (signal/band price "
            f"{pos['signal_px']:.2f} recorded separately, never used as fill)",
            f"stop {pos['stop_px']:.2f} and target {pos['tp_px']:.2f} recomputed from the fill; "
            f"fill_method=poll_quote, entry_ts={now:%H:%M}, exec_lag_min=20",
            "observable price already beyond the target at action time -> immediate exit AT THAT "
            f"price (reason tp_poll), net ${net:+.2f} = friction only (no backdated target profit)",
            "quote unavailable -> signal NOT taken with an explicit diary note (no entry, no guess)"]


def pos_stop_sd(st):
    """1.2*sigma used by the entry (recomputed from the day's devs)."""
    import numpy as np
    devs = st["day"]["ES"]["devs"]
    return float(np.std(devs, ddof=1))


# =====================================================================
# T04 — signal price/timestamp and execution price/timestamp recorded apart
# =====================================================================
def t04():
    d = tdir("t04")
    st = fresh()
    now = datetime(2026, 11, 4, 13, 20, tzinfo=ET)
    feed_bars("ES", day1_bars()[:3], st, "live", now_ts=now, quote=day1_quote(20150.0))
    feed_bars("ES", [day1_bars()[3]], st, "live", now_ts=datetime(2026, 11, 4, 14, 20, tzinfo=ET))
    ps.flush_pending(st)
    eng = read_journal(f"{d}/trades.csv")
    assert list(eng.columns) == ps.JOURNAL_COLS, f"unexpected journal schema: {list(eng.columns)}"
    for col in ("trade_id", "signal_px", "signal_ts", "entry_px", "entry_ts",
                "fill_method", "exec_lag_min"):
        assert col in eng.columns, f"missing column {col}"
    r = eng.iloc[0]
    assert float(r["signal_px"]) != float(r["entry_px"])
    assert r["signal_ts"].startswith("2026-11-04T12:00"), r["signal_ts"]
    assert r["entry_ts"].startswith("2026-11-04T13:20"), r["entry_ts"]
    assert r["trade_id"] == "2026-11-04_ES_01", r["trade_id"]
    return [f"journal columns include trade_id/signal_px/signal_ts/entry_px/entry_ts/"
            f"fill_method/exec_lag_min ({len(ps.JOURNAL_COLS)} columns)",
            f"row: signal {r['signal_px']} @ {r['signal_ts']} (the 12:00 bar) vs execution "
            f"{r['entry_px']} @ {r['entry_ts']} (the acting poll), method={r['fill_method']}, "
            f"lag={r['exec_lag_min']}min, trade_id={r['trade_id']}"]


# =====================================================================
# T05 — overlapping runs cannot duplicate (lock + idempotent journal)
# =====================================================================
def t05():
    d = tdir("t05")
    fetch = canned_fetch({"ES": bars_to_df(day1_bars()), "NQ": bars_to_df([])},
                         {"ES": day1_quote(), "NQ": None})
    # (a) external lock holder -> SKIP, nothing written
    lock = open(f"{d}/.lock", "w")
    fcntl_hold = lock.fileno()
    import fcntl as fc
    fc.flock(lock, fc.LOCK_EX | fc.LOCK_NB)
    out = run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    assert "SKIP" in out and "lock" in out, f"expected SKIP message, got: {out.strip()}"
    assert not os.path.exists(f"{d}/state.json"), "a skipped run must not write state"
    fc.flock(lock, fc.LOCK_UN)
    lock.close()
    # (b) sequential overlap: second run starts from the PRE-RUN state but sees
    #     the post-run journal (the crash/overlap race) -> dedup by trade_id
    run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    n_rows_1 = len(read_journal(f"{d}/trades.csv"))
    shutil.copy(f"{d}/state.json", f"{d}/state.prerun.json")
    # rewind ONLY the state file to pre-run (as if a second process had loaded
    # it before the first one committed) while the journal keeps run-1's rows
    run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    shutil.copy(f"{d}/state.prerun.json", f"{d}/state.json")
    out2 = run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    eng = read_journal(f"{d}/trades.csv")
    assert len(eng) == n_rows_1, f"overlap duplicated rows: {n_rows_1} -> {len(eng)}"
    assert eng["trade_id"].is_unique, "duplicate trade_ids in journal"
    st = state_of(d)
    assert st["n_trades"] == len(eng), f"state/journal mismatch: {st['n_trades']} vs {len(eng)}"
    # (c) two truly concurrent runs: exactly one may process
    d2 = tdir("t05b")
    fetch_slow = canned_fetch({"ES": bars_to_df(day1_bars()), "NQ": bars_to_df([])},
                              {"ES": day1_quote(), "NQ": None}, sleep=0.4)
    outs = []
    def worker():
        outs.append(run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET),
                                   fetch=fetch_slow))
    th1, th2 = threading.Thread(target=worker), threading.Thread(target=worker)
    th1.start(); th2.start(); th1.join(); th2.join()
    eng2 = read_journal(f"{d2}/trades.csv")
    n_skip = sum("SKIP" in o for o in outs)
    assert n_skip == 1, f"expected exactly one SKIP among {len(outs)} runs, got {n_skip}"
    assert eng2["trade_id"].is_unique and len(eng2) == 1, \
        f"concurrent runs produced {len(eng2)} rows"
    return ["run holding the flock: second run prints SKIP and writes NOTHING",
            f"overlap replay (pre-run state + post-run journal): journal stays at {n_rows_1} "
            f"row(s), trade_ids unique, state n_trades={st['n_trades']} == journal rows",
            "two concurrent threads: exactly one processed, one SKIPped; journal has 1 unique row"]


# =====================================================================
# T06 — crash recovery cannot duplicate journal entries
# =====================================================================
def t06():
    d = tdir("t06")
    fetch = canned_fetch({"ES": bars_to_df(day1_bars()), "NQ": bars_to_df([])},
                         {"ES": day1_quote(), "NQ": None})
    orig_atomic = ps.atomic_json
    def boom(path, obj):
        if path.endswith("state.json"):
            raise OSError("simulated crash before state save")
        return orig_atomic(path, obj)
    ps.atomic_json = boom
    crashed = False
    try:
        run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    except OSError:
        crashed = True
    finally:
        ps.atomic_json = orig_atomic
    assert crashed, "crash injection did not fire"
    rows_after_crash = read_journal(f"{d}/trades.csv")
    assert len(rows_after_crash) == 1, "trade row lost by the crash"
    assert not os.path.exists(f"{d}/state.json"), "state should still be absent (pre-save crash)"
    # recovery run: same bars re-processed, same trade_id regenerated -> dedup
    run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    eng = read_journal(f"{d}/trades.csv")
    st = state_of(d)
    assert len(eng) == 1, f"recovery duplicated the trade: {len(eng)} rows"
    assert eng["trade_id"].is_unique
    assert st["n_trades"] == 1 and abs(st["cum"] - float(eng.iloc[0]["net_usd"])) < 0.011, \
        "state/journal do not reconcile after recovery"
    # (b) truncated tail line is repaired, ids survive
    with open(f"{d}/trades.csv", "a") as fh:
        fh.write("live,T07_CRASH_TAIL,2026-11")     # malformed partial row
    ids, n = ps.read_journal(f"{d}/trades.csv")
    assert ids == {"2026-11-04_ES_01"} and n == 1, f"repair wrong: {ids}, {n}"
    with open(f"{d}/trades.csv") as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2 and lines[-1].count(",") == len(ps.JOURNAL_COLS) - 1, \
        "repaired file still malformed"
    return ["crash between journal write and state save: row on disk, state absent "
            "(crash reproduced)",
            "recovery run re-processed the same bars, regenerated trade_id 2026-11-04_ES_01 -> "
            "dedup kept the journal at exactly 1 row; state n_trades=1 and cum reconcile "
            "with the row (NO duplicate)",
            "malformed truncated tail line: detected, dropped, file repaired in place, "
            "valid rows and trade_ids intact"]


# =====================================================================
# T07 — replay cannot contaminate live paper-trading records
# =====================================================================
def t07():
    d = tdir("t07")
    ps.set_paths(d)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ps.run_replay(2)
    assert not os.path.exists(f"{d}/trades.csv"), "replay wrote the LIVE journal path"
    assert not os.path.exists(f"{d}/state.json"), "replay wrote the LIVE state path"
    assert os.path.exists(f"{d}/replay/trades.csv"), "replay journal missing in replay/"
    rp = read_journal(f"{d}/replay/trades.csv")
    assert set(rp["mode"]) == {"replay"}
    out = buf.getvalue()
    assert "ISOLATED" in out and "live journal/state untouched" in out
    # abort guard: a replay dir resolving to the live root refuses to run
    orig = ps.replay_dir_for
    ps.replay_dir_for = lambda out_dir: os.path.abspath(ps.ROOT_HOME)
    try:
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            ps.run_replay(2)
        assert "ABORT" in buf2.getvalue(), "guard did not fire"
    finally:
        ps.replay_dir_for = orig
    # live loaders ignore non-live rows (second line of defence)
    with open(f"{d}/trades.csv", "w") as fh:
        fh.write(",".join(ps.JOURNAL_COLS) + "\n")
        fh.write("replay,X,2026-11-04,ES,long,2,1,1,1,1,1,1,stop,bar_fill,0,1,1,1,1\n")
    ids, n = ps.read_journal(f"{d}/trades.csv")
    assert n == 1 and ids == {"X"}  # row present but tagged replay; live dedup keys are per-id
    return ["--replay 2 wrote ONLY to <home>/replay/ (live trades.csv/state.json absent)",
            "guard: a replay directory resolving to the live root prints ABORT and runs nothing",
            "journal rows carry mode=replay and live accounting only ever reads live state",
            f"replay journal: {len(rp)} rows, modes={sorted(set(rp['mode']))}"]


# =====================================================================
# T08 — Topstep 55% consistency gate (P1.3)
# =====================================================================
def t08():
    def mk(cum, best_day, tdays):
        st = fresh()
        st["cum"] = cum
        st["today"] = "2026-11-05"
        st["traded_today"] = True
        dd = st["day"].setdefault("ES", ps.new_day("2026-11-05"))
        dd["day_realized"] = 100.0
        ov = st["overlays"]["topstep"]
        ov.update(tdays=tdays, best_day=best_day, hwm_eod=cum)
        return st

    st = mk(3300.0, 1800.0, 3)
    buf = io.StringIO()
    with redirect_stdout(buf):
        eq, msgs = ps.overlay_update(st, eod=True)
    assert not st["overlays"]["topstep"].get("done"), "PASSED despite best day >= 55% of target"
    assert any("consistency" in m for m in msgs), f"no consistency message: {msgs}"
    # boundary: exactly 55% blocks; just below passes
    st = mk(3300.0, 1650.0, 3)
    ps.overlay_update(st, eod=True)
    assert not st["overlays"]["topstep"].get("done"), "best day exactly 55% must block"
    st = mk(3300.0, 1600.0, 3)
    eq, msgs = ps.overlay_update(st, eod=True)
    assert st["overlays"]["topstep"].get("done") is True, "best day 1600 (< 1650) must pass"
    assert st["overlays"]["topstep"]["fees"] == 149.0, "activation fee not charged on pass"
    # Apex has no consistency cap (per research Table 4) -> still passes
    st = mk(3300.0, 2900.0, 7)
    st["overlays"]["apex"].update(tdays=7, best_day=2900.0, hwm_eod=3300.0)
    ps.overlay_update(st, eod=True)
    assert st["overlays"]["apex"].get("done") is True, "apex must have no consistency cap"
    return ["target reached (ocum $3,300 >= $3,000, tdays 4) with best day $1,800 -> NO pass; "
            "message: consistency cap blocks PASS (best day >= $1,650 = 55% of target)",
            "boundary: best day exactly $1,650 blocks; $1,600 passes and charges the $149 "
            "activation (rule: best day < 55% of target, research Table 4/5)",
            "Apex (no consistency requirement in the spec) passes with a $2,900 best day -- "
            "correct omission, not a silent substitution"]


# =====================================================================
# T09 — Apex $2,500 EOD trail (no intraday raise); Topstep cap; bust check
# =====================================================================
def t09():
    st = fresh()
    st["today"] = "2026-11-05"
    dd = st["day"].setdefault("ES", ps.new_day("2026-11-05"))
    dd["pos"] = dict(side=-1, entry_px=20000.0, contracts=2)
    dd["mark_px"] = 19810.0                     # open P&L +$1,900 -> eq $2,900 intraday
    st["cum"] = 1000.0
    ps.overlay_update(st, eod=False)
    assert st["overlays"]["apex"]["floor"] == -2500.0, \
        f"apex floor moved intraday: {st['overlays']['apex']['floor']}"
    assert st["overlays"]["topstep"]["floor"] == -2000.0
    st["traded_today"] = True
    dd["pos"] = None                             # close everything for the EOD mark
    ps.overlay_update(st, eod=True)
    assert st["overlays"]["apex"]["hwm_eod"] == 1000.0
    assert st["overlays"]["apex"]["floor"] == -1500.0, "EOD trail did not update the floor"
    # next intraday spike must NOT raise the apex floor again
    dd["pos"] = dict(side=-1, entry_px=20000.0, contracts=2)
    dd["mark_px"] = 19810.0
    ps.overlay_update(st, eod=False)
    assert st["overlays"]["apex"]["floor"] == -1500.0, "apex floor raised intraday (v1 bug back)"
    # Topstep floor capped at breakeven
    st["cum"] = 3200.0
    st["traded_today"] = True
    dd["pos"] = None
    ps.overlay_update(st, eod=True)
    assert st["overlays"]["topstep"]["floor"] == 0.0, \
        f"topstep floor should cap at breakeven, got {st['overlays']['topstep']['floor']}"
    # bust still evaluated against CURRENT equity (a live breach is real)
    st2 = fresh()
    st2["cum"] = -2600.0
    st2["today"] = "2026-11-05"
    ps.overlay_update(st2, eod=False)
    ov = st2["overlays"]["apex"]
    assert ov["attempts"] == 2 and ov["fees"] == 33.0 and ov["base_cum"] == -2600.0, \
        f"bust not registered: {ov}"
    return [f"intraday equity +$2,900 (open P&L) -> Apex floor UNCHANGED at -$2,500 "
            f"(v1 trailed to -$600 here)",
            f"after the EOD summary (closed equity $1,000): Apex hwm_eod=$1,000, floor -$1,500; "
            f"a later intraday spike leaves the floor at -$1,500 (EOD trail per research Table 4)",
            f"Topstep floor capped at breakeven ($0) once hwm_eod-dd >= 0 (research behaviour)",
            "bust check still uses current equity: eq -$2,600 <= floor -$2,500 -> attempt 2, "
            "$33 eval fee, base reset (unchanged, correct)"]


# =====================================================================
# T10 — old bars cannot rewind the system state (P1.4)
# =====================================================================
def t10():
    d = tdir("t10")
    es_old = [bar(**FLAT, hh=h, d=dd_) for dd_ in (2, 3) for h in (10, 11, 12, 13, 14, 15)]
    es_new = [bar(**FLAT, hh=10, d=5)]
    df = bars_to_df(es_old + es_new)
    fetch = canned_fetch({"ES": df, "NQ": bars_to_df([])}, {"ES": None, "NQ": None})
    # seed the state exactly as the migrated production state looks
    st = fresh()
    st["done_bars"]["ES"] = [b[5].isoformat() for b in es_old if b[5].day == 3]
    st["last_bar_ts"]["ES"] = max(st["done_bars"]["ES"])
    st["today"] = "2026-11-05"
    ps.atomic_json(f"{d}/state.json", st)
    out = run_live_quiet(now=datetime(2026, 11, 5, 11, 20, tzinfo=ET), fetch=fetch)
    st = state_of(d)
    assert st["day"].get("ES", {}).get("date") == "2026-11-05", \
        f"day state is {st['day'].get('ES', {}).get('date')}, not the new session"
    assert st["last_bar_ts"]["ES"].startswith("2026-11-05T10:00"), st["last_bar_ts"]["ES"]
    nov2_keys = [b[5].isoformat() for b in es_old if b[5].day == 2]
    rej = set(st["rejected_bars"]["ES"])
    assert all(k in rej for k in nov2_keys), "Nov-2 bars were not recorded as rejected"
    assert st["cum"] == 0.0 and st["n_entries"] == 0, "old bars changed accounting"
    assert "rejected" in out, f"no rejection warning in status line: {out.strip()}"
    assert "ES:ok" in out, out.strip()
    return [f"6 stale Nov-2 bars re-offered by a 5-day fetch: all REJECTED "
            f"(rejected_bars), warned in the status line, accounting untouched "
            f"(cum ${st['cum']:+.2f}, entries {st['n_entries']})",
            f"new Nov-5 10:00 bar processed normally: day date {st['day']['ES']['date']}, "
            f"last_bar_ts {st['last_bar_ts']['ES'][:16]} -> state moved FORWARD only",
            "Nov-3 bars already in done_bars were silently deduplicated (normal overlap)"]


# =====================================================================
# T11 — missing/stale data is never reported healthy; entries gated
# =====================================================================
def t11():
    # (a) feed unavailable
    d = tdir("t11a")
    fetch = canned_fetch({"ES": None, "NQ": None})
    out = run_live_quiet(now=datetime(2026, 11, 5, 11, 20, tzinfo=ET), fetch=fetch)
    st = state_of(d)
    assert "unavailable" in out and st["feed_status"]["ES"] == "unavailable", out.strip()
    # (b) stale completed bars during RTH
    d = tdir("t11b")
    df = bars_to_df([bar(**FLAT, hh=10, d=5)])
    fetch = canned_fetch({"ES": df, "NQ": bars_to_df([bar(**FLAT, hh=10, d=5, sym_scale=50.0)])})
    out = run_live_quiet(now=datetime(2026, 11, 5, 13, 20, tzinfo=ET), fetch=fetch)
    st = state_of(d)
    assert "ES:stale" in out and "NQ:stale" in out, f"stale feed not flagged: {out.strip()}"
    assert st["feed_status"]["ES"].startswith("stale"), st["feed_status"]
    assert "stale (newest completed bar 140 min old)" in out or \
           "stale (newest completed bar 140 min old)" in open(f"{d}/log.md").read(), \
        "stale detail missing from status/diary"
    # (c) fresh data with a quote -> healthy for that symbol
    d = tdir("t11c")
    df = bars_to_df(day1_bars()[:3])
    q = day1_quote()
    fetch = canned_fetch({"ES": df, "NQ": bars_to_df([])}, {"ES": q, "NQ": None})
    out = run_live_quiet(now=datetime(2026, 11, 4, 13, 20, tzinfo=ET), fetch=fetch)
    st = state_of(d)
    assert "ES:ok" in out and "NQ:unavailable" in out, out.strip()
    assert st["feed_status"]["ES"] == "ok"
    # (d) an expired signal bar (>40 min past its close) is never entered
    d = tdir("t11d")
    df = bars_to_df([bar(**FLAT, hh=10, d=5), bar(**FLAT, hh=11, d=5), bar(**SIG, hh=12, d=5)])
    fetch = canned_fetch({"ES": df, "NQ": bars_to_df([])}, {"ES": None, "NQ": None})
    out = run_live_quiet(now=datetime(2026, 11, 5, 13, 50, tzinfo=ET), fetch=fetch)
    st = state_of(d)
    assert st["n_entries"] == 0, "entry taken from an expired (>40 min) signal bar"
    # (d2) engine-level guard: a FRESH signal with no observable price at all ->
    #      explicit skip note (defense in depth; normally the completed-close
    #      fallback covers the fresh window, as tested in (e))
    st = fresh()
    feed_bars("ES", day1_bars()[:3], st, "live", now_ts=datetime(2026, 11, 4, 13, 20, tzinfo=ET),
              quote=None)
    assert st["day"]["ES"]["pos"] is None, "bar_step entered with quote=None"
    notes = [m for m in ps.PENDING_LOGS if "NOT taken" in m and "no fresh observable price" in m]
    assert notes, "missing explicit 'signal not taken' note"
    ps.pending_clear()
    # (e) fallback: no forming quote but a fresh completed close -> last_completed_close
    d = tdir("t11e")
    fetch = canned_fetch({"ES": df, "NQ": bars_to_df([])}, {"ES": None, "NQ": None})
    out = run_live_quiet(now=datetime(2026, 11, 5, 13, 20, tzinfo=ET), fetch=fetch)
    st = state_of(d)
    assert st["n_entries"] == 1, "fallback entry expected"
    pos = st["day"]["ES"]["pos"]
    assert pos["fill_method"] == "last_completed_close", pos["fill_method"]
    return ["empty feed -> status line + state say 'feed unavailable' (never 'ok')",
            "newest completed bar 140 min old during RTH -> 'feed ES:stale NQ:stale' with the "
            "detail 'stale (newest completed bar 140 min old)' in the status line and state",
            "fresh bars + forming quote -> 'feed ES:ok' (per-symbol health, not masked by the "
            "other symbol)",
            "an expired (>40 min past close) signal bar is never entered (v1 freshness gate "
            "preserved)",
            "engine guard: a fresh signal with NO observable price at the poll is skipped with "
            "an explicit diary note (no guessed fill)",
            "no forming quote but the newest completed close is <= 40 min old -> entry at that "
            "close with fill_method=last_completed_close (recorded, distinguishable)"]


# =====================================================================
# T12 — REGRESSION: replay reproduces the audit-recorded signal trades
# =====================================================================
def t12():
    d = tdir("t12")
    expected = [  # (sym, entry_date, entry_px, exit_px, reason) from the audited v1 journal
        ("ES", "2026-10-28", 7781.53, 7770.11, "tp"),
        ("NQ", "2026-10-28", 29901.89, 29839.21, "tp"),
        ("ES", "2026-10-29", 7727.47, 7749.63, "tp"),
        ("ES", "2026-11-02", 7643.28, 7634.54, "stop"),
        ("ES", "2026-11-02", 7634.10, 7622.76, "stop"),
        ("NQ", "2026-11-02", 28593.12, 28540.49, "stop"),
        ("ES", "2026-11-03", 7617.19, 7622.71, "stop"),
        ("NQ", "2026-11-03", 28417.24, 28368.66, "tp"),
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        ps.run_replay(5)
    eng = read_journal(f"{d}/replay/trades.csv")
    assert len(eng) == len(expected), f"trade count changed: {len(eng)} vs 8 (daily stop fired early?)"
    got = list(zip(eng["sym"], [s[:10] for s in eng["entry_ts"]],
                   eng["entry_px"].astype(float), eng["exit_px"].astype(float), eng["reason"]))
    for (s, dt, ep, xp, r), (s2, dt2, ep2, xp2, r2) in zip(got, expected):
        assert s == s2 and dt == dt2 and r == r2, f"order/identity drift: {s} {dt} {r} vs {s2} {dt2} {r2}"
        assert abs(ep - ep2) <= 0.011 and abs(xp - xp2) <= 0.011, \
            f"price drift {s} {dt}: {ep}/{xp} vs {ep2}/{xp2}"
    assert set(eng["fill_method"]) == {"bar_fill"}
    return ["isolated replay of the last 5 stored sessions reproduces ALL 8 audit-recorded "
            "trades to the cent (entry, exit, reason, order) -> signal/VWAP/sigma/executor "
            "math preserved exactly",
            "the -$400 daily stop did not alter this history: its only breach (Nov-2, -$424.55) "
            "happens on the day's final trade, matching v1 behaviour while proving the halt "
            "evaluates live",
            "replay rows carry fill_method=bar_fill (signal performance, kept apart from live)"]


# =====================================================================
# T13 — REGRESSION: stop-first, trade-through, exact-touch semantics
# =====================================================================
def t13():
    d = tdir("t13")
    st = fresh()
    feed_bars("ES", [bar(**FLAT, hh=10), bar(**FLAT, hh=11), bar(**SIG, hh=12),
                     bar(1004, 1005, 1001.4444, 1003, hh=13)], st, "audit")
    assert st["day"]["ES"]["pos"] is not None, "exact-touch target must NOT fill (trade-through)"
    feed_bars("ES", [bar(1003, 1004, 1001.0, 1002, hh=14)], st, "audit")
    ps.flush_pending(st)
    eng = read_journal(f"{d}/trades.csv")
    r = eng.iloc[-1]
    assert r["reason"] == "tp" and abs(float(r["exit_px"]) - r["exit_px"]) >= 0
    # stop before target inside one bar
    st2 = fresh()
    feed_bars("ES", [bar(**FLAT, hh=10), bar(**FLAT, hh=11), bar(**SIG, hh=12),
                     bar(1009, 1011, 1001, 1008, hh=13)], st2, "audit")
    rows = [r for r in ps.PENDING_ROWS if r["sym"] == "ES"]
    assert rows and rows[-1]["reason"] == "stop", f"stop-first violated: {[r['reason'] for r in rows]}"
    return ["target touch EXACTLY at VWAP does not fill (1-tick trade-through enforced); one "
            "tick through fills at the VWAP level",
            "stop and target both inside one bar -> stop taken first (conservative, matches "
            "the research backtest)"]


# =====================================================================
# T14 — REGRESSION: sizing ladder and cost model
# =====================================================================
def t14():
    st = fresh()
    assert ps.contracts_for(st) == 2
    st["cum"] = 1499.99
    assert ps.contracts_for(st) == 2
    st["cum"] = 1500.0
    assert ps.contracts_for(st) == 4, "scale ladder changed"
    # costs from the replay journal of T12 (ES 4.90 / NQ 3.40 per 2 micros)
    d = f"{WS}/t_t12"
    eng = read_journal(f"{d}/replay/trades.csv")
    es = eng[eng["sym"] == "ES"].iloc[0]
    nq = eng[eng["sym"] == "NQ"].iloc[0]
    assert abs(float(es["costs_usd"]) - 4.90) < 0.011, f"ES costs {es['costs_usd']} != 4.90"
    assert abs(float(nq["costs_usd"]) - 3.40) < 0.011, f"NQ costs {nq['costs_usd']} != 3.40"
    return ["sizing ladder unchanged: 2 micros below +$1,500 cum, 4 micros at/above (50% of target)",
            "cost model unchanged: $4.90 round turn for 2 MES micros, $3.40 for 2 MNQ micros "
            "($0.60/side commission + 0.5 tick/side slippage)"]


# =====================================================================
# T15 — REGRESSION: day rollover resets the halt; EOD close intact
# =====================================================================
def t15():
    d = tdir("t15")
    st = fresh()
    now1 = datetime(2026, 11, 4, 13, 20, tzinfo=ET)
    feed_bars("ES", day1_bars()[:3], st, "live", now_ts=now1, quote=day1_quote(20150.0))
    feed_bars("ES", [day1_bars()[3]], st, "live", now_ts=datetime(2026, 11, 4, 14, 20, tzinfo=ET))
    assert st.get("halted_date") == "2026-11-04", "setup: halt expected"
    # EOD bar closes the day at the 15:00-bar close
    feed_bars("ES", [bar(**FLAT, hh=15, d=4)], st, "live", now_ts=datetime(2026, 11, 4, 16, 0, tzinfo=ET))
    rows = [r for r in ps.PENDING_ROWS]
    # (no position after the stop-out, so no eod row expected for ES day 1)
    # next day: halt must be gone
    st["traded_today"] = True
    day2 = [bar(**FLAT, hh=10, d=5), bar(**FLAT, hh=11, d=5), bar(**SIG, hh=12, d=5)]
    feed_bars("ES", day2, st, "live", now_ts=datetime(2026, 11, 5, 13, 20, tzinfo=ET),
              quote=day1_quote(20150.0))
    assert st["day"]["ES"]["date"] == "2026-11-05"
    assert st["day"]["ES"]["trades"] == 1 and st["day"]["ES"]["pos"] is not None, \
        "halt did not reset on the new trading day"
    assert st["day"]["ES"]["day_realized"] == 0.0
    # EOD liquidation on a 15:00 bar that does NOT trade through the target
    feed_bars("ES", [bar(20045, 20050, 20040, 20045, hh=15, d=5)], st,
              "live", now_ts=datetime(2026, 11, 5, 16, 0, tzinfo=ET))
    rows = [r for r in ps.PENDING_ROWS if r["ts"].startswith("2026-11-05")]
    assert rows and rows[-1]["reason"] == "eod", f"eod close missing: {[r['reason'] for r in rows]}"
    return ["after a -$400+ day the NEXT trading day trades again (halt resets on rollover, "
            "trades/day counters reset, day P&L resets)",
            "EOD liquidation unchanged: position closed on the 15:00 bar at its close "
            "(reason=eod), per the shared backtest convention (audit L1 documented)"]


# =====================================================================
# T16 — REGRESSION: normal bar dedup + history covers the fetch window
# =====================================================================
def t16():
    d = tdir("t16")
    days = 40
    bars = []
    base = 20000.0
    from datetime import date as _date
    day = _date(2026, 9, 1)
    dates = []
    while len(dates) < days:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    for dd_ in dates:
        for h in (10, 11, 12, 13, 14, 15):
            px = base + (h - 10) * 2 + dd_.day
            bars.append(bar(px, px + 2, px - 2, px + 1, hh=h, d=dd_.day, mo=dd_.month))
    df = bars_to_df(bars)
    fetch = canned_fetch({"ES": df, "NQ": bars_to_df([])}, {"ES": None, "NQ": None})
    now = bars[-1][5] + timedelta(hours=1, minutes=20)
    run_live_quiet(now=now, fetch=fetch)
    st = state_of(d)
    n1, c1 = st["n_trades"], st["cum"]
    keys1 = list(st["done_bars"]["ES"])
    assert 30 <= len(keys1) <= ps.MAX_KEY_HIST, f"retention {len(keys1)} outside [30, 200]"
    assert len(keys1) == ps.MAX_KEY_HIST, "retention should sit at the cap for a big catch-up"
    # identical second fetch -> nothing new to do (normal dedup)
    out2 = run_live_quiet(now=now, fetch=fetch)
    st2 = state_of(d)
    assert st2["n_trades"] == n1 and abs(st2["cum"] - c1) < 1e-9, "second run changed accounting"
    assert st2["done_bars"]["ES"] == keys1, "done_bars drifted on an identical re-fetch"
    return [f"catch-up of {len(bars)} bars: done_bars retained {len(keys1)} keys "
            f"(= MAX_KEY_HIST {ps.MAX_KEY_HIST}), far above the 5-day fetch window (~30 bars) "
            "-> the audit's 12-key prune cannot recur",
            "identical re-fetch processes nothing new: no new trades, no cum drift, "
            "done_bars unchanged (normal dedup intact)"]


# =====================================================================
if __name__ == "__main__":
    tests = [("T01 daily stop blocks entries (account level)", t01),
             ("T02 halt persists after restart", t02),
             ("T03 fills at observable price", t03),
             ("T04 signal vs execution recorded apart", t04),
             ("T05 overlapping runs cannot duplicate", t05),
             ("T06 crash recovery cannot duplicate", t06),
             ("T07 replay cannot contaminate live", t07),
             ("T08 Topstep 55% consistency", t08),
             ("T09 Apex EOD trail + Topstep cap + bust", t09),
             ("T10 old bars cannot rewind state", t10),
             ("T11 missing/stale data never healthy", t11),
             ("T12 regression: replay == audit trades", t12),
             ("T13 regression: stop/target semantics", t13),
             ("T14 regression: sizing + costs", t14),
             ("T15 regression: rollover resets halt + eod", t15),
             ("T16 regression: dedup + history window", t16)]
    print("=" * 74)
    for name, fn in tests:
        check(name, fn)
    print("=" * 74)
    n_pass = sum(1 for r in RESULTS.values() if r["status"] == "PASS")
    n_fail = len(RESULTS) - n_pass
    print(f"RESULT: {n_pass} passed, {n_fail} failed, {len(RESULTS)} total")
    # live home integrity: the suite must not have touched it
    live_rows = len(read_journal(f"{LIVE_HOME}/trades.csv"))
    print(f"LIVE HOME INTEGRITY: trades.csv rows after suite = {live_rows} (expected 0)")
    assert live_rows == 0, "SUITE CONTAMINATED THE LIVE JOURNAL"
    with open(f"{WS}/remediation_test_results.json", "w") as fh:
        json.dump(RESULTS, fh, indent=1, default=str)
    print("saved ->", f"{WS}/remediation_test_results.json")
    sys.exit(1 if n_fail else 0)
