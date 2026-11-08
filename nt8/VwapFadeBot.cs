#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

// ---------------------------------------------------------------------------
// VwapFadeBot — NinjaTrader 8 port of the audited VWAP Fade v1 paper-trading
// engine (scripts/paper_step.py v2.1, freeze 2026-11-05).
//
// FROZEN SPEC (reports/vwap_fade_fixes_paper10_launch.md — do not "improve"):
//   * Session VWAP from the 10:00 ET bar; sigma = sample-std(close - vwap),
//     expanding, computed on 60-minute bars stamped 10:00..15:00 ET only.
//   * SHORT when a bar's High >= vwap + 2.0*sigma  (signal px = max(band, open))
//     LONG symmetric on vwap - 2.0*sigma. Signals evaluated on bars stamped
//     10:00/11:00/12:00/13:00 ET with >= 3 bars of sigma history
//     (practically: the 12:00 and 13:00 bars).
//   * Stop = fill -/+ 1.2*sigma (stop-market). Target = signal-bar VWAP
//     (limit; the 1-tick trade-through rule is native to real exchange orders).
//   * Trend filter: skip NEW fades when |close - day_open| > 0.6% of day_open.
//   * Max 3 trades/day/symbol, one position per symbol (ES and NQ instances
//     may hold simultaneously, exactly like the audited engine).
//   * Account-level gates (both symbols, dedicated eval account):
//       - daily loss stop: no entries once day P&L <= -$400 (persisted, survives
//         restarts, live-condition double gate).
//       - daily profit cap: no entries once day P&L >= +$1,650 (Topstep 55%
//         consistency; set 0 to disable for Apex). Persisted triple gate.
//   * Sizing: 2 micros/symbol; 4 once engine-cumulative P&L >= +$1,500.
//   * Flat by 15:59 ET (market exit + order cleanup).
//
// ACCEPTANCE TEST for this port: same bars -> same signals as paper_step.py
// replay, to the cent (see nt8/PORT_SPEC.md). Any parameter change or logic
// "improvement" is a spec violation, not an improvement.
//
// RUN MODE: one instance per instrument (MES 12-24 and MNQ 12-24), 60-minute
// bars, ETH session (top-of-hour stamps), dedicated eval account. Calculate is
// OnPriceChange with IsFirstTickOfBar bar-close detection so the 15:59 ET
// flatten fires on time; signal logic runs exactly once per closed bar.
//
// SAFETY: this file is for the NEW Topstep evaluation bot. It shares nothing
// with any production PropBot workspace — install into its own NT8 workspace,
// attach ONLY to the dedicated evaluation account. See nt8/DEPLOY.md.
// ---------------------------------------------------------------------------
namespace NinjaTrader.NinjaScript.Strategies
{
    public class VwapFadeBot : Strategy
    {
        #region Frozen parameters (defaults = audited freeze; changing them violates the spec)
        [NinjaScriptProperty, Range(0.5, 5.0)]
        [Display(Name = "Entry sigma (k)", GroupName = "1) Frozen spec", Order = 0),
         Description("VWAP band in sigmas. FROZEN: 2.0 (research Table 8).")]
        public double K { get; set; }

        [NinjaScriptProperty, Range(0.5, 5.0)]
        [Display(Name = "Stop sigma (stop_k)", GroupName = "1) Frozen spec", Order = 1),
         Description("Stop distance in sigmas from the FILL. FROZEN: 1.2.")]
        public double StopK { get; set; }

        [NinjaScriptProperty, Range(0.0, 0.05)]
        [Display(Name = "Trend filter (max |close-open|/open)", GroupName = "1) Frozen spec", Order = 2),
         Description("Skip new fades when |close - day_open| exceeds this fraction. FROZEN: 0.006.")]
        public double TrendPct { get; set; }

        [NinjaScriptProperty, Range(1, 10)]
        [Display(Name = "Max trades/day/symbol", GroupName = "1) Frozen spec", Order = 3),
         Description("FROZEN: 3.")]
        public int MaxTrades { get; set; }

        [NinjaScriptProperty, Range(2, 50)]
        [Display(Name = "Base contracts/symbol", GroupName = "1) Frozen spec", Order = 4),
         Description("FROZEN: 2 micros.")]
        public int BaseQty { get; set; }

        [NinjaScriptProperty, Range(2, 100)]
        [Display(Name = "Scaled contracts/symbol", GroupName = "1) Frozen spec", Order = 5),
         Description("FROZEN: 4 micros.")]
        public int ScaledQty { get; set; }

