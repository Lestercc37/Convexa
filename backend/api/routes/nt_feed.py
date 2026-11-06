"""GET /api/v1/feeds/nt/{future}: Convexa's levels and heatmap for Lester's NinjaTrader indicator 'Convexa Niveles' (2026-11-06).

Lester's plan (2026-11-06): he is the only futures trader of the group and already has NinjaTrader with a CME license, so the ES / NQ / RTY
charts live in NinjaTrader; Convexa sends them its levels (Gamma Flip, walls, ABS, max pain, anchored VWAP, expected move, ATR bands) and
the whole heatmap of the tab and figure he picks, already in futures points, and NinjaTrader sends back only where absorption / imbalance
happened. CME data never leaves NinjaTrader; what travels here is Convexa's own data.

Not behind the browser session (NinjaTrader has none): the anchor's shared key (X-Convexa-Key, futures_anchor_feed._key_matches).
Plain text, one fact per line, so the NinjaScript side needs no JSON library:

    convexa 1
    symbol ES
    as_of 2026-11-06T15:01:02Z
    basis 31.20 ninjatrader            (ES / NQ: future - index in use; RTY: 'ratio 10.0412 ninjatrader')
    level gamma_flip 7412.50           (one line per level; a level with no value is left out)
    heatmap 0dte_volume gamma 10       (view, figure, bucket seconds)
    strikes 7380.00 7385.00 ...
    col 1762441210 1.23e+09 _ -4.5e+08 ...   (epoch seconds of the bucket start, then one value per strike of the LAST `strikes` line; _ = none)
    end

Several sessions (2026-11-06, Lester: "que el heatmap esté permanentemente en NT sin cortarse por sesión"): `days` sessions back (today
included), each one a `strikes` line followed by its `col` lines, each shifted by ITS OWN basis (a past day: that day's 09:31 anchor minus the
index's first minute; today: the live basis), so nothing has to be thrown away when a strike list changes. The pressure works the same way
(`pstrikes` then its `prow` lines), and before today's flow exists the latest sessions with flow are sent: the map never goes blank overnight
or on a weekend. `since` / `psince` only ask for today's new columns / minutes.

`since` (epoch seconds) asks only for the columns from that bucket on (the indicator's incremental poll, as the dashboard does).
ES / NQ come from SPX / NDX shifted by futures_proxy.future_level_offset (NinjaTrader's live basis while fresh, else the 09:31 anchor);
RTY from IWM multiplied by the live RTY / IWM ratio from the 'Convexa Feed' bars (none without them: an ETF has no additive basis)."""

from __future__ import annotations

import statistics
import time as clock
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from backend.api.routes.futures_anchor_feed import _key_matches
from backend.core.container import Container
from backend.domain.entities import GammaView
from backend.domain.use_cases.flow_pressure import HALF_LIFE_MINUTES, pressure_matrix
from backend.domain.use_cases.futures_live import live_ratio, normalize_live_future
from backend.domain.use_cases.futures_proxy import resolve_future_offset_async
from backend.domain.use_cases.gamma import get_gamma_exposure_async
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.read_models import build_market_snapshot_async, get_gamma_heatmap_async
from backend.domain.use_cases.strike_flow import flow_source_for, pick_expirations, price_mapping

router = APIRouter(tags=["feeds"])

HEATMAP_METRICS = ("gamma", "absolute_gamma", "delta", "vanna", "charm", "call_gamma", "put_gamma", "total_gamma")
FAST_BUCKET_SECONDS = {"NDX": 30}
CYCLE_BUCKET_SECONDS = 60
SOURCE_SYMBOL = {"ES": "ES", "NQ": "NQ", "RTY": "IWM"}
# The day's per-minute strike flow is one grouped query (SPX ~60 ms at midday, ~150 ms by the close, measured 2026-11-06): shared for a few
# seconds by every chart asking for the same flow source and expirations.
PRESSURE_CACHE_SECONDS = 5.0
_pressure_cache: dict[tuple[str, str, date], tuple[float, list, list]] = {}
# A past session's heatmap never changes: kept for PAST_DAY_CACHE_SECONDS (the indicator re-reads everything every 5 minutes).
PAST_DAY_CACHE_SECONDS = 600.0
_past_heatmap_cache: dict[tuple[str, str, str, date], tuple[float, object]] = {}
INDEX_OF = {"ES": "SPX", "NQ": "NDX", "RTY": "IWM"}


