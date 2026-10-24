using System.Buffers;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Threading.Channels;

namespace Convexa.StreamProxy;

/// <summary>One frame waiting to go downstream: a rented buffer and its length.</summary>
public readonly record struct PooledFrame(byte[] Buffer, int Length);

/// <summary>Counters for the per-minute log line; read and reset by <see cref="ProxyStats.TakeSnapshot"/>.</summary>
public sealed class ProxyStats
{
    public long UpstreamFrames;
    public long UpstreamBytes;
    public long Forwarded;
    public long RepeatsDropped;
    public long DownstreamMessages;
    public long MaxQueueDepth;
    public long MaxUpstreamGapTicks;
    public long MaxSendTicks;
    public long QueueDepth;

    public (long Up, long Bytes, long Fwd, long Rep, long Down, long MaxQueue, double MaxGapMs, double MaxSendMs) TakeSnapshot()
    {
        double ms(long ticks) => ticks * 1000.0 / Stopwatch.Frequency;
        return (
            Interlocked.Exchange(ref UpstreamFrames, 0),
            Interlocked.Exchange(ref UpstreamBytes, 0),
            Interlocked.Exchange(ref Forwarded, 0),
            Interlocked.Exchange(ref RepeatsDropped, 0),
            Interlocked.Exchange(ref DownstreamMessages, 0),
            Interlocked.Exchange(ref MaxQueueDepth, Interlocked.Read(ref QueueDepth)),
            ms(Interlocked.Exchange(ref MaxUpstreamGapTicks, 0)),
            ms(Interlocked.Exchange(ref MaxSendTicks, 0)));
    }

    public static void Max(ref long target, long value)
    {
        long current;
        while (value > (current = Interlocked.Read(ref target)) && Interlocked.CompareExchange(ref target, value, current) != current)
        {
        }
    }
}

/// <summary>
/// Pipes ONE downstream client (the Python worker) to ONE upstream connection (the Theta Terminal's local WebSocket),
/// so ThetaData still sees a single client per account. Upstream -> downstream frames go through an unbounded queue:
/// the upstream socket is drained as fast as the network allows (big receive buffer, no per-frame await on the
/// consumer), repeated quotes are dropped on the way, and a slow downstream only grows this process's queue instead
/// of making the Terminal report SLOW CONSUMER. Downstream -> upstream messages (subscriptions) pass through untouched.
/// When either side closes, both are closed: the worker reconnects and re-subscribes exactly as it does today.
/// </summary>
public sealed class ProxySession
{
    private readonly WebSocket _downstream;
    private readonly Uri _upstreamUri;
    private readonly QuoteDeduper _deduper;
    private readonly ProxyStats _stats;
    private readonly int _receiveBufferBytes;
    private readonly Action<string> _log;
    private readonly long _maxQueue;

    public ProxySession(WebSocket downstream, Uri upstreamUri, QuoteDeduper deduper, ProxyStats stats, int receiveBufferBytes, Action<string> log, long maxQueue = 2_000_000)
    {
        _maxQueue = maxQueue;
        _downstream = downstream;
        _upstreamUri = upstreamUri;
        _deduper = deduper;
        _stats = stats;
        _receiveBufferBytes = receiveBufferBytes;
        _log = log;
    }

    public async Task RunAsync(CancellationToken outer)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(outer);
        using var upstream = new ClientWebSocket();
        upstream.Options.KeepAliveInterval = TimeSpan.Zero;
        upstream.Options.SetBuffer(1 << 20, 1 << 16);
        using var handler = new SocketsHttpHandler
        {
            ConnectCallback = async (context, token) =>
            {
                var socket = new Socket(SocketType.Stream, ProtocolType.Tcp) { NoDelay = true, ReceiveBufferSize = _receiveBufferBytes };
                try
                {
                    await socket.ConnectAsync(context.DnsEndPoint, token).ConfigureAwait(false);
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }
            },
        };
        using var invoker = new HttpMessageInvoker(handler);
        await upstream.ConnectAsync(_upstreamUri, invoker, cts.Token).ConfigureAwait(false);
        _log($"upstream connected to {_upstreamUri} (receive buffer {_receiveBufferBytes / 1024} KB)");

