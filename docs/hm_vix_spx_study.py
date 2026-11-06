"""READ-ONLY (READ ONLY transaction). VIX -> SPX / NDX study (2026-11-05, Lester "si"). Stored data only.
Part 1: SPX / NDX points (and %) per VIX point over 5 / 15 / 60-minute windows and the whole day, from the last price of each minute
        (market_snapshots, 09:30-16:00 ET, days where all three have >= 300 session minutes); OLS slope through 0 and R2, the ratio's
        median / p25-p75 on windows where VIX moved enough, split by VIX up / down and by the starting VIX level; how often SPX moved opposite.
Part 2: VIX vs its own levels (ABS, call wall, put wall, the 3 strikes of largest |net gamma| per cycle) per gamma view: first touches
        and what VIX did in the next 15 minutes (bounce / cross), against the same levels shifted +-0.5 / +-1.0 VIX points (control);
        and day-scale: day high / low / 15:55 close within 0.25 of the 10:00 call wall / put wall / ABS vs the shifted controls.
Part 3: at the 09:45 cycle, the call-side share of gamma and |gamma| above vs below spot (0dte_volume, 0dte, tactical) as a predictor of
        VIX's direction to 11:00 and to the close, and so SPX's.
usage: hm_vix_spx_study.py"""
import statistics
import sys
from collections import defaultdict
from datetime import time
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
c = build_container()


def pct(values, q):
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def ols0(xs, ys):
    """Slope through the origin and R2 (of that fit, centered)."""
    sxx = sum(x * x for x in xs)
    if not sxx:
        return float("nan"), float("nan")
    b = sum(x * y for x, y in zip(xs, ys)) / sxx
    my = statistics.mean(ys)
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - b * x) ** 2 for x, y in zip(xs, ys))
    return b, (1 - sse / sst) if sst else float("nan")


with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    # ---------- minute closes
    rows = s.execute(text(
        """select u.symbol, date_trunc('minute', m.time) as minute, (array_agg(m.price order by m.time desc))[1]
           from market_snapshots m join underlyings u on u.id = m.underlying_id
           where u.symbol in ('VIX', 'SPX', 'NDX') and (m.time at time zone 'America/New_York')::time >= '09:30'
             and (m.time at time zone 'America/New_York')::time < '16:00'
           group by 1, 2""")).all()
    series = defaultdict(dict)  # (day) -> {symbol: {minute_index: price}}
    for sym, minute, price in rows:
        local = minute.astimezone(ET)
        idx = (local.hour * 60 + local.minute) - 570
        series[local.date()].setdefault(sym, {})[idx] = float(price)
    days = sorted(d for d, by in series.items() if all(len(by.get(x, {})) >= 300 for x in ("VIX", "SPX", "NDX")))
    print(f"PART 1 days with >=300 minutes of VIX, SPX and NDX: {len(days)} ({days[0]} .. {days[-1]})")

    def at(day, sym, idx, back=3):
        by = series[day][sym]
        for k in range(idx, idx - back - 1, -1):
            if k in by:
                return by[k]
        return None

    MIN_VIX_MOVE = {5: 0.10, 15: 0.15, 60: 0.30, 390: 0.50}
    for target in ("SPX", "NDX"):
        print(f"\n== {target} per VIX point")
        print("window  n    slope    R2   | ratio (|dVIX|>=min) n  median   p25    p75  | % per VIX pt median | opposite direction")
        for w in (5, 15, 60, 390):
            pairs = []  # (dvix, dtarget, dtarget%, start_vix)
            for day in days:
                starts = [0] if w == 390 else range(0, 390 - w + 1, w)
                for a in starts:
                    b = 389 if w == 390 else a + w
                    v0, v1 = at(day, "VIX", a), at(day, "VIX", b)
                    t0, t1 = at(day, target, a), at(day, target, b)
                    if None in (v0, v1, t0, t1):
                        continue
                    pairs.append((v1 - v0, t1 - t0, (t1 - t0) / t0 * 100, v0))
            slope, r2 = ols0([p[0] for p in pairs], [p[1] for p in pairs])
            big = [p for p in pairs if abs(p[0]) >= MIN_VIX_MOVE[w]]
            ratios = [p[1] / p[0] for p in big]
            pratios = [p[2] / p[0] for p in big]
            moved = [p for p in pairs if p[0] != 0 and p[1] != 0]
            opposite = sum(1 for p in moved if (p[0] > 0) != (p[1] > 0)) / len(moved) if moved else float("nan")
            label = "day" if w == 390 else f"{w}m"
            print(f"{label:6} {len(pairs):5} {slope:+8.1f} {r2:5.2f} | {len(ratios):5} {statistics.median(ratios) if ratios else float('nan'):+7.1f} "
                  f"{pct(ratios, .25):+7.1f} {pct(ratios, .75):+7.1f} | {statistics.median(pratios) if pratios else float('nan'):+6.3f} % | {opposite:5.1%}")
            for name, subset in (
                ("VIX up", [p for p in big if p[0] > 0]),
                ("VIX down", [p for p in big if p[0] < 0]),
                ("VIX<17", [p for p in big if p[3] < 17]),
                ("17-20", [p for p in big if 17 <= p[3] < 20]),
                ("20-25", [p for p in big if 20 <= p[3] < 25]),
                (">25", [p for p in big if p[3] >= 25]),
            ):
                if len(subset) >= 3:
                    r = [p[1] / p[0] for p in subset]
                    sl, rr = ols0([p[0] for p in subset], [p[1] for p in subset])
                    print(f"         {name:9} n={len(r):4}  ratio median {statistics.median(r):+7.1f} [{pct(r, .25):+7.1f}, {pct(r, .75):+7.1f}]  slope {sl:+7.1f} R2 {rr:4.2f}")
                elif subset:
                    print(f"         {name:9} n={len(subset):4}  (too few)")

    # ---------- VIX levels per cycle
    level_rows = s.execute(text(
        """select g.view, g.time, g.call_wall, g.put_wall, g.absolute_gamma_strike from gamma_aggregates g join underlyings u on u.id = g.underlying_id
           where u.symbol = 'VIX' and g.view in ('0dte', '0dte_volume', 'tactical')
             and (g.time at time zone 'America/New_York')::time between '09:30' and '16:00'""")).all()
    top_rows = s.execute(text(
        """select view, time, strike from (
             select i.view, i.time, i.strike, row_number() over (partition by i.view, i.time order by abs(i.net_gamma) desc) as rk
             from gamma_aggregate_items i join underlyings u on u.id = i.underlying_id
             where u.symbol = 'VIX' and i.view in ('0dte', '0dte_volume', 'tactical')
               and (i.time at time zone 'America/New_York')::time between '09:30' and '16:00') x where rk <= 3""")).all()
    sides_rows = s.execute(text(
        """select i.view, i.time, i.strike, i.call_gamma_exposure, i.put_gamma_exposure, i.net_gamma
           from gamma_aggregate_items i join underlyings u on u.id = i.underlying_id
           where u.symbol = 'VIX' and i.view in ('0dte', '0dte_volume', 'tactical')
             and (i.time at time zone 'America/New_York')::time between '09:44' and '09:50'""")).all()

