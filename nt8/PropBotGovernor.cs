#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
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
// PropBotGovernor — shared account-level risk state for the VWAP Fade
// evaluation bot (static, shared by the MES and MNQ strategy instances).
//
// Ports the audited gate logic of scripts/paper_step.py v2.1:
//   * P0.1 daily loss stop  : day P&L <= -$400 -> persisted haltedDate, no
//     entries on any symbol until the next ET day (flag + live condition).
//   * N1   daily profit cap : day P&L >= cap (default $1,650 = 55% x $3,000
//     Topstep consistency) -> persisted capBlockedDate, same triple gate.
//   * Sizing ladder         : cum >= $1,500 -> 4 micros, else 2.
//   * N2 day-scoped books   : realized P&L is accumulated per symbol PER ET
//     DAY and reset on rollover; a new ET day clears both flags.
//   * Persistence: JSON state file survives NT8 restarts (halt cannot be
//     outrun by a restart). Crash-safe: write-to-temp + atomic replace.
//
// Day P&L here is the SUM of the two strategy instances' closed-trade nets on
// the dedicated evaluation account — the account's real realized P&L
// including real commissions (the paper engine modelled $2.45/$1.70 RT; the
// live account shows actual fees, which is strictly more truthful).
// ---------------------------------------------------------------------------
namespace NinjaTrader.NinjaScript.Strategies
{
    public class PropBotGovernor
    {
        private static readonly PropBotGovernor instance = new PropBotGovernor();
        public static PropBotGovernor Instance { get { return instance; } }
        private static readonly object sync = new object();

        [DataContract]
        public class PosRecord
        {
            [DataMember] public string Id;
            [DataMember] public int Side;         // +1 long, -1 short
            [DataMember] public int Qty;
            [DataMember] public double EntryAvg;
            [DataMember] public double StopPx;
            [DataMember] public double TargetPx;
            [DataMember] public string Sym;
            [DataMember] public string DateIso;
        }

        [DataContract]
        private class GovState
        {
            [DataMember] public string SchemaVersion = "2";
            [DataMember] public string StateDateIso = "";          // ET day the books belong to
            [DataMember] public string HaltedDateIso = "";         // daily-loss-stop flag (persisted)
            [DataMember] public string HaltedReason = "";
            [DataMember] public string CapBlockedDateIso = "";     // daily profit cap flag (persisted)
            [DataMember] public string CapBlockedReason = "";
            [DataMember] public double CumTotal = 0.0;             // engine-style cumulative net (sizing ladder)
            [DataMember] public Dictionary<string, double> RealizedTodayBySym = new Dictionary<string, double>();
            [DataMember] public PosRecord OpenPosition = null;     // one open position per symbol; stored flat
        }

        private GovState st = new GovState();
        private string dir = "", file = "", logFile = "";
        private readonly Dictionary<string, PosRecord> openBySym = new Dictionary<string, PosRecord>();
        public string LastGateMessage { get; private set; }

        private PropBotGovernor() { }

        public void EnsureLoaded(string stateDir)
        {
            lock (sync)
            {
                dir = stateDir; file = Path.Combine(dir, "propbot_state.json");
                logFile = Path.Combine(dir, "logs", "vwapfade_" + EtNow().ToString("yyyyMMdd") + ".log");
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                if (!Directory.Exists(Path.Combine(dir, "logs"))) Directory.CreateDirectory(Path.Combine(dir, "logs"));
                if (File.Exists(file))
                {
                    try
                    {
                        using (var fs = new FileStream(file, FileMode.Open, FileAccess.Read))
                        {
                            var ser = new DataContractJsonSerializer(typeof(GovState));
                            st = (GovState)ser.ReadObject(fs);
                        }
                    }
                    catch (Exception e) { LogLine("STATE LOAD FAILED (" + e.Message + ") - starting fresh books. " +
                        "If a position was open, flatten manually and verify."); }
                }
                // rebuild in-memory open-position index from persisted record(s)
                openBySym.Clear();
                if (st.OpenPosition != null) openBySym[st.OpenPosition.Sym] = st.OpenPosition;
                ResetDayIfNeeded(EtNow().Date);
                Save();
            }
        }

        public void RegisterInstance(string sym) { lock (sync) { if (!st.RealizedTodayBySym.ContainsKey(sym)) st.RealizedTodayBySym[sym] = 0.0; Save(); } }

        // ---------- gates ----------

        public double DayPnl { get { lock (sync) { double t = 0; foreach (var kv in st.RealizedTodayBySym) t += kv.Value; return t; } } }
        public double CumTotal { get { lock (sync) { return st.CumTotal; } } }
        public string HaltedDate { get { lock (sync) { return string.IsNullOrEmpty(st.HaltedDateIso) ? null : st.HaltedDateIso; } } }
        public string CapBlockedDate { get { lock (sync) { return string.IsNullOrEmpty(st.CapBlockedDateIso) ? null : st.CapBlockedDateIso; } } }

