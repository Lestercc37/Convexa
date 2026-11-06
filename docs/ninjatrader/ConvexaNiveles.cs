// Convexa Niveles — NinjaTrader 8 indicator (Lester 2026-11-06).
//
// Draws Convexa's levels and heatmap on an ES / MES, NQ / MNQ or RTY / M2K chart, already in futures points:
//   - the heatmap of the tab ("View") and figure ("Metric") picked in the properties, behind the candles, coloured like Convexa (each column
//     against its own strongest strikes), the latest column stretched to the right edge in a softer tone;
//   - the lines: Gamma Flip, Call Wall, Put Wall, ABS, Max Pain, anchored VWAP, expected move and ATR bands, each with its name.
// Style "Presion" (docs/diseno-heatmap-presion-2026-10-29.txt, Lester 2026-11-06) draws instead a Prussian-blue background of faint gamma tiles,
// the fixed bands (Call Wall green, Put Wall red, ABS deep blue, Flip amber) and, minute by minute, only the 3 strikes above and the 3 below
// price with the most options-flow PRESSURE (signed 0DTE delta flow decaying by half every 15 min, at or above the day's 90th percentile):
// green bullish, red bearish, deeper = stronger, never white. A level price crosses and stays beyond for 5 minutes is cut (a gap) until
// fresh pressure rebuilds it. The pressure is read every 10 s (only the new minutes after the first read).
// It reads GET /api/v1/feeds/nt/{future} every 2 s (the first read brings the day, the next ones only the new columns; a full read again every
// 5 minutes). ES / NQ use Convexa's live basis from 'Convexa Feed' (else the 09:31 anchor); RTY needs 'Convexa Feed' on an RTY chart.
// Nothing from NinjaTrader is sent: this indicator only READS Convexa. The key is the anchor's (%USERPROFILE%\.convexa\ninjatrader_anchor_key.txt).
//
// Install: copy to Documents\NinjaTrader 8\bin\Custom\Indicators\, F5 in the NinjaScript Editor, add "Convexa Niveles" to the chart.

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using NinjaTrader.Data;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
#endregion

// Outside the NinjaScript namespace, as NinjaTrader recommends for enums used as properties (its generated code references them).
public enum ConvexaView { Dte0Volumen, Dte0OI, Dte0a2, Dte1, Dte2, Dias90 }
public enum ConvexaMetric { gamma, absolute_gamma, delta, vanna, charm, call_gamma, put_gamma, total_gamma }
public enum ConvexaStyle { Clasico, Presion }
public enum ConvexaFlowExpirations { Dte0, Dte0a2, Todas }

namespace NinjaTrader.NinjaScript.Indicators
{

    public class ConvexaNiveles : Indicator
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        private const int RampSteps = 32;
        private const double FaintShare = 0.06;

        private readonly object gate = new object();
        private double[] strikes = new double[0];
        private readonly List<KeyValuePair<long, double[]>> columns = new List<KeyValuePair<long, double[]>>();
        private readonly List<double> columnScales = new List<double>();
        private readonly Dictionary<string, double> levels = new Dictionary<string, double>();
        private string basisText = "";
        private string requestKey = "";
        private DateTime lastFullUtc = DateTime.MinValue;
        private DateTime lastLogUtc = DateTime.MinValue;
        private Timer timer;
        private int polling;
        private string future;

        // Pressure (style Presion): the flow source's strikes in futures points and one row per minute (epoch -> pressure per strike).
        private double[] pressureStrikes = new double[0];
        private readonly SortedDictionary<long, double[]> pressureRows = new SortedDictionary<long, double[]>();
        private double pressureP90, pressureP99;
        private DateTime lastPressureUtc = DateTime.MinValue;
        private string pressureKey = "";
        private SharpDX.Direct2D1.SolidColorBrush[] pressureUp, pressureDown, blueTiles;
        private SharpDX.Direct2D1.SolidColorBrush bandCall, bandPut, bandAbs, bandFlip;

