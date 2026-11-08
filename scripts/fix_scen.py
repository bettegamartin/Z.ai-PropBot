"""Scenario data for the POST-AUDIT FIX VERIFICATION bench (fix stage).

Fresh crafted sessions (numbers chosen independently of both earlier
benches). Includes an independent re-implementation of the session
VWAP / sigma / band math used for expected-value cross-checks.

Main day (ES/NQ, 2026-11-06 Friday) -- N1 organic test:
  * 12:00 ES bar = SHORT signal; the 12:20 poll fills at the observable
    quote 6220.00 (band 6139.72 recorded separately).
  * 13:00 bar carries the short to its target (session VWAP) -> the close
    pushes the account day P&L to ~ +$1,806, past the $1,650 Topstep
    consistency cap -> check_daily_cap must fire in the same bar_step.
  * the SAME poll also offers an NQ 13:00 SHORT signal -> must be BLOCKED
    by the account-level cap gate (cross-symbol, organic).
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")


def T(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def indep(bars):
    """Independent per-bar VWAP/sigma/bands. bars: (o,h,l,c,v)."""
    vn = vd = 0.0
    devs = []
    out = []
    for o, h, l, c, v in bars:
        vn += (h + l + c) / 3 * v
        vd += v
        vwap = vn / vd
        devs.append(c - vwap)
        sd = float(np.std(devs, ddof=1)) if len(devs) >= 2 else 0.0
        out.append(dict(vwap=vwap, sd=sd, up=vwap + 2 * sd, dn=vwap - 2 * sd))
    return out


def bars_df(stamps, rows):
    idx = pd.DatetimeIndex([datetime.fromisoformat(s) for s in stamps], name="ts")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)


# --- ES cap day: 2026-11-06 (Friday). 12:00 SHORT signal; 13:00 bar hits target.
ES_CAP_STAMPS = [
    "2026-11-06T10:00:00-05:00", "2026-11-06T11:00:00-05:00",
    "2026-11-06T12:00:00-05:00", "2026-11-06T13:00:00-05:00",
]
ES_CAP = [
    (6000., 6000., 6000., 6000., 1000.),   # 10:00
    (6000., 6150., 5850., 6100., 1000.),   # 11:00 (wide range -> big sigma)
    (6100., 6200., 6050., 6030., 1000.),   # 12:00 SHORT signal
    (6030., 6060., 6000., 6020., 900.),    # 13:00 -> target hit, no new signal
]
ES_CAP_EOD_STAMPS = ES_CAP_STAMPS + ["2026-11-06T14:00:00-05:00"]
ES_CAP_EOD = ES_CAP + [(6020., 6040., 6010., 6030., 800.)]   # 14:00 (no entry, hour 14)

# --- NQ same day: 13:00 SHORT signal offered right after the cap fires.
NQ_CAP_STAMPS = [
    "2026-11-06T10:00:00-05:00", "2026-11-06T11:00:00-05:00",
    "2026-11-06T13:00:00-05:00",
]
NQ_CAP = [
    (25000., 25000., 25000., 25000., 1000.),
    (25000., 25350., 24750., 25100., 1000.),
    (25100., 25400., 25050., 25120., 800.),
]

# --- next trading day 2026-11-09 (Monday): cap reset -> entries allowed again
ES_MON_STAMPS = [
    "2026-11-09T10:00:00-05:00", "2026-11-09T11:00:00-05:00",
    "2026-11-09T12:00:00-05:00",
]
ES_MON = [
    (6000., 6000., 6000., 6000., 1000.),
    (6000., 6150., 5850., 6100., 1000.),
    (6100., 6200., 6050., 6030., 1000.),
]

_S = indep(ES_CAP)
ES_SIG12 = dict(vwap=_S[2]["vwap"], sd=_S[2]["sd"], band=_S[2]["up"],
                signal_px=max(_S[2]["up"], ES_CAP[2][0]))
NQ_S = indep(NQ_CAP)
NQ_SIG13 = dict(vwap=NQ_S[2]["vwap"], sd=NQ_S[2]["sd"], band=NQ_S[2]["up"],
                signal_px=max(NQ_S[2]["up"], NQ_CAP[2][0]))

FILL_P1 = 6220.0          # observable price at the 12:20 poll (worse than band)
ES_NET_EXPECT = (ES_SIG12["vwap"] - FILL_P1) * -1 * 5.0 * 2 \
    - (0.60 * 2 + 0.5 * 1.25 * 2) * 2          # target close, 2 micros, ES costs
CAP_USD = round(0.55 * 3000.0, 2)              # 1650.00

# Preconditions: the crafted bars MUST signal under the independent math
assert ES_CAP[2][1] >= ES_SIG12["band"], "ES 12:00 bar no longer signals"
assert abs(ES_CAP[2][3] - 6000.0) <= 0.006 * 6000.0, "ES trend filter would block"
assert NQ_CAP[2][1] >= NQ_SIG13["band"], "NQ 13:00 bar no longer signals"
assert abs(NQ_CAP[2][3] - 25000.0) <= 0.006 * 25000.0, "NQ trend filter would block"
assert ES_NET_EXPECT >= CAP_USD, f"crafted day {ES_NET_EXPECT:.2f} would not reach the cap"
assert ES_CAP[3][2] <= ES_SIG12["vwap"] - 0.25, "13:00 bar would not hit the target"
assert _S[3]["up"] > ES_CAP[3][1], "13:00 bar would re-signal (must not)"


# --- polls: key -> (now_iso, {sym: (stamps, rows, quote_px or None)})
POLLS = {
    # F1 organic cap day: entry poll, cap-firing poll, quiet poll, EOD poll, reset poll
    "P1": ("2026-11-06T12:20:00-05:00",
           {"ES": (ES_CAP_STAMPS[:3], ES_CAP[:3], FILL_P1)}),
    "P2": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_CAP_STAMPS, ES_CAP, 6160.0),
            "NQ": (NQ_CAP_STAMPS, NQ_CAP, 25100.0)}),
    "P3": ("2026-11-06T14:40:00-05:00",
           {"ES": (ES_CAP_EOD_STAMPS, ES_CAP_EOD, 6030.0),
            "NQ": (NQ_CAP_STAMPS, NQ_CAP, 25100.0)}),
    "P4": ("2026-11-06T16:20:00-05:00",
           {"ES": (ES_CAP_EOD_STAMPS, ES_CAP_EOD, 6030.0),
            "NQ": (NQ_CAP_STAMPS, NQ_CAP, 25100.0)}),
    "P5": ("2026-11-09T13:20:00-05:00",
           {"ES": (ES_MON_STAMPS, ES_MON, FILL_P1)}),
    # F2 N2 tests: one-symbol feed outage at the EOD poll
    "S1": ("2026-11-06T16:20:00-05:00",
           {"ES": (["2026-11-06T15:00:00-05:00"],
                   [(6020., 6040., 6010., 6030., 800.)], 6030.0),
            "NQ": None}),
    "S2": ("2026-11-06T16:20:00-05:00",
           {"ES": None, "NQ": None}),
}


def build_fetch(spec):
    def fetch(yh, want_quote=False):
        sym = "ES" if yh.startswith("ES") else "NQ"
        if sym not in spec or spec[sym] is None:
            return (None, None) if want_quote else None
        stamps, rows, q = spec[sym]
        df = bars_df(stamps, rows)
        quote = dict(px=float(q), ts=df.index[-1], method="poll_quote") if q is not None else None
        return (df, quote) if want_quote else df
    return fetch


def get_poll(key):
    now_iso, spec = POLLS[key]
    return datetime.fromisoformat(now_iso), build_fetch(spec)


# Signal bar + quote used by the cold gate probe (fix_w_gate.py): Nov-9 12:00
GATE_BAR_TS = datetime.fromisoformat("2026-11-09T12:00:00-05:00")
GATE_BAR = (*ES_MON[2], GATE_BAR_TS)
GATE_NOW = datetime.fromisoformat("2026-11-09T13:20:00-05:00")
GATE_QUOTE_PX = FILL_P1