        /// <summary>P0.1/N1 entry gate: persisted flag OR live condition blocks; resets on a new ET day.</summary>
        public bool CanEnter(string sym, DateTime etBarDate, double dailyStopUsd, double dailyProfitCapUsd)
        {
            lock (sync)
            {
                ResetDayIfNeeded(etBarDate);
                string today = etBarDate.ToString("yyyy-MM-dd");
                double day = DayPnl;

                if (st.HaltedDateIso == today)
                { LastGateMessage = "BLOCKED: daily stop " + st.HaltedReason; return false; }
                if (day <= -dailyStopUsd)
                { FireHalt(today, day, dailyStopUsd, sym); return false; }

                if (dailyProfitCapUsd > 0)
                {
                    if (st.CapBlockedDateIso == today)
                    { LastGateMessage = "BLOCKED: profit cap " + st.CapBlockedReason; return false; }
                    if (day >= dailyProfitCapUsd)
                    { FireCap(today, day, dailyProfitCapUsd, sym); return false; }
                }
                LastGateMessage = "ok";
                return true;
            }
        }

        private void FireHalt(string today, double day, double stop, string sym)
        {
            st.HaltedDateIso = today;
            st.HaltedReason = string.Format("account day P&L {0:F2} breached the -{1:F0} daily stop", day, stop);
            LogLine("**DAILY STOP " + today + ": " + st.HaltedReason + " — no further entries until next trading day (sym " + sym + ")**");
            Save();
        }

        private void FireCap(string today, double day, double cap, string sym)
        {
            st.CapBlockedDateIso = today;
            st.CapBlockedReason = string.Format("account day P&L {0:F2} reached the {1:F0} consistency cap", day, cap);
            LogLine("**DAILY PROFIT CAP " + today + ": " + st.CapBlockedReason + " — no further entries until next trading day (sym " + sym + ")**");
            Save();
        }

        /// <summary>N2: on a new ET day, reset the day books and both gate flags.</summary>
        public void ResetDayIfNeeded(DateTime etDate)
        {
            string today = etDate.ToString("yyyy-MM-dd");
            if (st.StateDateIso == today) return;
            st.StateDateIso = today;
            st.RealizedTodayBySym = new Dictionary<string, double>();
            st.HaltedDateIso = ""; st.HaltedReason = "";
            st.CapBlockedDateIso = ""; st.CapBlockedReason = "";
            st.OpenPosition = null;
            openBySym.Clear();
            LogLine("--- new ET trading day " + today + " — day books and gate flags reset ---");
            Save();
        }

        // ---------- books ----------

        public void OnClosed(string sym, double net, string reason, double dailyStopUsd, double dailyProfitCapUsd)
        {
            lock (sync)
            {
                if (!st.RealizedTodayBySym.ContainsKey(sym)) st.RealizedTodayBySym[sym] = 0.0;
                st.RealizedTodayBySym[sym] += net;
                st.CumTotal += net;
                st.OpenPosition = null;
                openBySym.Remove(sym);
                // symmetric post-close checks (paper check_daily_stop / check_daily_cap)
                string today = EtNow().ToString("yyyy-MM-dd");
                if (st.HaltedDateIso != today && DayPnl <= -dailyStopUsd) FireHalt(today, DayPnl, dailyStopUsd, sym);
                if (dailyProfitCapUsd > 0 && st.CapBlockedDateIso != today && DayPnl >= dailyProfitCapUsd)
                    FireCap(today, DayPnl, dailyProfitCapUsd, sym);
                Save();
            }
        }

        // ---------- open-position persistence (crash recovery) ----------

        public void PersistOpenPosition(string sym, int side, int qty, double entryAvg, double stopPx, double targetPx, string id)
        {
            lock (sync)
            {
                var p = new PosRecord { Sym = sym, Side = side, Qty = qty, EntryAvg = entryAvg, StopPx = stopPx, TargetPx = targetPx, Id = id, DateIso = EtNow().ToString("yyyy-MM-dd") };
                st.OpenPosition = p; openBySym[sym] = p; Save();
            }
        }

        public void ClearOpenPosition(string sym) { lock (sync) { openBySym.Remove(sym); st.OpenPosition = null; Save(); } }
        public PosRecord GetOpenPosition(string sym) { lock (sync) { PosRecord p; return openBySym.TryGetValue(sym, out p) ? p : null; } }

        // ---------- io ----------

        public void Save()
        {
            try
            {
                string tmp = file + ".tmp";
                using (var fs = new FileStream(tmp, FileMode.Create, FileAccess.Write))
                {
                    var ser = new DataContractJsonSerializer(typeof(GovState));
                    ser.WriteObject(fs, st);
                }
                if (File.Exists(file)) File.Delete(file);
                File.Move(tmp, file);                      // atomic replace: a crash cannot corrupt the books
            }
            catch (Exception e) { LogLine("STATE SAVE FAILED: " + e.Message); }
        }

        public void AppendLog(string line) { lock (sync) { LogLine(line); } }

        private void LogLine(string msg)
        {
            try
            {
                if (dir == "") return;
                File.AppendAllText(Path.Combine(dir, "logs", "vwapfade_" + EtNow().ToString("yyyyMMdd") + ".log"),
                    msg + Environment.NewLine);
            }
            catch { }                                       // logging must never break trading
        }

        private DateTime EtNow() { return TimeZoneInfo.ConvertTime(DateTime.Now, TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time")); }
    }
}
