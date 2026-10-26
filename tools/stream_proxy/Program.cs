using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.WebSockets;
using System.Text;
using Convexa.StreamProxy;

// Convexa stream proxy: a native reader between the Theta Terminal's local WebSocket and the Python worker.
//   StreamProxy run   [--listen-port 25521] [--upstream ws://127.0.0.1:25520/v1/events] [--refresh 1.0] [--recvbuf-mb 16] [--log-every 60]
//   StreamProxy bench [--rate 70000] [--seconds 20] [--repeat-share 0.85] [--burst-every 5] [--burst-frames 70000] [--client-delay-us 0]
// See README.md next to this file. Nothing here touches Postgres or ThetaData's servers.

var options = Options.Parse(args);
return options.Mode switch
{
    "run" => await Proxy.RunAsync(options),
    "bench" => await Bench.RunAsync(options),
    "dedupe-check" => DedupeCheck.Run(options),
    _ => Usage(),
};

static int Usage()
{
    Console.Error.WriteLine("usage: StreamProxy run|bench [--option value ...] (see README.md)");
    return 2;
}

public sealed record Options(string Mode, Dictionary<string, string> Values)
{
    public static Options Parse(string[] args)
    {
        var mode = args.Length > 0 && !args[0].StartsWith("--", StringComparison.Ordinal) ? args[0] : "run";
        var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i].StartsWith("--", StringComparison.Ordinal))
            {
                values[args[i][2..]] = args[i + 1];
            }
        }
        return new Options(mode, values);
    }

    public string Get(string name, string fallback) => Values.TryGetValue(name, out var v) ? v : fallback;

    public double GetDouble(string name, double fallback) => double.Parse(Get(name, fallback.ToString(CultureInfo.InvariantCulture)), CultureInfo.InvariantCulture);

    public int GetInt(string name, int fallback) => int.Parse(Get(name, fallback.ToString(CultureInfo.InvariantCulture)), CultureInfo.InvariantCulture);
}

public static class Log
{
    public static void Write(string message) =>
        Console.WriteLine($"{DateTime.Now:yyyy-MM-dd HH:mm:ss,fff} {message}");
}

public static class Proxy
{
    /// <summary>The proxy itself: listens on 127.0.0.1:listen-port at /v1/events and serves ONE client at a time.</summary>
    public static async Task<int> RunAsync(Options options, CancellationToken stopToken = default)
    {
        var port = options.GetInt("listen-port", 25521);
        var upstream = new Uri(options.Get("upstream", "ws://127.0.0.1:25520/v1/events"));
        var refresh = options.GetDouble("refresh", 1.0);
        var receiveBuffer = options.GetInt("recvbuf-mb", 16) * 1024 * 1024;
        var logEvery = options.GetInt("log-every", 60);
        var maxQueue = options.GetInt("max-queue", 2_000_000);
        var stats = new ProxyStats();
        var busy = new SemaphoreSlim(1, 1);

        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(k => k.Listen(IPAddress.Loopback, port));
        var app = builder.Build();
        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.Zero });
        app.Map("/v1/events", async context =>
        {
            if (!context.WebSockets.IsWebSocketRequest)
            {
                context.Response.StatusCode = 400;
                return;
            }
            if (!await busy.WaitAsync(0))
            {
                Log.Write("refused a second client: the Terminal allows one stream client per account");
                context.Response.StatusCode = 409;
                return;
            }
            try
            {
                using var downstream = await context.WebSockets.AcceptWebSocketAsync(new WebSocketAcceptContext { KeepAliveInterval = TimeSpan.Zero, DangerousEnableCompression = false });
                Log.Write($"client connected from {context.Connection.RemoteIpAddress}:{context.Connection.RemotePort}");
                // A new deduper per session: the first quote of every contract after a (re)connect always goes through.
                var session = new ProxySession(downstream, upstream, new QuoteDeduper(refresh), stats, receiveBuffer, Log.Write, maxQueue);
                await session.RunAsync(context.RequestAborted);
                Log.Write("client session ended");
            }
            catch (Exception exception)
            {
                Log.Write($"client session failed: {exception.GetBaseException().Message}");
            }
            finally
            {
                busy.Release();
            }
        });

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(stopToken);
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
        var reporter = Task.Run(async () =>
        {
            while (!cts.IsCancellationRequested)
            {
                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(logEvery), cts.Token);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                var s = stats.TakeSnapshot();
                var repeatPct = s.Up > 0 ? 100.0 * s.Rep / s.Up : 0.0;
                Log.Write(string.Create(CultureInfo.InvariantCulture,
                    $"proxy: {s.Up / (double)logEvery:F0} frames/s in ({s.Bytes / (double)logEvery / 1e6:F1} MB/s), {s.Fwd / (double)logEvery:F0}/s forwarded, " +
                    $"{s.Rep / (double)logEvery:F0}/s repeated quotes dropped ({repeatPct:F1} %), {s.Down} client messages, max queue {s.MaxQueue}, " +
                    $"max upstream gap {s.MaxGapMs:F0} ms, max downstream send {s.MaxSendMs:F1} ms"));
            }
        });
        Log.Write($"stream proxy listening on ws://127.0.0.1:{port}/v1/events -> {upstream} (repeat refresh {refresh:F1} s)");
        await app.StartAsync(cts.Token);
        try
        {
            await Task.Delay(Timeout.Infinite, cts.Token);
        }
        catch (OperationCanceledException)
        {
        }
        await app.StopAsync();
        await reporter;
        return 0;
    }
}