def _recent_weekdays(today: date, count: int) -> list[date]:
    """`count` weekdays ending with `today` (or the last weekday before a weekend), oldest first. Holidays simply have no data and are skipped."""
    days: list[date] = []
    day = today
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


async def _day_shift(storage, future: str, day: date, today: date, today_ratio: float, today_offset: float) -> tuple[float, float]:
    """(ratio, offset) turning `day`'s index strikes into futures points: today the live basis; a past day its own 09:31 anchor minus the index's
    first-minute median (the basis shrinks toward expiry, so today's would misplace older days by a few points); RTY always today's ratio."""
    if day == today or future == "RTY":
        return today_ratio, today_offset
    anchor = await storage.get_future_price_anchor(future, day)
    day_open = datetime.combine(day, time(9, 30), EASTERN_TIME)
    readings = await storage.get_price_history(INDEX_OF[future], day_open, day_open + timedelta(seconds=59))
    if anchor is None or not readings:
        return today_ratio, today_offset
    return 1.0, float(anchor) - statistics.median(float(reading.price) for reading in readings)


def _num(value: Decimal | float | None, scale: float = 1.0) -> str | None:
    if value is None:
        return None
    return f"{float(value) * scale:.2f}"


def _cell(value: Decimal | None) -> str:
    return "_" if value is None else f"{float(value):.6g}"


