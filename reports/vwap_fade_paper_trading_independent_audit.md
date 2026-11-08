# VWAP Fade Paper-Trading Independent Audit

**Scope:** the paper-trading harness (`scripts/paper_step.py` + cron poll) that paper-trades the VWAP Fade v1 strategy on MES/MNQ micro specifications against Topstep 50K and Apex 50K evaluation overlays.
**Authoritative strategy reference:** `download/futures_prop_bot_research.pdf` (sections 2, 3, 5, 7.1, 7.2; Tables 2, 4, 8).
**Audit date:** 4 November 2026, 19:30 ET. **Mode:** audit-only. No defect was fixed, no parameter changed, no engine code rewritten, and the scheduler was not touched.
**Production safety:** the live Tradovate/NT8 production bot was never approached — this sandbox has no connectivity to Tradovate or to any NT8 installation, no credentials, and no order-routing capability of any kind. All audit work was read-only against originals; all engine writes during testing were redirected to a sandbox copy (`audit_workspace/`).

---

## 1. Executive summary

The harness's **signal mathematics is correct**: an independent re-implementation of session VWAP, the ±2σ bands, the 1.2σ stops, the trend filter and the executor reproduced **all 8 recorded replay trades exactly** (every entry price, exit price and exit reason, to the cent), and independently re-derived the engine's persisted Nov-2 VWAP state with **zero** deviation.

However, the harness **does not yet implement the researched bot**, and its first three production polls already demonstrated operational fragility:

