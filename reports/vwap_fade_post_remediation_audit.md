# VWAP Fade Paper-Trading System — Fresh Independent Audit (post-remediation)

**Scope:** the remediated paper-trading research system only — `scripts/paper_step.py` (engine v2), its data home `download/paper_trading/`, and the remediation deliverables (`scripts/remediation_tests.py`, `scripts/remediate_data.py`, `remediation_workspace/`).
**Baseline documents:** the first independent audit (`reports/vwap_fade_paper_trading_independent_audit.md`, defects C1/C2, H1–H6, M1–M7, L1–L4) and the remediation report (`reports/vwap_fade_paper_trading_remediation.md`), checked against the authoritative research specification (`download/futures_prop_bot_research.pdf`, "Prop Evaluation Bot Research — November 4, 2026": §2.1–2.2, Table 2, Table 4, §7.1).
**Audit date:** 4 November 2026, ~21:00–24:00 ET. **Mode:** audit-only. No engine file, parameter, state file, journal, scheduler job or production artifact was modified by this audit.
**Production safety:** the live Tradovate evaluation account and NT8 bridge (production PropBot) were never approached — this sandbox has no connectivity to Tradovate/NT8, holds no production credentials and has no order-routing capability. The paper-trading data home was touched **read-only** (one `--status` call, file hashes verified unchanged before/after). All engine writes during testing were redirected to isolated homes under `audit_workspace/post_audit/`.

---

## 1. Executive summary

This audit was performed **independently of the remediation**: I did not re-run the remediator's 16-test suite as proof. I wrote a new bench (`scripts/post_audit_tests.py` + `scripts/post_audit_scen.py`, ~90 assertions in 15 groups) with my own crafted market data (different numbers, dates and session shapes than the remediator's), my own independent re-implementation of the VWAP/σ/band math, and my own from-scratch simulator of the backtest executor. Every claim in the remediation report was re-derived from primary evidence: the code, the research PDF, the archived data, and live execution of the engine through its real entry points.

**Verdict: the remediation is verified. All six mandated items (P0.1, P0.2, P1.1–P1.4) are genuinely implemented and behave as claimed — 15/15 of my test groups pass.** The signal core is proven unchanged by a three-way equivalence: the v2 engine's replay of the last five stored sessions, the eight trades archived from the v1 journal, and my own independent simulator produce **identical trades to the cent** (entry, exit, reason, net P&L, final cum $279.54).

My probes also found **five new/residual issues that the remediation report does not disclose** — none invalidates the fixes, but two should be resolved (or explicitly accepted) before the paper results are quoted as research evidence:

| # | Finding | Severity |
|---|---|---|
| N1 | **Topstep consistency is enforced only as a PASS gate, not as the research's bot-side daily profit cap.** A day ≥ $1,650 (55% × $3,000) permanently blocks that attempt's PASS while the engine keeps trading it — a state the researched bot cannot reach (it would have stopped entering for the day at the cap). The first audit listed the daily profit cap as HIGH; the remediation report declares P1.3 complete without mentioning it. | **MEDIUM** |
| N2 | **Stale day entries contaminate end-of-day accounting when one symbol has no bars all day** (e.g., a one-symbol feed outage): the EOD summary's day P&L mixes yesterday's realized P&L for the dead symbol into today's, `traded_today` stays true on zero trades today, and `tdays`/`trading_days` increment without any trade — which can satisfy `min_days` early. The account-level −$400 stop is immune (it filters by date). Pre-existing v1 behaviour, outside the remediation's disclosed-remaining list. | **MEDIUM** |
| N3 | A crash inside the narrow end-of-day window duplicates the `DAY SUMMARY` diary block (accounting recovers single-count — cosmetic). | LOW |
| N4 | The replay "abort if the replay directory resolves to the live root" guard is **structurally unreachable dead code** (`replay_dir_for` always appends `/replay`). The real isolation guarantee holds anyway — replay writes only to a subdirectory — verified behaviourally. | LOW |
| N5 | `remediate_data.py` is **not safely re-runnable**: a second run would overwrite the pre-remediation archive with already-clean files (the "idempotent" claim is wrong for the archive step). Evidence survives in `remediation_workspace/backup_live_data_2026-11-05/` (verified byte-identical to the archive), so nothing is lost today. | LOW |