@router.get("/feeds/nt/{future}", response_class=PlainTextResponse)
async def nt_levels(
    future: str,
    request: Request,
    view: GammaView = Query(default="0dte_volume"),
    metric: str = Query(default="gamma"),
    since: int | None = Query(default=None, description="epoch seconds: only the heatmap columns from that bucket on"),
    heatmap: bool = Query(default=True),
    pressure: bool = Query(default=False, description="add the options-flow pressure per strike and minute (blue heatmap)"),
    pexp: str = Query(default="0dte", pattern="^(0dte|0-2dte|all)$"),
    psince: int | None = Query(default=None, description="epoch seconds: only the pressure minutes from that one on"),
    days: int = Query(default=2, ge=1, le=5, description="sessions sent on a full read (today included)"),
    x_convexa_key: str | None = Header(default=None),
) -> PlainTextResponse:
    if not _key_matches(x_convexa_key):
        raise HTTPException(status_code=401, detail="bad key")
    name = normalize_live_future(future)
    if name is None:
        raise HTTPException(status_code=404, detail="ES / NQ / RTY (or the micros) only")
    if metric not in HEATMAP_METRICS:
        raise HTTPException(status_code=422, detail=f"metric must be one of {', '.join(HEATMAP_METRICS)}")
    container: Container = request.app.state.container
    storage = container.async_market_storage
    symbol = SOURCE_SYMBOL[name]
    scale = 1.0
    today_offset = 0.0
    lines = ["convexa 1", f"symbol {name}"]

    if name == "RTY":
        ratio = live_ratio(await storage.get_futures_live_bars("RTY", datetime.now(UTC) - timedelta(minutes=20)), datetime.now(UTC))
        if ratio is None:
            raise HTTPException(status_code=409, detail="no fresh RTY bars from 'Convexa Feed' yet: the RTY / IWM ratio is unknown")
        scale = ratio[0]
        lines.append(f"basis_ratio {scale:.6f} ninjatrader")
    else:
        index = {"ES": "SPX", "NQ": "NDX"}[name]
        offset, source, _, _ = await resolve_future_offset_async(storage, name, index)
        if offset is None:
            raise HTTPException(status_code=409, detail=f"{name} has no basis yet: no 09:31 anchor and no NinjaTrader bars")
        lines.append(f"basis {float(offset):.2f} {source}")
        today_offset = float(offset)

    gamma = await get_gamma_exposure_async(storage, symbol, view=view)
    snapshot = await build_market_snapshot_async(storage, symbol)
    lines.insert(2, f"as_of {gamma.as_of.astimezone(UTC).isoformat().replace('+00:00', 'Z')}")
    levels = {
        "gamma_flip": gamma.gamma_flip,
        "call_wall": gamma.call_wall,
        "put_wall": gamma.put_wall,
        "abs": gamma.absolute_gamma_strike or None,
        "max_pain": gamma.max_pain or None,
        "price": snapshot.price,
    }
    if snapshot.anchored_vwap is not None and not snapshot.anchored_vwap.not_applicable:
        levels["avwap"] = snapshot.anchored_vwap.value
    if snapshot.expected_move is not None:
        levels["em_upper"] = snapshot.expected_move.upper_bound
        levels["em_lower"] = snapshot.expected_move.lower_bound
    if snapshot.atr_range is not None:
        levels["atr_outer_upper"] = snapshot.atr_range.outer_upper_band
        levels["atr_inner_upper"] = snapshot.atr_range.inner_upper_band
        levels["atr_inner_lower"] = snapshot.atr_range.inner_lower_band
        levels["atr_outer_lower"] = snapshot.atr_range.outer_lower_band
    for key, value in levels.items():
        text = _num(value, scale)
        if text is not None:
            lines.append(f"level {key} {text}")
    lines.append(f"dealer {'long_gamma' if gamma.net_gamma >= 0 else 'short_gamma'}")

    if heatmap:
        lines.extend(await _heatmap_lines(storage, name, view, metric, since, days, scale, today_offset))
    if pressure:
        lines.extend(await _pressure_lines(storage, name, scale, today_offset, pexp, psince, days))
    lines.append("end")
    return PlainTextResponse("\n".join(lines) + "\n")


async def _heatmap_lines(storage, future: str, view: str, metric: str, since: int | None, days: int, today_ratio: float, today_offset: float) -> list[str]:
    """One `strikes` line + its `col` lines per session (oldest first), each shifted by that session's own basis. Today from the fast layer
    (the cycle's minutes when it has none), past sessions from the cycle's minutes (cached). With `since`: today's new columns only."""
    now = datetime.now(UTC)
    index_symbol = INDEX_OF[future]
    today = now.astimezone(EASTERN_TIME).date()
    fast_bucket = FAST_BUCKET_SECONDS.get(index_symbol, 10)
    lines = [f"heatmap {view} {metric} {fast_bucket}"]
    for day in [today] if since is not None else _recent_weekdays(today, days):
        start = datetime.combine(day, time(8, 30), EASTERN_TIME)
        end = min(now, datetime.combine(day, time(16, 15), EASTERN_TIME))
        if since is not None:
            start = max(start, datetime.fromtimestamp(since, UTC))
        if end <= start:
            continue
        if day == today:
            matrix = await get_gamma_heatmap_async(storage, index_symbol, view, metric, start, end, fast_bucket, "fast")
            bucket = fast_bucket
            if not matrix.columns:
                matrix = await get_gamma_heatmap_async(storage, index_symbol, view, metric, start, end, CYCLE_BUCKET_SECONDS, "cycle")
                bucket = CYCLE_BUCKET_SECONDS
        else:
            key = (index_symbol, view, metric, day)
            cached = _past_heatmap_cache.get(key)
            if cached is not None and clock.monotonic() - cached[0] < PAST_DAY_CACHE_SECONDS:
                matrix = cached[1]
            else:
                matrix = await get_gamma_heatmap_async(storage, index_symbol, view, metric, start, end, CYCLE_BUCKET_SECONDS, "cycle")
                _past_heatmap_cache[key] = (clock.monotonic(), matrix)
            bucket = CYCLE_BUCKET_SECONDS
        if not matrix.columns:
            continue
        ratio, offset = await _day_shift(storage, future, day, today, today_ratio, today_offset)
        lines.append("strikes " + " ".join(f"{float(strike) * ratio + offset:.2f}" for strike in matrix.strikes))
        for column in matrix.columns:
            epoch = int(column.as_of.timestamp()) // bucket * bucket
            lines.append(f"col {epoch} " + " ".join(_cell(value) for value in column.values))
    return lines


