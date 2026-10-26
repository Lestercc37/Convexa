using System.Text;
using System.Text.Json;

namespace Convexa.StreamProxy;

/// <summary>
/// The same rule as backend/stream_processor_worker.py's _ProcessorState.is_repeated_quote, moved in front of Python:
/// a QUOTE frame whose bid AND ask equal the last ones forwarded for the same contract (root, expiration, strike,
/// right), forwarded less than <see cref="RefreshSeconds"/> ago, is a size-only update and is dropped. About 85 % of
/// the open's frames are such repeats; the processor already dropped them, but only after the worker had read and
/// relayed them and the processor had parsed them. Every other frame (TRADE, STATUS, REQ_RESPONSE, a QUOTE that
/// cannot be parsed) is forwarded untouched.
/// </summary>
public sealed class QuoteDeduper
{
    private readonly Dictionary<string, (double? Bid, double? Ask, long Ticks)> _last = new(4096);
    private readonly long _refreshTicks;

    public QuoteDeduper(double refreshSeconds = 1.0)
    {
        RefreshSeconds = refreshSeconds;
        _refreshTicks = (long)(refreshSeconds * System.Diagnostics.Stopwatch.Frequency);
    }

    public double RefreshSeconds { get; }

    public int TrackedContracts => _last.Count;

    /// <summary>True when the frame is a repeated quote and must NOT be forwarded.</summary>
    public bool IsRepeatedQuote(ReadOnlySpan<byte> frame, long nowTicks)
    {
        if (!TryReadQuote(frame, out var key, out var bid, out var ask))
        {
            return false;
        }
        if (_last.TryGetValue(key, out var last) && last.Bid == bid && last.Ask == ask && nowTicks - last.Ticks < _refreshTicks)
        {
            return true;
        }
        _last[key] = (bid, ask, nowTicks);
        return false;
    }

    /// <summary>
    /// Reads header.type, contract.{root, expiration, strike, right} and quote.{bid, ask} from one frame. Returns false for
    /// anything that is not a well-formed QUOTE object (those frames are always forwarded). Missing bid/ask read as null,
    /// like Python's dict.get, so two quotes both missing a bid still compare equal.
    /// </summary>
    public static bool TryReadQuote(ReadOnlySpan<byte> frame, out string key, out double? bid, out double? ask)
    {
        key = string.Empty;
        bid = null;
        ask = null;
        string? type = null, root = null, expiration = null, strike = null, right = null;
        try
        {
            var reader = new Utf8JsonReader(frame, new JsonReaderOptions { AllowTrailingCommas = false, CommentHandling = JsonCommentHandling.Disallow });
            if (!reader.Read() || reader.TokenType != JsonTokenType.StartObject)
            {
                return false;
            }
            string? section = null;
            string? property = null;
            while (reader.Read())
            {
                switch (reader.TokenType)
                {
                    case JsonTokenType.PropertyName:
                        if (reader.CurrentDepth == 1)
                        {
                            section = reader.GetString();
                            property = null;
                        }
                        else if (reader.CurrentDepth == 2)
                        {
                            property = reader.GetString();
                        }
                        else
                        {
                            property = null;
                        }
                        break;
                    case JsonTokenType.StartObject:
                    case JsonTokenType.StartArray:
                        if (reader.CurrentDepth >= 2)
                        {
                            reader.Skip();
                        }
                        break;
                    case JsonTokenType.String:
                    case JsonTokenType.Number:
                    case JsonTokenType.True:
                    case JsonTokenType.False:
                    case JsonTokenType.Null:
                        if (reader.CurrentDepth != 2 || property is null)
                        {
                            break;
                        }
                        switch (section)
                        {
                            case "header" when property == "type":
                                type = reader.TokenType == JsonTokenType.String ? reader.GetString() : null;
                                break;
                            case "contract":
                                switch (property)
                                {
                                    case "root": root = Raw(ref reader); break;
                                    case "expiration": expiration = Raw(ref reader); break;
                                    case "strike": strike = Raw(ref reader); break;
                                    case "right": right = Raw(ref reader); break;
                                }
                                break;
                            case "quote":
                                if (property == "bid")
                                {
                                    bid = Number(ref reader);
                                }
                                else if (property == "ask")
                                {
                                    ask = Number(ref reader);
                                }
                                break;
                        }
                        break;
                }
            }
        }
        catch (JsonException)
        {
            return false;
        }
        if (type != "QUOTE")
        {
            return false;
        }
        key = string.Concat(root ?? "~", "|", expiration ?? "~", "|", strike ?? "~", "|", right ?? "~");
        return true;
    }

    private static string Raw(ref Utf8JsonReader reader) =>
        reader.TokenType == JsonTokenType.String ? reader.GetString() ?? "" : Encoding.UTF8.GetString(reader.ValueSpan);

    private static double? Number(ref Utf8JsonReader reader) =>
        reader.TokenType == JsonTokenType.Number && reader.TryGetDouble(out var value) ? value : null;
}
