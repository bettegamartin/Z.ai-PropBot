# VWAP Fade Paper-Trading Remediation Report

**Date:** 5 November 2026 (work window 20:29–21:10 ET, 4–5 Nov)
**Scope:** the MES/MNQ VWAP Fade v1 paper-trading research system only — `scripts/paper_step.py`, its state/journal/diary under `download/paper_trading/`, and its isolated test workspace.
**Inputs:** `reports/vwap_fade_paper_trading_independent_audit.md` (the defect list) and `download/futures_prop_bot_research.pdf` — "Prop Evaluation Bot Research — November 4, 2026" (§2.2, §5, Table 4, Table 8, §7.1) as the authoritative specification.
**Mandate:** P0.1–P0.2 and P1.1–P1.4 remediation with isolated tests. No strategy optimisation, no performance evaluation, no parameter changes.

---

## 1. Executive summary

All six mandated remediation items are implemented and demonstrated by tests: **16 of 16 isolated tests pass**, including the eleven tests the task requires and five regression tests protecting behaviour the audit had verified as correct.

The most important correctness proof: an isolated replay of the last five stored sessions still reproduces **all 8 audit-recorded trades to the cent** (entry, exit, reason, order). The signal engine — VWAP, sigma, bands, stops, targets, executor ordering — is bit-for-bit the configuration the audit independently verified against the research specification.

During testing the suite caught and I fixed **two real defects in my own first implementation** (a floating-point artifact that let a best-day of exactly $1,650 pass the 55% cap, and an Apex floor that computed before the EOD high-water update). Both are covered by dedicated assertions now. Nothing was hidden or downgraded.

The live paper-trading home was de-contaminated (the 8 replay rows the audit found in the live journal are archived; the journal now starts empty) and its state migrated to schema v2 (the rewound Nov-2 day state is gone; `last_bar_ts` is seeded). The scheduled cron poll was **not** modified and will run engine v2 automatically at its next window (10:00 ET, 5 November 2026).

**Ready for a fresh independent audit: YES** at the code level. The journal starts from zero trades, so any statistical judgement still requires the proposed multi-session paper run, which has **not** been started, per instruction 6.

## 2. Safety and scope compliance

