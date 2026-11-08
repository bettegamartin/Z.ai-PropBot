# DEPLOY — VwapFadeBot installation & go-live runbook (NT8 + Topstep)

**Bot:** `nt8/VwapFadeBot.cs` + `nt8/PropBotGovernor.cs` (one compiled strategy, two chart instances).
**Firm:** Topstep 50K evaluation (primary path per research §7). **Apex variant:** set `Daily profit cap = 0` and remember Apex is *semi-auto only* — a human must be present every session.
**Read together with:** `nt8/PORT_SPEC.md` (what the bot does and why), research `download/futures_prop_bot_research.pdf` §7.2 (launch sequence).

> ⚠️ **Production isolation (non-negotiable).** This bot is for the NEW evaluation account only.
> - Create a **brand-new NT8 workspace** named `PropBot-Eval`. Never add the bot to your existing production PropBot workspace, and never attach it to any production-connected account.
> - The eval connection is a **separate NT8 connection** (its own credentials, its own account). Verify in Control Center that the bot's charts point at the evaluation account before enabling.
> - Nothing in this repo holds credentials. You enter Topstep/Rithmic credentials only inside NinjaTrader's Connection window, on your machine.

---

## 1. Install the code

1. Clone the repo (or unzip) anywhere on the NT8 machine.
2. Copy `nt8/VwapFadeBot.cs` and `nt8/PropBotGovernor.cs` into
   `Documents\NinjaTrader 8\bin\Custom\Strategies\`.
3. Open NinjaTrader 8 → **New → NinjaScript Editor** → right-click *Strategies* → **Compile** (F5).
4. Expected result: 0 errors. If the compiler complains, fix ONLY mechanical issues
   (using directives, overload signatures). Any logic change requires a written deviation
   note in `nt8/PORT_SPEC.md` §3 first. Cursor is good for this loop — keep the frozen
   parameters untouchable.

## 2. Connect the data + eval account (Topstep)

1. Purchase the Topstep 50K eval; at checkout **select the daily loss limit option** ($1,000 — our $400 stop sits inside it).
2. Topstep → request **Rithmic** (or Tradovate) credentials for the eval account.
3. NT8 Control Center → **Connections → Configure** → add Rithmic (or Tradovate) → enter YOUR credentials there (never in chat, never in code).
4. Connect; confirm the evaluation account appears (e.g. `SIMxxxx` / eval account id) and market data for MES/MNQ streams.
5. **Dedicated account rule:** the bot's gates read account-realized P&L. Do not trade manually on that account while the bot runs.

## 3. Configure the charts (one workspace, two instances)

1. New chart: **MES 12-24**, data series = 60-minute bars, trading hours template = **CME US Index Futures ETH** (top-of-hour stamps). Repeat for **MNQ 12-24**.
2. Right-click chart → Strategies → `VwapFadeBot`.
3. Verify parameters show the frozen defaults (K 2.0 / StopK 1.2 / Trend 0.006 / MaxTrades 3 / Base 2 / Scaled 4 / Scale 1500 / DailyStop 400 / DailyCap 1650 / Flatten 959). Do not change them.
4. Set **Account = the evaluation account**, `Calculate: On price change` (default from code).
5. Enable **one instance per chart**. The on-chart status box shows day P&L, cum, VWAP/σ/bands, and gate state; full logs land in `Documents\NinjaTrader 8\propbot\logs\`.

## 4. Market Replay shakedown (≥ 2 weeks — research §7.2; do not skip)

1. Control Center → **Tools → Market Replay** (or Replay+Sim): replay recent MES/MNQ sessions at speed.
2. Run the bot on Replay/Sim101 exactly as §3.
3. Verify the acceptance test in `nt8/PORT_SPEC.md` §4 (signals match `paper_step.py --replay` to the cent; gate bench for −$400 / +$1,650; restart-resync test: kill NT8 with a position open → restart → OCO exits resubmitted from the state file).
4. Confirm limit-order touch-through behaviour at the VWAP target and stop-market behaviour at the 1.2σ stop on the replay tape.
5. Only when §4 passes: move to the paid eval.

## 5. Supervised operation (every session — this is also the Apex-compliance pattern)

Daily checklist (≈2 min at the 10:00 poll, then occasional glances):
- [ ] Both instances show `gate: ok`, feeds streaming, status box updating.
- [ ] No order errors in Control Center → Orders tab.
- [ ] After any entry: OCO pair visible (stop + target) with expected distances.
- [ ] At 15:59: positions flat; logs show `FLATTEN eod`.
- [ ] Halt/cap events announce loudly in the log (`**DAILY STOP …**`) — investigate, don't override.

**Kill switch:** Disable the strategy (no new entries; existing OCO exits stay working). To flatten manually: flatten via the chart or DOM. The governor state file persists halts — a restart cannot re-enter past a fired gate.

## 6. After the eval passes

1. Pay the $149 activation (PA account) — expect the **floor to reset to starting balance** after the first payout (research §7): withdraw at the $2,000 cap, re-trade at reduced size the following week.
2. Re-verify firm rules monthly (Table 4 changed repeatedly in 2025–2026).
3. Scaling decision (second account / Apex) only after the first payout is in the bank.

## 7. Rollback / uninstall

Disable both instances → remove from charts → state file remains (`propbot_state.json`) for the books. To fully reset the books for a NEW evaluation attempt, move (don't delete) `propbot_state.json` to `propbot_state_<attempt>.json` and note the attempt number in the log.
