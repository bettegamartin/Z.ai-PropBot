Archived 2026-11-05 during P0/P1 remediation (task: REMEDIATE VWAP FADE
PAPER-TRADING SYSTEM).

trades.csv here contains 8 rows, ALL mode=replay: these were written by
the 2026-11-04 15:42 ET --replay smoke test into the LIVE journal (audit
finding H6 -- replay/live records were not separated in engine v1). The
live state.json counts 0 live trades, so no genuine paper-trade history
was lost by starting the v2 journal empty.

log.md here is the v1 diary (replay lines + Nov-4 day summary).
state.json here is the v1 state (day entries rewound to 2026-11-02 by
the stale-feed reprocessing defect H4; done_bars covering Nov 3-4).
equity.csv (1 live mark) was preserved unchanged in the live home.

A full pre-remediation copy also lives in
remediation_workspace/backup_live_data_2026-11-05/.
