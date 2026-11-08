# PORT_SPEC — paper_step.py → VwapFadeBot.cs traceability

**Status:** code-complete, NOT yet compiled (this sandbox has no Windows/NinjaTrader assemblies — first compile happens in NT8 on the trading machine, see `nt8/DEPLOY.md` §2).
**Rule:** the Python engine `scripts/paper_step.py` v2.1 is the audited reference implementation. The C# must produce the **same signals on the same bars**. Anything not listed in §3 (Disclosed differences) is a bug.

---

## 1. Frozen parameters (identical both sides)

| Parameter | Value | Paper location | C# location |
|---|---|---|---|
| Entry band k | 2.0 σ | `P["k"]` | `K` |
| Stop k | 1.2 σ | `P["stop_k"]` | `StopK` |
| Trend filter | 0.006 | `P["trend"]` | `TrendPct` |
| Max trades/day/symbol | 3 | `P["max_trades"]` | `MaxTrades` |
| Entry cutoff | bars stamped < 14:00 ET | `P["cutoff_h"]=14` | `barHour < 14` |
| Sizing | 2 → 4 micros at cum ≥ $1,500 | `P["base_c","scaled_c","scale_level"]` | `BaseQty/ScaledQty/ScaleLevel` |
| Daily loss stop | −$400 account-day | `P["dstop"]` | `DailyStopUsd` |
| Daily profit cap | +$1,650 (Topstep 55%×$3,000) | `consistency_cap_usd()` | `DailyProfitCapUsd` |
| Session window | bars stamped 10:00–15:59 ET | `between_time("10:00","15:59")` | `SessionStartHour=10`, hour < 16 |
| Flatten | by 15:59 ET | Table 8 / `ts.hour==15` close | `FlattenEtMinutes=959` |
| Target | signal-bar VWAP | `tp_px=vwap` | `sigVwap` |

## 2. Signal math — line-by-line mapping

| # | Paper (paper_step.py) | C# (VwapFadeBot.cs) | Notes |
|---|---|---|---|
| S1 | Day state reset when bar date changes (`new_day`) | `ProcessClosedBar` day-rollover block | per-symbol day books |
| S2 | `day_open` = first session bar's open (line 603) | `if (dayOpen == null) dayOpen = o` | 10:00 bar |
| S3 | VWAP incl. current bar: `Σ((H+L+C)/3·V)/ΣV` (line 606-608) | `vwapN += (h+l+c)/3*v; vwapD += v` | typical-price VWAP |
| S4 | σ = `np.std(devs, ddof=1)`, devs = closes − VWAP-so-far, expanding (line 609-610); σ=0 until 2 devs | `StdSample()` (ddof=1), 0 until 2 devs | sample std, population=false |
| S5 | zero-volume bars skipped entirely (fetch filter) | `if (Volume[i] <= 0) return` | no VWAP/dev update |
| S6 | SHORT iff `H >= vwap + 2σ` (signal px `max(band, O)`); LONG iff `L <= vwap − 2σ` (`min(band, O)`) (lines 645-651) | identical in `ProcessClosedBar` | entry bar close evaluation |
| S7 | Trend filter gate `|C − day_open| <= 0.6%·day_open` AND σ>0 (line 644) | identical | skips NEW fades only |
| S8 | Entry gates: no pos (per symbol) ∧ devs≥3 ∧ hour<14 ∧ trades<3 ∧ ¬halted(flag∨live) ∧ dayP&L>−400 ∧ cap allows (lines 638-642) | identical, gates in `PropBotGovernor.CanEnter` + local checks | dev count ≥3 ⇒ practical entry bars = 12:00/13:00 stamps |
| S9 | Stop first, then target with 1-tick trade-through (lines 624-631) | native OCO: stop-market + limit at VWAP | see D2 |
| S10 | Live fill = first observable price at action time; stop/target recomputed from fill; target = signal-bar VWAP (P0.2, `live_enter`) | market entry at bar close; OCO submitted from `execution.Order.AverageFillPrice` | see D1/D3 |
| S11 | Target already trade-through at action → immediate exit (`tp_poll`) | not implemented — impossible with a market entry at a 2σ touch (target is ~2σ away) | documented, not reachable |
| S12 | EOD flat: close on the hour-15 bar (16:00 close) | market flatten at 15:59:00 ET | see D4 |
| S13 | `check_daily_stop`: after EVERY close, day P&L ≤ −400 → persisted `halted_date`, all symbols (lines 420-440) | `PropBotGovernor.OnClosed → FireHalt` | persists across restarts |
| S14 | `check_daily_cap`: day P&L ≥ cap → persisted `cap_blocked_date` (N1, lines 472-497) | `FireCap` | Topstep only (`DailyProfitCapUsd>0`) |
| S15 | `cap_allows_entry` triple gate: flag ∨ live condition ∨ post-close set (lines 456-469) | `CanEnter` (flag + live) + `OnClosed` (post-close) | restart cannot re-enter |
| S16 | `contracts_for`: cum ≥ 1500 → 4 else 2 (line 536) | `gov.CumTotal >= ScaleLevel ? ScaledQty : BaseQty` | cum = governor books |
| S17 | Trade ID `{date}_{sym}_{nn}` | `VFB_{date}_{sym}_{nn}` | journal correlation |
| S18 | One position per symbol; two symbols may hold concurrently | same (per-instance position; no cross-symbol block) | matches audited code (header line 10) |

