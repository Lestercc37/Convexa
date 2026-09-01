"""Lee-Ready (1991) trade-direction classification for individual trades.

Lee, C. M. C., & Ready, M. J. (1991). "Inferring Trade Direction from
Intraday Data." The Journal of Finance, 46(2), 733-746.

Unlike Bulk Volume Classification (calculate_bvc.py), which estimates a
probabilistic buy/sell split for a whole period's aggregated volume from
price movement alone, Lee-Ready classifies each individual trade against
the bid/ask quote actually prevailing when it printed — a real market
microstructure signal, not a statistical proxy. It requires trade-level
data (price + a contemporaneous quote), which is why it replaces BVC only
for providers with a live Trade Stream + Quote Stream (see
stream_whale_alerts.py); process()/BVC remain the fallback for providers
that only ever see periodic OptionChain snapshots (see flow.py).
"""

from decimal import Decimal

from backend.domain.entities import Side


def classify_trade_side(
    price: Decimal,
    bid: Decimal | None,
    ask: Decimal | None,
    previous_price: Decimal | None,
    previous_side: Side,
) -> Side:
    """Classify one trade as buyer- or seller-initiated (Lee-Ready, 1991).

    Quote rule (the primary rule): a trade printing above the bid/ask
    midpoint is buyer-initiated, below it is seller-initiated.

    Tick rule (tie-break, only when the trade prints exactly at the
    midpoint): compare against the previous trade's price — higher means
    buyer-initiated, lower means seller-initiated, and an exact repeat
    ("zero tick") carries forward `previous_side` rather than guessing.

    No prevailing quote yet for this contract (e.g. right at startup,
    before its first Quote Stream message arrives) returns `Side.UNKNOWN`
    — same "no informative signal yet" convention `calculate_bvc_split`
    documents for `sigma == 0`, and `calculate_iv_rank` for a flat
    window: neutral, not a guess. The caller (WhaleAlertsEngine.
    process_trade) treats `Side.UNKNOWN` as a neutral 50/50 volume split,
    mirroring BVC's own neutral fallback.
    """
    if bid is None or ask is None:
        return Side.UNKNOWN

    midpoint = (bid + ask) / 2
    if price > midpoint:
        return Side.BUY
    if price < midpoint:
        return Side.SELL

    if previous_price is None:
        return Side.UNKNOWN
    if price > previous_price:
        return Side.BUY
    if price < previous_price:
        return Side.SELL
    return previous_side
