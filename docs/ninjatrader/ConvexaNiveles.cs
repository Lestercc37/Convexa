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
// Smooth, behind the candles (Lester 2026-11-06, with his mock-up): the indicator draws BEHIND the bars (SetZOrder) and every column is ONE
// vertical gradient through all its strikes (Direct2D interpolates between them: no squares). Style Presion: deep blue ground; strong gamma
// strikes light up GREEN under the price of that moment (support) and RED / ORANGE above it (resistance).
// Style Lineas (Lester 2026-11-06 21:20, Bookmap next to it; 21:45 colours): near-black ground, ONE thin horizontal line per strike with real
// gamma (>= 25 % of the column's p98), GREEN under the price of that moment (support) and RED above it (resistance), brighter and thicker the
// more gamma; the strongest strike of the latest column is drawn across the whole plot and labelled in dollars. No bubbles, candles untouched.
// GEX profile (same day): a bar per strike in a strip at the right of the chart (the chart's right margin is widened to hold it), the latest
// value of each strike, square-root length on the p98, the SAME colours (green under the last price, red above), the dollar figure inside
// each bar, the strongest strike outlined in white.
// Never cut by session (Lester 2026-11-06): the last `Days` sessions are kept, each with its own strike list and basis; a read is only ever
// ADDED to what is held (a full read every 5 minutes replaces it whole), each column lasts until the next one, so nights and weekends keep
// the last picture Convexa sent, and the lines move only when Convexa updates them.
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
public enum ConvexaStyle { Clasico, Presion, Lineas }
public enum ConvexaFlowExpirations { Dte0, Dte0a2, Todas }

namespace NinjaTrader.NinjaScript.Indicators
{

    public class ConvexaNiveles : Indicator
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        private const int RampSteps = 32;
        private const double FaintShare = 0.06;

        private readonly object gate = new object();
        // epoch (bucket start, UTC) -> the column: its values, the strikes they belong to (futures points) and its own p98.
        private SortedDictionary<long, HeatColumn> columns = new SortedDictionary<long, HeatColumn>();
        private readonly Dictionary<string, double> levels = new Dictionary<string, double>();
        private string basisText = "";
        private string requestKey = "";
        private DateTime lastFullUtc = DateTime.MinValue;
        private DateTime lastLogUtc = DateTime.MinValue;
        private Timer timer;
        private int polling;
        private string future;

        // Pressure (style Presion): the flow source's strikes in futures points and one row per minute (epoch -> pressure per strike).
        private SortedDictionary<long, HeatColumn> pressureRows = new SortedDictionary<long, HeatColumn>();
        private double pressureP90, pressureP99;
        private DateTime lastPressureUtc = DateTime.MinValue;
        private string pressureKey = "";
        private SharpDX.Direct2D1.SolidColorBrush[] pressureUp, pressureDown, blueTiles;
        private SharpDX.Direct2D1.SolidColorBrush bandCall, bandPut, bandAbs, bandFlip;

        private SharpDX.Direct2D1.SolidColorBrush[] positive, negative, projectedPositive, projectedNegative;
        private readonly Dictionary<string, SharpDX.Direct2D1.SolidColorBrush> levelBrushes = new Dictionary<string, SharpDX.Direct2D1.SolidColorBrush>();
        private SharpDX.DirectWrite.TextFormat labelFormat;
        // One gradient brush per heatmap column (built once, re-positioned every frame), keyed by the column's epoch.
        private readonly Dictionary<long, KeyValuePair<HeatColumn, SharpDX.Direct2D1.LinearGradientBrush>> columnBrushes =
            new Dictionary<long, KeyValuePair<HeatColumn, SharpDX.Direct2D1.LinearGradientBrush>>();
        private string brushStyleKey = "";
        private SharpDX.Direct2D1.SolidColorBrush ground;
        // Soft-edged pressure bars: one vertical gradient brush per (side, strength step), re-positioned for every bar.
        private const int PressureSteps = 8;
        private const int PressureMinStreak = 3;
        private readonly Dictionary<int, SharpDX.Direct2D1.LinearGradientBrush> pressureBrushes = new Dictionary<int, SharpDX.Direct2D1.LinearGradientBrush>();

        // Lineas: line brushes per strength step (core and glow), the near-black ground; the profile's ramps; where the plot ends (the profile strip
        // starts there).
        private const int LineSteps = 6;
        private const double LineMinShare = 0.25;
        private SharpDX.Direct2D1.SolidColorBrush[] lineCore, lineGlow, lineCoreDown, lineGlowDown, profileUp, profileDown;
        private SharpDX.Direct2D1.SolidColorBrush lineGround, lineLabel, profileAxis;
        private float plotRight;
        private bool marginAsked;

        private class LineRun
        {
            public float X0, X1, Y, StepPx;
            public int Step, Side;
        }

        private class PressureRun
        {
            public float X0, X1, Y, Half;
            public int Sign, Count;
            public double ShareSum;
        }