        private SharpDX.Direct2D1.SolidColorBrush[] positive, negative, projectedPositive, projectedNegative;
        private readonly Dictionary<string, SharpDX.Direct2D1.SolidColorBrush> levelBrushes = new Dictionary<string, SharpDX.Direct2D1.SolidColorBrush>();
        private SharpDX.DirectWrite.TextFormat labelFormat;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "Convexa Niveles";
                Description = "Convexa's levels and heatmap (any tab and figure) in futures points, read every 2 s.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;
                Url = "https://dashboard.convexatrading.com/backend/api/v1/feeds/nt/";
                View = ConvexaView.Dte0Volumen;
                Metric = ConvexaMetric.gamma;
                ShowHeatmap = true;
                ShowLevels = true;
                Style = ConvexaStyle.Clasico;
                FlowExpirations = ConvexaFlowExpirations.Dte0;
                PressureLevelsPerSide = 3;
                Opacity = 60;
            }
            else if (State == State.DataLoaded)
            {
                future = Instrument.MasterInstrument.Name;
            }
            else if (State == State.Realtime)
            {
                if (timer == null)
                    timer = new Timer(_ => Poll(), null, 500, 2000);
            }
            else if (State == State.Terminated)
            {
                if (timer != null)
                {
                    timer.Dispose();
                    timer = null;
                }
                DisposeBrushes();
            }
        }

        protected override void OnBarUpdate() { }

        // The API's view names (Convexa's tabs: 0DTE Volumen, 0DTE OI, 0-2 DTE, 1DTE, 2DTE, 90 días).
        private static string ViewName(ConvexaView view)
        {
            switch (view)
            {
                case ConvexaView.Dte0OI: return "0dte";
                case ConvexaView.Dte0a2: return "tactical";
                case ConvexaView.Dte1: return "1dte";
                case ConvexaView.Dte2: return "2dte";
                case ConvexaView.Dias90: return "90d";
                default: return "0dte_volume";
            }
        }

        private void Poll()
        {
            if (Interlocked.CompareExchange(ref polling, 1, 0) != 0)
                return;
            Task.Run(async () =>
            {
                try
                {
                    string key = ViewName(View) + "|" + Metric;
                    long since = 0;
                    bool full;
                    lock (gate)
                    {
                        full = key != requestKey || columns.Count == 0 || (DateTime.UtcNow - lastFullUtc).TotalMinutes >= 5;
                        if (!full)
                            since = columns[columns.Count - 1].Key;
                    }
                    string keyFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".convexa", "ninjatrader_anchor_key.txt");
                    if (!File.Exists(keyFile))
                    {
                        Log("key file missing (" + keyFile + ").");
                        return;
                    }
                    string url = Url + Uri.EscapeDataString(future) + "?view=" + ViewName(View) + "&metric=" + Metric + (full ? "" : "&since=" + since.ToString(CultureInfo.InvariantCulture)) + (ShowHeatmap ? "" : "&heatmap=false");
                    // The pressure changes by the minute: asked every 10 s, from its last minute on (that minute is rewritten while it is still running).
                    string pexp = FlowExpirations == ConvexaFlowExpirations.Dte0a2 ? "0-2dte" : FlowExpirations == ConvexaFlowExpirations.Todas ? "all" : "0dte";
                    bool pressureFull = false;
                    if (Style == ConvexaStyle.Presion && (DateTime.UtcNow - lastPressureUtc).TotalSeconds >= 10)
                    {
                        long psince = 0;
                        lock (gate)
                        {
                            pressureFull = pressureKey != pexp || pressureRows.Count == 0;
                            if (!pressureFull)
                                psince = pressureRows.Keys.Last();
                        }
                        url += "&pressure=true&pexp=" + pexp + (pressureFull ? "" : "&psince=" + psince.ToString(CultureInfo.InvariantCulture));
                        lastPressureUtc = DateTime.UtcNow;
                    }
                    using (var request = new HttpRequestMessage(HttpMethod.Get, url))
                    {
                        request.Headers.Add("X-Convexa-Key", File.ReadAllText(keyFile).Trim());
                        using (HttpResponseMessage response = await Http.SendAsync(request).ConfigureAwait(false))
                        {
                            string body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                            if (!response.IsSuccessStatusCode)
                            {
                                Log(future + " -> HTTP " + (int)response.StatusCode + " " + body);
                                return;
                            }
                            Apply(body, key, full, pexp, pressureFull);
                        }
                    }
                    ForceRefresh();
                }
                catch (Exception error)
                {
                    Log("read failed: " + error.GetType().Name + " " + error.Message);
                }
                finally
                {
                    Interlocked.Exchange(ref polling, 0);
                }
            });
        }

        private void Apply(string body, string key, bool full, string pexp, bool pressureFull)
        {
            var newLevels = new Dictionary<string, double>();
            double[] newPressureStrikes = null;
            var newPressureRows = new List<KeyValuePair<long, double[]>>();
            double[] newStrikes = null;
            var newColumns = new List<KeyValuePair<long, double[]>>();
            string basis = "";
            foreach (string raw in body.Split('\n'))
            {
                string line = raw.Trim();
                if (line.Length == 0)
                    continue;
                string[] parts = line.Split(' ');
                if (parts[0] == "level" && parts.Length >= 3)
                    newLevels[parts[1]] = double.Parse(parts[2], CultureInfo.InvariantCulture);
                else if (parts[0] == "basis" || parts[0] == "basis_ratio")
                    basis = line;
                else if (parts[0] == "strikes")
                    newStrikes = parts.Skip(1).Select(p => double.Parse(p, CultureInfo.InvariantCulture)).ToArray();
                else if (parts[0] == "pstrikes")
                    newPressureStrikes = parts.Skip(1).Select(p => double.Parse(p, CultureInfo.InvariantCulture)).ToArray();
                else if (parts[0] == "prow" && parts.Length >= 2)
                    newPressureRows.Add(new KeyValuePair<long, double[]>(long.Parse(parts[1], CultureInfo.InvariantCulture),
                        parts.Skip(2).Select(p => double.Parse(p, CultureInfo.InvariantCulture)).ToArray()));
                else if (parts[0] == "col" && parts.Length >= 2)
                {
                    long epoch = long.Parse(parts[1], CultureInfo.InvariantCulture);
                    double[] values = parts.Skip(2).Select(p => p == "_" ? double.NaN : double.Parse(p, CultureInfo.InvariantCulture)).ToArray();
                    newColumns.Add(new KeyValuePair<long, double[]>(epoch, values));
                }
            }
            lock (gate)
            {
                levels.Clear();
                foreach (var pair in newLevels)
                    levels[pair.Key] = pair.Value;
                basisText = basis;
                if (newPressureStrikes != null)
                    ApplyPressure(newPressureStrikes, newPressureRows, pexp, pressureFull);
                if (newStrikes == null)
                    return;
                bool sameStrikes = strikes.Length == newStrikes.Length && strikes.SequenceEqual(newStrikes);
                if (full || !sameStrikes)
                {
                    // A full read, or the strike list changed: start again from what arrived (the next poll is full if it was partial).
                    strikes = newStrikes;
                    columns.Clear();
                    columnScales.Clear();
                    requestKey = full ? key : "";
                    if (full)
                        lastFullUtc = DateTime.UtcNow;
                }
                foreach (var column in newColumns)
                {
                    double scale = P98(column.Value);
                    int at = columns.FindIndex(c => c.Key == column.Key);
                    if (at >= 0)
                    {
                        columns[at] = column;
                        columnScales[at] = scale;
                    }
                    else
                    {
                        columns.Add(column);
                        columnScales.Add(scale);
                    }
                }
            }
        }

        // Called under `gate`. A different strike list (the 0DTE chain widened, or a full read) starts the day again from what arrived.
        private void ApplyPressure(double[] newStrikes, List<KeyValuePair<long, double[]>> rows, string pexp, bool full)
        {
            if (full || !pressureStrikes.SequenceEqual(newStrikes))
            {
                pressureStrikes = newStrikes;
                pressureRows.Clear();
                pressureKey = full ? pexp : "";
            }
            foreach (var row in rows)
                if (row.Value.Length == pressureStrikes.Length)
                    pressureRows[row.Key] = row.Value;
            double[] magnitudes = pressureRows.Values.SelectMany(v => v).Where(v => v != 0).Select(Math.Abs).OrderBy(v => v).ToArray();
            pressureP90 = magnitudes.Length == 0 ? 0 : magnitudes[(int)Math.Floor(0.90 * (magnitudes.Length - 1))];
            pressureP99 = magnitudes.Length == 0 ? 0 : magnitudes[(int)Math.Floor(0.99 * (magnitudes.Length - 1))];
        }

        private static double P98(double[] values)
        {
            double[] magnitudes = values.Where(v => !double.IsNaN(v)).Select(Math.Abs).OrderBy(v => v).ToArray();
            if (magnitudes.Length == 0)
                return 0;
            return magnitudes[Math.Min(magnitudes.Length - 1, (int)Math.Floor(0.98 * magnitudes.Length))];
        }

        public override void OnRenderTargetChanged()
        {
            DisposeBrushes();
            if (RenderTarget == null)
                return;
            double alpha = Math.Max(0.05, Math.Min(1.0, Opacity / 100.0));
            positive = Ramp(PositiveStops(), alpha);
            negative = Ramp(NegativeStops(), alpha);
            projectedPositive = Ramp(PositiveStops(), alpha * 0.55);
            projectedNegative = Ramp(NegativeStops(), alpha * 0.55);
            levelBrushes["gamma_flip"] = Brush(250, 204, 21, 1);
            levelBrushes["call_wall"] = Brush(34, 197, 94, 1);
            levelBrushes["put_wall"] = Brush(239, 68, 68, 1);
            levelBrushes["abs"] = Brush(255, 255, 255, 1);
            levelBrushes["max_pain"] = Brush(148, 163, 184, 1);
            levelBrushes["avwap"] = Brush(249, 115, 22, 1);
            levelBrushes["em"] = Brush(167, 139, 250, 0.9);
            levelBrushes["atr"] = Brush(100, 116, 139, 0.9);
            levelBrushes["text"] = Brush(226, 232, 240, 1);
            // Style Presion: Prussian blue (#003153) tiles barely lighter where gamma is stronger; pressure from dark to deep, never white.
            blueTiles = new SharpDX.Direct2D1.SolidColorBrush[RampSteps];
            pressureUp = new SharpDX.Direct2D1.SolidColorBrush[RampSteps];
            pressureDown = new SharpDX.Direct2D1.SolidColorBrush[RampSteps];
            for (int i = 0; i < RampSteps; i++)
            {
                double t = (i + 1) / (double)RampSteps;
                blueTiles[i] = Brush(0, 49 + 22 * t, 83 + 37 * t, Math.Min(1.0, alpha + 0.2));
                pressureUp[i] = Brush(4 + 10 * t, 70 + 130 * t, 40 + 50 * t, Math.Min(1.0, 0.55 + 0.45 * t));
                pressureDown[i] = Brush(80 + 140 * t, 12 + 18 * t, 24 + 26 * t, Math.Min(1.0, 0.55 + 0.45 * t));
            }
            bandCall = Brush(34, 197, 94, 0.45);
            bandPut = Brush(239, 68, 68, 0.45);
            bandAbs = Brush(29, 78, 216, 0.6);
            bandFlip = Brush(243, 201, 105, 1);
            labelFormat = new SharpDX.DirectWrite.TextFormat(Core.Globals.DirectWriteFactory, "Segoe UI", 11f);
        }

        // Signed figures: green above zero, red below (Convexa's gamma ramp); unsigned ones: blue; vanna purple; charm orange.
        private double[][] PositiveStops()
        {
            switch (Metric)
            {
                case ConvexaMetric.absolute_gamma:
                case ConvexaMetric.total_gamma:
                    return new[] { new double[] { 12, 36, 80 }, new double[] { 59, 130, 246 }, new double[] { 255, 255, 255 } };
                case ConvexaMetric.vanna:
                    return new[] { new double[] { 46, 16, 101 }, new double[] { 168, 85, 247 }, new double[] { 255, 255, 255 } };
                case ConvexaMetric.charm:
                    return new[] { new double[] { 90, 45, 10 }, new double[] { 234, 140, 60 }, new double[] { 255, 255, 255 } };
                default:
                    return new[] { new double[] { 6, 60, 36 }, new double[] { 22, 180, 100 }, new double[] { 255, 255, 255 } };
            }
        }

        private double[][] NegativeStops()
        {
            if (Metric == ConvexaMetric.absolute_gamma || Metric == ConvexaMetric.total_gamma || Metric == ConvexaMetric.vanna || Metric == ConvexaMetric.charm)
                return PositiveStops();
            return new[] { new double[] { 80, 10, 24 }, new double[] { 225, 45, 65 }, new double[] { 255, 255, 255 } };
        }

        private SharpDX.Direct2D1.SolidColorBrush[] Ramp(double[][] stops, double alpha)
        {
            var brushes = new SharpDX.Direct2D1.SolidColorBrush[RampSteps];
            for (int i = 0; i < RampSteps; i++)
            {
                double share = (i + 1) / (double)RampSteps;
                double[] a = share < 0.6 ? stops[0] : stops[1];
                double[] b = share < 0.6 ? stops[1] : stops[2];
                double t = share < 0.6 ? share / 0.6 : (share - 0.6) / 0.4;
                brushes[i] = Brush(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, alpha);
            }
            return brushes;
        }

        private SharpDX.Direct2D1.SolidColorBrush Brush(double r, double g, double b, double a)
        {
            return new SharpDX.Direct2D1.SolidColorBrush(RenderTarget, new SharpDX.Color4((float)(r / 255), (float)(g / 255), (float)(b / 255), (float)a));
        }

        private void DisposeBrushes()
        {
            foreach (var set in new[] { positive, negative, projectedPositive, projectedNegative })
                if (set != null)
                    foreach (var brush in set)
                        brush.Dispose();
            positive = negative = projectedPositive = projectedNegative = null;
            foreach (var set in new[] { blueTiles, pressureUp, pressureDown })
                if (set != null)
                    foreach (var brush in set)
                        brush.Dispose();
            blueTiles = pressureUp = pressureDown = null;
            foreach (var brush in new[] { bandCall, bandPut, bandAbs, bandFlip })
                if (brush != null)
                    brush.Dispose();
            bandCall = bandPut = bandAbs = bandFlip = null;
            foreach (var brush in levelBrushes.Values)
                brush.Dispose();
            levelBrushes.Clear();
            if (labelFormat != null)
            {
                labelFormat.Dispose();
                labelFormat = null;
            }
        }

        protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
        {
            base.OnRender(chartControl, chartScale);
            if (RenderTarget == null || positive == null || ChartBars == null)
                return;
            lock (gate)
            {
                if (Style == ConvexaStyle.Presion)
                    RenderPressureStyle(chartControl, chartScale);
                else if (ShowHeatmap && strikes.Length > 1 && columns.Count > 0)
                    RenderHeatmap(chartControl, chartScale);
                if (ShowLevels)
                    RenderLevels(chartControl, chartScale);
            }
        }

        private float XOf(ChartControl chartControl, long epoch)
        {
            DateTime local = TimeZoneInfo.ConvertTimeFromUtc(DateTimeOffset.FromUnixTimeSeconds(epoch).UtcDateTime, Core.Globals.GeneralOptions.TimeZoneInfo);
            return chartControl.GetXByTime(local);
        }

        private void RenderHeatmap(ChartControl chartControl, ChartScale chartScale)
        {
            RenderHeatmapWith(chartControl, chartScale, positive, negative, projectedPositive, projectedNegative, FaintShare);
        }

        private void RenderHeatmapWith(ChartControl chartControl, ChartScale chartScale, SharpDX.Direct2D1.SolidColorBrush[] up, SharpDX.Direct2D1.SolidColorBrush[] down,
            SharpDX.Direct2D1.SolidColorBrush[] projectedUp, SharpDX.Direct2D1.SolidColorBrush[] projectedDown, double faint)
        {
            float right = ChartPanel.X + ChartPanel.W;
            float left = ChartPanel.X;
            var ys = strikes.Select(s => (float)chartScale.GetYByValue(s)).ToArray();
            for (int c = 0; c < columns.Count; c++)
            {
                float x = XOf(chartControl, columns[c].Key);
                bool last = c == columns.Count - 1;
                float next = last ? x + 4 : XOf(chartControl, columns[c + 1].Key);
                if (next <= x && !last)
                    continue; // several columns inside one bar: the later one paints it
                if (next < left || x > right)
                    continue;
                DrawColumn(columns[c].Value, columnScales[c], ys, x, Math.Max(1f, next - x), up, down, faint);
                if (last && right > next)
                    DrawColumn(columns[c].Value, columnScales[c], ys, next, right - next, projectedUp, projectedDown, faint);
            }
        }

        private void DrawColumn(double[] values, double scale, float[] ys, float x, float width, SharpDX.Direct2D1.SolidColorBrush[] up, SharpDX.Direct2D1.SolidColorBrush[] down, double faint)
        {
            if (scale <= 0)
                return;
            for (int s = 0; s < values.Length && s < ys.Length; s++)
            {
                double value = values[s];
                if (double.IsNaN(value))
                    continue;
                double share = Math.Min(1.0, Math.Abs(value) / scale);
                if (share < faint)
                    continue;
                float half = HalfRow(ys, s);
                var brushes = value >= 0 ? up : down;
                var brush = brushes[Math.Min(RampSteps - 1, (int)(share * RampSteps))];
                RenderTarget.FillRectangle(new SharpDX.RectangleF(x, ys[s] - half, width, half * 2), brush);
            }
        }

        private static float HalfRow(float[] ys, int s)
        {
            float gap = s + 1 < ys.Length ? Math.Abs(ys[s + 1] - ys[s]) : s > 0 ? Math.Abs(ys[s] - ys[s - 1]) : 6f;
            return Math.Max(1f, gap / 2f);
        }

        private void RenderPressureStyle(ChartControl chartControl, ChartScale chartScale)
        {
            float left = ChartPanel.X, right = ChartPanel.X + ChartPanel.W;
            // 1. The blue ground: the gamma heatmap's own columns, as faint tiles.
            if (ShowHeatmap && strikes.Length > 1 && columns.Count > 0)
                RenderHeatmapWith(chartControl, chartScale, blueTiles, blueTiles, blueTiles, blueTiles, 0.0);
            // 2. The fixed bands across the chart.
            float bandHalf = strikes.Length > 1 ? Math.Max(2f, HalfRow(strikes.Select(v => (float)chartScale.GetYByValue(v)).ToArray(), 0)) : 3f;
            DrawBand(chartScale, "call_wall", bandCall, bandHalf, left, right);
            DrawBand(chartScale, "put_wall", bandPut, bandHalf, left, right);
            DrawBand(chartScale, "abs", bandAbs, bandHalf, left, right);
            double flip;
            if (levels.TryGetValue("gamma_flip", out flip))
            {
                float y = (float)chartScale.GetYByValue(flip);
                RenderTarget.DrawLine(new SharpDX.Vector2(left, y), new SharpDX.Vector2(right, y), bandFlip, 2f);
            }
            // 3. The pressure: per minute the strongest PressureLevelsPerSide strikes above and below that minute's close, with their life.
            if (pressureStrikes.Length == 0 || pressureRows.Count == 0 || pressureP90 <= 0 || ChartBars == null)
                return;
            var ys = pressureStrikes.Select(v => (float)chartScale.GetYByValue(v)).ToArray();
            int n = pressureStrikes.Length;
            var lit = new bool[n];        // drawn in the previous minute
            var side = new int[n];        // +1 strike under the close when it lit, -1 over it
            var beyond = new int[n];      // consecutive minutes on the other side
            var cut = new bool[n];        // crossed: not drawn until its pressure drops under the threshold and comes back
            long[] epochs = pressureRows.Keys.ToArray();
            for (int m = 0; m < epochs.Length; m++)
            {
                double[] values = pressureRows[epochs[m]];
                double close = CloseAt(chartControl, epochs[m]);
                if (double.IsNaN(close))
                    continue;
                float x = XOf(chartControl, epochs[m]);
                bool last = m == epochs.Length - 1;
                float next = last ? right : XOf(chartControl, epochs[m + 1]);
                var above = Enumerable.Range(0, n).Where(i => pressureStrikes[i] > close && Math.Abs(values[i]) >= pressureP90).OrderByDescending(i => Math.Abs(values[i])).Take(PressureLevelsPerSide);
                var below = Enumerable.Range(0, n).Where(i => pressureStrikes[i] <= close && Math.Abs(values[i]) >= pressureP90).OrderByDescending(i => Math.Abs(values[i])).Take(PressureLevelsPerSide);
                var chosen = new HashSet<int>(above.Concat(below));
                for (int i = 0; i < n; i++)
                {
                    if (Math.Abs(values[i]) < pressureP90)
                    {
                        cut[i] = false; // faded: fresh pressure may rebuild it later
                        lit[i] = false;
                        continue;
                    }
                    int now = close >= pressureStrikes[i] ? 1 : -1;
                    if (lit[i] && now != side[i])
                    {
                        beyond[i]++;
                        if (beyond[i] >= 5)
                            cut[i] = true;
                    }
                    else
                        beyond[i] = 0;
                    if (!chosen.Contains(i) || cut[i])
                    {
                        lit[i] = lit[i] && !cut[i];
                        continue;
                    }
                    if (!lit[i])
                    {
                        side[i] = now;
                        beyond[i] = 0;
                    }
                    lit[i] = true;
                    if (next < left || x > right || next <= x)
                        continue;
                    double share = pressureP99 > 0 ? Math.Min(1.0, Math.Abs(values[i]) / pressureP99) : 1.0;
                    var brushes = values[i] >= 0 ? pressureUp : pressureDown;
                    float half = HalfRow(ys, i);
                    RenderTarget.FillRectangle(new SharpDX.RectangleF(x, ys[i] - half, next - x, half * 2), brushes[Math.Min(RampSteps - 1, (int)(share * RampSteps))]);
                }
            }
        }

        private void DrawBand(ChartScale chartScale, string key, SharpDX.Direct2D1.SolidColorBrush brush, float half, float left, float right)
        {
            double value;
            if (!levels.TryGetValue(key, out value))
                return;
            float y = (float)chartScale.GetYByValue(value);
            RenderTarget.FillRectangle(new SharpDX.RectangleF(left, y - half, right - left, half * 2), brush);
        }

        // The close of the bar that holds `epoch` (NaN when the chart has no bar there).
        private double CloseAt(ChartControl chartControl, long epoch)
        {
            DateTime local = TimeZoneInfo.ConvertTimeFromUtc(DateTimeOffset.FromUnixTimeSeconds(epoch).UtcDateTime, Core.Globals.GeneralOptions.TimeZoneInfo);
            int index = ChartBars.GetBarIdxByTime(chartControl, local.AddSeconds(59));
            if (index < 0 || index >= ChartBars.Bars.Count)
                return double.NaN;
            return ChartBars.Bars.GetClose(index);
        }

        private void RenderLevels(ChartControl chartControl, ChartScale chartScale)
        {
            float left = ChartPanel.X;
            float right = ChartPanel.X + ChartPanel.W;
            foreach (var level in levels)
            {
                string group = level.Key.StartsWith("em_") ? "em" : level.Key.StartsWith("atr_") ? "atr" : level.Key;
                SharpDX.Direct2D1.SolidColorBrush brush;
                if (!levelBrushes.TryGetValue(group, out brush) || level.Key == "price")
                    continue;
                float y = (float)chartScale.GetYByValue(level.Value);
                if (y < ChartPanel.Y || y > ChartPanel.Y + ChartPanel.H)
                    continue;
                float width = group == "em" || group == "atr" ? 1f : 2f;
                RenderTarget.DrawLine(new SharpDX.Vector2(left, y), new SharpDX.Vector2(right, y), brush, width);
                string label = Label(level.Key) + " " + level.Value.ToString("0.00", CultureInfo.InvariantCulture);
                RenderTarget.DrawText(label, labelFormat, new SharpDX.RectangleF(right - 190, y - 15, 185, 14), brush);
            }
            if (basisText.Length > 0)
                RenderTarget.DrawText("Convexa " + ViewName(View) + " " + Metric + " · " + basisText, labelFormat,
                    new SharpDX.RectangleF(left + 6, ChartPanel.Y + 4, 420, 14), levelBrushes["text"]);
        }

        private static string Label(string key)
        {
            switch (key)
            {
                case "gamma_flip": return "Gamma Flip";
                case "call_wall": return "Call Wall";
                case "put_wall": return "Put Wall";
                case "abs": return "ABS";
                case "max_pain": return "Max Pain";
                case "avwap": return "VWAP anclado";
                case "em_upper": return "Mov. esp. +";
                case "em_lower": return "Mov. esp. −";
                case "atr_outer_upper": return "ATR ext +";
                case "atr_inner_upper": return "ATR int +";
                case "atr_inner_lower": return "ATR int −";
                case "atr_outer_lower": return "ATR ext −";
                default: return key;
            }
        }

        private void Log(string text)
        {
            DateTime now = DateTime.UtcNow;
            if ((now - lastLogUtc).TotalSeconds < 60)
                return;
            lastLogUtc = now;
            Print("Convexa Niveles: " + text);
        }

        [NinjaScriptProperty]
        [Display(Name = "View (pestaña)", Order = 1, GroupName = "Convexa")]
        public ConvexaView View { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Metric (figura)", Order = 2, GroupName = "Convexa")]
        public ConvexaMetric Metric { get; set; }

        [Display(Name = "Show heatmap", Order = 3, GroupName = "Convexa")]
        public bool ShowHeatmap { get; set; }

        [Display(Name = "Show levels", Order = 4, GroupName = "Convexa")]
        public bool ShowLevels { get; set; }

        [Range(5, 100)]
        [Display(Name = "Heatmap opacity %", Order = 5, GroupName = "Convexa")]
        public int Opacity { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Estilo (Clasico / Presion)", Order = 7, GroupName = "Convexa")]
        public ConvexaStyle Style { get; set; }

        [Display(Name = "Presion: vencimientos del flujo", Order = 8, GroupName = "Convexa")]
        public ConvexaFlowExpirations FlowExpirations { get; set; }

        [Range(1, 10)]
        [Display(Name = "Presion: niveles arriba y abajo", Order = 9, GroupName = "Convexa")]
        public int PressureLevelsPerSide { get; set; }

        [Display(Name = "Convexa URL", Order = 6, GroupName = "Convexa")]
        public string Url { get; set; }
    }
}