async def _pressure_day(storage, source: str, pexp: str, day: date, now: datetime) -> tuple[list[float], list[tuple[datetime, list[float]]]]:
    day_open = datetime.combine(day, time(9, 30), EASTERN_TIME)
    end = min(now, day_open + timedelta(hours=6, minutes=30))
    if end <= day_open:
        return [], []
    cached = _pressure_cache.get((source, pexp, day))
    # Today's matrix grows by the minute (shared for PRESSURE_CACHE_SECONDS); a past day's never changes.
    ttl = PRESSURE_CACHE_SECONDS if day == now.astimezone(EASTERN_TIME).date() else PAST_DAY_CACHE_SECONDS
    if cached is not None and clock.monotonic() - cached[0] < ttl:
        return cached[1], cached[2]
    traded = await storage.get_strike_flow_expirations(source, day_open, end)
    expirations = pick_expirations(pexp, traded, day)
    rows = await storage.get_strike_flow_minutes(source, day_open, end, expirations) if expirations != [] else []
    strikes, minutes = pressure_matrix(rows, day_open, end + timedelta(minutes=1))
    _pressure_cache[(source, pexp, day)] = (clock.monotonic(), strikes, minutes)
    return strikes, minutes


async def _pressure_lines(
    storage, future: str, today_ratio: float, today_offset: float, pexp: str, psince: int | None, days: int
) -> list[str]:
    """`pressure <flow source> <expirations> <half life>`, then per session a `pstrikes` line (the flow source's strikes in futures points) and
    one `prow <epoch> ...` per minute: the decayed signed flow per strike (use_cases/flow_pressure.py). ES reads SPX, NQ QQQ, RTY IWM. A full read
    sends the last `days` sessions with flow (so the map is there before the open and on weekends); `psince` sends today's new minutes only."""
    now = datetime.now(UTC)
    today = now.astimezone(EASTERN_TIME).date()
    source = flow_source_for(future, None) if future != "RTY" else "IWM"
    lines = [f"pressure {source} {pexp} {HALF_LIFE_MINUTES:g}"]
    # The flow source's strikes -> futures points: ES = SPX strike + basis; NQ = QQQ strike x NDX / QQQ + basis; RTY = IWM strike x ratio.
    if future == "RTY":
        today_ratio_source, today_offset_source = today_ratio, 0.0
    else:
        mapped_ratio, _ = await price_mapping(storage, future, source, now)
        if mapped_ratio is None:
            return lines
        today_ratio_source, today_offset_source = mapped_ratio, today_offset
    for day in [today] if psince is not None else _recent_weekdays(today, days):
        strikes, minutes = await _pressure_day(storage, source, pexp, day, now)
        if not strikes:
            continue
        _, offset = await _day_shift(storage, future, day, today, 1.0, today_offset_source)
        lines.append("pstrikes " + " ".join(f"{strike * today_ratio_source + offset:.2f}" for strike in strikes))
        first = datetime.fromtimestamp(psince, UTC) if psince is not None else datetime.combine(day, time(9, 30), EASTERN_TIME)
        for minute, values in minutes:
            if minute >= first:
                lines.append(f"prow {int(minute.timestamp())} " + " ".join(f"{value:.4g}" if value else "0" for value in values))
    return lines
