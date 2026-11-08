"""Scenario data for the FRESH post-remediation audit bench (audit-only).

Crafted ES/NQ sessions designed by the second auditor with numbers chosen
INDEPENDENTLY of the remediator's test data. Includes an independent
re-implementation of the session VWAP / sigma / band math used for
preconditions and cross-checks (this is the auditor's own numpy code, not
the engine's).
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")


def T(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def indep_session(bars):
    """Auditor's independent per-bar VWAP/sigma/bands. bars: (o,h,l,c,v)."""
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


# --- ES halt day: 2026-11-06 (Friday). 12:00 bar = SHORT signal; 13:00 bar stops it out.
ES_FRI_STAMPS = [
    "2026-11-06T10:00:00-05:00", "2026-11-06T11:00:00-05:00",
    "2026-11-06T12:00:00-05:00", "2026-11-06T13:00:00-05:00",
]
ES_FRI = [
    (6000., 6000., 6000., 6000., 1000.),   # 10:00
    (6000., 6040., 5700., 6020., 1000.),   # 11:00
    (6020., 6050., 5682., 6034., 1000.),   # 12:00 SHORT signal (h >= vwap+2sd)
    (6034., 6100., 6030., 6095., 900.),    # 13:00 stops the short out (h 6100 >= stop)
]
# --- NQ same-day bars: signal on the 13:00 stamp (fresh at a 14:40 poll)
NQ_FRI_STAMPS = [
    "2026-11-06T10:00:00-05:00", "2026-11-06T11:00:00-05:00",
    "2026-11-06T13:00:00-05:00",
]
NQ_FRI = [
    (25000., 25000., 25000., 25000., 1000.),
    (25000., 25200., 24800., 25150., 1000.),
    (25150., 25300., 25000., 25140., 1000.),
]
# --- next trading day 2026-11-09 (Monday): identical shapes, re-dated
ES_MON_STAMPS = [
    "2026-11-09T10:00:00-05:00", "2026-11-09T11:00:00-05:00",
    "2026-11-09T12:00:00-05:00",
]
ES_MON = [r for r in ES_FRI[:3]]

# Precomputed independent expectations for the ES Friday signal bar (index 2)
_S = indep_session(ES_FRI)
ES_SIG = dict(
    vwap=_S[2]["vwap"], sd=_S[2]["sd"], band=_S[2]["up"],
    signal_px=max(_S[2]["up"], ES_FRI[2][0]),
)
NQ_S = indep_session(NQ_FRI)
NQ_SIG = dict(vwap=NQ_S[2]["vwap"], sd=NQ_S[2]["sd"], band=NQ_S[2]["up"])