- **Production PropBot (Tradovate eval + NT8 bridge): never accessed, connected to, modified, stopped or restarted.** This sandbox has no connectivity to Tradovate or any NT8 installation, holds no production credentials, and has no order-routing capability. No production file, setting, process or schedule was touched. The production bot does not exist inside this environment; nothing in this task could reach it.
- **Scheduler untouched:** cron job #503762 (`paper_step.py --live`, every 20 min 10:00–16:40 ET Mon–Fri) was neither modified, paused nor re-created. It will simply execute the remediated engine file at its next fire.
- **Strategy parameters preserved and verified:** k=2.0, stop_k=1.2, trend filter 0.6%, max 3 trades/day/symbol, one position per symbol, 12:00/13:00 signal bars (the backtest's effective window), 15:00-bar EOD flat, sizing ladder 2→4 micros at +$1,500, $0.60/side commission + 0.5 tick/side slippage — all unchanged (regression tests T12–T16).
- **Isolation:** all tests ran in `remediation_workspace/t_*/` directories via `paper_step.set_paths()`; market data was canned; the only live-network call in testing was a single read-only smoke poll in an isolated home. The suite ends with an integrity check that the live journal still has zero rows — it passed on every run.

## 3. Files changed

| File | Change |
|---|---|
| `scripts/paper_step.py` | Engine rewritten to v2 (backup: `remediation_workspace/paper_step_pre_remediation.py.bak`). All P0/P1 items below. |
| `scripts/remediate_data.py` | NEW — one-time, idempotent data remediation (journal de-contamination + state migration). |
| `scripts/remediation_tests.py` | NEW — the 16-test isolated suite. |
| `download/paper_trading/trades.csv` | De-contaminated: 8 `mode=replay` rows removed (archived), now header-only with the v2 19-column schema. Zero live trades existed (state counts 0), so no genuine history was lost. |
| `download/paper_trading/log.md` | v1 diary archived; fresh v2 diary started with an explanatory header. |
| `download/paper_trading/state.json` | Migrated to schema v2: rewound Nov-2 `day` entries pruned, `last_bar_ts` seeded from `done_bars` (2026-11-04T15:00 both symbols), `best_day` added to overlays, intraday-trail `hwm` artifact removed. `cum`/`n_trades` (0) preserved. |
| `download/paper_trading/archive/pre_remediation_2026-11-05/` | NEW — archived originals + README explaining the contamination. |
| `remediation_workspace/` | NEW — pre-remediation backups, 16 isolated test homes, `remediation_test_results.json`. |
| `reports/vwap_fade_paper_trading_remediation.md` (+ copy in `download/reports/`) | This report. |

**Not changed:** `scripts/02_backtest.py`, `scripts/audit_tests.py`, everything under `download/data/`, the research PDF/XLSX, the audit report, and all scheduler configuration.

## 4. Defect-by-defect remediation

### P0.1 — Daily loss limit is now a genuine halt (audit C1)

- **Rule implemented:** account-level halt — when realized day P&L across both symbols reaches **−$400**, no new entries on **any** symbol until the next trading day. Interpretation documented: the research states the stop at account level (§2.2 "a daily loss stop ($400 on 50K accounts)"; Table 8 "Halt for the day at −$400 (50K)"), and it sits below the firm DLLs exactly as designed. A per-symbol-only halt would allow a combined −$800 day, which is not what the research models.
- **Mechanics:** `check_daily_stop()` runs after every close and sets `st["halted_date"]` + per-symbol `halted`/`halted_reason` (persisted in `state.json`, survives restarts). The entry gate checks **both** the persisted flag **and** the live condition `account_day_pnl > -$400`, so a restart or any state inconsistency still cannot enter past the limit. Reset is implicit and total: `halted_date` is compared against each bar's date, so the first bar of a new trading day re-enables entries; day counters reset with the day state.
- **Evidence:** T01 — crafted session stops out for **−$497.57**, `halted_date` set, the same bar's band re-touch produced **no** re-entry (v1 would have re-entered), and a same-day NQ signal was blocked (account level). T02 — cold reload from disk still blocks, and with the flag deliberately deleted the −$450 condition alone blocks (this is the audit's test-D scenario, now passing the other way). T15 — the next trading day trades again.

### P0.2 — Live fills are executable prices; signal and execution recorded apart (audit C2)

- **Signal record:** `signal_px` (the backtest-semantics band price, e.g. `max(VWAP+2σ, bar open)`) and `signal_ts` (the signal bar's stamp) are stored on the position and journaled, but **never used as a live fill**.
- **Execution record:** in `--live` the fill is the first price observable when the engine acts —
  - `poll_quote`: the forming bar's latest price from the same fetch (Yahoo's current indicative price at poll time);
  - `last_completed_close`: when no forming price exists but the newest completed bar closed ≤ 40 min ago (recorded as its own method, never silently mixed);
  - if neither exists, the signal is **not taken** and the diary says so explicitly (`signal ... NOT taken: no fresh observable price`).
  Execution timestamp = the acting poll time; `exec_lag_min` records signal-close → execution delay; `fill_method` records the price class used.