/// <summary>
/// Offline bench, everything in this one process on loopback: a fake Terminal (paced frames with a repeat share, plus a
/// burst sent as fast as possible every few seconds, like the Terminal's catch-up after its ~1 s silences), the proxy,
/// and a client that reads (optionally slowly). It reports how fast the proxy drained the bursts and what reached the client.
/// </summary>
public static class Bench
{
    public static async Task<int> RunAsync(Options options)
    {
        var rate = options.GetInt("rate", 70000);
        var seconds = options.GetInt("seconds", 20);
        var repeatShare = options.GetDouble("repeat-share", 0.85);
        var burstEvery = options.GetInt("burst-every", 5);
        var burstFrames = options.GetInt("burst-frames", 70000);
        var clientDelayUs = options.GetInt("client-delay-us", 0);
        const int terminalPort = 26530, proxyPort = 26531, contracts = 764;

        var sent = 0L;
        var burstRates = new List<double>();
        var feedDone = new TaskCompletionSource();
        var terminal = WebApplication.CreateSlimBuilder();
        terminal.Logging.ClearProviders();
        terminal.WebHost.ConfigureKestrel(k => k.Listen(IPAddress.Loopback, terminalPort));
        var terminalApp = terminal.Build();
        terminalApp.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.Zero });
        terminalApp.Map("/v1/events", async context =>
        {
            using var ws = await context.WebSockets.AcceptWebSocketAsync();
            var buffer = new byte[4096];
            await ws.ReceiveAsync(buffer, CancellationToken.None); // the client's first subscription starts the feed
            var rng = new Random(7);
            var asks = new double[contracts];
            for (var i = 0; i < contracts; i++)
            {
                asks[i] = 1.09;
            }
            var index = 0;
            byte[] NextFrame()
            {
                var c = index++ % contracts;
                string json;
                if (index % 100 == 0)
                {
                    json = $"{{\"header\":{{\"type\":\"TRADE\",\"status\":\"CONNECTED\"}},\"contract\":{{\"security_type\":\"OPTION\",\"root\":\"SPXW\",\"expiration\":20261023,\"strike\":{7000000 + c * 5000},\"right\":\"{(c % 2 == 0 ? "C" : "P")}\"}},\"trade\":{{\"ms_of_day\":{index},\"sequence\":{index},\"size\":1,\"condition\":18,\"price\":1.09,\"exchange\":5,\"date\":20261023}}}}";
                }
                else
                {
                    if (rng.NextDouble() >= repeatShare)
                    {
                        asks[c] = Math.Round(asks[c] + (rng.Next(2) == 0 ? 0.05 : -0.05), 2);
                    }
                    json = string.Create(CultureInfo.InvariantCulture, $"{{\"header\":{{\"type\":\"QUOTE\",\"status\":\"CONNECTED\"}},\"contract\":{{\"security_type\":\"OPTION\",\"root\":\"SPXW\",\"expiration\":20261023,\"strike\":{7000000 + c * 5000},\"right\":\"{(c % 2 == 0 ? "C" : "P")}\"}},\"quote\":{{\"ms_of_day\":{index},\"bid_size\":{rng.Next(1, 200)},\"bid_exchange\":5,\"bid\":{asks[c] - 0.1},\"bid_condition\":50,\"ask_size\":{rng.Next(1, 200)},\"ask_exchange\":5,\"ask\":{asks[c]},\"ask_condition\":50,\"date\":20261023}}}}");
                }
                return Encoding.UTF8.GetBytes(json);
            }
            var clock = Stopwatch.StartNew();
            var nextBurst = burstEvery;
            while (clock.Elapsed.TotalSeconds < seconds)
            {
                if (burstEvery > 0 && clock.Elapsed.TotalSeconds >= nextBurst)
                {
                    nextBurst += burstEvery;
                    var started = Stopwatch.GetTimestamp();
                    for (var i = 0; i < burstFrames; i++)
                    {
                        await ws.SendAsync(NextFrame(), WebSocketMessageType.Text, true, CancellationToken.None);
                    }
                    var elapsed = Stopwatch.GetElapsedTime(started).TotalSeconds;
                    burstRates.Add(burstFrames / elapsed);
                    sent += burstFrames;
                }
                var due = (long)(clock.Elapsed.TotalSeconds * rate) - sent + burstRates.Count * (long)burstFrames;
                for (var i = 0; i < Math.Min(due, 2000); i++)
                {
                    await ws.SendAsync(NextFrame(), WebSocketMessageType.Text, true, CancellationToken.None);
                    sent++;
                }
                if (due <= 0)
                {
                    await Task.Delay(1);
                }
            }
            feedDone.TrySetResult();
            await Task.Delay(3000);
            await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "bench done", CancellationToken.None);
        });
        await terminalApp.StartAsync();

        using var proxyStop = new CancellationTokenSource();
        var proxyOptions = new Options("run", new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["listen-port"] = proxyPort.ToString(CultureInfo.InvariantCulture),
            ["upstream"] = $"ws://127.0.0.1:{terminalPort}/v1/events",
            ["log-every"] = "5",
        });
        var proxyTask = Proxy.RunAsync(proxyOptions, proxyStop.Token);
        await Task.Delay(500);

        var received = 0L;
        var receivedQuotes = 0L;
        using var client = new ClientWebSocket();
        await client.ConnectAsync(new Uri($"ws://127.0.0.1:{proxyPort}/v1/events"), CancellationToken.None);
        await client.SendAsync(Encoding.UTF8.GetBytes("{\"msg_type\":\"STREAM\",\"sec_type\":\"OPTION\",\"req_type\":\"QUOTE\",\"add\":true,\"id\":1}"), WebSocketMessageType.Text, true, CancellationToken.None);
        var receiveBuffer = new byte[1 << 16];
        var clientClock = Stopwatch.StartNew();
        while (true)
        {
            WebSocketReceiveResult result;
            try
            {
                result = await client.ReceiveAsync(receiveBuffer, CancellationToken.None);
            }
            catch (WebSocketException)
            {
                break;
            }
            if (result.MessageType == WebSocketMessageType.Close)
            {
                break;
            }
            if (result.EndOfMessage)
            {
                received++;
                if (receiveBuffer.AsSpan(0, Math.Min(40, result.Count)).IndexOf("QUOTE"u8) >= 0)
                {
                    receivedQuotes++;
                }
                if (clientDelayUs > 0)
                {
                    var until = Stopwatch.GetTimestamp() + clientDelayUs * Stopwatch.Frequency / 1_000_000;
                    while (Stopwatch.GetTimestamp() < until)
                    {
                    }
                }
            }
        }
        var clientSeconds = clientClock.Elapsed.TotalSeconds;
        proxyStop.Cancel();
        await proxyTask;
        await terminalApp.StopAsync();
        Console.WriteLine(string.Create(CultureInfo.InvariantCulture,
            $"BENCH rate {rate}/s for {seconds} s, repeat share {repeatShare:F2}, bursts of {burstFrames} frames every {burstEvery} s, client delay {clientDelayUs} us/frame"));
        Console.WriteLine(string.Create(CultureInfo.InvariantCulture,
            $"BENCH terminal sent {sent:N0} frames; client received {received:N0} ({100.0 * received / Math.Max(1, sent):F1} % of sent; quotes {receivedQuotes:N0}) in {clientSeconds:F1} s"));
        Console.WriteLine("BENCH burst drain rates (frames/s the proxy took from the Terminal while it sent as fast as it could): " +
            string.Join(", ", burstRates.Select(r => r.ToString("N0", CultureInfo.InvariantCulture))));
        return 0;
    }
}

/// <summary>
/// Parity check with the Python rule: reads lines "seconds<TAB>frame json" and prints one line per frame, 1 = repeated
/// quote (dropped) / 0 = forwarded. tests/test_stream_proxy_parity.py feeds the same lines to
/// _ProcessorState.is_repeated_quote and compares.
/// </summary>
public static class DedupeCheck
{
    public static int Run(Options options)
    {
        var deduper = new QuoteDeduper(options.GetDouble("refresh", 1.0));
        using var input = new StreamReader(options.Get("file", "frames.tsv"), Encoding.UTF8);
        var output = new StringBuilder();
        string? line;
        while ((line = input.ReadLine()) is not null)
        {
            var tab = line.IndexOf('\t');
            if (tab < 0)
            {
                continue;
            }
            var seconds = double.Parse(line[..tab], CultureInfo.InvariantCulture);
            var ticks = (long)(seconds * Stopwatch.Frequency);
            output.Append(deduper.IsRepeatedQuote(Encoding.UTF8.GetBytes(line[(tab + 1)..]), ticks) ? '1' : '0').Append('\n');
        }
        Console.Write(output);
        return 0;
    }
}
