// Convexa Ancla — NinjaTrader 8 indicator (Lester 2026-11-04).
//
// On a 1-minute ES / MES / NQ / MNQ chart, when the 09:30 ET bar closes (NinjaTrader stamps it 09:31:00), it sends that bar's high, low and
// close to Convexa, which computes the opening anchor ((H + L + C) / 3 rounded to 0.25) and saves it for the day: only if the day has no
// anchor yet (a number Lester typed is never overwritten) and only if it passes the same check as the 09:41 Yahoo task. Once per instrument
// per day. If NinjaTrader is opened after 09:31, the bar is sent when the chart loads (the server still accepts today's 09:30 bar).
//
// It never places orders: it only reads the chart's bars. The shared key is read from %USERPROFILE%\.convexa\ninjatrader_anchor_key.txt
// (copied there by Convexa's setup; never typed into the indicator). The result is printed in New > NinjaScript Output.
//
// Install: New > NinjaScript Editor > Indicators > right click > New Indicator... is NOT needed: copy this file to
// Documents\NinjaTrader 8\bin\Custom\Indicators\, open the NinjaScript Editor and press F5 to compile, then add "Convexa Ancla" to the
// 1-minute ES (or MES) chart and to the 1-minute NQ (or MNQ) chart.

#region Using declarations
using System;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class ConvexaAncla : Indicator
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(20) };
        private static readonly TimeZoneInfo Eastern = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
        private DateTime sentForDay = DateTime.MinValue;
        private bool warnedPeriod;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "Convexa Ancla";
                Description = "Sends the 09:30 ET 1-minute bar of ES / NQ (or MES / MNQ) to Convexa as the day's opening anchor.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;
                Url = "https://dashboard.convexatrading.com/api/v1/feeds/futures-anchor";
                DryRun = false;
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute || BarsPeriod.Value != 1)
            {
                if (!warnedPeriod)
                {
                    Print("Convexa Ancla: put it on a 1-minute chart (this one is " + BarsPeriod + ").");
                    warnedPeriod = true;
                }
                return;
            }
            DateTime closeEt = TimeZoneInfo.ConvertTime(Time[0], Core.Globals.GeneralOptions.TimeZoneInfo, Eastern);
            DateTime todayEt = TimeZoneInfo.ConvertTime(DateTime.UtcNow, TimeZoneInfo.Utc, Eastern).Date;
            if (closeEt.Date != todayEt || closeEt.TimeOfDay != new TimeSpan(9, 31, 0) || sentForDay == todayEt)
                return;
            sentForDay = todayEt;
            DateTime startEt = closeEt.AddMinutes(-1);
            string barStart = new DateTimeOffset(startEt, Eastern.GetUtcOffset(startEt)).ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture);
            string json = string.Format(
                CultureInfo.InvariantCulture,
                "{{\"symbol\":\"{0}\",\"bar_start\":\"{1}\",\"high\":{2},\"low\":{3},\"close\":{4},\"dry_run\":{5}}}",
                Instrument.MasterInstrument.Name, barStart, High[0], Low[0], Close[0], DryRun ? "true" : "false");
            string label = Instrument.FullName + " 09:30 H " + High[0].ToString(CultureInfo.InvariantCulture)
                + " L " + Low[0].ToString(CultureInfo.InvariantCulture) + " C " + Close[0].ToString(CultureInfo.InvariantCulture);
            Task.Run(() => Send(json, label));
        }

        private async Task Send(string json, string label)
        {
            try
            {
                string keyFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".convexa", "ninjatrader_anchor_key.txt");
                if (!File.Exists(keyFile))
                {
                    Print("Convexa Ancla: key file missing (" + keyFile + "); nothing sent.");
                    return;
                }
                using (var request = new HttpRequestMessage(HttpMethod.Post, Url))
                {
                    request.Headers.Add("X-Convexa-Key", File.ReadAllText(keyFile).Trim());
                    request.Content = new StringContent(json, Encoding.UTF8, "application/json");
                    using (HttpResponseMessage response = await Http.SendAsync(request).ConfigureAwait(false))
                    {
                        string body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        Print("Convexa Ancla: " + label + " -> HTTP " + (int)response.StatusCode + " " + body);
                    }
                }
            }
            catch (Exception error)
            {
                Print("Convexa Ancla: send failed for " + label + ": " + error.GetType().Name + " " + error.Message);
            }
        }


        [Display(Name = "Convexa URL", Order = 1, GroupName = "Convexa")]
        public string Url { get; set; }


        [Display(Name = "Dry run (test, saves nothing)", Order = 2, GroupName = "Convexa")]
        public bool DryRun { get; set; }
    }
}
