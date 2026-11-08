# Prop Evaluation Bot — VWAP Fade v1 (Topstep 50K)

Automated futures evaluation bot: a **session-VWAP fade on micro E-mini (MES) and micro Nasdaq (MNQ)** futures, researched, backtested, Monte-Carlo'd, audited twice, and now in live **paper-trading validation**. Target: pass a Topstep 50K evaluation with a withdrawable payout — win% ~51.5%, median ~80 days to first cash, expected program fees ~$287 (base scenario).

**Strategy parameters are FROZEN** (research Table 8, freeze 2026-11-05). Any change requires a written deviation note.

## Repository map

| Path | What it is |
|---|---|
| `nt8/VwapFadeBot.cs` | **The production bot** — NinjaTrader 8 NinjaScript strategy (frozen port of the audited engine) |
| `nt8/PropBotGovernor.cs` | Shared account-level risk governor: −$400 daily stop, +$1,650 consistency cap, sizing ladder, persisted crash-safe state |
| `nt8/PORT_SPEC.md` | Line-by-line traceability paper engine → C# + disclosed differences + acceptance test |
| `nt8/DEPLOY.md` | Install, Topstep connection, Market Replay shakedown, supervised-operation and go-live runbook |
| `scripts/` | Research pipeline + the audited live paper-trading engine `paper_step.py` (v2.1) |
| `reports/` | Independent audit, remediation, post-audit fixes (N1–N5), launch report, paper10 session ledger |
| `download/` | Research report (PDF/XLSX), market data, live paper-trading journal snapshot |
| `remediation_workspace/`, `audit_workspace/`, `fixes_workspace/` | Test evidence: 16/16 + 15/15 + 68/68 assertions, all passing |

## Status

- ✅ Research → backtest → synthetic stress → strategy selection (VWAP Fade v1 won)
- ✅ Paper-trading engine v2.1 (post-audit fixes verified, signal engine unchanged to the cent)
- ✅ NT8 bot code + runbooks (this repo's `nt8/`) — pending first compile in NinjaTrader
- 🔄 **10-session paper-trading validation** — runs to Nov 18, 2026; daily ledger in `reports/paper10_sessions.md`
- ⏳ Then: Market Replay shakedown → buy Topstep 50K eval → supervised deployment

## The one rule

`nt8/VwapFadeBot.cs` must produce the same signals as `scripts/paper_step.py` on the same bars, to the cent. "Improvements" to frozen parameters are spec violations — see `nt8/PORT_SPEC.md`.

*Research report: `download/futures_prop_bot_research.pdf`. Not financial advice; futures trading carries substantial risk of loss.*