- **Stop/target recomputed from the fill** (spec: "1.2σ from entry"): stop = fill ± 1.2σ, target = signal-bar VWAP (unchanged level). A fill already beyond the target at action time exits immediately **at that observable price** (`reason=tp_poll`, friction-only P&L) — no backdated target profit is possible.
- **Live exits cannot look backward:** exit checks on bars that closed before the entry was actioned are skipped (`predates` guard) — a resting stop/target could not have existed then. Resting-order exits on post-entry bars keep the research's fill semantics (stop-market at trigger, limit at VWAP with 1-tick trade-through), which the audit verified.
- **Costs:** unchanged and NOT double-counted — slippage remains the 0.5 tick/side term in `costs_usd`; the observable price is booked as-is.
- **Signal vs executable kept apart:** replay rows are `fill_method=bar_fill` (signal performance); live rows are `poll_quote`/`last_completed_close` (executable performance). The `signal_px`/`entry_px` pair makes the delay cost measurable per trade.
- **Evidence:** T03 — entry booked at observable 20150.00 while the band price 20111.00 is recorded as signal; stop 20199.27 anchored to the fill; instant-target case books −$4.90 (friction only); quote-less poll takes nothing. T04 — journal schema and row values. T11(d/e) — skip-note and fallback paths.
- **Documented caveat:** Yahoo futures prices are delayed/indicative and bid/ask does not exist in this feed; the delay is not measurable from here and is not compensated. `fill_method` exists precisely so a later audit can treat price classes differently. A sanity guard rejects a forming price >5% away from the last completed close.

### P1.1 — Duplicate execution and journal entries prevented (audit H3)

- **Concurrency guard:** every `--live` run takes an exclusive `flock` on `<home>/.lock`; a second overlapping run prints `SKIP: another paper_step --live run holds the lock` and writes nothing.
- **Unique trade IDs:** `trade_id = <entry-date>_<SYM>_<seq>` — deterministic from the processed bars, so a recovery re-run regenerates the *same* IDs.
- **Transactional commit:** trade rows are buffered during the run and committed once, in order journal → equity → diary → `state.json` last. A crash after the journal write but before the state save is recovered by the next run, which re-processes the same bars, regenerates the same trade IDs, and **skips** rows whose ID is already on disk (with a `JOURNAL DEDUP` notice). A truncated tail line (crash mid-write) is detected and repaired on load.
- **Evidence:** T05 — lock-holder SKIP with zero writes; overlap replay (pre-run state + post-run journal) stays at 1 row with unique IDs and reconciling state; two concurrent threads → exactly one processed. T06 — injected crash before state save → 1 row on disk, state absent; recovery run leaves the journal at exactly 1 row with `n_trades=1` and matching cum; malformed tail repaired in place. This is the mechanism that produced the duplicate rows reported from the earlier crashed replay — now closed.

### P1.2 — Replay and live records separated (audit H6)

- `--replay` writes only to `<home>/replay/` (own `trades.csv`/`state.json`/`log.md`), restores live paths afterwards, and **aborts outright** if the resolved replay directory would be the live root. Replay state is separate; live accounting never reads replay rows.
- **Live journal de-contaminated:** the 8 replay rows are archived in `download/paper_trading/archive/pre_remediation_2026-11-05/` with a README; the live journal now starts clean, so CSV↔state reconciliation is possible by construction.
- **Evidence:** T07 — replay leaves live paths nonexistent, writes `replay/trades.csv`, the abort guard fires under injection, and a manually injected `mode=replay` row is inert for live accounting.

### P1.3 — Evaluation overlays corrected to the research specification (audit H1, H2)