Also documented (cosmetic): journal rows mix timestamp semantics — `entry_ts` is the acting poll time while `exit_ts` is the exiting bar's stamp (a stop booked from the 13:00 bar shows exit_ts 13:00 < entry_ts 13:20; the bar *close* 14:00 postdates the entry, so the record is interpretable but mixed); and the "signal NOT taken: no fresh observable price" note is unreachable through `run_live` because the `last_completed_close` fallback always supplies a price while a signal bar is still fresh (belt-and-braces only — verified, and conservative in the right direction).

**Bottom line:** the paper-trading system is now a faithful, instrumented implementation of the researched bot at the code level. What it cannot yet offer is evidence: the de-contaminated journal starts at zero trades, so executable performance, win% and overlay behaviour remain unmeasured until the approved multi-session paper run.

## 2. How this audit established independence

| Layer | Remediator's evidence | This audit's evidence |
|---|---|---|
| Daily stop | their crafted ES day, their T01/T02 | my own ES session (12:00-bar short, fill 6044.00, stop 6097.27 → −$537.57) driven through **four fresh `run_live` processes**; halt, account-level block, restart survival and next-day reset re-derived |
| Fills | their T03/T04/T11 | my own worse-fill / pre-traded-target / no-price / fallback scenarios; stop anchored to **my** σ, not the engine's |
| Concurrency/crash | their T05/T06 | my own flock holder + true concurrent process pair; my own injected crash after journal write, recovery, torn-tail repair, per-day trade-ID sequencing |
| Overlays | their T08/T09 | my own boundary cases (exactly $1,650 blocks; $1,600 passes; Apex uncapped; EOD-only floors; breakeven cap; bust at current equity) |
| Stale data | their T10/T11 | my own re-offered Nov-2 bars against a seeded Nov-3/Nov-4 state; full `classify_feed` matrix; per-symbol status line |
| Signal math | their T12 | **my own simulator**, written from the research semantics, reproducing all 8 archived trades independently of both engine versions |

## 3. Verification matrix — every mandated fix, independently confirmed