# --- polls: key -> (now_iso, {sym: (stamps, rows, quote_px or None)})
POLLS = {
    # B01: entry poll, stop-out poll, cross-symbol block poll, next-day reset poll
    "A1": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_FRI_STAMPS[:3], ES_FRI[:3], 6044.0)}),
    "A2": ("2026-11-06T14:20:00-05:00",
           {"ES": (ES_FRI_STAMPS, ES_FRI, 6044.0)}),
    "A3": ("2026-11-06T14:40:00-05:00",
           {"ES": (ES_FRI_STAMPS, ES_FRI, 6096.0),
            "NQ": (NQ_FRI_STAMPS, NQ_FRI, 25100.0)}),
    "A4": ("2026-11-09T13:20:00-05:00",
           {"ES": (ES_MON_STAMPS, ES_MON, 6044.0)}),
    # B03: observable-fill recording variants
    "B1": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_FRI_STAMPS[:3], ES_FRI[:3], 6020.0)}),
    "B2": ("2026-11-06T14:20:00-05:00",
           {"ES": (ES_FRI_STAMPS, ES_FRI, 6073.27)}),
    "C1": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_FRI_STAMPS[:3], ES_FRI[:3], 5940.0)}),   # already through target
    "D1": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_FRI_STAMPS[:3], ES_FRI[:3], None)}),     # no quote -> no entry
    "D2": ("2026-11-06T13:30:00-05:00",
           {"ES": (ES_FRI_STAMPS[:3], ES_FRI[:3], 6044.0)}),   # quote arrives later
    # B11: fallback observable = newest completed close (no forming quote)
    "F1": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_FRI_STAMPS[:3], ES_FRI[:3], "fallback")}),
    "F2": ("2026-11-06T14:20:00-05:00",
           {"ES": (ES_FRI_STAMPS, ES_FRI, 6100.0)}),
    # B11: one symbol stale, the other healthy
    "G1": ("2026-11-06T13:20:00-05:00",
           {"ES": (["2026-11-04T10:00:00-05:00", "2026-11-04T11:00:00-05:00"],
                   [(6100., 6110., 6090., 6105., 1000.), (6105., 6120., 6100., 6115., 900.)], None),
            "NQ": (NQ_FRI_STAMPS, NQ_FRI, 25100.0)}),
    # B06: single-poll entry+stop-out (13:00 bar fed while still forming -- deliberate stress)
    "X1": ("2026-11-06T13:20:00-05:00",
           {"ES": (ES_FRI_STAMPS, ES_FRI, 6044.0)}),
    # B06: a later trading day closing another trade (exercises seq + repair)
    "X2": ("2026-11-09T13:20:00-05:00",
           {"ES": (ES_MON_STAMPS + ["2026-11-09T13:00:00-05:00"],
                   ES_MON + [(6034., 6100., 6030., 6095., 900.)], 6044.0)}),
    # B07: no-signal poll (contamination-inert check)
    "N1": ("2026-11-06T11:20:00-05:00",
           {"ES": (ES_FRI_STAMPS[:2], ES_FRI[:2], 6010.0)}),
    # B10: re-offered bars + new-day bars (state pre-seeded with Nov-3/Nov-4 history).
    # Nov-2 bars are OLDER than the seeded done_bars window -> hit the last_bar_ts
    # rejection path; the Nov-3 10:00 bar is inside done_bars -> silent dedup.
    "H": ("2026-11-05T10:20:00-05:00",
          {"ES": (["2026-11-02T10:00:00-05:00", "2026-11-02T11:00:00-05:00",
                   "2026-11-03T10:00:00-05:00",
                   "2026-11-05T10:00:00-05:00", "2026-11-05T11:00:00-05:00"],
                  [(6100., 6110., 6090., 6105., 1000.), (6105., 6120., 6100., 6115., 900.),
                   (6200., 6210., 6190., 6205., 800.),
                   (6300., 6310., 6290., 6305., 1000.), (6305., 6320., 6300., 6315., 900.)],
                  6310.0),
           "NQ": (["2026-11-05T10:00:00-05:00", "2026-11-05T11:00:00-05:00"],
                  [(25000., 25000., 25000., 25000., 1000.), (25000., 25060., 24940., 25050., 900.)],
                  25100.0)}),
}

# Precondition: the crafted signal bars MUST trigger under the auditor's own math
# (bar tuples are (o, h, l, c, v): short signal needs HIGH >= upper band)
assert ES_FRI[2][1] >= ES_SIG["band"], "ES Friday 12:00 bar no longer signals"
assert abs(ES_FRI[2][3] - 6000.0) <= 0.006 * 6000.0, "ES trend filter would block"
assert NQ_FRI[2][1] >= NQ_SIG["band"], "NQ Friday bar no longer signals"
assert abs(NQ_FRI[2][3] - 25000.0) <= 0.006 * 25000.0, "NQ trend filter would block"


def build_fetch(spec):
    """spec: {sym: (stamps, rows, quote)} -> fetch(yh, want_quote=False)."""
    def fetch(yh, want_quote=False):
        sym = "ES" if yh.startswith("ES") else "NQ"
        if sym not in spec:
            return (None, None) if want_quote else None
        stamps, rows, q = spec[sym]
        df = bars_df(stamps, rows)
        quote = None
        if q == "fallback":
            quote = None                      # run_live must build the fallback itself
        elif q is not None:
            quote = dict(px=float(q), ts=df.index[-1], method="poll_quote")
        return (df, quote) if want_quote else df
    return fetch


def get_poll(key):
    now_iso, spec = POLLS[key]
    return datetime.fromisoformat(now_iso), build_fetch(spec)
