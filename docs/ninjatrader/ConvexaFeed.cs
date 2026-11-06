// Convexa Feed — NinjaTrader 8 indicator (Lester 2026-11-06, phase 2 of docs/diseno-es-nq-en-vivo-ninjatrader-2026-11-06.txt).
//
// On a 1-minute ES / MES, NQ / MNQ or RTY / M2K chart it sends Convexa, about once a second:
//   - the last price (with bid / ask), only when it changed or every 5 s as a heartbeat;
//   - every closed 1-minute bar (open / high / low / close / volume, with the bar's START time), regular and evening hours.
// When the chart loads, today's bars since 00:00 ET are sent too (in pieces of 200), so a NinjaTrader opened late still fills the day.
// Convexa uses the bars for the live ES - SPX / NQ - NDX difference (so ES / NQ levels follow the real futures, not the 09:31 anchor) and
// shows the last price only to Lester (CME real-time data is for his personal use).
//
// It never places orders: it only reads the chart. The shared key is the anchor's, read from %USERPROFILE%\.convexa\ninjatrader_anchor_key.txt
// (never typed into the indicator, never printed). Results and errors go to New > NinjaScript Output, at most once a minute.
//
// Install: copy this file to Documents\NinjaTrader 8\bin\Custom\Indicators\, open the NinjaScript Editor and press F5 to compile, then add
// "Convexa Feed" to the 1-minute ES (or MES), NQ (or MNQ) and RTY (or M2K) charts. It can live on the same chart as "Convexa Ancla".

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class ConvexaFeed : Indicator
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        private static readonly TimeZoneInfo Eastern = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
        private const int MaxQueuedBars = 1500;
        private const int BarsPerPost = 200;

        private readonly object gate = new object();
        private readonly List<string> pendingBars = new List<string>();
        private Timer timer;
        private int sending;
        private double last, bid, ask;
        private DateTime lastTimeUtc = DateTime.MinValue;
        private DateTime lastSentQuoteUtc = DateTime.MinValue;
        private double lastSentPrice;
        private DateTime lastLogUtc = DateTime.MinValue;
        private string master, contract;
        private bool warnedPeriod;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "Convexa Feed";
                Description = "Sends the last price and every closed 1-minute bar of ES / NQ / RTY (or the micros) to Convexa, about once a second.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;
                Url = "https://dashboard.convexatrading.com/backend/api/v1/feeds/futures-live";
            }
            else if (State == State.DataLoaded)
            {
                master = Instrument.MasterInstrument.Name;
                contract = Instrument.FullName;
            }
            else if (State == State.Realtime)
            {
                if (timer == null)
                    timer = new Timer(_ => Tick(), null, 1000, 1000);
            }
            else if (State == State.Terminated)
            {
                if (timer != null)
                {
                    timer.Dispose();
                    timer = null;
                }
            }
        }

        protected override void OnMarketData(MarketDataEventArgs e)
        {
            lock (gate)
            {
                if (e.MarketDataType == MarketDataType.Last)
                {
                    last = e.Price;
                    lastTimeUtc = TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(e.Time, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo);
                }
                else if (e.MarketDataType == MarketDataType.Bid)
                    bid = e.Price;
                else if (e.MarketDataType == MarketDataType.Ask)
                    ask = e.Price;
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute || BarsPeriod.Value != 1)
            {
                if (!warnedPeriod)
                {
                    Print("Convexa Feed: put it on a 1-minute chart (this one is " + BarsPeriod + ").");
                    warnedPeriod = true;
                }
                return;
            }
            // NinjaTrader stamps a bar with its CLOSE time: the bar started a minute earlier.
            DateTime closeUtc = TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(Time[0], DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo);
            DateTime startUtc = closeUtc.AddMinutes(-1);
            if (State == State.Historical)
            {
                DateTime todayEt = TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, Eastern).Date;
                if (TimeZoneInfo.ConvertTimeFromUtc(startUtc, Eastern).Date != todayEt)
                    return;
            }
            string json = string.Format(
                CultureInfo.InvariantCulture,
                "{{\"symbol\":\"{0}\",\"contract\":\"{1}\",\"start\":\"{2}\",\"open\":{3},\"high\":{4},\"low\":{5},\"close\":{6},\"volume\":{7}}}",
                master, contract, startUtc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
                Open[0], High[0], Low[0], Close[0], (long)Volume[0]);
            lock (gate)
            {
                pendingBars.Add(json);
                if (pendingBars.Count > MaxQueuedBars)
                    pendingBars.RemoveRange(0, pendingBars.Count - MaxQueuedBars);
            }
        }

        private void Tick()
        {
            if (Interlocked.CompareExchange(ref sending, 1, 0) != 0)
                return; // the previous post is still out
            string body;
            List<string> bars;
            lock (gate)
            {
                bars = pendingBars.GetRange(0, Math.Min(BarsPerPost, pendingBars.Count));
                string quote = "";
                DateTime now = DateTime.UtcNow;
                bool changed = last > 0 && (last != lastSentPrice || (now - lastSentQuoteUtc).TotalSeconds >= 5);
                if (changed && lastTimeUtc > DateTime.MinValue)
                {
                    quote = string.Format(
                        CultureInfo.InvariantCulture,
                        "{{\"symbol\":\"{0}\",\"contract\":\"{1}\",\"last\":{2},\"bid\":{3},\"ask\":{4},\"time\":\"{5}\"}}",
                        master, contract, last, bid > 0 ? bid.ToString(CultureInfo.InvariantCulture) : "null",
                        ask > 0 ? ask.ToString(CultureInfo.InvariantCulture) : "null",
                        lastTimeUtc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture));
                    lastSentPrice = last;
                    lastSentQuoteUtc = now;
                }
                if (quote.Length == 0 && bars.Count == 0)
                {
                    Interlocked.Exchange(ref sending, 0);
                    return;
                }
                body = "{\"quotes\":[" + quote + "],\"bars\":[" + string.Join(",", bars) + "]}";
            }
            Task.Run(() => Send(body, bars.Count));
        }

        private async Task Send(string body, int barCount)
        {
            try
            {
                string keyFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".convexa", "ninjatrader_anchor_key.txt");
                if (!File.Exists(keyFile))
                {
                    Log("key file missing (" + keyFile + "); nothing sent.");
                    return;
                }
                using (var request = new HttpRequestMessage(HttpMethod.Post, Url))
                {
                    request.Headers.Add("X-Convexa-Key", File.ReadAllText(keyFile).Trim());
                    request.Content = new StringContent(body, Encoding.UTF8, "application/json");
                    using (HttpResponseMessage response = await Http.SendAsync(request).ConfigureAwait(false))
                    {
                        string answer = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        if (response.IsSuccessStatusCode)
                        {
                            lock (gate)
                                pendingBars.RemoveRange(0, Math.Min(barCount, pendingBars.Count));
                            if (barCount > 0 || answer.Contains("\"skipped\":[\""))
                                Log(contract + " -> HTTP " + (int)response.StatusCode + " " + answer);
                        }
                        else
                            Log(contract + " -> HTTP " + (int)response.StatusCode + " " + answer);
                    }
                }
            }
            catch (Exception error)
            {
                Log("send failed for " + contract + ": " + error.GetType().Name + " " + error.Message);
            }
            finally
            {
                Interlocked.Exchange(ref sending, 0);
            }
        }

        // At most one line a minute in NinjaScript Output, so a long outage does not flood it.
        private void Log(string text)
        {
            DateTime now = DateTime.UtcNow;
            if ((now - lastLogUtc).TotalSeconds < 60)
                return;
            lastLogUtc = now;
            Print("Convexa Feed: " + text);
        }

        [Display(Name = "Convexa URL", Order = 1, GroupName = "Convexa")]
        public string Url { get; set; }
    }
}
