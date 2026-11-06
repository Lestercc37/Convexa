// Convexa Detector — NinjaTrader 8 indicator (Lester 2026-11-06).
//
// Watches the live tape of the chart's instrument (ES / NQ / RTY, or the micros) and finds:
//   - STACKED IMBALANCES: when each minute closes, its footprint (aggressive buys and sells per price) is checked diagonally: buys at a price
//     >= Ratio x sells one tick below (and the mirror), with at least MinImbalanceVolume; StackCount or more such prices in a row = one event.
//     300 % / 3 levels is the common convention (no rigorous public backtest exists: Convexa measures it on our own data).
//   - ABSORPTION: aggressive selling into the same best bid adds up to AbsorptionVolume while the bid keeps refilling and price never trades
//     below it for at least AbsorptionHoldSeconds (buyers absorb: side "buy"); the mirror on the ask (side "sell"). Level 1 is enough.
//   - BIG TRADES: one aggressive ORDER of BigTradeVolume or more. CME reports a sweeping order as many 1-3 lot prints in the same instant (Lester's
//     Time & Sales, 2026-11-06), so consecutive prints of the same side less than 5 ms apart are added up into one order first (as NinjaTrader's
//     and Bookmap's bubbles do); the order's price is its last print.
// The aggressor side comes from the best bid / ask: at or above the ask = buy, at or below the bid = sell, in between = the tick rule.
// Each event is drawn on this chart and sent to Convexa (POST /api/v1/feeds/futures-events), which draws it on SPX / SPY (ES), NDX / QQQ (NQ)
// and IWM (RTY). Only the events leave NinjaTrader, never the tape. Live data only (it does not replay history).
// Sizes left at 0 take the instrument's default (see AutoSizes). Key: the anchor's (%USERPROFILE%\.convexa\ninjatrader_anchor_key.txt).
//
// Install: copy to Documents\NinjaTrader 8\bin\Custom\Indicators\, F5 in the NinjaScript Editor, add "Convexa Detector" to the ES and NQ
// charts (the full contracts: the default sizes are theirs).

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Media;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class ConvexaDetector : Indicator
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };

        private readonly object gate = new object();
        private readonly List<string> pending = new List<string>();
        private Timer timer;
        private int sending;
        private DateTime lastLogUtc = DateTime.MinValue;
        private string master, contract;
        private double tick;
        private int imbalanceVolume, absorptionVolume, bigTradeVolume;

        private double bid, ask, lastPrice;
        private long bidSize, askSize;
        private bool lastWasBuy = true;

        // The aggressive order being added up: consecutive same-side prints less than 5 ms apart.
        private long orderVolume;
        private bool orderBuy;
        private double orderPrice;
        private DateTime orderLastUtc = DateTime.MinValue;

        // The minute being built: price (in ticks) -> [aggressive buys, aggressive sells].
        private DateTime minuteUtc = DateTime.MinValue;
        private readonly Dictionary<long, long[]> footprint = new Dictionary<long, long[]>();

        // One absorption episode per side: the price being hit, volume, refills, start and last hit.
        private Episode bidEpisode, askEpisode;
        private int drawn;

        private class Episode
        {
            public double Price;
            public long Volume;
            public int Refills;
            public DateTime StartUtc, LastHitUtc;
            public long LastSize;
        }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "Convexa Detector";
                Description = "Absorption, stacked imbalances and big trades on the live tape; drawn here and sent to Convexa.";
                Calculate = Calculate.OnEachTick;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;
                Url = "https://dashboard.convexatrading.com/backend/api/v1/feeds/futures-events";
                Ratio = 3.0;
                StackCount = 3;
                MinImbalanceVolume = 0;
                AbsorptionVolume = 0;
                AbsorptionHoldSeconds = 10;
                BigTradeVolume = 0;
                DrawOnChart = true;
            }
            else if (State == State.DataLoaded)
            {
                master = Instrument.MasterInstrument.Name;
                contract = Instrument.FullName;
                tick = Instrument.MasterInstrument.TickSize;
                int[] auto = AutoSizes(master);
                imbalanceVolume = MinImbalanceVolume > 0 ? MinImbalanceVolume : auto[0];
                absorptionVolume = AbsorptionVolume > 0 ? AbsorptionVolume : auto[1];
                bigTradeVolume = BigTradeVolume > 0 ? BigTradeVolume : auto[2];
            }
            else if (State == State.Realtime)
            {
                if (timer == null)
                    timer = new Timer(_ => Flush(), null, 1000, 1000);
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

        // [min volume per imbalanced level per minute, absorption volume at one price, big trade]: starting points to be measured, not rules.
        private static int[] AutoSizes(string name)
        {
            switch (name)
            {
                case "NQ": return new[] { 15, 120, 50 };
                case "RTY": return new[] { 10, 80, 30 };
                case "MES": return new[] { 60, 600, 200 };
                case "MNQ": return new[] { 30, 240, 100 };
                case "M2K": return new[] { 20, 160, 60 };
                default: return new[] { 30, 300, 100 }; // ES
            }
        }

        protected override void OnBarUpdate() { }

        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (State != State.Realtime)
                return;
            DateTime utc = TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(e.Time, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo);
            if (e.MarketDataType == MarketDataType.Bid)
            {
                if (bidEpisode != null && e.Price == bidEpisode.Price && e.Volume > bidEpisode.LastSize)
                    bidEpisode.Refills++;
                if (bidEpisode != null && e.Price == bidEpisode.Price)
                    bidEpisode.LastSize = e.Volume;
                bid = e.Price;
                bidSize = e.Volume;
                CheckEpisodes(utc);
                return;
            }
            if (e.MarketDataType == MarketDataType.Ask)
            {
                if (askEpisode != null && e.Price == askEpisode.Price && e.Volume > askEpisode.LastSize)
                    askEpisode.Refills++;
                if (askEpisode != null && e.Price == askEpisode.Price)
                    askEpisode.LastSize = e.Volume;
                ask = e.Price;
                askSize = e.Volume;
                CheckEpisodes(utc);
                return;
            }
            if (e.MarketDataType != MarketDataType.Last || e.Volume <= 0)
                return;

            double price = e.Price;
            bool buy = ask > 0 && price >= ask ? true : bid > 0 && price <= bid ? false : price > lastPrice ? true : price < lastPrice ? false : lastWasBuy;
            lastWasBuy = buy;
            lastPrice = price;
            long volume = e.Volume;

            DateTime minute = new DateTime(utc.Year, utc.Month, utc.Day, utc.Hour, utc.Minute, 0, DateTimeKind.Utc);
            if (minute != minuteUtc)
            {
                if (minuteUtc != DateTime.MinValue)
                    CheckImbalances(minuteUtc);
                footprint.Clear();
                minuteUtc = minute;
            }
            long key = (long)Math.Round(price / tick);
            long[] cell;
            if (!footprint.TryGetValue(key, out cell))
            {
                cell = new long[2];
                footprint[key] = cell;
            }
            cell[buy ? 0 : 1] += volume;

            // A new order: the other side, more than 5 ms later, or a price going back (a sweep only moves one way: up for buys, down for sells;
            // this also keeps the grouping honest if the feed's times only carry whole seconds).
            bool goesBack = orderBuy ? price < orderPrice : price > orderPrice;
            if (orderVolume > 0 && (buy != orderBuy || goesBack || (utc - orderLastUtc).TotalMilliseconds > 5))
                CloseOrder();
            orderBuy = buy;
            orderVolume += volume;
            orderPrice = price;
            orderLastUtc = utc;

            // Absorption: selling hits the bid (buyers absorb) / buying lifts the ask (sellers absorb).
            if (!buy)
            {
                if (bidEpisode == null || bidEpisode.Price != price)
                    bidEpisode = new Episode { Price = price, StartUtc = utc, LastSize = bidSize };
                bidEpisode.Volume += volume;
                bidEpisode.LastHitUtc = utc;
            }
            else
            {
                if (askEpisode == null || askEpisode.Price != price)
                    askEpisode = new Episode { Price = price, StartUtc = utc, LastSize = askSize };
                askEpisode.Volume += volume;
                askEpisode.LastHitUtc = utc;
            }
            // A trade through the absorbed price breaks it: that episode failed, nothing is reported.
            if (bidEpisode != null && price < bidEpisode.Price)
                bidEpisode = null;
            if (askEpisode != null && price > askEpisode.Price)
                askEpisode = null;
            CheckEpisodes(utc);
        }

        private void CloseOrder()
        {
            if (orderVolume >= bigTradeVolume)
                Emit(orderLastUtc, "big_trade", orderBuy ? "buy" : "sell", orderPrice, orderPrice, orderPrice, orderVolume, (orderBuy ? "B " : "S ") + orderVolume);
            orderVolume = 0;
        }

        // An episode is reported once it held long enough with enough volume and price left it in the absorbers' direction (or it went quiet
        // for 60 s still holding). A bid that moves away upward by 2 ticks = held; the bid dropping below it = broken (dropped).
        private void CheckEpisodes(DateTime utc)
        {
            if (bidEpisode != null)
            {
                bool movedUp = bid >= bidEpisode.Price + 2 * tick;
                bool broken = bid > 0 && bid < bidEpisode.Price;
                bool quiet = (utc - bidEpisode.LastHitUtc).TotalSeconds >= 60;
                if (broken)
                    bidEpisode = null;
                else if (movedUp || quiet)
                {
                    Report(bidEpisode, "buy", utc);
                    bidEpisode = null;
                }
            }
            if (askEpisode != null)
            {
                bool movedDown = ask > 0 && ask <= askEpisode.Price - 2 * tick;
                bool broken = ask > askEpisode.Price;
                bool quiet = (utc - askEpisode.LastHitUtc).TotalSeconds >= 60;
                if (broken)
                    askEpisode = null;
                else if (movedDown || quiet)
                {
                    Report(askEpisode, "sell", utc);
                    askEpisode = null;
                }
            }
        }

        private void Report(Episode episode, string side, DateTime utc)
        {
            double held = (episode.LastHitUtc - episode.StartUtc).TotalSeconds;
            if (episode.Volume < absorptionVolume || held < AbsorptionHoldSeconds)
                return;
            Emit(utc, "absorption", side, episode.Price, episode.Price, episode.Price, episode.Volume,
                string.Format(CultureInfo.InvariantCulture, "refills {0}, held {1:0} s", episode.Refills, held));
        }

        private void CheckImbalances(DateTime minute)
        {
            if (footprint.Count < 2)
                return;
            List<long> prices = footprint.Keys.OrderBy(k => k).ToList();
            FindStacks(prices, minute, true);
            FindStacks(prices, minute, false);
        }

        private void FindStacks(List<long> prices, DateTime minute, bool buySide)
        {
            long runStart = -1, runEnd = -1, runVolume = 0;
            double runRatio = 0;
            foreach (long p in Enumerable.Range(0, (int)(prices[prices.Count - 1] - prices[0] + 1)).Select(i => prices[0] + i))
            {
                long[] here, other;
                footprint.TryGetValue(p, out here);
                footprint.TryGetValue(buySide ? p - 1 : p + 1, out other);
                long mine = here == null ? 0 : here[buySide ? 0 : 1];
                long theirs = other == null ? 0 : other[buySide ? 1 : 0];
                bool imbalanced = mine >= imbalanceVolume && mine >= Ratio * Math.Max(theirs, 1);
                if (imbalanced && runEnd == p - 1)
                {
                    runEnd = p;
                    runVolume += mine;
                    runRatio = Math.Min(runRatio, mine / (double)Math.Max(theirs, 1));
                }
                else if (imbalanced)
                {
                    CloseRun(runStart, runEnd, runVolume, runRatio, minute, buySide);
                    runStart = runEnd = p;
                    runVolume = mine;
                    runRatio = mine / (double)Math.Max(theirs, 1);
                }
                else
                {
                    CloseRun(runStart, runEnd, runVolume, runRatio, minute, buySide);
                    runStart = runEnd = -1;
                    runVolume = 0;
                }
            }
            CloseRun(runStart, runEnd, runVolume, runRatio, minute, buySide);
        }

        private void CloseRun(long start, long end, long volume, double ratio, DateTime minute, bool buySide)
        {
            if (start < 0 || end - start + 1 < StackCount)
                return;
            double low = start * tick, high = end * tick;
            Emit(minute.AddMinutes(1), "stacked_imbalance", buySide ? "buy" : "sell", (low + high) / 2, low, high, volume,
                string.Format(CultureInfo.InvariantCulture, "{0} levels, min {1:0}%", end - start + 1, ratio * 100));
        }

        private void Emit(DateTime utc, string type, string side, double price, double low, double high, long volume, string detail)
        {
            string json = string.Format(
                CultureInfo.InvariantCulture,
                "{{\"symbol\":\"{0}\",\"contract\":\"{1}\",\"time\":\"{2}\",\"type\":\"{3}\",\"side\":\"{4}\",\"price\":{5},\"price_low\":{6},\"price_high\":{7},\"volume\":{8},\"detail\":\"{9}\"}}",
                master, contract, utc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture), type, side, price, low, high, volume, detail);
            lock (gate)
            {
                pending.Add(json);
                if (pending.Count > 500)
                    pending.RemoveRange(0, pending.Count - 500);
            }
            if (!DrawOnChart)
                return;
            string tag = "cvx" + (drawn++).ToString(CultureInfo.InvariantCulture);
            Brush brush = side == "buy" ? Brushes.LimeGreen : Brushes.Red;
            if (type == "absorption")
                Draw.Diamond(this, tag, false, 0, price, brush);
            else if (type == "stacked_imbalance")
            {
                if (side == "buy")
                    Draw.TriangleUp(this, tag, false, 0, low - 2 * tick, brush);
                else
                    Draw.TriangleDown(this, tag, false, 0, high + 2 * tick, brush);
            }
            else
                Draw.Dot(this, tag, false, 0, price, side == "buy" ? Brushes.DeepSkyBlue : Brushes.Orange);
        }

        private void Flush()
        {
            if (Interlocked.CompareExchange(ref sending, 1, 0) != 0)
                return;
            List<string> batch;
            lock (gate)
            {
                batch = pending.GetRange(0, Math.Min(200, pending.Count));
            }
            if (batch.Count == 0)
            {
                Interlocked.Exchange(ref sending, 0);
                return;
            }
            Task.Run(() => Send("{\"events\":[" + string.Join(",", batch) + "]}", batch.Count));
        }

        private async Task Send(string body, int count)
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
                                pending.RemoveRange(0, Math.Min(count, pending.Count));
                        }
                        Log(contract + " -> HTTP " + (int)response.StatusCode + " " + answer);
                    }
                }
            }
            catch (Exception error)
            {
                Log("send failed: " + error.GetType().Name + " " + error.Message);
            }
            finally
            {
                Interlocked.Exchange(ref sending, 0);
            }
        }

        private void Log(string text)
        {
            DateTime now = DateTime.UtcNow;
            if ((now - lastLogUtc).TotalSeconds < 60)
                return;
            lastLogUtc = now;
            Print("Convexa Detector: " + text);
        }

        [Range(1.0, 20.0)]
        [Display(Name = "Imbalance ratio (3 = 300 %)", Order = 1, GroupName = "Convexa")]
        public double Ratio { get; set; }

        [Range(2, 20)]
        [Display(Name = "Stacked levels", Order = 2, GroupName = "Convexa")]
        public int StackCount { get; set; }

        [Range(0, 100000)]
        [Display(Name = "Min volume per imbalanced level (0 = auto)", Order = 3, GroupName = "Convexa")]
        public int MinImbalanceVolume { get; set; }

        [Range(0, 1000000)]
        [Display(Name = "Absorption volume at one price (0 = auto)", Order = 4, GroupName = "Convexa")]
        public int AbsorptionVolume { get; set; }

        [Range(1, 600)]
        [Display(Name = "Absorption must hold (s)", Order = 5, GroupName = "Convexa")]
        public int AbsorptionHoldSeconds { get; set; }

        [Range(0, 1000000)]
        [Display(Name = "Big trade (0 = auto)", Order = 6, GroupName = "Convexa")]
        public int BigTradeVolume { get; set; }

        [Display(Name = "Draw on this chart", Order = 7, GroupName = "Convexa")]
        public bool DrawOnChart { get; set; }

        [Display(Name = "Convexa URL", Order = 8, GroupName = "Convexa")]
        public string Url { get; set; }
    }
}