- The **$400 daily loss stop — the core risk control in Table 8 — is not implemented** (dead config; proven behaviourally: the engine opened trade #3 on a day already −$450).
- **Consistency caps are not implemented anywhere** (Topstep "best day < 55 % of target" is absent), so the overlay can declare an evaluation "PASSED" on a path the real firm would reject.
- The Apex overlay is trailed **intraday** while the research document specifies an **EOD trail** for Apex — the overlay misimplements the rule it claims to test.
- **Live trade records are not executable prices**: entries are booked at the signal bar's open/band price, which is 60–100 minutes old when the 20-minute poll acts. Across 1,127 historical signal events the booked entry is optimistic by a mean of **+$61.94 per trade (2 micros)** versus the earliest realistically achievable fill.
- **State rewind was caught in production**: `state.json` currently holds a session state dated **2026-11-02** (two days stale) because the last poll re-processed pruned bars from a stale feed **while printing "feed ok"**. Reproduced deterministically in isolation.
- **Crash or overlapping runs duplicate trade records** (no lockfile; journal appended immediately, state saved only at end). This is the mechanism behind the duplicate rows previously reported from the crashed replay attempt; reproduced twice in isolation.
- The replay smoke test **wrote 8 rows into the live trade journal** (`mode=replay`), permanently contaminating it; the CSV and `state.json` can never reconcile by design.

**Zero live trades have occurred** (state counts 0; all journal rows are replay rows), so no paper result has yet been invalidated — but the defects above guarantee unreliable records if left uncorrected.

## 2. Overall readiness verdict

> **CONDITIONAL PASS — suitable for controlled paper trading ONLY after the specified corrections.**
>
> In its current form the live poll output must **not** be trusted as research evidence. The verdict is conditional, not FAIL, because the signal/executor core was independently verified exact against the research, and every defect found is bounded and correctable without a redesign. PASS is withheld because two spec-critical controls (daily stop, consistency) are absent and three reliability defects (stale-feed rewind, duplicate-on-crash, stale fills) were demonstrated, not merely theorised.

Gates that must pass before the live output is trusted (see §13 for detail): daily-stop enforcement, consistency-cap enforcement, fill-price policy for delayed polls, lockfile + transactional journaling, staleness detection, roll-day guard, journal de-contamination.

## 3. Architecture and file inventory

| Component | File | Inspected |
|---|---|---|
| Paper-trading engine | `scripts/paper_step.py` (430 lines) | full read |
| Strategy/executor reference | `scripts/02_backtest.py` (`sig_vwap`, `run_trades`, `session_vwap`, `load_rth`) | full read |
| Research specification | `download/futures_prop_bot_research.pdf` (§2, 3, 5, 7.1, 7.2) | text-extracted & read |
| Live state | `download/paper_trading/state.json` | full read |
| Trade journal | `download/paper_trading/trades.csv` (8 rows) | full read + recompute |
| Equity marks | `download/paper_trading/equity.csv` (1 row) | full read |
| Diary | `download/paper_trading/log.md` (20 lines) | full read |
| Market data (replay/storage) | `download/data/es_1h.csv`, `nq_1h.csv` (also 5m/15m/1d) | read + probe |
| Scheduler | cron job #503762, `0 0,20,40 10-16 ? * 2-6`, tz America/New_York | read via scheduler API |
| Scheduler history | run_count=3 (16:00/16:20/16:40 ET Nov 4), status "succeeded", next 10:00 ET | read |
| Audit test bench (new) | `scripts/audit_tests.py` + `audit_workspace/` | created by this audit |

**Environment limitations:** no connectivity to Tradovate/NT8/any broker feed (production audit impossible and correctly out of scope); no bid/ask or tick data anywhere in the environment; NT8-side behaviour cannot be observed from here.

## 4. Strategy specification versus implementation

Verified against PDF Table 8 and §2:

| Table-8 parameter | Implementation | Verdict |
|---|---|---|
| Entry: fade limit at VWAP ±2.0σ | `bar_step` L194–207, `k=2.0`; short on `High ≥ vwap+2σ` at `max(band, open)`, long symmetric | **matches backtest exactly** (test B) |
| Stop: 1.2σ from entry | `stop_k=1.2`, applied to fill price | **matches** |
| Target: session VWAP touch | `tp_px = vwap` at the entry bar (frozen thereafter) | matches backtest; note the target is the entry-time VWAP value, not the moving live VWAP — inherited from the research, acceptable for like-for-like paper testing |
| Trend filter: skip if session move > 0.6 % | `abs(close − day_open) ≤ 0.6 %·day_open`, anchor = 10:00-bar open | **matches backtest**; anchor is the 10:00 open (the research builds sessions from 10:00), not the 9:30 RTH open |
| Trade cap: 3/day/symbol, one position at a time | per-symbol `trades < 3`, single `pos` per symbol (two symbols may be in the market simultaneously — consistent with the per-symbol research) | **matches**, but see the same-bar re-entry divergence below |
| Session: signals 10:00–14:00 ET, flat 15:59 | effective signal bars are **12:00 and 13:00 only** (`len(devs) ≥ 3` excludes 10:00/11:00; `hour < 14` excludes 14:00+) | **matches the backtest** (`range(2, n)` makes 12:00 the earliest possible signal bar); the PDF's "10:00–14:00" is the nominal loop window, not the effective one. The 12:00/13:00 harness **does** test the winning configuration — verified by independent recalculation across the stored history |
| **Daily stop: halt at −$400 (50K)** | `dstop=400` configured (L47) and **never referenced**; `halted` initialised (L125) and **never set**; behavioural test: trade #3 opened with the day at −$450 | **NOT IMPLEMENTED — CRITICAL** |
| **Daily profit cap: min(day P&L, firm consistency cap)** | absent from `bar_step` and `overlay_update` | **NOT IMPLEMENTED — HIGH** |
| Sizing: 2 micros/symbol; 4 after 50 % of target | `contracts_for`: 4 when cum ≥ $1,500 | **matches** |
| Supervision: human monitors each session | the 20-minute cron replies are the monitoring surface | partially satisfied; see silent-failure finding (§8) |

**Executor deltas versus the researched backtest (both proven, not assumed):**

1. **Same-bar re-entry.** The engine permits a new entry on the very bar that closed the previous position (exits are processed before entries within `bar_step`); the backtest forbids it (`i <= trades[-1]["exit_i"]` skip in `sig_vwap`). On crafted bars the engine takes 2 trades where the backtest takes 1. Over the full stored history, **74 of 1,174 ES+NQ sessions (6.3 %) would produce a different trade count** under the two rules — the harness is slightly more aggressive than the researched configuration.
2. **Live fill timing.** In `--replay` (no wall-clock gate) the engine fills at the signal bar, identical to the backtest. In `--live`, a bar can only be acted on at a poll ≥ its close (0–20 min later, plus the 40-minute `fresh` gate), yet the fill is still booked at `max(band, signal-bar open)` — a price up to ~100 minutes old (the 13:00 signal bar is acted on at the 14:00–14:40 polls, i.e. partly outside the documented signal window in wall-clock terms). See §6.

Everything else — VWAP anchored at the 10:00 bar, typical-price × volume weighting, expanding σ (ddof=1) of close − running VWAP including the current bar, 1-tick trade-through for targets, stop-before-target, eod liquidation at the 15:00 bar close, cost model ($0.60/side + 0.5 tick/side = $2.45 MES / $1.70 MNQ round turn), ET/DST handling via `zoneinfo` and an America/New_York cron — **verified correct**.

## 5. Market-data assessment

- **Provider:** Yahoo Finance chart API, symbols `ES=F` / `NQ=F` (continuous front-month), `range=5d&interval=1h`. Signals on ES/NQ prices executed at micro specs — consistent with PDF §2.1.
- **Real-time status: not real-time.** Yahoo futures intraday data is delayed/indicative. The actual delay could not be measured at audit time (probe ran after the close); it must be treated as delayed until measured intraday. Nothing in the engine claims or compensates for delay.
- **Bid/ask: unavailable** (last-trade OHLCV only). Spread is not modelled; slippage is a flat 0.5 tick/side.
- **Volume weighting:** volume is present and sane. The engine's persisted Nov-2 VWAP accumulators match the stored file **exactly** (0.00 % volume delta, 0.000-point dev delta), and a fresh probe found **0.00-point close revision** on 18 overlapping bars — the feed is internally consistent across fetches.
- **Polling:** cron every 20 min, 10:00–16:40 ET, Mon–Fri. Each poll processes **all** un-processed closed bars (dedup via `done_bars`, 90-second completion filter), so 1-hour bars are never skipped outright — but see the rewind defect below.
- **Staleness detection: NONE.** Only an empty frame is flagged. **Proven in production:** the last poll (16:40 ET Nov 4) re-fetched bars that included the already-pruned Nov-2 session, re-processed them, and **rewound `state.json`'s day state two days** — while printing "feed ok". Reproduced deterministically in isolation (test H). Entries on stale bars happen to be blocked by the 40-minute `fresh` gate, but VWAP/σ/`day_open`/`last_close` and hence open-position marking and overlay equity are computed from the stale session.
- **Root cause:** `done_bars` is pruned to the last 12 keys per symbol (≈ 2 days) while every poll re-downloads ≈ 5 days of bars; any bar whose key fell out of the window is silently re-processed. This is a design flaw, not a one-off feed glitch.
- **Contract rollover: not handled.** The engine consumes the continuous `ES=F`/`NQ=F` series with no roll-week guard, while the research backtest explicitly excluded abnormal roll-week sessions (PDF §2.1). The December roll (≈ mid-December) will introduce artificial gaps into VWAP state and signals. **MEDIUM.**

## 6. Execution realism

- **Stop before target** inside a bar: verified (conservative, matches backtest).
- **Target requires 1-tick trade-through:** verified — a touch exactly at VWAP does **not** fill.
- **Gap-through stop fills at the trigger price, not the gapped price:** verified and quantified on crafted bars (engine net −$29.53 where a realistic stop-market yields ≈ −$44.40 → **$14.87 optimism per event** on 2 micros). This optimism is shared with the research backtest (the PDF declares conservative fills but the executor books stop exits at the trigger level regardless of gaps).
- **20-minute polling — the critical issue:** signal *detection* is reliable (bars accumulate and are processed in order), but *recorded fill prices* are not executable. Across 1,127 historical signal events, engine entry vs the earliest realistic reaction fill (next-bar open proxy) differs by mean **+$61.94**, median **+$40.60**, P95 **+$280.38** per 2-micro trade — ~25–35× the round-turn friction — and exceeds one tick in **99 %** of events. The paper equity line therefore inherits a systematic optimism that neither the backtest haircuts nor the firm overlays account for.
- **Commissions/slippage:** $2.45 MES / $1.70 MNQ round turn — matches PDF Table 2 exactly.
- **Partial fills:** not applicable (2–4 micro lots, simulated whole fills — acceptable at this size).
- **End-of-session liquidation:** executes at the 15:00 bar's close = the **16:00 ET price**, not "flat by 15:59" (1-minute miss, inherited from the backtest; LOW). If the 15:00 bar is missing from the feed, the position is **held phantom-overnight** and liquidated retroactively at the previous close under `reason=eod_missed` (test G) — **MEDIUM**.

## 7. Logging and reconciliation

- **Per-row arithmetic:** all 8 journal rows pass an independent gross/cost/net recompute to within $0.03 (engine books P&L on full-precision VWAP exits but journals rounded prices — rows are not *exactly* reproducible from the CSV alone; immaterial but violates strict reconstruction).
- **`cum_eq` chain:** internally consistent.
- **Reconstruction gaps:** the CSV carries entry/exit prices, timestamps, side, contracts, reason, gross/costs/net and cum equity — but **not the stop, target, VWAP or σ at entry** (those exist only in `log.md`), no unique trade ID, no data-source or execution-status fields.
- **Replay contamination:** all 8 rows in the live journal are `mode=replay` rows from the 15:42 smoke test; `run_replay` writes to the same `trades.csv`/`log.md` as `--live`. The journal can never reconcile with `state.json` (which counts zero trades). The previously reported duplicate rows from the crashed replay attempt are no longer present in the current file (no exact duplicates found), but the *mechanism* that produced them is confirmed and reproduced (§8, tests I/J).
- **Diary:** no wall-clock timestamps per line (bar timestamps only), no run delimiters, and a cosmetic `"- -"` double-bullet defect in day summaries.
- **Equity marks:** one row per day summary only; no intraday equity trail; the mark excludes open P&L while overlay decisions include it.

## 8. Scheduler and recovery

- **Schedule verified live:** job #503762, cron `0 0,20,40 10-16 ? * 2-6` (America/New_York) — every 20 minutes 10:00–16:40 ET, weekdays only; run_count=3; next run 10:00 ET. DST is handled by the scheduler timezone and `zoneinfo` in the engine. Weekend guard exists in the engine.
- **Silent poll failure (evidence-based):** the 16:00 ET poll left **no artifact** — no equity row, no day summary (it fired only at 16:20), no log line — although the scheduler counted the run as executed. Poll failures are therefore both possible and invisible unless the user notices a missing status line. **MEDIUM.**
- **No lockfile / no concurrency guard:** two overlapping runs both read the same `state.json` and both append to the journal. Reproduced: 2 identical rows, state counts 1. **HIGH.**
- **Crash window:** trade rows are appended to the CSV the moment they close, but `state.json` is saved once, atomically (`os.replace` — good), at the very end. A crash in between loses the run's `done_bars` while keeping its journal rows; the recovery run then re-processes the same bars and **duplicates the trades**. Reproduced: journal 2 rows / 1 duplicate / state counts 1. This is precisely the mechanism behind the previously reported duplicate rows from the crashed replay attempt. **HIGH.**
- **Daily reset / rollover:** verified correct (per-symbol day state resets on date change; overlays increment trading days only on EOD summaries; bust resets start a fresh attempt from current equity).
- **EOD-summary coupling:** day summaries (and therefore `tdays`, trailing-floor updates) fire only on polls with `now.hour ≥ 16`. If all 16:00–16:40 polls fail, that trading day is silently lost from the overlay accounting.

## 9. Topstep and Apex overlays

Rules **implemented in code** (`overlay_update`, `OVERLAYS`, verified behaviourally):

| Rule | Topstep 50K | Apex 50K | Code |
|---|---|---|---|
| Profit target $3,000 | ✓ | ✓ | pass gate `ocum ≥ TARGET` |
| Drawdown | $2,000 **EOD** trail | $2,500 trail | Topstep: EOD HWM of closed equity, floor capped at breakeven — matches PDF. **Apex: implemented as INTRADAY trail (HWM includes open P&L, updated every poll) — contradicts the PDF's "$2,500 EOD trail"**; errs pessimistic but misrepresents the rule under test (HIGH) |
| Min trading days | 2 ✓ | 7 ✓ | matches PDF Table 4; `tdays` counts any day with ≥1 trade |
| Daily loss limit | $1,000 — **message only**: logs "would flatten" but neither halts entries nor flattens, and a breach is not treated as a violation | n/a (Apex has none — correctly omitted) | MEDIUM/HIGH |
| Consistency | **"best day < 55 % of target" — NOT implemented** | none required — correctly omitted | HIGH |
| Eval window | unlimited (correct) | **30-day, no resets — NOT modeled** | MEDIUM |
| Fees | eval fee on bust, activation on pass — correct amounts ($49/$149, $33/$99) but **no monthly rebills** after month 1 | same | MEDIUM (cost reporting understates) |
| Bust handling | real-time equity vs current floor, fresh attempt from current equity — reasonable | same | OK |
| Funded stage / payouts | not modeled (out of the harness's declared scope) | — | documented limitation |

**Firm-rule caveat (per audit instructions):** all rule values were taken from the supplied research PDF as the authoritative reference and were **not** independently re-verified against the firms' current published policies during this audit; PDF §7.2 itself warns every parameter changed at least once during 2025–2026.

## 10. Independent test results

**Tests executed** (isolated bench, `audit_workspace/`; originals untouched; engine imported unmodified with its write paths redirected):

1. **Replay of the last 5 stored RTH sessions** through the engine — reproduced the recorded 8 trades **exactly** (deterministic; journal faithful to engine behaviour).
2. **Independent recalculation of strategy signals** (fresh implementation from the PDF/backtest semantics) — 5/5 ES and 3/3 NQ trades identical in entry, exit and reason; persisted Nov-2 VWAP state re-derived with **0.000** deviation.
3. **Entry between two 20-minute polls** — 1,127 historical events quantified (mean +$61.94 optimism; 13:00-bar signals acted on at 14:00–14:40 polls). Crafted-bar proof of the stale-fill mechanism.
4. **Stop reached before target** — verified stop-first (conservative).
5. **Target reached before stop** — verified; trade-through rule enforced (exact touch does not fill).
6. **Missing market data** — dropped 15:00 bar → phantom overnight hold, retroactive liquidation at a stale price (reproduced).
7. **Stale market data** — production state rewind reproduced in isolation; "feed ok" printed throughout.
8. **Duplicate scheduled execution** — overlapping-run model: duplicate journal rows, state divergence (reproduced).
9. **Process crash and recovery** — crash between journal append and state save: duplicate rows, CSV/state divergence (reproduced; explains the previously reported duplicate rows).
10. **Daily rollover and session-end liquidation** — day reset, EOD close at the 15:00-bar close (16:00 ET price), overlay `tdays` accounting (verified).
11. **Executor semantics vs backtest on identical bars** — same-bar re-entry divergence proven on crafted bars and quantified over 1,174 sessions (74 differ).
12. **Live feed probe** (single fetch per symbol) — feed alive and byte-consistent with stored data on overlapping bars.
13. **Journal arithmetic & reconciliation** — all rows recompute to ≤ $0.03; contamination and reconciliation break documented.

**Tests inspected through code only:** DST edge behaviour (code review: `zoneinfo` + ET-localised indexes; no boundary-date test executed), NQ/ES positional alignment (latent bug confirmed by reading `run_replay`, not triggered by current data).

**Tests not possible with available resources:** measuring actual Yahoo delay intraday; bid/ask or tick-level fill validation; any interaction with the production Tradovate/NT8 stack (correctly prohibited and impossible from this sandbox); re-verification of firm rules against firms' websites (out of scope by instruction); observing the cron's next live session (after audit close).

## 11. Critical defects

| # | Defect | Evidence | Why it matters | Correction |
|---|---|---|---|---|
| C1 | **$400 daily loss stop absent** | `paper_step.py` L47 (`dstop` never referenced), L125/L196 (`halted` never set); behavioural test D: trade #3 opened at day −$450 | The paper run exists to validate the researched bot's risk model (PDF Table 8: "Halt for the day at −$400"); without it the harness can take paths the real bot never would, and paper results cannot support the prop-risk thesis | Check `day_realized ≤ −dstop` after each close; set `halted=True`; block further entries for the day |
| C2 | **Live fills recorded at stale, non-executable prices** | `bar_step` L203/L206 fill at signal-bar `open`/band; test F: mean +$61.94 / 99 % > 1 tick vs earliest realistic fill across 1,127 events | Paper P&L, overlay pass/bust decisions and any comparison against the research haircuts are built on prices that were never executable in live mode | In live mode, fill at the first price observed at/after the acting poll (e.g., the latest completed bar's close, plus slippage), and record both signal and fill timestamps |

## 12. Other defects and limitations

**HIGH**

- **H1 Consistency cap not enforced** (Topstep best-day < 55 % of target): overlay can declare PASS on paths real firms would reject.
- **H2 Apex trailed intraday vs the PDF's $2,500 EOD trail** (`overlay_update` L252–254): the overlay tests a different (stricter) rule than documented.
- **H3 Duplicate records on crash or overlapping runs** (no lockfile; non-transactional journaling): reproduced twice; journal/state diverge.
- **H4 Stale-feed state rewind** (12-key `done_bars` prune vs 5-day re-fetch; no staleness alarm): rewound production state; reproduced in isolation.
- **H5 Same-bar re-entry after stop-outs** (engine) vs backtest prohibition: behavioural divergence on ~6 % of sessions.
- **H6 Replay rows contaminate the live journal** (8/8 rows; reconciliation with state impossible by design).

**MEDIUM**

- **M1** Topstep DLL ($1,000) is message-only — no halt, no flatten, no violation accounting.
- **M2** No contract-roll guard (backtest excluded roll weeks; December roll upcoming).
- **M3** Missing 15:00 bar → phantom overnight hold, retroactive `eod_missed` fill at a stale price.
- **M4** Journal cannot fully reconstruct trades: no stop/target/VWAP/σ in CSV, no trade ID, no data-source/execution-status fields, rounded-price P&L (≤ $0.03), log lines lack wall-clock timestamps, equity marks are day-only.
- **M5** Scheduler failures are silent (16:00 poll left no artifact); EOD summaries (hence `tdays`/floor trails) depend entirely on the 16:00–16:40 polls succeeding.
- **M6** Apex 30-day window not modeled; monthly rebill fees not accumulated (cost reporting understates).
- **M7** `run_replay` indexes NQ sessions positionally against ES dates — a missing NQ session would silently replay the wrong day (latent).

**LOW**

- **L1** EOD flat at the 16:00 ET bar close vs "flat by 15:59" (shared with the backtest).
- **L2** `"- -"` double-bullet cosmetic defect in day summaries.
- **L3** `state.json` `day` dict never pruned (grows one entry per symbol-day).
- **L4** `traded_today` briefly reflects the previous day on pre-first-bar morning polls (no tdays impact found).

**Verified-good (for balance):** signal/VWAP/σ math exact; executor order & trade-through; cost model; sizing ladder & scale trigger; day rollover; atomic state writes; weekend guard; bar dedup under normal feeds; deterministic replay; feed consistency with stored data.

## 13. Recommended remediation (priority order)

1. **P0 — daily stop (C1):** enforce −$400 halt per symbol-day (halt flag + entry gate), exactly as Table 8.
2. **P0 — live fill policy (C2):** fill live entries/exits at the first observable price at or after the acting poll; log signal-vs-fill deltas; keep bar-level fills only in `--replay`.
3. **P1 — journal integrity (H3, H6):** add a lockfile (or atomic single-writer discipline); write trades to a temp file and rename together with the state save; route `--replay` rows to a separate file; add a unique trade ID.
4. **P1 — consistency + Apex EOD trail (H1, H2):** enforce Topstep best-day < 55 % of target before declaring pass; switch Apex floor updates to EOD closed-equity HWM.
5. **P1 — staleness & rewind (H4):** widen `done_bars` to cover the full fetch window (≈ 30 keys), reject bars older than the current session, and alarm when the newest bar lags wall-clock by more than one interval.
6. **P2 — Topstep DLL enforcement (M1), roll-day guard (M2), missing-15:00-bar force-flat (M3).**
7. **P2 — journal completeness (M4):** add stop/target/σ/vwap, wall-clock run timestamps, equity marks per poll; separate file or mode tag already exists — keep it strict.
8. **P3 — Apex window & rebill fees (M6), replay alignment (M7), cosmetic fixes (L1–L4).**
9. **After remediation:** re-run the audit bench (it is reusable), then judge the live output over ≥ 10 sessions before comparing against the research's degraded expectancy.

## 14. Final recommendation

The harness is a **faithful implementation of the VWAP Fade v1 signal** — independently verified to the cent — wrapped in an **incomplete implementation of the researched bot's risk and evaluation layer**, and its first three production polls already exhibited a silent failure and a stale-data state rewind. It is safe to *keep running* as an engineering smoke test (it touches no broker and risks nothing), but its numbers must not yet be quoted as paper-trading evidence. Apply P0 items 1–2 and P1 items 3–5, re-audit, and only then treat the live equity line as a valid test of the research's win% / timeframe / cost predictions.

**Changes made during this audit:** created `scripts/audit_tests.py` (reusable audit bench), `audit_workspace/` (isolated test artifacts and results JSON), and this report (`reports/vwap_fade_paper_trading_independent_audit.md`, with a copy in `download/reports/`). **No** engine file, state file, journal, data file, or scheduler job was modified; the production Tradovate/NT8 environment was never contacted.