        private class HeatColumn
        {
            public double[] Values;
            public double[] Strikes;
            public double Scale;
        }

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
                Days = 2;
                Opacity = 60;
                ShowProfile = true;
                ProfileWidth = 220;
            }
            else if (State == State.DataLoaded)
            {
                future = Instrument.MasterInstrument.Name;
            }
            else if (State == State.Historical)
            {
                SetZOrder(-1); // behind the candles: NinjaTrader draws indicators on top of the bars otherwise
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
                            since = columns.Keys.Last();
                    }
                    string keyFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".convexa", "ninjatrader_anchor_key.txt");
                    if (!File.Exists(keyFile))
                    {
                        Log("key file missing (" + keyFile + ").");
                        return;
                    }
                    string url = Url + Uri.EscapeDataString(future) + "?view=" + ViewName(View) + "&metric=" + Metric + (full ? "" : "&since=" + since.ToString(CultureInfo.InvariantCulture)) + (ShowHeatmap || ShowProfile ? "" : "&heatmap=false") + "&days=" + Days.ToString(CultureInfo.InvariantCulture);
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
            var newColumns = new SortedDictionary<long, HeatColumn>();
            var newPressure = new SortedDictionary<long, HeatColumn>();
            bool sawHeatmap = false, sawPressure = false;
            double[] blockStrikes = null, blockPressureStrikes = null;
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
                else if (parts[0] == "heatmap")
                    sawHeatmap = true;
                else if (parts[0] == "pressure")
                    sawPressure = true;
                else if (parts[0] == "strikes")
                    blockStrikes = Numbers(parts);
                else if (parts[0] == "pstrikes")
                    blockPressureStrikes = Numbers(parts);
                else if (parts[0] == "col" && parts.Length >= 2 && blockStrikes != null)
                {
                    double[] values = parts.Skip(2).Select(p => p == "_" ? double.NaN : double.Parse(p, CultureInfo.InvariantCulture)).ToArray();
                    if (values.Length == blockStrikes.Length)
                        newColumns[long.Parse(parts[1], CultureInfo.InvariantCulture)] = new HeatColumn { Values = values, Strikes = blockStrikes, Scale = P98(values) };
                }
                else if (parts[0] == "prow" && parts.Length >= 2 && blockPressureStrikes != null)
                {
                    double[] values = parts.Skip(2).Select(p => double.Parse(p, CultureInfo.InvariantCulture)).ToArray();
                    if (values.Length == blockPressureStrikes.Length)
                        newPressure[long.Parse(parts[1], CultureInfo.InvariantCulture)] = new HeatColumn { Values = values, Strikes = blockPressureStrikes };
                }
            }
            lock (gate)
            {
                levels.Clear();
                foreach (var pair in newLevels)
                    levels[pair.Key] = pair.Value;
                basisText = basis;
                if (sawHeatmap)
                {
                    // A full read replaces the held picture only when it brought something (an empty answer never blanks the chart).
                    if (full && newColumns.Count > 0)
                    {
                        columns = newColumns;
                        requestKey = key;
                        lastFullUtc = DateTime.UtcNow;
                    }
                    else
                        foreach (var column in newColumns)
                            columns[column.Key] = column.Value;
                }
                if (sawPressure)
                {
                    if (pressureFull && newPressure.Count > 0)
                    {
                        pressureRows = newPressure;
                        pressureKey = pexp;
                    }
                    else
                        foreach (var row in newPressure)
                            pressureRows[row.Key] = row.Value;
                    double[] magnitudes = pressureRows.Values.SelectMany(v => v.Values).Where(v => v != 0).Select(Math.Abs).OrderBy(v => v).ToArray();
                    pressureP90 = magnitudes.Length == 0 ? 0 : magnitudes[(int)Math.Floor(0.90 * (magnitudes.Length - 1))];
                    pressureP99 = magnitudes.Length == 0 ? 0 : magnitudes[(int)Math.Floor(0.99 * (magnitudes.Length - 1))];
                }
            }
        }

        private static double[] Numbers(string[] parts)
        {
            return parts.Skip(1).Select(p => double.Parse(p, CultureInfo.InvariantCulture)).ToArray();
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
            ground = Brush(3, 9, 36, Math.Min(1.0, alpha + 0.3));
            ClearColumnBrushes();
            bandFlip = Brush(243, 201, 105, 1);
            // Lineas and the profile: green for support (under the price), red for resistance (above), dark to bright with the strength.
            lineCore = new SharpDX.Direct2D1.SolidColorBrush[LineSteps];
            lineGlow = new SharpDX.Direct2D1.SolidColorBrush[LineSteps];
            lineCoreDown = new SharpDX.Direct2D1.SolidColorBrush[LineSteps];
            lineGlowDown = new SharpDX.Direct2D1.SolidColorBrush[LineSteps];
            for (int i = 0; i < LineSteps; i++)
            {
                double t = i / (double)(LineSteps - 1);
                lineCore[i] = Brush(20 + 40 * t, 110 + 125 * t, 60 + 50 * t, 0.55 + 0.45 * t);
                lineGlow[i] = Brush(20 + 40 * t, 110 + 125 * t, 60 + 50 * t, 0.10 + 0.08 * t);
                lineCoreDown[i] = Brush(150 + 105 * t, 30 + 40 * t, 35 + 25 * t, 0.55 + 0.45 * t);
                lineGlowDown[i] = Brush(150 + 105 * t, 30 + 40 * t, 35 + 25 * t, 0.10 + 0.08 * t);
            }
            lineGround = Brush(15, 18, 24, 1);
            lineLabel = Brush(255, 255, 255, 0.95);
            profileUp = new SharpDX.Direct2D1.SolidColorBrush[RampSteps];
            profileDown = new SharpDX.Direct2D1.SolidColorBrush[RampSteps];
            for (int i = 0; i < RampSteps; i++)
            {
                double t = (i + 1) / (double)RampSteps;
                profileUp[i] = Brush(14 + 36 * t, 80 + 150 * t, 45 + 60 * t, 0.92);
                profileDown[i] = Brush(95 + 150 * t, 22 + 45 * t, 28 + 32 * t, 0.92);
            }
            profileAxis = Brush(209, 212, 220, 0.7);
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
            foreach (var set in new[] { blueTiles, pressureUp, pressureDown, lineCore, lineGlow, lineCoreDown, lineGlowDown, profileUp, profileDown })
                if (set != null)
                    foreach (var brush in set)
                        brush.Dispose();
            blueTiles = pressureUp = pressureDown = lineCore = lineGlow = lineCoreDown = lineGlowDown = profileUp = profileDown = null;
            foreach (var brush in new[] { lineGround, lineLabel, profileAxis })
                if (brush != null)
                    brush.Dispose();
            lineGround = lineLabel = profileAxis = null;
            foreach (var brush in new[] { bandCall, bandPut, bandAbs, bandFlip })
                if (brush != null)
                    brush.Dispose();
            bandCall = bandPut = bandAbs = bandFlip = null;
            if (ground != null)
            {
                ground.Dispose();
                ground = null;
            }
            ClearColumnBrushes();
            foreach (var brush in pressureBrushes.Values)
                brush.Dispose();
            pressureBrushes.Clear();
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
                float profile = ShowProfile ? Math.Min(ProfileWidth, ChartPanel.W / 3f) : 0f;
                plotRight = ChartPanel.X + ChartPanel.W - profile;
                if (ShowProfile)
                    KeepProfileMargin(chartControl, (int)profile);
                if (Style == ConvexaStyle.Presion)
                    RenderPressureStyle(chartControl, chartScale);
                else if (Style == ConvexaStyle.Lineas)
                    RenderLines(chartControl, chartScale);
                else if (ShowHeatmap && columns.Count > 0)
                    RenderSmoothColumns(chartControl, chartScale, false);
                if (ShowProfile && columns.Count > 0)
                    RenderProfile(chartScale);
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
            var ysByStrikes = new Dictionary<double[], float[]>();
            long[] epochs = columns.Keys.ToArray();
            for (int c = 0; c < epochs.Length; c++)
            {
                HeatColumn column = columns[epochs[c]];
                float x = XOf(chartControl, epochs[c]);
                bool last = c == epochs.Length - 1;
                // Each column lasts until the next one (overnight and weekends included: the last picture Convexa sent stays).
                float next = last ? x + 4 : XOf(chartControl, epochs[c + 1]);
                if (next <= x && !last)
                    continue; // several columns inside one bar: the later one paints it
                if (next < left || x > right)
                    continue;
                float[] ys;
                if (!ysByStrikes.TryGetValue(column.Strikes, out ys))
                {
                    ys = column.Strikes.Select(v => (float)chartScale.GetYByValue(v)).ToArray();
                    ysByStrikes[column.Strikes] = ys;
                }
                DrawColumn(column.Values, column.Scale, ys, x, Math.Max(1f, next - x), up, down, faint);
                if (last && right > next)
                    DrawColumn(column.Values, column.Scale, ys, next, right - next, projectedUp, projectedDown, faint);
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
            float left = ChartPanel.X, right = plotRight;
            // 1. The blue ground: the gamma heatmap's own columns, as faint tiles.
            RenderTarget.FillRectangle(new SharpDX.RectangleF(left, ChartPanel.Y, ChartPanel.W, ChartPanel.H), ground);
            if (ShowHeatmap && columns.Count > 0)
                RenderSmoothColumns(chartControl, chartScale, true);
            // 2. The fixed bands across the chart, as tall as one strike step of the latest column.
            double[] latestStrikes = columns.Count > 0 ? columns[columns.Keys.Last()].Strikes : new double[0];
            float bandHalf = latestStrikes.Length > 1 ? Math.Max(2f, HalfRow(latestStrikes.Select(v => (float)chartScale.GetYByValue(v)).ToArray(), 0)) : 3f;
            DrawBand(chartScale, "call_wall", bandCall, bandHalf, left, right);
            DrawBand(chartScale, "put_wall", bandPut, bandHalf, left, right);
            DrawBand(chartScale, "abs", bandAbs, bandHalf, left, right);
            double flip;
            if (levels.TryGetValue("gamma_flip", out flip))
            {
                float y = (float)chartScale.GetYByValue(flip);
                RenderTarget.DrawLine(new SharpDX.Vector2(left, y), new SharpDX.Vector2(right, y), bandFlip, 2f);
            }
            // 3. The pressure: per minute the strongest PressureLevelsPerSide strikes above and below that minute's close.
            // Calm (Lester 2026-11-06 21:10, "demasiado picotillo"): a strike LIGHTS only after PressureMinStreak minutes in a row among the
            // strongest, then stays while its pressure holds (>= p90) and the price has not crossed it for 5 minutes; consecutive minutes are
            // ONE soft-edged bar, never stretched over a gap between sessions. The state follows the strike's PRICE (lists differ by day).
            if (pressureRows.Count == 0 || pressureP90 <= 0 || ChartBars == null)
                return;
            var lit = new Dictionary<double, bool>();
            var streak = new Dictionary<double, int>();
            var side = new Dictionary<double, int>();
            var beyond = new Dictionary<double, int>();
            var cut = new Dictionary<double, bool>();
            var runs = new Dictionary<double, PressureRun>();
            var ysByStrikes = new Dictionary<double[], float[]>();
            long[] epochs = pressureRows.Keys.ToArray();
            for (int m = 0; m < epochs.Length; m++)
            {
                HeatColumn row = pressureRows[epochs[m]];
                double close = CloseAt(chartControl, epochs[m]);
                if (double.IsNaN(close))
                    continue;
                float x = XOf(chartControl, epochs[m]);
                bool contiguous = m < epochs.Length - 1 && epochs[m + 1] - epochs[m] <= 120;
                float next = m == epochs.Length - 1 ? right : contiguous ? XOf(chartControl, epochs[m + 1]) : XOf(chartControl, epochs[m] + 60);
                double[] strikes = row.Strikes, values = row.Values;
                int n = strikes.Length;
                float[] ys;
                if (!ysByStrikes.TryGetValue(strikes, out ys))
                {
                    ys = strikes.Select(v => (float)chartScale.GetYByValue(v)).ToArray();
                    ysByStrikes[strikes] = ys;
                }
                var above = Enumerable.Range(0, n).Where(i => strikes[i] > close && Math.Abs(values[i]) >= pressureP90).OrderByDescending(i => Math.Abs(values[i])).Take(PressureLevelsPerSide);
                var below = Enumerable.Range(0, n).Where(i => strikes[i] <= close && Math.Abs(values[i]) >= pressureP90).OrderByDescending(i => Math.Abs(values[i])).Take(PressureLevelsPerSide);
                var chosen = new HashSet<int>(above.Concat(below));
                var drawn = new HashSet<double>();
                for (int i = 0; i < n; i++)
                {
                    double k = strikes[i];
                    bool wasLit = lit.ContainsKey(k) && lit[k];
                    bool isCut = cut.ContainsKey(k) && cut[k];
                    if (Math.Abs(values[i]) < pressureP90)
                    {
                        cut[k] = false; // faded: fresh pressure may rebuild it later
                        lit[k] = false;
                        streak[k] = 0;
                        continue;
                    }
                    int now = close >= k ? 1 : -1;
                    int count = beyond.ContainsKey(k) ? beyond[k] : 0;
                    if (wasLit && side.ContainsKey(k) && now != side[k])
                    {
                        count++;
                        if (count >= 5)
                            isCut = true;
                    }
                    else
                        count = 0;
                    beyond[k] = count;
                    cut[k] = isCut;
                    int run = chosen.Contains(i) ? (streak.ContainsKey(k) ? streak[k] : 0) + 1 : 0;
                    streak[k] = run;
                    bool show = !isCut && (wasLit || run >= PressureMinStreak);
                    if (show && !wasLit)
                    {
                        side[k] = now;
                        beyond[k] = 0;
                    }
                    lit[k] = show;
                    if (!show || next <= x)
                        continue;
                    double share = pressureP99 > 0 ? Math.Min(1.0, Math.Abs(values[i]) / pressureP99) : 1.0;
                    int sign = values[i] >= 0 ? 1 : -1;
                    PressureRun open;
                    if (runs.TryGetValue(k, out open) && open.Sign == sign && Math.Abs(open.X1 - x) < 1.5f)
                    {
                        open.X1 = next;
                        open.ShareSum += share;
                        open.Count++;
                    }
                    else
                    {
                        if (open != null)
                            DrawPressureRun(open, left, right);
                        runs[k] = new PressureRun { X0 = x, X1 = next, Y = ys[i], Half = HalfRow(ys, i), Sign = sign, Count = 1, ShareSum = share };
                    }
                    drawn.Add(k);
                }
                foreach (double k in runs.Keys.Where(k => !drawn.Contains(k)).ToList())
                {
                    DrawPressureRun(runs[k], left, right);
                    runs.Remove(k);
                }
            }
            foreach (PressureRun open in runs.Values)
                DrawPressureRun(open, left, right);
        }

        // One pressure bar: transparent at its top and bottom edges, full colour in the middle (no hard tiles).
        private void DrawPressureRun(PressureRun run, float left, float right)
        {
            if (run.X1 < left || run.X0 > right || run.X1 - run.X0 < 1f)
                return;
            double share = run.ShareSum / Math.Max(1, run.Count);
            int step = Math.Min(PressureSteps - 1, (int)(share * PressureSteps));
            int key = run.Sign * (step + 1);
            SharpDX.Direct2D1.LinearGradientBrush brush;
            if (!pressureBrushes.TryGetValue(key, out brush))
            {
                double t = (step + 1) / (double)PressureSteps;
                var color = run.Sign > 0
                    ? new SharpDX.Color4((float)((20 + 60 * t) / 255), (float)((150 + 105 * t) / 255), (float)((70 + 40 * t) / 255), (float)(0.5 + 0.45 * t))
                    : new SharpDX.Color4((float)((200 + 55 * t) / 255), (float)((40 + 50 * t) / 255), (float)((35 + 15 * t) / 255), (float)(0.5 + 0.45 * t));
                var clear = new SharpDX.Color4(color.Red, color.Green, color.Blue, 0f);
                var stops = new[]
                {
                    new SharpDX.Direct2D1.GradientStop { Position = 0f, Color = clear },
                    new SharpDX.Direct2D1.GradientStop { Position = 0.35f, Color = color },
                    new SharpDX.Direct2D1.GradientStop { Position = 0.65f, Color = color },
                    new SharpDX.Direct2D1.GradientStop { Position = 1f, Color = clear },
                };
                using (var collection = new SharpDX.Direct2D1.GradientStopCollection(RenderTarget, stops))
                    brush = new SharpDX.Direct2D1.LinearGradientBrush(RenderTarget,
                        new SharpDX.Direct2D1.LinearGradientBrushProperties { StartPoint = new SharpDX.Vector2(0, 0), EndPoint = new SharpDX.Vector2(0, 1) }, collection);
                pressureBrushes[key] = brush;
            }
            float half = Math.Max(2f, run.Half);
            brush.StartPoint = new SharpDX.Vector2(run.X0, run.Y - half);
            brush.EndPoint = new SharpDX.Vector2(run.X0, run.Y + half);
            float x0 = Math.Max(left, run.X0), x1 = Math.Min(right, run.X1);
            RenderTarget.FillRectangle(new SharpDX.RectangleF(x0, run.Y - half, x1 - x0, half * 2), brush);
        }

        // The chart's right margin is widened once so the candles stop before the profile strip (never narrowed: the trader's own margin wins).
        private void KeepProfileMargin(ChartControl chartControl, int width)
        {
            if (marginAsked || chartControl == null)
                return;
            marginAsked = true;
            int wanted = width + 12;
            try
            {
                if (chartControl.Properties.BarMarginRight < wanted)
                    chartControl.Dispatcher.InvokeAsync(() =>
                    {
                        chartControl.Properties.BarMarginRight = wanted;
                        chartControl.InvalidateVisual();
                    });
            }
            catch (Exception error)
            {
                Log("right margin not set: " + error.Message);
            }
        }

        // Style Lineas: per strike one line through the columns, split only when its strength moves by 2+ steps or the price crosses it.
        private void RenderLines(ChartControl chartControl, ChartScale chartScale)
        {
            float left = ChartPanel.X, right = plotRight;
            RenderTarget.FillRectangle(new SharpDX.RectangleF(left, ChartPanel.Y, ChartPanel.W, ChartPanel.H), lineGround);
            if (!ShowHeatmap || columns.Count == 0)
                return;
            var runs = new Dictionary<double, LineRun>();
            var ysByStrikes = new Dictionary<double[], float[]>();
            long[] epochs = columns.Keys.ToArray();
            for (int c = 0; c < epochs.Length; c++)
            {
                HeatColumn column = columns[epochs[c]];
                if (column.Strikes.Length < 2 || column.Scale <= 0)
                    continue;
                float x = XOf(chartControl, epochs[c]);
                float next = c == epochs.Length - 1 ? right : Math.Min(right, XOf(chartControl, epochs[c + 1]));
                if (next <= x)
                    continue;
                double price = CloseAt(chartControl, epochs[c]);
                float[] ys;
                if (!ysByStrikes.TryGetValue(column.Strikes, out ys))
                {
                    ys = column.Strikes.Select(v => (float)chartScale.GetYByValue(v)).ToArray();
                    ysByStrikes[column.Strikes] = ys;
                }
                var seen = new HashSet<double>();
                for (int i = 0; i < column.Strikes.Length; i++)
                {
                    double value = column.Values[i];
                    double share = double.IsNaN(value) ? 0 : Math.Min(1.0, Math.Abs(value) / column.Scale);
                    if (share < LineMinShare)
                        continue;
                    int step = Math.Min(LineSteps - 1, (int)((share - LineMinShare) / (1 - LineMinShare) * LineSteps));
                    double k = column.Strikes[i];
                    int sideNow = double.IsNaN(price) ? 0 : k <= price ? 1 : -1;
                    seen.Add(k);
                    LineRun run;
                    if (runs.TryGetValue(k, out run) && Math.Abs(run.X1 - x) < 1.5f && Math.Abs(run.Step - step) <= 1 && (sideNow == 0 || sideNow == run.Side))
                    {
                        run.X1 = next;
                        continue;
                    }
                    if (run != null)
                        DrawLineRun(run, left, right);
                    runs[k] = new LineRun { X0 = x, X1 = next, Y = ys[i], StepPx = HalfRow(ys, i) * 2, Step = step, Side = sideNow == 0 ? (run != null ? run.Side : 1) : sideNow };
                }
                foreach (double k in runs.Keys.Where(k => !seen.Contains(k)).ToList())
                {
                    DrawLineRun(runs[k], left, right);
                    runs.Remove(k);
                }
            }
            foreach (LineRun run in runs.Values)
                DrawLineRun(run, left, right);
            // The strongest strike of the latest column: across the whole plot, white core, its dollar figure at the right end.
            HeatColumn latest = columns[epochs[epochs.Length - 1]];
            int top = -1;
            for (int i = 0; i < latest.Strikes.Length; i++)
                if (!double.IsNaN(latest.Values[i]) && (top < 0 || Math.Abs(latest.Values[i]) > Math.Abs(latest.Values[top])))
                    top = i;
            if (top < 0)
                return;
            float y = (float)chartScale.GetYByValue(latest.Strikes[top]);
            if (y < ChartPanel.Y || y > ChartPanel.Y + ChartPanel.H)
                return;
            bool support = ChartBars.Bars.Count > 0 && latest.Strikes[top] <= ChartBars.Bars.GetClose(ChartBars.Bars.Count - 1);
            var glow = support ? lineGlow[LineSteps - 1] : lineGlowDown[LineSteps - 1];
            var core = support ? lineCore[LineSteps - 1] : lineCoreDown[LineSteps - 1];
            RenderTarget.FillRectangle(new SharpDX.RectangleF(left, y - 6, right - left, 12), glow);
            RenderTarget.FillRectangle(new SharpDX.RectangleF(left, y - 2.5f, right - left, 5), core);
            RenderTarget.DrawLine(new SharpDX.Vector2(left, y), new SharpDX.Vector2(right, y), lineLabel, 1f);
            RenderTarget.DrawText("Máx. gamma " + Money(latest.Values[top]), labelFormat, new SharpDX.RectangleF(right - 150, y - 17, 146, 14), lineLabel);
        }

        private void DrawLineRun(LineRun run, float left, float right)
        {
            float x0 = Math.Max(left, run.X0), x1 = Math.Min(right, run.X1);
            if (x1 - x0 < 1f)
                return;
            float core = Math.Max(1f, Math.Min(run.StepPx * 0.3f, 1f + 0.6f * run.Step));
            float glow = Math.Min(run.StepPx * 0.6f, core * 2.4f);
            bool support = run.Side >= 0;
            RenderTarget.FillRectangle(new SharpDX.RectangleF(x0, run.Y - glow / 2, x1 - x0, glow), support ? lineGlow[run.Step] : lineGlowDown[run.Step]);
            RenderTarget.FillRectangle(new SharpDX.RectangleF(x0, run.Y - core / 2, x1 - x0, core), support ? lineCore[run.Step] : lineCoreDown[run.Step]);
        }

        // Dollars, short: -$242.5B, $12.3M.
        private static string Money(double value)
        {
            double a = Math.Abs(value);
            string sign = value < 0 ? "-$" : "$";
            if (a >= 1e9) return sign + (a / 1e9).ToString("0.0", CultureInfo.InvariantCulture) + "B";
            if (a >= 1e6) return sign + (a / 1e6).ToString("0.0", CultureInfo.InvariantCulture) + "M";
            if (a >= 1e3) return sign + (a / 1e3).ToString("0.0", CultureInfo.InvariantCulture) + "K";
            return sign + a.ToString("0", CultureInfo.InvariantCulture);
        }

        // GEX profile: per strike its latest value in the last 12 columns; bars from the price axis towards the candles (square root on the p98),
        // green under the last price (support) and red above (resistance), the dollar figure inside, the strongest strike outlined in white.
        private void RenderProfile(ChartScale chartScale)
        {
            float stripLeft = plotRight, stripRight = ChartPanel.X + ChartPanel.W;
            if (stripRight - stripLeft < 40 || ChartBars == null || ChartBars.Bars.Count == 0)
                return;
            double price = ChartBars.Bars.GetClose(ChartBars.Bars.Count - 1);
            var latest = new SortedDictionary<double, double>();
            foreach (HeatColumn column in columns.Values.Skip(Math.Max(0, columns.Count - 12)))
                for (int i = 0; i < column.Strikes.Length; i++)
                    if (!double.IsNaN(column.Values[i]))
                        latest[column.Strikes[i]] = column.Values[i];
            if (latest.Count == 0)
                return;
            double[] magnitudes = latest.Values.Select(Math.Abs).Where(v => v > 0).OrderBy(v => v).ToArray();
            if (magnitudes.Length == 0)
                return;
            double scale = magnitudes[Math.Min(magnitudes.Length - 1, (int)Math.Floor(0.98 * magnitudes.Length))];
            double strongest = latest.Keys.OrderByDescending(k => Math.Abs(latest[k])).First();
            float[] ys = latest.Keys.Select(k => (float)chartScale.GetYByValue(k)).ToArray();
            var gaps = new List<float>();
            for (int i = 1; i < ys.Length; i++)
                if (Math.Abs(ys[i] - ys[i - 1]) > 0)
                    gaps.Add(Math.Abs(ys[i] - ys[i - 1]));
            gaps.Sort();
            float rowStep = gaps.Count > 0 ? gaps[gaps.Count / 2] : 6f;
            float thickness = Math.Max(1f, Math.Min(rowStep * 0.8f, 18f));
            bool roomForText = thickness >= 11f;
            float maxBar = stripRight - stripLeft - 18f, rightEdge = stripRight - 4f;
            int index = 0;
            foreach (var pair in latest)
            {
                float y = ys[index++];
                if (y < ChartPanel.Y - 20 || y > ChartPanel.Y + ChartPanel.H + 20)
                    continue;
                double share = Math.Min(1.0, Math.Abs(pair.Value) / scale);
                float width = Math.Max(2f, (float)Math.Sqrt(share) * maxBar);
                bool support = pair.Key <= price;
                var brushes = support ? profileUp : profileDown;
                var rect = new SharpDX.RectangleF(rightEdge - width, y - thickness / 2, width, thickness);
                RenderTarget.FillRectangle(rect, brushes[Math.Min(RampSteps - 1, (int)(Math.Sqrt(share) * RampSteps))]);
                if (pair.Key == strongest)
                    RenderTarget.DrawRectangle(rect, lineLabel, 2f);
                if (roomForText && (share >= 0.05 || pair.Key == strongest))
                {
                    // inside the bar when it is long enough, otherwise just left of it
                    string text = Money(pair.Value);
                    float textX = width >= 64 ? rightEdge - width + 4 : rightEdge - width - 62;
                    RenderTarget.DrawText(text, labelFormat, new SharpDX.RectangleF(Math.Max(stripLeft + 2, textX), y - 7.5f, 70, 14), width >= 64 ? lineLabel : profileAxis);
                }
            }
            var dashes = new SharpDX.Direct2D1.StrokeStyle(RenderTarget.Factory, new SharpDX.Direct2D1.StrokeStyleProperties { DashStyle = SharpDX.Direct2D1.DashStyle.Dash });
            RenderTarget.DrawLine(new SharpDX.Vector2(stripLeft, ChartPanel.Y), new SharpDX.Vector2(stripLeft, ChartPanel.Y + ChartPanel.H), profileAxis, 1f, dashes);
            dashes.Dispose();
            RenderTarget.DrawText("PERFIL GEX  " + Metric, labelFormat, new SharpDX.RectangleF(stripLeft + 8, ChartPanel.Y + ChartPanel.H - 18, stripRight - stripLeft - 10, 14), profileAxis);
        }

        private void ClearColumnBrushes()
        {
            foreach (var entry in columnBrushes.Values)
                entry.Value.Dispose();
            columnBrushes.Clear();
        }

        // Each visible column painted with one vertical gradient through its strikes; the last one stretched to the right edge, softer.
        private void RenderSmoothColumns(ChartControl chartControl, ChartScale chartScale, bool pressureStyle)
        {
            string styleKey = (pressureStyle ? "P" : "C") + Metric + Opacity;
            if (styleKey != brushStyleKey)
            {
                ClearColumnBrushes();
                brushStyleKey = styleKey;
            }
            float left = ChartPanel.X, right = plotRight;
            long[] epochs = columns.Keys.ToArray();
            var used = new HashSet<long>();
            for (int c = 0; c < epochs.Length; c++)
            {
                HeatColumn column = columns[epochs[c]];
                if (column.Strikes.Length < 2 || column.Scale <= 0)
                    continue;
                float x = XOf(chartControl, epochs[c]);
                bool last = c == epochs.Length - 1;
                float next = last ? right : XOf(chartControl, epochs[c + 1]);
                if (next <= x || next < left || x > right)
                    continue;
                KeyValuePair<HeatColumn, SharpDX.Direct2D1.LinearGradientBrush> entry;
                if (!columnBrushes.TryGetValue(epochs[c], out entry) || !ReferenceEquals(entry.Key, column))
                {
                    if (entry.Value != null)
                        entry.Value.Dispose();
                    var brush = ColumnBrush(column, pressureStyle ? CloseAt(chartControl, epochs[c]) : double.NaN, pressureStyle);
                    if (brush == null)
                        continue;
                    entry = new KeyValuePair<HeatColumn, SharpDX.Direct2D1.LinearGradientBrush>(column, brush);
                    columnBrushes[epochs[c]] = entry;
                }
                used.Add(epochs[c]);
                float yLow = (float)chartScale.GetYByValue(column.Strikes[0]);
                float yHigh = (float)chartScale.GetYByValue(column.Strikes[column.Strikes.Length - 1]);
                entry.Value.StartPoint = new SharpDX.Vector2(x, yLow);
                entry.Value.EndPoint = new SharpDX.Vector2(x, yHigh);
                // A strike step above and below the range so the outermost strikes fade instead of ending in a hard edge.
                float pad = Math.Abs(yLow - yHigh) / Math.Max(1, column.Strikes.Length - 1);
                entry.Value.Opacity = last && right > x + 4 ? 0.85f : 1f;
                RenderTarget.FillRectangle(new SharpDX.RectangleF(x, Math.Min(yLow, yHigh) - pad, next - x + 0.75f, Math.Abs(yLow - yHigh) + 2 * pad), entry.Value);
            }
            // Free the brushes of columns no longer on screen (a long session holds thousands of columns).
            if (columnBrushes.Count > used.Count + 200)
                foreach (long epoch in columnBrushes.Keys.Where(k => !used.Contains(k)).ToList())
                {
                    columnBrushes[epoch].Value.Dispose();
                    columnBrushes.Remove(epoch);
                }
        }

        private SharpDX.Direct2D1.LinearGradientBrush ColumnBrush(HeatColumn column, double priceThen, bool pressureStyle)
        {
            double low = column.Strikes[0], high = column.Strikes[column.Strikes.Length - 1];
            if (high <= low)
                return null;
            var stops = new List<SharpDX.Direct2D1.GradientStop>();
            for (int i = 0; i < column.Strikes.Length; i++)
            {
                double value = column.Values[i];
                double share = double.IsNaN(value) ? 0 : Math.Min(1.0, Math.Abs(value) / column.Scale);
                float position = (float)((column.Strikes[i] - low) / (high - low));
                stops.Add(new SharpDX.Direct2D1.GradientStop { Position = position, Color = pressureStyle ? PressureColor(share, column.Strikes[i], priceThen) : ClassicColor(share, value) });
            }
            using (var collection = new SharpDX.Direct2D1.GradientStopCollection(RenderTarget, stops.ToArray()))
                return new SharpDX.Direct2D1.LinearGradientBrush(RenderTarget,
                    new SharpDX.Direct2D1.LinearGradientBrushProperties { StartPoint = new SharpDX.Vector2(0, 0), EndPoint = new SharpDX.Vector2(0, 1) }, collection);
        }

        // Style Presion: deep blue -> royal blue for weak strikes; strong ones green under the price of that moment (support), red / orange above.
        private SharpDX.Color4 PressureColor(double share, double strike, double priceThen)
        {
            double alpha = Math.Max(0.05, Math.Min(1.0, Opacity / 100.0));
            // Weak strikes stay deep navy (2026-11-06 21:10: the royal blue was too light); colour only from 45 % of the column's p98.
            if (share < 0.45)
                return Mix(new[] { 3.0, 9, 36 }, new[] { 12.0, 38, 115 }, share / 0.45, alpha);
            double t = (share - 0.45) / 0.55;
            bool support = !double.IsNaN(priceThen) && strike <= priceThen;
            double[] mid = support ? new[] { 30.0, 190, 90 } : new[] { 235.0, 70, 40 };
            double[] top = support ? new[] { 190.0, 255, 120 } : new[] { 255.0, 205, 60 };
            return t < 0.6 ? Mix(new[] { 12.0, 38, 115 }, mid, t / 0.6, Math.Min(1.0, alpha + 0.2)) : Mix(mid, top, (t - 0.6) / 0.4, Math.Min(1.0, alpha + 0.3));
        }

        // Style Clasico: Convexa's ramps (green positive / red negative; blue, purple or orange for the unsigned figures), transparent when weak.
        private SharpDX.Color4 ClassicColor(double share, double value)
        {
            double alpha = Math.Max(0.05, Math.Min(1.0, Opacity / 100.0));
            if (double.IsNaN(value) || share < FaintShare)
                return new SharpDX.Color4(0, 0, 0, 0);
            double[][] stops = value >= 0 ? PositiveStops() : NegativeStops();
            return share < 0.6 ? Mix(stops[0], stops[1], share / 0.6, alpha) : Mix(stops[1], stops[2], (share - 0.6) / 0.4, alpha);
        }

        private static SharpDX.Color4 Mix(double[] a, double[] b, double t, double alpha)
        {
            t = Math.Max(0, Math.Min(1, t));
            return new SharpDX.Color4((float)((a[0] + (b[0] - a[0]) * t) / 255), (float)((a[1] + (b[1] - a[1]) * t) / 255), (float)((a[2] + (b[2] - a[2]) * t) / 255), (float)alpha);
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
            float right = plotRight;
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
        [Display(Name = "Estilo (Clasico / Presion / Lineas)", Order = 7, GroupName = "Convexa")]
        public ConvexaStyle Style { get; set; }

        [Display(Name = "Presion: vencimientos del flujo", Order = 8, GroupName = "Convexa")]
        public ConvexaFlowExpirations FlowExpirations { get; set; }

        [Range(1, 10)]
        [Display(Name = "Presion: niveles arriba y abajo", Order = 9, GroupName = "Convexa")]
        public int PressureLevelsPerSide { get; set; }

        [Range(1, 5)]
        [Display(Name = "Sesiones a mostrar", Order = 10, GroupName = "Convexa")]
        public int Days { get; set; }

        [Display(Name = "Perfil GEX", Order = 11, GroupName = "Convexa")]
        public bool ShowProfile { get; set; }

        [Range(80, 400)]
        [Display(Name = "Perfil GEX: ancho (px)", Order = 12, GroupName = "Convexa")]
        public int ProfileWidth { get; set; }

        [Display(Name = "Convexa URL", Order = 6, GroupName = "Convexa")]
        public string Url { get; set; }
    }
}