- **Topstep 55% consistency (Table 4/5: "best day < 55% of target"):** `overlay_update` now tracks `best_day` per attempt at every EOD summary and **refuses to declare PASS** while `best_day ≥ $1,650` (55% of $3,000), emitting an explicit message instead. Boundary verified: exactly $1,650 blocks; $1,600 passes and charges the $149 activation. (A float artifact — `0.55×3000 = 1650.0000000000002` — initially let the exact boundary pass; caught by test T08, fixed with a rounded cap, re-tested.)
- **Apex EOD trail (Table 4: "$2,500 EOD trail"):** the intraday trail is removed. Both overlays derive their floor from `hwm_eod`, the high-water mark of **closed equity at day summaries**; intraday open-P&L spikes never raise the floor. Topstep keeps its breakeven cap (floor ≤ $0); Apex's floor is uncapped per the research's plain "$2,500 EOD trail" wording. Bust detection still evaluates current equity against the floor (a live breach is real) — unchanged and correct.
- **Nothing silently substituted.** Rules from the research that are still **not** implemented are listed in §6, unchanged from the audit's findings: Apex 30-day window (M6), monthly rebill fees (M6), Topstep DLL enforcement beyond a message (M1). The DLL message is kept verbatim; the enforced −$400 bot stop sits well inside it (Table 8's rationale), but open-position slippage past −$400 could still print the message — documented, not hidden.
- **Evidence:** T08 (consistency gate + boundary + Apex's correct lack of a cap), T09 (floor frozen intraday at −$2,500 while equity showed +$2,900; EOD close $1,000 → floor −$1,500; later spike leaves it; Topstep caps at $0; bust-at-current-equity still fires).

### P1.4 — Stale market data cannot rewind state or masquerade as healthy (audit H4)

- **No rewind:** `last_bar_ts` per symbol (seeded from the migrated state) rejects any bar at or older than the last processed bar; such bars are recorded in `rejected_bars` (retained 200 keys) and warned about in the status line ("N stale/out-of-order bar(s) rejected (no rewind)"). The 12-key `done_bars` prune that let the Nov-2 session re-enter is gone: retention is 200 keys per symbol (~33 sessions vs the ~30-bar 5-day fetch window).
- **Feed health is derived and per-symbol:** `ok` (forming price present) / `awaiting first completed bar` (early RTH) / `stale (<detail>)` (newest completed bar >100 min old during RTH, or no completed bar today after 11:00 ET) / `unavailable`. The status line now always shows `feed ES:<status> NQ:<status>` — a healthy ES can no longer mask an unhealthy NQ, and a stale feed is never printed as healthy. Warnings also go to state and diary.
- **Entries require an observable price:** no forming quote and no fresh completed close ⇒ no entry, with an explicit diary note; the v1 40-minute signal-freshness gate is preserved (expired signals are never acted on).
- **Evidence:** T10 — six re-offered Nov-2 bars all rejected, accounting untouched, new Nov-5 bar processed forward-only; T11 — unavailable/stale/awaiting/ok classifications, per-symbol display, expired-signal and no-price skips, fallback entry tagged `last_completed_close`.

## 5. Regression tests — audit-verified behaviour preserved

| Test | Protects | Result |
|---|---|---|
| T12 | Signal math: replay of the last 5 stored sessions reproduces **all 8 audit-recorded trades to the cent** (order, entry, exit, reason); also proves the −$400 halt does not alter this history (its only breach, Nov-2 −$424.55, occurs on the day's final trade) | PASS |
| T13 | Exact-touch target does not fill (1-tick trade-through); stop-before-target inside one bar | PASS |
| T14 | Sizing ladder 2→4 micros at +$1,500; costs $4.90 (2 MES) / $3.40 (2 MNQ) round turn | PASS |
| T15 | Day rollover resets halt/counters/P&L; EOD liquidation on the 15:00 bar | PASS |
| T16 | Normal re-fetch dedups silently; retained key history = 200 ≫ fetch window | PASS |

## 6. Test results — full list (actual runs, `remediation_workspace/remediation_test_results.json`)

| # | Required demonstration | Result |
|---|---|---|
| 1 | Daily loss limit prevents further entries | PASS — −$497.57 day ⇒ halt; re-entry and cross-symbol entries blocked |
| 2 | Halt persists after restart | PASS — cold reload blocks; condition-check also blocks with the flag lost |
| 3 | Fills use realistically observable prices | PASS — poll_quote 20150.00 booked; band 20111.00 only recorded; tp_poll friction-only |
| 4 | Signal and execution prices/times recorded separately | PASS — 19-column schema, distinct values, `exec_lag_min` 20 |
| 5 | Overlapping runs cannot duplicate | PASS — flock SKIP; overlap dedup; concurrent threads → 1 unique row |
| 6 | Crash recovery cannot duplicate journal entries | PASS — crash injected, recovery dedups, state/journal reconcile; tail repair works |
| 7 | Replay cannot contaminate live records | PASS — isolated `replay/`, abort guard, de-contaminated live journal |
| 8 | Topstep consistency correctly enforced | PASS — $1,800/$1,650 block, $1,600 passes with $149 fee, Apex uncapped by design |
| 9 | Apex drawdown uses the specified EOD trail | PASS — floor frozen intraday, updated only at EOD from closed-equity HWM |
| 10 | Old bars cannot rewind state | PASS — 6 stale bars rejected + warned; state moved forward only |
| 11 | Missing/stale data never reported healthy | PASS — unavailable/stale/awaiting/ok per symbol; entries gated on observable prices |
| 12–16 | Regressions (T12–T16 above) | PASS |

**Totals: 16 passed, 0 failed.** Plus an end-to-end smoke test against the real Yahoo feed in an isolated home: `LIVE Wed 11-04 20:53 ET | cum $+0.00 | open: flat | … | feed ES:ok NQ:ok`, day summary written, 24 keys/symbol retained, exit 0 — confirming the cron path runs v2 cleanly. `--status` on the real home is read-only (state MD5 verified unchanged).

**No new performance evaluation was run.** The replay in T12 is an equivalence check against the audit's recorded trades, not a performance claim.

## 7. Remaining defects and limitations (unchanged or newly known)

Carried from the audit, **out of the mandated P0/P1 scope**, unmodified:

1. **M1** — Topstep $1,000 DLL remains message-only (no flatten). The −$400 realized-stop makes a breach unlikely but open-position drawdown past −$400 can still reach it.
2. **M2** — No contract-roll guard; the December roll will inject artificial gaps into `ES=F`/`NQ=F` (the research backtest excluded roll weeks).
3. **M3 / L1** — A missing 15:00 bar still leads to a retroactive `eod_missed` close; the EOD fill uses the 15:00 bar's close (16:00 ET price), shared with the research backtest.
4. **M4 (partial)** — The journal now carries `trade_id`, `signal_px/signal_ts`, `fill_method`, `exec_lag_min`, but per-trade stop/target/σ-at-entry still live only in the diary (P2 item).
5. **M5 (mitigated, not solved)** — EOD summaries still depend on a ≥16:00 ET poll succeeding; every run now leaves an immediate diary delimiter, so a silent failure is detectable.
6. **M6** — Apex 30-day window and monthly rebill fees not modeled.
7. **H5** — Same-bar re-entry after a stop-out (engine) vs the backtest's prohibition is **deliberately preserved**: changing it would alter strategy behaviour, which this task forbids; the audit quantified the divergence at ~6.3% of sessions.
8. **Feed reality** — Yahoo futures prices are delayed/indicative; no bid/ask; delay not measured (requires an intraday probe the current window cannot make). All fills are last-trade based with 0.5 tick/side slippage in costs.
9. **New, minor:** `--status` displays `feed: {}` until the first live poll of the day populates `feed_status` (cosmetic).

## 8. Production PropBot — explicit confirmation

The live Tradovate evaluation account, the NT8 bridge, any brokerage connection, production credentials, production files, processes and schedules were **not accessed, connected to, modified, stopped or restarted** — no such system is reachable from this environment, and no attempt was made. All work touched only the paper-trading research system named in the mandate. No real order of any kind was placed; nothing here can place one.

## 9. Readiness for a fresh independent audit

**Ready — yes, at the code and data level.** Every P0/P1 defect has a passing demonstration test, the signal engine is proven unchanged against the audit's recorded trades, the live journal starts clean with a reconciliation-possible design, and the scheduler will run v2 at the next poll without intervention. The reusable audit bench (`scripts/audit_tests.py`) will need small updates where it asserts v1 behaviour (journal schema, replay paths, fetch signature) — noted for the auditor, not patched silently here.

What a fresh audit still cannot judge until data accumulates: executable performance, since the de-contaminated journal starts at zero trades. Per instruction 6, the proposed 10-session paper-trading test and any performance evaluation have **not** been started and await approval.

**Stopping here as instructed** — remediation report presented; awaiting review and approval before a fresh independent audit or the 10-session paper-trading test begins.