levels = defaultdict(lambda: defaultdict(dict))  # view -> day -> minute_idx -> {kind: [levels]}
for view, t, cw, pw, abs_ in level_rows:
    local = t.astimezone(ET)
    idx = local.hour * 60 + local.minute - 570
    entry = levels[view][local.date()].setdefault(idx, {})
    for kind, value in (("call_wall", cw), ("put_wall", pw), ("abs", abs_)):
        if value is not None:
            entry[kind] = [float(value)]
for view, t, strike in top_rows:
    local = t.astimezone(ET)
    idx = local.hour * 60 + local.minute - 570
    levels[view][local.date()].setdefault(idx, {}).setdefault("bright3", []).append(float(strike))


def vix_minutes(day):
    return series.get(day, {}).get("VIX", {})


_FILLED = {}


def level_now(view_day, idx, kind):
    """The level of the latest cycle at or before minute idx (forward-filled once per day and kind)."""
    key = (id(view_day), kind)
    if key not in _FILLED:
        filled, current = [], None
        for k in range(390):
            if k in view_day and kind in view_day[k]:
                current = view_day[k][kind]
            filled.append(current)
        _FILLED[key] = filled
    return _FILLED[key][idx] if 0 <= idx < 390 else None


TOL, APPROACH, HORIZON, MOVE = 0.10, 0.30, 15, 0.15
print("\nPART 2 VIX vs its own levels: first touch (within 0.10) after being >= 0.30 away; at +15 min bounce = back >= 0.15 on the approach side,")
print("cross = >= 0.15 on the other side. Control = the same level path shifted -1.0 / -0.5 / +0.5 / +1.0.")
for view in ("0dte", "0dte_volume", "tactical"):
    vdays = sorted(d for d in levels[view] if len(vix_minutes(d)) >= 300)
    print(f"\n== view {view}: {len(vdays)} days ({vdays[0] if vdays else '-'} .. {vdays[-1] if vdays else '-'})")
    for kind in ("abs", "call_wall", "put_wall", "bright3"):
        stats = {}
        for shift in (0.0, -1.0, -0.5, 0.5, 1.0):
            touches = bounces = crosses = 0
            for day in vdays:
                prices = vix_minutes(day)
                armed = {}
                for idx in range(0, 390 - HORIZON):
                    if idx not in prices:
                        continue
                    lv = level_now(levels[view][day], idx, kind)
                    if not lv:
                        continue
                    p = prices[idx]
                    for raw in lv:
                        level = raw + shift
                        dist = p - level
                        key = round(level, 2)
                        if abs(dist) >= APPROACH:
                            armed[key] = 1 if dist > 0 else -1
                        elif abs(dist) <= TOL and armed.get(key):
                            side = armed.pop(key)
                            later = next((prices[k] for k in range(idx + HORIZON, idx + HORIZON - 4, -1) if k in prices), None)
                            if later is None:
                                continue
                            touches += 1
                            after = (later - level) * side
                            if after >= MOVE:
                                bounces += 1
                            elif after <= -MOVE:
                                crosses += 1
            stats[shift] = (touches, bounces, crosses)
        t0, b0, x0 = stats[0.0]
        ctrl = [stats[k] for k in (-1.0, -0.5, 0.5, 1.0)]
        ct, cb, cx = (sum(x[i] for x in ctrl) for i in range(3))
        rate = lambda n, d: (n / d) if d else float("nan")
        print(f"  {kind:10} real: touches {t0:4} bounce {rate(b0, t0):5.1%} cross {rate(x0, t0):5.1%} | control: touches {ct:4} bounce {rate(cb, ct):5.1%} cross {rate(cx, ct):5.1%}")

    # day-scale magnetism with the 10:00 levels
    print("  day-scale (levels of the 10:00 cycle): share of days within 0.25 VIX of the level, real vs control (+-0.5, +-1.0)")
    for kind, target in (("call_wall", "high"), ("put_wall", "low"), ("abs", "close"), ("call_wall", "low"), ("put_wall", "high")):
        real, ctrl, n = 0, 0, 0
        for day in vdays:
            prices = vix_minutes(day)
            lv = level_now(levels[view][day], 30, kind)
            if not lv or not prices:
                continue
            values = [prices[k] for k in sorted(prices) if k >= 30]
            if not values:
                continue
            x = {"high": max(values), "low": min(values), "close": prices.get(max(k for k in prices if k <= 385), values[-1])}[target]
            n += 1
            real += abs(x - lv[0]) <= 0.25
            ctrl += sum(abs(x - (lv[0] + sh)) <= 0.25 for sh in (-1.0, -0.5, 0.5, 1.0))
        if n:
            print(f"    {kind:10} vs day {target:5}: {real}/{n} = {real / n:5.1%}  control {ctrl / (4 * n):5.1%}")