## 3. Disclosed differences (intentional; none change signal logic)

| # | Difference | Why | Direction |
|---|---|---|---|
| D1 | **Fill quality**: paper acts on delayed (~15 min) Yahoo data every 20 min; NT8 acts on the real-time feed within seconds of bar close. `exec_lag_min` collapses from ~20+ to ~0. | Real-time data was never available in paper mode | **Improves** execution; entry prices will differ from paper on the same bar |
| D2 | **Intrabar exits**: paper approximates stop/target on hourly bar extremes (stop-first heuristic); NT8 rests real stop-market/limit orders that fill on the true intrabar path | Exchange-native OCO is strictly more accurate | **Improves** fills; e.g. the paper's "stop wins when both hit in one bar" heuristic disappears |
| D3 | **Entry order type**: research Table 8 says "fade limit orders at ±2σ"; the audited paper engine implements touch-on-closed-bar → market entry at observable price. C# follows the **audited engine**, not the research phrasing | The audit froze the engine, not the research prose; equivalence target is `paper_step.py` | Neutral (same signals, market execution) |
| D4 | **Flatten time**: paper books EOD at the 15:00-stamped bar's close (≈16:00:00); C# flattens at 15:59:00 ET | Table 8 says "flat by 15:59"; firm EOD accounting | **More conservative** (≤1 min earlier) |
| D5 | **Costs**: paper models $2.45/$1.70 RT inside P&L; C# books real fills and gates on account-realized P&L (real commissions included by the broker) | Dedicated eval account ⇒ account P&L = bot P&L + real fees | More truthful; live day P&L ≈ paper − modeled-costs + real-costs |
| D6 | **Historical warm-up**: on enable, the strategy never signals on historical chart bars (State==Realtime only) | Prevents fake trades on chart load | Safety |
| D7 | **Books ownership**: paper gates on engine-modeled day P&L; C# gates on the sum of its own closed-trade nets (both instances share one static governor, JSON-persisted under `Documents\NinjaTrader 8\propbot\`) | Cross-instance account-level view in NT8 | Equivalent on a dedicated account |
| D8 | **Halt on restart with open position**: paper's `eod_missed` closes stale-day positions at last close; C# emergency-flattens at market on rollover | Real-time equivalent | Neutral-safety |

## 4. Acceptance test (run before the eval, in Market Replay)

1. Pick 3 recent sessions with trades in the paper journal (`download/paper_trading/trades.csv`).
2. Run `python3 scripts/paper_step.py --replay <N>` (signal mode) for those sessions — record signal bars/bands.
3. Run Market Replay over the same sessions with `VwapFadeBot` on MES+MNQ.
4. **Pass criterion:** same signal bars, same sides, same VWAP/σ/band values to 0.01; entry prices may differ only per D1/D2; stop/target levels identical relative to fill.
5. Also verify the gate bench: force day P&L −$400 → no entries on **either** symbol; +$1,650 → same; NT8 restart mid-halt → still blocked (state file).

## 5. Out of scope for the C# (stays with the research/paper layer)

Firm overlay PASS accounting (best-day < 55%, EOD trailing floors, payout mechanics) is a reporting layer, not an execution concern — the bot enforces the *bot-side* caps ($400/$1,650) that keep those rules satisfied. Paper-side overlay math remains in `paper_step.py` / `paper10_final.py`.
