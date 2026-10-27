"""READ-ONLY check 2026-10-27 ~11:17 ET. (1) strikes per gamma view; (2) gamma just outside the ATR window for 1dte/2dte vs the walls inside; (3) 0DTE cycle columns
and fast rows today; (4) volume lag; (5) fast-layer pass timing per step on the cached chain (nothing written: the layer's private steps are called directly)."""
import sys
import time
from dataclasses import replace

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.fast_gamma import FastGammaLayer
from backend.domain.use_cases.gamma import listed_expiration_for_view
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
SYMBOLS = ["SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA", "VIX"]
VIEWS = ["tactical", "0dte", "1dte", "2dte", "0dte_volume"]

print("(1) strikes per view (latest aggregate)")
for sym in SYMBOLS:
    sets = {}
    for v in VIEWS:
        g = c.storage.get_latest_gamma_aggregate(sym, view=v)
        sets[v] = {float(i.strike) for i in g.items} if g else set()
    union = set().union(*sets.values())
    only = {v: sorted(union - s)[:6] for v, s in sets.items() if union - s}
    print(f"  {sym}: " + " ".join(f"{v}={len(s)}" for v, s in sets.items()) + (f" | missing in: {only}" if only else " | identical"))

print("(2) 1dte/2dte: biggest |net| just outside the window (width..2x width) vs the walls inside")
orch = c.calculate_gamma_exposure_orchestrator
for sym in ["SPX", "NDX", "SPY", "QQQ", "IWM"]:
    chain = c.storage.get_latest_chain_snapshot(sym)
    bars = c.storage.get_daily_bars(sym, REQUIRED_DAILY_BARS)
    for v in ("1dte", "2dte"):
        inside = orch.build_expiration_view(sym, chain, bars, v)
        if not inside.items or inside.near_the_money_width is None:
            print(f"  {sym} {v}: empty")
            continue
        width = float(inside.near_the_money_width)
        spot = float(chain.spot_price)
        exp = listed_expiration_for_view(chain, v)
        one = replace(chain, contracts=tuple(k for k in chain.contracts if k.expiration == exp))
        wide = orch._aggregate.execute(orch._greeks.execute(one))
        net = {float(i.strike): float(i.call_gamma_exposure) + float(i.put_gamma_exposure) for i in wide.items}
        ins = {k: x for k, x in net.items() if abs(k - spot) <= width}
        out = {k: x for k, x in net.items() if width < abs(k - spot) <= 2 * width}
        best_in_pos = max(ins.values(), default=0)
        best_in_neg = min(ins.values(), default=0)
        best_out_pos = max(out.items(), key=lambda kv: kv[1], default=(None, 0))
        best_out_neg = min(out.items(), key=lambda kv: kv[1], default=(None, 0))
        flag_c = "MISSED" if best_out_pos[1] > best_in_pos else "ok"
        flag_p = "MISSED" if best_out_neg[1] < best_in_neg else "ok"
        print(f"  {sym} {v} exp {exp} width {width:.1f}: call wall {inside.call_wall} vs outside max {best_out_pos[0]} ({best_out_pos[1] / best_in_pos if best_in_pos else 0:.2f}x) {flag_c} | put wall {inside.put_wall} vs outside min {best_out_neg[0]} ({best_out_neg[1] / best_in_neg if best_in_neg else 0:.2f}x) {flag_p}")

print("(3)/(4) 0DTE columns and fast rows today, volume lag")
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='60s'"))
    start = "(date_trunc('day', now() at time zone 'America/New_York') + interval '9 hours 30 minutes') at time zone 'America/New_York'"
    for row in s.execute(text(f"""select u.symbol, g.view, count(*) from gamma_aggregates g join underlyings u on u.id = g.underlying_id
            where g.view in ('0dte','0dte_volume') and g.time >= {start} and u.symbol in ('SPX','NDX','SPY','QQQ','IWM') group by 1,2 order by 1,2""")).all():
        print(f"  cycle {row[0]} {row[1]}: {row[2]} columns")
    for row in s.execute(text(f"""select u.symbol, f.view, count(distinct f.time), count(*), max(f.time) from gamma_fast_items f join underlyings u on u.id = f.underlying_id
            where f.time >= {start} group by 1,2 order by 1,2""")).all():
        print(f"  fast {row[0]} {row[1]}: {row[2]} passes, {row[3]} rows, last {row[4].astimezone(EASTERN_TIME):%H:%M:%S}")
    cv = s.execute(text("select max(updated_at) from cumulative_volume")).scalar() if s.execute(text("select 1 from information_schema.columns where table_name='cumulative_volume' and column_name='updated_at'")).first() else None
    fast_last = s.execute(text("select max(time) from gamma_fast_items where view = '0dte_volume'")).scalar()
    print(f"  newest cumulative_volume write {cv.astimezone(EASTERN_TIME):%H:%M:%S}" if cv else "  cumulative_volume has no updated_at column")
    print(f"  newest 0dte_volume fast row {fast_last.astimezone(EASTERN_TIME):%H:%M:%S}" if fast_last else "  no 0dte_volume fast rows")
    size = s.execute(text("select pg_size_pretty(pg_total_relation_size('gamma_fast_items')), (select count(*) from gamma_fast_items where time >= now() - interval '1 day')")).one()
    print(f"  gamma_fast_items size {size[0]}, rows last 24 h {size[1]:,}")
    s.rollback()

print("(5) fast pass timing per step (no writes)")
provider = c.market_data_provider
from backend.fast_gamma_worker import _time_to_expiration_years  # noqa: E402

layer = FastGammaLayer(c.storage, orch, time_to_expiration=_time_to_expiration_years, risk_free_rate=provider.risk_free_rate,
                       itm_iv_from_twin=c.settings.thetadata_itm_iv_from_twin)
from datetime import datetime  # noqa: E402

total = 0.0
for sym in ["SPX", "NDX", "SPY", "QQQ", "IWM"]:
    t0 = time.perf_counter(); cached = layer._cached_chain(sym); t_read = time.perf_counter() - t0
    price = c.storage.get_latest_price(sym)
    now_et = datetime.now(EASTERN_TIME)
    t0 = time.perf_counter(); moved = layer._moved_chain(cached, price.price, now_et); t_reprice = time.perf_counter() - t0
    t0 = time.perf_counter(); orch.build_tactical(sym, moved, cached.daily_bars); t_tac = time.perf_counter() - t0
    t0 = time.perf_counter(); orch.build_expiration_view(sym, moved, cached.daily_bars, "0dte"); t_0 = time.perf_counter() - t0
    t0 = time.perf_counter(); live = layer._with_live_volume(moved); t_live = time.perf_counter() - t0
    t0 = time.perf_counter(); orch.build_expiration_view(sym, live, cached.daily_bars, "0dte_volume"); t_v = time.perf_counter() - t0
    per = t_reprice + t_tac + t_0 + t_live + t_v
    total += per
    print(f"  {sym}: contracts {len(cached.chain.contracts)} | chain read {t_read:.2f}s (cached 30 s) | reprice {t_reprice:.3f}s | 0-2DTE {t_tac:.3f}s | 0DTE {t_0:.3f}s | live volume read {t_live:.3f}s | 0DTE vol {t_v:.3f}s | pass {per:.3f}s")
print(f"  full round of 5 (without the chain read): {total:.2f}s")