# ---------- part 3
print("\nPART 3 at the 09:45 cycle: call share = sum(call gamma) / (sum(call) + sum(|put|)); above share = sum(|net gamma|) above spot / all.")
print("Prediction: share > 0.5 -> VIX up (Lester: green call-side gamma dominant, VIX went that way). Outcome VIX 11:00 and 15:55 vs 09:45; SPX opposite.")
cycles = defaultdict(lambda: defaultdict(list))
for view, t, strike, cg, pg, ng in sides_rows:
    cycles[view][t].append((float(strike), float(cg or 0), float(pg or 0), float(ng or 0)))
for view in ("0dte_volume", "0dte", "tactical"):
    by_day = {}
    for t, items in cycles[view].items():
        day = t.astimezone(ET).date()
        if day not in by_day or t < by_day[day][0]:
            by_day[day] = (t, items)
    print(f"\n== view {view}: {len(by_day)} days")
    hits = defaultdict(lambda: [0, 0])
    for day in sorted(by_day):
        t, items = by_day[day]
        prices, spx = vix_minutes(day), series.get(day, {}).get("SPX", {})
        idx = t.astimezone(ET).hour * 60 + t.astimezone(ET).minute - 570
        p0 = next((prices[k] for k in range(idx, idx - 4, -1) if k in prices), None)
        s0 = next((spx[k] for k in range(idx, idx - 4, -1) if k in spx), None)
        if p0 is None or s0 is None:
            continue
        calls = sum(cg for _, cg, _, _ in items)
        puts = sum(abs(pg) for _, _, pg, _ in items)
        above = sum(abs(ng) for k, _, _, ng in items if k > p0)
        total = sum(abs(ng) for _, _, _, ng in items)
        call_share = calls / (calls + puts) if calls + puts else float("nan")
        above_share = above / total if total else float("nan")
        line = f"  {day} 09:{t.astimezone(ET).minute:02d} VIX {p0:6.2f} call share {call_share:5.2f} above share {above_share:5.2f}"
        for label, end in (("11:00", 90), ("close", 385)):
            p1 = next((prices[k] for k in range(end, end - 6, -1) if k in prices), None)
            s1 = next((spx[k] for k in range(end, end - 6, -1) if k in spx), None)
            if p1 is None or s1 is None:
                continue
            up = p1 > p0
            line += f" | {label} VIX {p1 - p0:+5.2f} SPX {s1 - s0:+7.1f}"
            for name, share in (("call", call_share), ("above", above_share)):
                if share == share and share != 0.5:
                    key = (name, label)
                    hits[key][0] += (share > 0.5) == up
                    hits[key][1] += 1
                    hits[("spx " + name, label)][0] += (share > 0.5) == (s1 < s0)
                    hits[("spx " + name, label)][1] += 1
        print(line)
    for (name, label), (h, n) in sorted(hits.items()):
        print(f"    predictor {name:10} -> {label:5}: {h}/{n} = {h / n:5.1%}" if n else "")
