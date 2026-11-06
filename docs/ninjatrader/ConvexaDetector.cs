// Convexa Detector — NinjaTrader 8 indicator (Lester 2026-11-06).
//
// Reads NinjaTrader's Order Flow+ VOLUMETRIC data of the chart's instrument as a hidden 1-minute series (AddVolumetric: the chart itself stays
// a normal candle chart, nothing has to be read there; Lester 2026-11-06) plus the live tape, and finds:
//   - STACKED IMBALANCES: each closed minute's footprint (aggressive buys and sells per price, from Volumetric) is checked diagonally: buys at
//     a price >= Ratio x sells one tick below (and the mirror), with at least MinImbalanceVolume; StackCount or more in a row = one event.
//     300 % / 3 levels is the common convention (no rigorous public backtest exists: Convexa measures it on our own data).
//   - ABSORPTION IN THE BAR (Volumetric, history too): at the minute's low, sells >= BarAbsorptionVolume and the minute closes in its upper
//     half (buyers absorbed; side "buy"), or the mirror at the high.
//   - ABSORPTION: aggressive selling into the same best bid adds up to AbsorptionVolume while the bid keeps refilling and price never trades
//     below it for at least AbsorptionHoldSeconds (buyers absorb: side "buy"); the mirror on the ask (side "sell"). Level 1 is enough.
//   - BIG TRADES: one aggressive ORDER in the top (100 - BigTradePercentile) % of the day's orders so far (Lester 2026-11-06: a fixed size either
//     floods a busy day or hides a quiet one), never under the instrument's floor; a fixed BigTradeVolume > 0 replaces it. Every order of
//     OrderLogMin contracts or more is written to Documents\Convexa\nt_export\orders_<future>_<yyyyMMdd>.csv to measure the real distribution. CME reports a sweeping order as many 1-3 lot prints in the same instant (Lester's
//     Time & Sales, 2026-11-06), so consecutive prints of the same side less than 5 ms apart are added up into one order first (as NinjaTrader's
//     and Bookmap's bubbles do); the order's price is its last print.
// The aggressor side comes from the best bid / ask: at or above the ask = buy, at or below the bid = sell, in between = the tick rule.
// Live events are drawn on this chart and sent to Convexa (POST /api/v1/feeds/futures-events), which draws them on SPX / SPY (ES), NDX / QQQ
// (NQ) and IWM (RTY). Only the events leave NinjaTrader, never the tape.
// EXPORT (ExportDays > 0): every minute of the last ExportDays days loaded on the chart is written, footprint included, to
// Documents\Convexa\nt_export\<future>_<yyyyMMdd>.csv (one file per ET day), for Convexa's study of what happens after each pattern near the
// gamma bands. The chart must have at least that many days loaded (Data Series > Days to load). Requires Order Flow+.
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

        private int barAbsorptionVolume;
        // The day's aggregated order sizes (index = contracts, capped) for the adaptive big-trade threshold, and the order log.
        private readonly long[] sizeCounts = new long[5001];
        private long ordersToday;
        private DateTime sizesDay = DateTime.MinValue;
        private StreamWriter orderLog;
        private DateTime orderLogDay = DateTime.MinValue;
        private int printsSeen, printsWithMs;
        private bool msReported;
        private static readonly TimeZoneInfo Eastern = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
        // Export: one open file per ET day.
        private StreamWriter exportWriter;
        private DateTime exportDay = DateTime.MinValue;

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
                Calculate = Calculate.OnBarClose; // the tape comes through OnMarketData whatever this is
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
                BarAbsorptionVolume = 0;
                ExportDays = 0;
                BigTradePercentile = 99.0;
                OrderLogMin = 20;
            }
            else if (State == State.Configure)
            {
                // The Order Flow+ footprint as a hidden series: 1-minute bars, bid/ask delta, one tick per level.
                AddVolumetric(Instrument.FullName, BarsPeriodType.Minute, 1, VolumetricDeltaType.BidAsk, 1);
            }
            else if (State == State.DataLoaded)
            {
                master = Instrument.MasterInstrument.Name;
                contract = Instrument.FullName;
                tick = Instrument.MasterInstrument.TickSize;
                int[] auto = AutoSizes(master);
                imbalanceVolume = MinImbalanceVolume > 0 ? MinImbalanceVolume : auto[0];
                absorptionVolume = AbsorptionVolume > 0 ? AbsorptionVolume : auto[1];
                bigTradeVolume = auto[2]; // the floor of the adaptive threshold (BigTradeThreshold)
                barAbsorptionVolume = BarAbsorptionVolume > 0 ? BarAbsorptionVolume : auto[3];
            }
            else if (State == State.Realtime)
            {
                CloseExport();
                if (timer == null)
                    timer = new Timer(_ => Flush(), null, 1000, 1000);
            }
            else if (State == State.Terminated)
            {
                CloseExport();
                if (orderLog != null)
                {
                    orderLog.Dispose();
                    orderLog = null;
                }
                if (timer != null)
                {
                    timer.Dispose();
                    timer = null;
                }
            }
        }

        // [min volume per imbalanced level per minute, live absorption volume at one price, big trade, bar absorption at the extreme]:
        // starting points to be measured, not rules.
        private static int[] AutoSizes(string name)
        {
            switch (name)
            {
                case "NQ": return new[] { 15, 120, 20, 60 };
                case "RTY": return new[] { 10, 80, 10, 40 };
                case "MES": return new[] { 60, 600, 100, 300 };
                case "MNQ": return new[] { 30, 240, 40, 120 };
                case "M2K": return new[] { 20, 160, 20, 80 };
                default: return new[] { 30, 300, 50, 150 }; // ES; the third number is the big-trade FLOOR
            }
        }

        // Each closed 1-minute Volumetric bar: stacked imbalances, absorption in the bar, and the export line (history included).
        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 1 || CurrentBars[1] < 0)
                return;
            var volumetric = BarsArray[1].BarsType as NinjaTrader.NinjaScript.BarsTypes.VolumetricBarsType;
            if (volumetric == null)
                return;
            var bar = volumetric.Volumes[CurrentBars[1]];
            double low = Lows[1][0], high = Highs[1][0];
            long lowKey = (long)Math.Round(low / tick), highKey = (long)Math.Round(high / tick);
            if (highKey < lowKey || highKey - lowKey > 2000)
                return;
            // price (ticks) -> [aggressive buys (at the ask), aggressive sells (at the bid)]
            var footprint = new Dictionary<long, long[]>();
            for (long k = lowKey; k <= highKey; k++)
            {
                double price = k * tick;
                long buys = bar.GetAskVolumeForPrice(price), sells = bar.GetBidVolumeForPrice(price);
                if (buys > 0 || sells > 0)
                    footprint[k] = new[] { buys, sells };
            }
            DateTime closeUtc = TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(Times[1][0], DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo);
            DateTime startUtc = closeUtc.AddMinutes(-1);
            bool live = State == State.Realtime;
            CheckImbalances(footprint, startUtc, live);
            CheckBarAbsorption(footprint, lowKey, highKey, Closes[1][0], startUtc, live);
            if (ExportDays > 0 && !live && (DateTime.UtcNow - startUtc).TotalDays <= ExportDays)
            {
                double poc;
                bar.GetMaximumVolume(null, out poc);
                Export(startUtc, bar.TotalVolume, bar.BarDelta, bar.MaxSeenDelta, bar.MinSeenDelta, poc, footprint);
            }
        }

        private void CheckBarAbsorption(Dictionary<long, long[]> footprint, long lowKey, long highKey, double close, DateTime startUtc, bool live)
        {
            if (highKey - lowKey < 2)
                return;
            double middle = (lowKey + highKey) * tick / 2;
            long[] atLow, atHigh;
            if (footprint.TryGetValue(lowKey, out atLow) && atLow[1] >= barAbsorptionVolume && close > middle)
                Emit(startUtc.AddMinutes(1), "absorption", "buy", lowKey * tick, lowKey * tick, lowKey * tick, atLow[1], "bar: sells " + atLow[1] + " at the low", live);
            if (footprint.TryGetValue(highKey, out atHigh) && atHigh[0] >= barAbsorptionVolume && close < middle)
                Emit(startUtc.AddMinutes(1), "absorption", "sell", highKey * tick, highKey * tick, highKey * tick, atHigh[0], "bar: buys " + atHigh[0] + " at the high", live);
        }

        private void Export(DateTime startUtc, long totalVolume, long delta, long maxDelta, long minDelta, double poc, Dictionary<long, long[]> footprint)
        {
            DateTime dayEt = TimeZoneInfo.ConvertTimeFromUtc(startUtc, Eastern).Date;
            if (exportWriter == null || dayEt != exportDay)
            {
                CloseExport();
                string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Convexa", "nt_export");
                Directory.CreateDirectory(folder);
                string file = Path.Combine(folder, master + "_" + dayEt.ToString("yyyyMMdd", CultureInfo.InvariantCulture) + ".csv");
                exportWriter = new StreamWriter(file, false, Encoding.UTF8);
                exportWriter.WriteLine("minute_utc,contract,open,high,low,close,volume,delta,max_delta,min_delta,poc,footprint");
                exportDay = dayEt;
            }
            string cells = string.Join("|", footprint.OrderBy(f => f.Key).Select(f => (f.Key * tick).ToString("0.00", CultureInfo.InvariantCulture) + ":" + f.Value[1] + ":" + f.Value[0]));
            exportWriter.WriteLine(string.Format(CultureInfo.InvariantCulture, "{0},{1},{2},{3},{4},{5},{6},{7},{8},{9},{10},{11}",
                startUtc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture), contract, Opens[1][0], Highs[1][0], Lows[1][0], Closes[1][0],
                totalVolume, delta, maxDelta, minDelta, poc, cells));
        }

        private void CloseExport()
        {
            if (exportWriter != null)
            {
                exportWriter.Flush();
                exportWriter.Dispose();
                exportWriter = null;
                Print("Convexa Detector: export written up to " + exportDay.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + " (Documents\\Convexa\\nt_export)");
            }
        }

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

            // Whether this feed stamps prints with milliseconds: the grouping of one order's prints relies on it (said once in NinjaScript Output).
            if (!msReported)
            {
                printsSeen++;
                if (e.Time.Millisecond != 0)
                    printsWithMs++;
                if (printsSeen >= 300)
                {
                    msReported = true;
                    Print("Convexa Detector: " + printsWithMs + " of " + printsSeen + " prints carry milliseconds" + (printsWithMs == 0 ? " (whole seconds only: orders are grouped by side and price direction within the second)" : ""));
                }
            }
            double price = e.Price;
            bool buy = ask > 0 && price >= ask ? true : bid > 0 && price <= bid ? false : price > lastPrice ? true : price < lastPrice ? false : lastWasBuy;
            lastWasBuy = buy;
            lastPrice = price;
            long volume = e.Volume;

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
            DateTime dayEt = TimeZoneInfo.ConvertTimeFromUtc(orderLastUtc, Eastern).Date;
            if (dayEt != sizesDay)
            {
                Array.Clear(sizeCounts, 0, sizeCounts.Length);
                ordersToday = 0;
                sizesDay = dayEt;
            }
            long threshold = BigTradeThreshold();
            sizeCounts[Math.Min(sizeCounts.Length - 1, orderVolume)]++;
            ordersToday++;
            if (orderVolume >= OrderLogMin)
                LogOrder(dayEt);
            if (orderVolume >= threshold)
                Emit(orderLastUtc, "big_trade", orderBuy ? "buy" : "sell", orderPrice, orderPrice, orderPrice, orderVolume,
                    (orderBuy ? "B " : "S ") + orderVolume + " (>= " + threshold + ")");
            orderVolume = 0;
        }

        // A fixed BigTradeVolume when set; else the day's BigTradePercentile of order sizes (after 300 orders), never under the floor.
        private long BigTradeThreshold()
        {
            if (BigTradeVolume > 0)
                return BigTradeVolume;
            if (ordersToday < 300)
                return bigTradeVolume;
            long rank = (long)Math.Ceiling(ordersToday * BigTradePercentile / 100.0);
            long seen = 0;
            for (int size = 1; size < sizeCounts.Length; size++)
            {
                seen += sizeCounts[size];
                if (seen >= rank)
                    return Math.Max(bigTradeVolume, size);
            }
            return Math.Max(bigTradeVolume, sizeCounts.Length - 1);
        }

        private void LogOrder(DateTime dayEt)
        {
            try
            {
                if (orderLog == null || dayEt != orderLogDay)
                {
                    if (orderLog != null)
                        orderLog.Dispose();
                    string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Convexa", "nt_export");
                    Directory.CreateDirectory(folder);
                    string file = Path.Combine(folder, "orders_" + master + "_" + dayEt.ToString("yyyyMMdd", CultureInfo.InvariantCulture) + ".csv");
                    bool fresh = !File.Exists(file);
                    orderLog = new StreamWriter(file, true, Encoding.UTF8) { AutoFlush = true };
                    if (fresh)
                        orderLog.WriteLine("time_utc,contract,side,price,contracts");
                    orderLogDay = dayEt;
                }
                orderLog.WriteLine(string.Format(CultureInfo.InvariantCulture, "{0},{1},{2},{3},{4}",
                    orderLastUtc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture), contract, orderBuy ? "buy" : "sell", orderPrice, orderVolume));
            }
            catch (Exception error)
            {
                Log("order log failed: " + error.Message);
            }
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

        private void CheckImbalances(Dictionary<long, long[]> footprint, DateTime minute, bool live)
        {
            if (footprint.Count < 2)
                return;
            List<long> prices = footprint.Keys.OrderBy(k => k).ToList();
            FindStacks(footprint, prices, minute, true, live);
            FindStacks(footprint, prices, minute, false, live);
        }

        private void FindStacks(Dictionary<long, long[]> footprint, List<long> prices, DateTime minute, bool buySide, bool live)
        {
            long runStart = -1, runEnd = -1, runVolume = 0;
            double runRatio = 0;
            for (long p = prices[0]; p <= prices[prices.Count - 1]; p++)
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
                    CloseRun(runStart, runEnd, runVolume, runRatio, minute, buySide, live);
                    runStart = runEnd = p;
                    runVolume = mine;
                    runRatio = mine / (double)Math.Max(theirs, 1);
                }
                else
                {
                    CloseRun(runStart, runEnd, runVolume, runRatio, minute, buySide, live);
                    runStart = runEnd = -1;
                    runVolume = 0;
                }
            }
            CloseRun(runStart, runEnd, runVolume, runRatio, minute, buySide, live);
        }

        private void CloseRun(long start, long end, long volume, double ratio, DateTime minute, bool buySide, bool live)
        {
            if (start < 0 || end - start + 1 < StackCount)
                return;
            double low = start * tick, high = end * tick;
            Emit(minute.AddMinutes(1), "stacked_imbalance", buySide ? "buy" : "sell", (low + high) / 2, low, high, volume,
                string.Format(CultureInfo.InvariantCulture, "{0} levels, min {1:0}%", end - start + 1, ratio * 100), live);
        }

        // `live` events go to Convexa; history (Volumetric bars of past minutes) is only drawn for today and written by the export.
        private void Emit(DateTime utc, string type, string side, double price, double low, double high, long volume, string detail, bool live = true)
        {
            bool today = TimeZoneInfo.ConvertTimeFromUtc(utc, Eastern).Date == TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, Eastern).Date;
            if (!live && !today)
                return;
            string json = string.Format(
                CultureInfo.InvariantCulture,
                "{{\"symbol\":\"{0}\",\"contract\":\"{1}\",\"time\":\"{2}\",\"type\":\"{3}\",\"side\":\"{4}\",\"price\":{5},\"price_low\":{6},\"price_high\":{7},\"volume\":{8},\"detail\":\"{9}\"}}",
                master, contract, utc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture), type, side, price, low, high, volume, detail);
            if (live)
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
            // Anchored by time, not by bars ago: events come from the hidden Volumetric series and from the tape, not from the chart's own bars.
            DateTime at = TimeZoneInfo.ConvertTimeFromUtc(utc, Core.Globals.GeneralOptions.TimeZoneInfo);
            if (type == "absorption")
                Draw.Diamond(this, tag, false, at, price, brush);
            else if (type == "stacked_imbalance")
            {
                if (side == "buy")
                    Draw.TriangleUp(this, tag, false, at, low - 2 * tick, brush);
                else
                    Draw.TriangleDown(this, tag, false, at, high + 2 * tick, brush);
            }
            else
                Draw.Dot(this, tag, false, at, price, side == "buy" ? Brushes.DeepSkyBlue : Brushes.Orange);
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
        [Display(Name = "Big trade fijo (0 = percentil del dia)", Order = 6, GroupName = "Convexa")]
        public int BigTradeVolume { get; set; }

        [Range(90.0, 99.99)]
        [Display(Name = "Big trade: percentil del dia (si Big trade = 0)", Order = 11, GroupName = "Convexa")]
        public double BigTradePercentile { get; set; }

        [Range(1, 100000)]
        [Display(Name = "Registrar ordenes desde (contratos)", Order = 12, GroupName = "Convexa")]
        public int OrderLogMin { get; set; }

        [Range(0, 1000000)]
        [Display(Name = "Bar absorption at the extreme (0 = auto)", Order = 9, GroupName = "Convexa")]
        public int BarAbsorptionVolume { get; set; }

        [Range(0, 365)]
        [Display(Name = "Exportar dias (0 = no)", Order = 10, GroupName = "Convexa")]
        public int ExportDays { get; set; }

        [Display(Name = "Draw on this chart", Order = 7, GroupName = "Convexa")]
        public bool DrawOnChart { get; set; }

        [Display(Name = "Convexa URL", Order = 8, GroupName = "Convexa")]
        public string Url { get; set; }
    }
}