        [NinjaScriptProperty, Range(100, 100000)]
        [Display(Name = "Sizing scale level ($ cum)", GroupName = "1) Frozen spec", Order = 6),
         Description("Scale to ScaledQty once cumulative P&L >= this. FROZEN: 1500 (50% of $3,000 target).")]
        public double ScaleLevel { get; set; }

        [NinjaScriptProperty, Range(50, 100000)]
        [Display(Name = "Daily loss stop ($)", GroupName = "1) Frozen spec", Order = 7),
         Description("Halt entries for the day at this account-day loss. FROZEN: 400 (50K accounts).")]
        public double DailyStopUsd { get; set; }

        [NinjaScriptProperty, Range(0, 100000)]
        [Display(Name = "Daily profit cap ($, 0=off)", GroupName = "1) Frozen spec", Order = 8),
         Description("Bot-side consistency cap: no entries once day P&L >= this. FROZEN: 1650 = 55% x $3,000 (Topstep). Set 0 for Apex (no consistency rule).")]
        public double DailyProfitCapUsd { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Session start (ET hour)", GroupName = "1) Frozen spec", Order = 9),
         Description("First bar of the session VWAP. FROZEN: 10.")]
        public int SessionStartHour { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Flatten time (ET, minutes)", GroupName = "1) Frozen spec", Order = 10),
         Description("Minutes after midnight ET to be flat. FROZEN: 959 = 15:59 ET.")]
        public int FlattenEtMinutes { get; set; }
        #endregion

        private PropBotGovernor gov;                 // shared static account governor
        private TimeZoneInfo etZone;

        // per-day session state (mirrors paper_step.py new_day())
        private DateTime dayDate = DateTime.MinValue;
        private double vwapN, vwapD;                 // VWAP numerator/denominator
        private List<double> devs;                   // expanding close-vwap deviations
        private double? dayOpen;
        private int tradesToday;
        private double sd;                           // sigma at signal time
        private double sigVwap;                      // target: signal-bar VWAP
        private string entryId;                      // current entry signal name
        private int entrySide;                       // +1 long, -1 short (0 flat)
        private double entryAvg;
        private int entryQty;

        private string Sym { get { return Instrument.MasterInstrument.Name; } }
        private double PointValue { get { return Instrument.MasterInstrument.PointValue; } }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "VWAP Fade v1 (FROZEN) - Topstep evaluation bot. Port of scripts/paper_step.py v2.1. See nt8/PORT_SPEC.md.";
                Name = "VwapFadeBot";
                Calculate = Calculate.OnPriceChange;     // bar-close via IsFirstTickOfBar + timely 15:59 flatten
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;    // we flatten ourselves at 15:59 ET (documented, D4)
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution = OrderFillResolution.Standard;
                Slippage = 0;
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Gtc;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 8;
                IsInstantiatedOnEachOptimizationIteration = false;

                // ---- frozen defaults ----
                K = 2.0; StopK = 1.2; TrendPct = 0.006; MaxTrades = 3;
                BaseQty = 2; ScaledQty = 4; ScaleLevel = 1500.0;
                DailyStopUsd = 400.0; DailyProfitCapUsd = 1650.0;
                SessionStartHour = 10; FlattenEtMinutes = 959;
            }
            else if (State == State.Configure)
            {
                etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
            }
            else if (State == State.Realtime)
            {
                gov = PropBotGovernor.Instance;
                gov.EnsureLoaded(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    @"NinjaTrader 8\propbot"));
                gov.RegisterInstance(Sym);
                // restart with a persisted open position (crash recovery): resubmit exits
                ResyncPersistedPosition();
                Log("REALTIME: governor loaded. haltedDate=" + (gov.HaltedDate ?? "-")
                    + " capBlockedDate=" + (gov.CapBlockedDate ?? "-")
                    + " cum=" + gov.CumTotal.ToString("F2"));
            }
            else if (State == State.Terminated)
            {
                if (gov != null) gov.Save();
            }
        }

        // ---------------- helpers ----------------

        private DateTime Et(DateTime machineTime) { return TimeZoneInfo.ConvertTime(machineTime, etZone); }
        private DateTime EtNow() { return TimeZoneInfo.ConvertTime(DateTime.Now, etZone); }

        private void Log(string msg)
        {
            string line = string.Format("{0:yyyy-MM-dd HH:mm:ss} ET [{1}] {2}", EtNow(), Sym, msg);
            Print(line);
            gov.AppendLog(line);
        }

        // ---------------- main loop ----------------

        protected override void OnBarUpdate()
        {
            if (State != State.Realtime || CurrentBar < BarsRequiredToTrade)
                return;                                   // historical chart warm-up: never signal (documented, D6)

            // 15:59 ET flatten runs on every price change (time-critical)
            if (EtNow().TimeOfDay >= TimeSpan.FromMinutes(FlattenEtMinutes))
            {
                FlattenIfOpen("eod");
                return;
            }

            if (!IsFirstTickOfBar || CurrentBar < 1)
                return;

            ProcessClosedBar(CurrentBar - 1);             // the just-closed hourly bar
        }

        private void ProcessClosedBar(int i)
        {
            DateTime barStartEt = Et(Time[i]);
            DateTime barDate = barStartEt.Date;
            int barHour = barStartEt.Hour;

            // session window: bars stamped 10:00..15:59 ET only (paper between_time("10:00","15:59"))
            if (barHour < SessionStartHour || barHour >= 16) return;

            // day rollover (paper bar_step day reset)
            if (barDate != dayDate)
            {
                if (Position.MarketPosition != MarketPosition.Flat)
                {
                    // carried position across days must never happen (15:59 flatten); emergency flatten
                    Log("WARNING: position carried into new day - emergency flatten (eod_missed)");
                    FlattenIfOpen("eod_missed");
                }
                dayDate = barDate;
                vwapN = vwapD = 0.0; devs = new List<double>();
                dayOpen = null; tradesToday = 0;
                gov.ResetDayIfNeeded(barDate);            // account-level halt/cap flags reset here
            }

            // zero-volume bar: paper skips it entirely (no VWAP/dev update)
            if (Volume[i] <= 0) return;

            double o = Open[i], h = High[i], l = Low[i], c = Close[i], v = Volume[i];
            if (dayOpen == null) dayOpen = o;

            // session VWAP + expanding sigma, INCLUDING the current bar (paper lines 605-610)
            vwapN += (h + l + c) / 3.0 * v;
            vwapD += v;
            double vwap = vwapN / Math.Max(vwapD, 1e-9);
            devs.Add(c - vwap);
            double sdNow = devs.Count >= 2 ? StdSample(devs) : 0.0;

            // 1) exits are handled by the resting OCO stop-market / limit orders
            //    (real-time native fills - strictly better than the paper's bar
            //    approximation; disclosed as D2 in PORT_SPEC.md)

            // 2) entry signal on the just-closed bar (paper bar_step section 2)
            if (Position.MarketPosition == MarketPosition.Flat
                && devs.Count >= 3
                && barHour < 14                              // bars stamped 10:00..13:00
                && tradesToday < MaxTrades
                && gov.CanEnter(Sym, barDate, DailyStopUsd, DailyProfitCapUsd))
            {
                if (Math.Abs(c - dayOpen.Value) <= TrendPct * dayOpen.Value && sdNow > 0)
                {
                    double up = vwap + K * sdNow, dn = vwap - K * sdNow;
                    int side = 0; double signalPx = 0;
                    if (h >= up) { side = -1; signalPx = Math.Max(up, o); }
                    else if (l <= dn) { side = +1; signalPx = Math.Min(dn, o); }

                    if (side != 0)
                    {
                        sd = sdNow; sigVwap = vwap;
                        tradesToday++;
                        int qty = gov.CumTotal >= ScaleLevel ? ScaledQty : BaseQty;
                        entryId = string.Format("VFB_{0:yyyyMMdd}_{1}_{2:00}", barDate, Sym, tradesToday);
                        entrySide = side;
                        if (side > 0) EnterLong(qty, entryId); else EnterShort(qty, entryId);
                        Log(string.Format(
                            "SIGNAL {0} {1} micro(s): bar {2:HH}:00 ET touched {3} band; signalPx {4:F2} " +
                            "(band {5:F2}, vwap {6:F2}, sd {7:F2}) -> entry order submitted at observable price",
                            side > 0 ? "LONG" : "SHORT", qty, barStartEt, side > 0 ? "lower" : "upper",
                            signalPx, side > 0 ? dn : up, vwap, sdNow));
                    }
                }
            }

            DrawStatus(vwap, sdNow);
        }

        // ---------------- fills and exits ----------------

        protected override void OnExecutionUpdate(Execution execution, string executionId,
            double price, int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution == null || execution.Order == null || execution.Order.OrderState != OrderState.Filled)
                return;

            string name = execution.Order.Name;

            // entry filled -> submit the OCO exits from the ACTUAL fill (paper P0.2)
            if (entryId != null && name == entryId)
            {
                entryAvg = execution.Order.AverageFillPrice;
                entryQty = (int)execution.Order.Filled;
                double stopPx = entrySide > 0 ? entryAvg - StopK * sd : entryAvg + StopK * sd;

                if (entrySide > 0)
                {
                    ExitLongStopMarket(0, true, entryQty, stopPx, "S_" + entryId, entryId);
                    ExitLongLimit(0, true, entryQty, sigVwap, "T_" + entryId, entryId);
                }
                else
                {
                    ExitShortStopMarket(0, true, entryQty, stopPx, "S_" + entryId, entryId);
                    ExitShortLimit(0, true, entryQty, sigVwap, "T_" + entryId, entryId);
                }
                gov.PersistOpenPosition(Sym, entrySide, entryQty, entryAvg, stopPx, sigVwap, entryId);
                Log(string.Format("FILLED {0} @ {1:F2} x{2}; stop {3:F2} (1.2sigma), target {4:F2} (signal vwap)",
                    name, entryAvg, entryQty, stopPx, sigVwap));
                return;
            }

            // exit filled -> account the realized P&L (paper close_pos, real costs)
            if (entryId != null && (name == "S_" + entryId || name == "T_" + entryId || name.StartsWith("EOD_")))
            {
                double exitPx = execution.Order.AverageFillPrice;
                double net = (exitPx - entryAvg) * entrySide * PointValue * entryQty;  // real commissions apply at account level
                string reason = name.StartsWith("S_") ? "stop" : (name.StartsWith("T_") ? "tp" : "eod");
                gov.OnClosed(Sym, net, reason, DailyStopUsd, DailyProfitCapUsd);
                Log(string.Format("CLOSED {0}: {1:F2} -> {2:F2} = {3:F2} USD (real fees) | day {4:F2} cum {5:F2} | gate {6}",
                    reason, entryAvg, exitPx, net, gov.DayPnl, gov.CumTotal, gov.LastGateMessage));
                return;
            }
        }

        protected override void OnPositionUpdate(Position position, double averagePrice,
            int quantity, MarketPosition marketPosition)
        {
            if (State != State.Realtime) return;
            if (marketPosition == MarketPosition.Flat)
            {
                gov.ClearOpenPosition(Sym);
                CleanupExitOrders();
            }
        }

        private void FlattenIfOpen(string reason)
        {
            if (Position.MarketPosition == MarketPosition.Flat) return;
            string id = "EOD_" + reason;
            if (Position.MarketPosition == MarketPosition.Long) ExitLong(Position.Quantity, id, entryId ?? "");
            else ExitShort(Position.Quantity, id, entryId ?? "");
            Log("FLATTEN " + reason + " at " + EtNow().ToString("HH:mm:ss") + " ET (flat-by-15:59 rule)");
        }

        private void CleanupExitOrders()
        {
            foreach (Order o in Orders)
            {
                if (o.OrderState == OrderState.Working
                    && (o.Name.StartsWith("S_VFB_") || o.Name.StartsWith("T_VFB_")))
                    CancelOrder(o);
            }
        }

        private void ResyncPersistedPosition()
        {
            var p = gov.GetOpenPosition(Sym);
            if (p == null) return;
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                gov.ClearOpenPosition(Sym);               // stale record, position is gone
                return;
            }
            entryId = p.Id; entrySide = p.Side; entryQty = p.Qty; entryAvg = p.EntryAvg;
            if (entrySide > 0)
            {
                ExitLongStopMarket(0, true, entryQty, p.StopPx, "S_" + entryId, entryId);
                ExitLongLimit(0, true, entryQty, p.TargetPx, "T_" + entryId, entryId);
            }
            else
            {
                ExitShortStopMarket(0, true, entryQty, p.StopPx, "S_" + entryId, entryId);
                ExitShortLimit(0, true, entryQty, p.TargetPx, "T_" + entryId, entryId);
            }
            Log("RESYNC: resubmitted OCO exits for persisted position " + entryId);
        }

        // ---------------- status / misc ----------------

        private void DrawStatus(double vwap, double sdNow)
        {
            string txt = string.Format(
                "VwapFadeBot {0}  |  day P&L {1:F2}  cum {2:F2}  trades {3}/{4}" +
                "\nVWAP {5:F2}  sd {6:F2}  bands {7:F2} / {8:F2}" +
                "\nhalts: {9}  cap: {10}  gate: {11}",
                Sym, gov.DayPnl, gov.CumTotal, tradesToday, MaxTrades,
                vwap, sdNow, vwap + K * sdNow, vwap - K * sdNow,
                gov.HaltedDate ?? "-", gov.CapBlockedDate ?? "-", gov.LastGateMessage);
            Draw.TextFixed(this, "vfbstatus", txt, TextPosition.TopRight);
        }

        private static double StdSample(List<double> xs)
        {
            double mean = 0; foreach (double x in xs) mean += x; mean /= xs.Count;
            double ss = 0; foreach (double x in xs) ss += (x - mean) * (x - mean);
            return Math.Sqrt(ss / (xs.Count - 1));        // ddof=1, as paper np.std(ddof=1)
        }
    }
}