        var queue = Channel.CreateUnbounded<PooledFrame>(new UnboundedChannelOptions { SingleReader = true, SingleWriter = true });
        var tasks = new[]
        {
            Task.Run(() => PumpUpstreamAsync(upstream, queue.Writer, cts.Token)),
            Task.Run(() => PumpDownstreamAsync(queue.Reader, cts.Token)),
            Task.Run(() => PumpClientMessagesAsync(upstream, cts.Token)),
        };
        var first = await Task.WhenAny(tasks).ConfigureAwait(false);
        if (first.IsFaulted)
        {
            _log($"session ending: {first.Exception?.GetBaseException().Message}");
        }
        cts.Cancel();
        await CloseQuietly(upstream).ConfigureAwait(false);
        await CloseQuietly(_downstream).ConfigureAwait(false);
        try
        {
            await Task.WhenAll(tasks).ConfigureAwait(false);
        }
        catch
        {
            // already logged / expected cancellation
        }
        while (queue.Reader.TryRead(out var leftover))
        {
            ArrayPool<byte>.Shared.Return(leftover.Buffer);
        }
        Interlocked.Exchange(ref _stats.QueueDepth, 0);
    }

    private async Task PumpUpstreamAsync(ClientWebSocket upstream, ChannelWriter<PooledFrame> writer, CancellationToken token)
    {
        var assembly = new ArrayBufferWriter<byte>(64 * 1024);
        var lastFrame = Stopwatch.GetTimestamp();
        while (!token.IsCancellationRequested)
        {
            var memory = assembly.GetMemory(16 * 1024);
            var result = await upstream.ReceiveAsync(memory, token).ConfigureAwait(false);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                _log("upstream closed by the Terminal");
                writer.TryComplete();
                return;
            }
            assembly.Advance(result.Count);
            if (!result.EndOfMessage)
            {
                continue;
            }
            var now = Stopwatch.GetTimestamp();
            ProxyStats.Max(ref _stats.MaxUpstreamGapTicks, now - lastFrame);
            lastFrame = now;
            Interlocked.Increment(ref _stats.UpstreamFrames);
            Interlocked.Add(ref _stats.UpstreamBytes, assembly.WrittenCount);
            var span = assembly.WrittenSpan;
            if (result.MessageType == WebSocketMessageType.Text && _deduper.IsRepeatedQuote(span, now))
            {
                Interlocked.Increment(ref _stats.RepeatsDropped);
            }
            else
            {
                var buffer = ArrayPool<byte>.Shared.Rent(span.Length);
                span.CopyTo(buffer);
                var depth = Interlocked.Increment(ref _stats.QueueDepth);
                ProxyStats.Max(ref _stats.MaxQueueDepth, depth);
                if (depth > _maxQueue)
                {
                    // Safety valve: a client that stopped reading must not grow this process without limit. Ending the session
                    // makes the worker reconnect and re-subscribe, the same recovery it already does for a dropped Terminal link.
                    ArrayPool<byte>.Shared.Return(buffer);
                    throw new InvalidOperationException($"queue above {_maxQueue} frames: the client is not reading, ending the session");
                }
                writer.TryWrite(new PooledFrame(buffer, span.Length));
            }
            assembly.ResetWrittenCount();
        }
    }

    private async Task PumpDownstreamAsync(ChannelReader<PooledFrame> reader, CancellationToken token)
    {
        await foreach (var frame in reader.ReadAllAsync(token).ConfigureAwait(false))
        {
            Interlocked.Decrement(ref _stats.QueueDepth);
            var started = Stopwatch.GetTimestamp();
            try
            {
                await _downstream.SendAsync(frame.Buffer.AsMemory(0, frame.Length), WebSocketMessageType.Text, true, token).ConfigureAwait(false);
            }
            finally
            {
                ArrayPool<byte>.Shared.Return(frame.Buffer);
            }
            ProxyStats.Max(ref _stats.MaxSendTicks, Stopwatch.GetTimestamp() - started);
            Interlocked.Increment(ref _stats.Forwarded);
        }
    }

    private async Task PumpClientMessagesAsync(ClientWebSocket upstream, CancellationToken token)
    {
        var assembly = new ArrayBufferWriter<byte>(4096);
        while (!token.IsCancellationRequested)
        {
            var result = await _downstream.ReceiveAsync(assembly.GetMemory(4096), token).ConfigureAwait(false);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                _log("downstream client closed");
                return;
            }
            assembly.Advance(result.Count);
            if (!result.EndOfMessage)
            {
                continue;
            }
            Interlocked.Increment(ref _stats.DownstreamMessages);
            await upstream.SendAsync(assembly.WrittenMemory, result.MessageType, true, token).ConfigureAwait(false);
            assembly.ResetWrittenCount();
        }
    }

    private static async Task CloseQuietly(WebSocket socket)
    {
        try
        {
            if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                await socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "proxy session ended", timeout.Token).ConfigureAwait(false);
            }
        }
        catch
        {
            // best effort
        }
        socket.Abort();
    }
}