**P0.1 — $400 daily loss stop (audit C1).** `check_daily_stop` runs after every close; on day P&L ≤ −$400 it sets `halted_date` + `halted_reason` in state and per-symbol `halted` flags. The entry gate checks **three** conditions (per-symbol flag, persisted `halted_date` vs bar date, and the live `account_day_pnl > −$400` condition), so a lost flag or a restart cannot re-enter past the limit. My −$537.57 stop-out fired the halt; the same-day NQ signal was blocked (account level, NQ's own day flag not even set); a cold process with the flag deleted was still blocked; a state with the flag kept but P&L recovered was still blocked (conservative double-lock); the next trading day re-enabled entries while the stale `halted_date` remained inert. The account-level interpretation is faithful to the PDF ("a daily loss stop ($400 on 50K accounts…)" — a bot-level, not per-symbol, breaker; Table 8: "Halt for the day at −$400 (50K)").

**P0.2 — executable live fills (audit C2).** Entries book the first observable price at action time (`poll_quote`, or `last_completed_close` ≤ 40 min old, or nothing with an explicit diary note). My probes: fill 6044.00 vs band 6036.11 recorded apart; stop recomputed from **the fill** using my own σ (6044 + 1.2σ); `exec_lag_min` 20/30 correct; a pre-traded target exited immediately **at the observable price** for friction-only −$4.90 (`tp_poll`, no backdated profit); bars that closed before the entry action are skipped for exits (a bar spanning both stop and target moved nothing); fallback entries carry `fill_method=last_completed_close` into the journal. Replay rows keep `bar_fill` (signal performance) so the two ledgers stay separable. Costs unchanged ($2.45 MES / $1.70 MNQ round turn per micro — Table 2), not double-counted.

**P1.1 — duplicate execution/journal (audit H3).** An `flock` on `<home>/.lock` serialises runs: my in-process lock holder got `SKIP` with **zero writes** (hash-verified), and a true concurrent process pair produced exactly one processed run and one SKIP with one entry. Crash recovery: I injected a crash after the journal write and before the state save — the row stayed durable, `state.json` was never written, the recovery run regenerated the same `trade_id` (`2026-11-06_ES_01`), printed `JOURNAL DEDUP`, and left exactly one row with state/journal reconciling (n_trades 1, cum == cum_eq). A torn tail line was detected and repaired on the next writing run; a second trading day produced a fresh per-day sequence id (`2026-11-09_ES_01`) and reconciled again.

**P1.2 — replay/live separation (audit H6).** The CLI `--replay 3` (real entry point, `PAPER_HOME` redirected) wrote **only** `<home>/replay/`; no live `trades.csv`/`state.json`/`log.md` exists in the home root; rows are tagged `mode=replay`. A manually injected `mode=replay` row in a live journal is inert (untouched, ignored by accounting). The live journal itself: **header-only, 19-column v2 schema, zero live trades**, with the contaminated v1 originals archived and byte-identical to the full backup.

**P1.3 — evaluation overlays (audit H1, H2).** Topstep: `round(0.55 × 3000, 2) = 1650.00` exactly; best day **exactly $1,650 blocks** the PASS (explicit message, keeps trading); $1,600 passes and charges the $149 activation; Apex passes with a $2,999 best day (no cap — correct per Table 4). Drawdown: **both** floors derive from `hwm_eod`, the high-water mark of closed equity at day summaries — my +$2,380 and +$7,380 intraday spikes never moved either floor; EOD closed equity set Apex −$1,500 and Topstep −$1,000 (breakeven-capped); Topstep's floor capped at 0 vs Apex's uncapped +500 exactly as specified; bust still evaluates **current** equity (−$1,501 vs −$1,500 floor fired a new attempt with the $33 fee). No rule was silently substituted — the still-unimplemented rules are listed in §5 below.

**P1.4 — stale market data (audit H4).** Re-offered Nov-2 bars (outside the seeded done-bars window) were rejected into `rejected_bars`, warned in the status line ("2 stale/out-of-order bar(s) rejected (no rewind)"), and the state moved strictly forward: day = Nov-5, `last_bar_ts` advanced, and the session VWAP built **only** from the new bars (matched my recompute to 0.001). A re-offered bar *inside* `done_bars` dedups silently (normal re-fetch, correctly not counted as a rejection). `classify_feed` returned the full matrix correctly (unavailable / stale / awaiting / ok, per symbol), the status line showed `feed ES:stale NQ:ok`, and the healthy symbol traded while the stale one was flagged — no symbol's health can hide behind the other's. Retention is 200 keys/symbol (~33 sessions) against a ~30-bar fetch window, with `last_bar_ts` as the second line of defence.

## 4. Core regression — the signal engine is unchanged

Three independent executions agree on all eight audit-recorded trades (last five stored RTH sessions):

| # | Sym | Side | Entry | Exit | Reason | Net | Cum |
|---|---|---|---|---|---|---|---|
| 1 | ES | short | 7781.53 | 7770.11 | tp | +109.30 | +109.30 |
| 2 | NQ | short | 29901.89 | 29839.21 | tp | +247.30 | +356.59 |
| 3 | ES | long | 7727.47 | 7749.63 | tp | +216.68 | +573.28 |
| 4 | ES | long | 7643.28 | 7634.54 | stop | −92.33 | +480.95 |
| 5 | ES | long | 7634.10 | 7622.76 | stop | −118.33 | +362.62 |
| 6 | NQ | long | 28593.12 | 28540.49 | stop | −213.90 | +148.73 |
| 7 | ES | short | 7617.19 | 7622.71 | stop | −60.10 | +88.63 |
| 8 | NQ | short | 28417.24 | 28368.66 | tp | +190.91 | +279.54 |

v2 engine replay == v1-era archived journal == my own simulator (entry, exit, reason, net to the cent; final cum $279.54). Strategy parameters re-verified against both `02_backtest.py` (VWAPFD variant 1: k=2.0, stop_k=1.2, trend 0.006, max 3, cutoff 14) and the research PDF (Table 2 costs, Table 4 overlays, §2.2 sizing ladder and daily stop): **all preserved** (test group B12).

## 5. New findings (this audit) — detail

**N1 — consistency pass-gate without the research's daily profit cap (MEDIUM).** The PDF's bot layer is "daily profit caps that enforce each firm's consistency rule" (§2.2): the researched bot stops entering for the day when day P&L reaches the cap, so an over-cap day never occurs. The remediated engine implements only the overlay-side gate (refuse PASS while `best_day ≥ $1,650`), which the first audit's P1 recommendation prescribed — but the first audit's §4 table had also flagged "Daily profit cap: min(day P&L, firm consistency cap)" as **NOT IMPLEMENTED — HIGH**, and the remediation report's remaining-defects list does not carry it. Demonstrated consequence: after a +$1,700 day, the attempt never passes even at +$5,000 profit (`best_day` is a running max) — the engine would trade a permanently unpassable attempt. Fix is small (a daily entry gate at the cap for consistency-capped overlays) or the limitation should be explicitly documented and accepted.

**N2 — stale-day contamination of EOD accounting (MEDIUM).** `overlay_update`/`eod_summary` compute `day_pnl` and `traded_today` over **all** `st["day"]` entries without a date filter. With ES trading today and NQ's feed dead all day (NQ's day entry still yesterday's, realized −$300), the summary printed **day P&L −$100.00** (true: +$200), and `tdays`/`trading_days` incremented although **no trade occurred today** — inflating minimum-day progress toward a PASS. Reachable via any one-symbol full-day outage; visible in the status line (NQ stale/unavailable) but silent in the accounting. The −$400 stop is immune (`account_day_pnl` filters by date). Pre-existing v1 behaviour, adjacent to the old L4 finding but with overlay impact the first audit did not quantify.

**N3 — EOD crash window duplicates the diary block (LOW).** A crash between `eod_summary`'s direct diary write and the end-of-run state save leaves the summary lines on disk while `last_summary` is still unset; the next ≥16:00 poll re-runs the summary → duplicate `DAY SUMMARY` block. Verified accounting recovers single-count (tdays 1, trading_days 1, one equity row) because all numeric mutations ride the atomically-saved state. Cosmetic; the wider "all three 16:xx polls fail" case remains M5 (disclosed by the remediation).

**N4 — unreachable replay abort guard (LOW).** `replay_dir_for(OUT)` always returns `OUT + "/replay"`, so both abort conditions (`rdir == ROOT_HOME`, `rdir == OUT`) are tautologically false. The isolation guarantee the guard is meant to enforce holds anyway by construction — verified behaviourally (replay writes only to the subdirectory; live files untouched; injected rows inert). Dead code, not a vulnerability.

**N5 — `remediate_data.py` re-run would destroy the archive it created (LOW).** Step 1 copies the *current* home files over `archive/pre_remediation_2026-11-05/` unconditionally, so a second run replaces the contaminated originals with clean ones. Not exercised (I did not run it — that would have destroyed evidence). Mitigated: `remediation_workspace/backup_live_data_2026-11-05/` holds byte-identical originals (verified by diff), so the audit trail survives regardless.

**Cosmetic observations.** (i) Journal rows mix time semantics: `entry_ts` = acting poll time, `exit_ts` = exiting bar's stamp — e.g. trade 2026-11-06_ES_01 shows entry_ts 13:20 / exit_ts 13:00 for a stop on the 13:00-stamped bar; the bar's close (14:00) postdates the entry, so the record is interpretable, but consumers should know the two columns use different clocks. (ii) The "signal NOT taken: no fresh observable price" path is unreachable via `run_live` (the `last_completed_close` fallback always provides a price while a signal bar is fresh — verified); it remains valid defence-in-depth at `bar_step` level. (iii) `migrate_state` prunes day entries against **wall-clock** today; in production poll time == wall clock so same-day entries are never pruned (this surfaced only because my bench injects future-dated polls — noted as a test-environment artifact, no production impact found).

## 6. Remaining defects carried from the first audit (re-checked, unchanged)

The remediation report's §6 list is accurate as far as I could re-verify: **M1** Topstep $1,000 DLL remains message-only (confirmed in code — the −$400 stop sits well inside it); **M2** no contract-roll guard (December roll upcoming); **M3/L1** missing-15:00-bar → retroactive `eod_missed` close at the 15:00-bar close (16:00 ET price); **M4 (partial)** stop/target/σ-at-entry live only in the diary; **M5** EOD summaries still depend on a ≥16:00 poll succeeding (immediate diary delimiters mitigate); **M6** Apex 30-day window and monthly rebills not modeled; **H5** same-bar re-entry deliberately preserved (strategy behaviour may not be changed — and the new −$400 stop now bounds its worst case); **feed reality** Yahoo prices are delayed/indicative, no bid/ask, delay uncompensated (`fill_method` tags preserve the distinction for later analysis). Plus the cosmetic `feed: {}` in `--status` before the first poll of the day.

## 7. Scheduler, safety and integrity checks

- **Cron job #503762 verified via the scheduler API (read-only):** expression `0 0,20,40 10-16 ? * 2-6` (America/New_York), payload unchanged (`python3 -W ignore scripts/paper_step.py --live`), run_count 3 (the three Nov-4 v1 polls), next run 10:00 ET Nov 5 — the remediated engine will take over automatically. Not modified, paused or re-created by remediation or by this audit.
- **Protected files untouched since the remediation commit** (git diff empty for `scripts/02_backtest.py`, `scripts/audit_tests.py`, `download/data/`, the research PDF, the first audit report; the only worktree deltas are file-mode bits, no content changes).
- **Live paper home integrity:** `state.json` schema v2 (cum 0, n_trades 0, no halt, `hwm` artifact gone, `last_bar_ts` seeded), journal header-only with the 19-column schema, fresh v2 diary, archive + README in place; `--status` left all four files hash-identical. The `done_bars` window still holds only 12 keys (Nov 3–4) from the migration; until it grows, `last_bar_ts` rejection covers the full fetch window — verified as the working second line of defence.
- **No performance evaluation was run and no strategy parameter was changed** — the replay in §4 is an equivalence check against recorded trades, not a performance claim.

## 8. Verdict and readiness

> **ACCEPT the remediation.** All six mandated items are implemented and independently verified; the signal engine is proven unchanged to the cent; the journal/state design is now reconcilable and crash-safe; the scheduler will run v2 without intervention. The paper system's future output can be treated as research evidence **for the engine's own behaviour** — with the standing caveat that Yahoo fills are delayed/indicative last-trade prices.

Conditions carried into the next stage (the proposed 10-session paper-trading test), in priority order:

1. **N1** — either implement the research's bot-side daily profit cap for consistency-capped overlays, or record it as an accepted, documented divergence before paper results are quoted against the research's consistency claims.
2. **N2** — filter `day_pnl`/`traded_today` by date (or prune stale day entries at EOD) so a one-symbol outage cannot inflate minimum-day progress or pollute the summary; small, contained fix.
3. N3–N5 and the cosmetic items — optional hygiene, no evidence impact.

**Per the standing instruction I stop here.** The 10-session paper-trading test (and any performance evaluation) has **not** been started and awaits explicit approval; the fresh audit report is delivered as `reports/vwap_fade_post_remediation_audit.md` (copy in `download/reports/`), with the full bench, scenarios and per-assertion results preserved under `audit_workspace/post_audit/` (`post_audit_results.json`) and `scripts/post_audit_*.py` / `scripts/pa_w_*.py`.

**Changes made during this audit:** created `scripts/post_audit_scen.py`, `scripts/post_audit_tests.py`, `scripts/pa_w_poll.py`, `scripts/pa_w_crash.py`, `scripts/pa_w_cold.py`, `audit_workspace/post_audit/` (isolated test homes + results JSON), and this report. **No** engine file, state file, journal, data file, scheduler job or production artifact was modified; the production Tradovate/NT8 environment was never contacted.
