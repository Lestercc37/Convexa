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
    col 1762441210 1.23e+09 _ -4.5e+08 ...   (epoch seconds of the bucket start, then one value per strike; _ = none)
    end

`since` (epoch seconds) asks only for the columns from that bucket on (the indicator's incremental poll, as the dashboard does).
ES / NQ come from SPX / NDX shifted by futures_proxy.future_level_offset (NinjaTrader's live basis while fresh, else the 09:31 anchor);
RTY from IWM multiplied by the live RTY / IWM ratio from the 'Convexa Feed' bars (none without them: an ETF has no additive basis)."""

from __future__ import annotations

import time as clock
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from backend.api.routes.futures_anchor_feed import _key_matches
from backend.core.container import Container
from backend.domain.entities import GammaView
from backend.domain.use_cases.flow_pressure import HALF_LIFE_MINUTES, pressure_matrix
from backend.domain.use_cases.futures_live import live_ratio, normalize_live_future
from backend.domain.use_cases.futures_proxy import _anchor_offset_async, _with_live_basis, live_future_basis_async
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
_pressure_cache: dict[tuple[str, str], tuple[float, list, list]] = {}


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
    lines = ["convexa 1", f"symbol {name}"]

    if name == "RTY":
        ratio = live_ratio(await storage.get_futures_live_bars("RTY", datetime.now(UTC) - timedelta(minutes=20)), datetime.now(UTC))
        if ratio is None:
            raise HTTPException(status_code=409, detail="no fresh RTY bars from 'Convexa Feed' yet: the RTY / IWM ratio is unknown")
        scale = ratio[0]
        lines.append(f"basis_ratio {scale:.6f} ninjatrader")
    else:
        index = {"ES": "SPX", "NQ": "NDX"}[name]
        anchor = await _anchor_offset_async(storage, name, index)
        live = await live_future_basis_async(storage, name)
        offset = _with_live_basis(name, anchor, live)
        if offset is None:
            raise HTTPException(status_code=409, detail=f"{name} has no basis yet: no 09:31 anchor and no fresh NinjaTrader bars")
        lines.append(f"basis {float(offset):.2f} {'anchor' if offset == anchor else 'ninjatrader'}")

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
        now = datetime.now(UTC)
        session_start = datetime.combine(now.astimezone(EASTERN_TIME).date(), time(8, 30), EASTERN_TIME)
        start = datetime.fromtimestamp(since, UTC) if since is not None else session_start
        start = max(start, session_start)
        index_symbol = {"ES": "SPX", "NQ": "NDX", "RTY": "IWM"}[name]
        fast_bucket = FAST_BUCKET_SECONDS.get(index_symbol, 10)
        matrix = await get_gamma_heatmap_async(storage, symbol, view, metric, start, now, fast_bucket, "fast")
        bucket = fast_bucket
        if not matrix.columns:
            matrix = await get_gamma_heatmap_async(storage, symbol, view, metric, start, now, CYCLE_BUCKET_SECONDS, "cycle")
            bucket = CYCLE_BUCKET_SECONDS
        lines.append(f"heatmap {view} {metric} {bucket}")
        lines.append("strikes " + " ".join(_num(strike, scale) for strike in matrix.strikes))
        for column in matrix.columns:
            epoch = int(column.as_of.timestamp()) // bucket * bucket
            lines.append(f"col {epoch} " + " ".join(_cell(value) for value in column.values))
    if pressure:
        lines.extend(await _pressure_lines(storage, name, scale, pexp, psince))
    lines.append("end")
    return PlainTextResponse("\n".join(lines) + "\n")


async def _pressure_lines(storage, future: str, rty_ratio: float, pexp: str, psince: int | None) -> list[str]:
    """`pressure <flow source> <expirations> <half life>`, `pstrikes` (the flow source's strikes in futures points) and one `prow <epoch> ...` per
    minute since 09:30 (or since `psince`): the decayed signed flow per strike (use_cases/flow_pressure.py). ES reads SPX, NQ QQQ, RTY IWM."""
    now = datetime.now(UTC)
    source = flow_source_for(future, None) if future != "RTY" else "IWM"
    day_open = datetime.combine(now.astimezone(EASTERN_TIME).date(), time(9, 30), EASTERN_TIME)
    end = min(now, day_open + timedelta(hours=6, minutes=30))
    if end <= day_open:
        return [f"pressure {source} {pexp} {HALF_LIFE_MINUTES:g}", "pstrikes"]
    cached = _pressure_cache.get((source, pexp))
    if cached is not None and clock.monotonic() - cached[0] < PRESSURE_CACHE_SECONDS:
        strikes, minutes = cached[1], cached[2]
    else:
        traded = await storage.get_strike_flow_expirations(source, day_open, end)
        expirations = pick_expirations(pexp, traded, day_open.date())
        rows = await storage.get_strike_flow_minutes(source, day_open, end, expirations) if expirations != [] else []
        strikes, minutes = pressure_matrix(rows, day_open, end + timedelta(minutes=1))
        _pressure_cache[(source, pexp)] = (clock.monotonic(), strikes, minutes)
    if future == "RTY":
        ratio, offset = rty_ratio, 0.0  # an IWM strike x the live RTY / IWM ratio
    else:
        ratio, offset = await price_mapping(storage, future, source, now)
    lines = [f"pressure {source} {pexp} {HALF_LIFE_MINUTES:g}"]
    if ratio is None or not strikes:
        lines.append("pstrikes")
        return lines
    lines.append("pstrikes " + " ".join(f"{strike * ratio + offset:.2f}" for strike in strikes))
    first = datetime.fromtimestamp(psince, UTC) if psince is not None else day_open
    for minute, values in minutes:
        if minute >= first:
            lines.append(f"prow {int(minute.timestamp())} " + " ".join(f"{value:.4g}" if value else "0" for value in values))
    return lines
