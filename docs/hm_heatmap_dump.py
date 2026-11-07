"""READ-ONLY: dumps one session of real stored data as JSON, the same shape the heatmap endpoint returns (120 s buckets, last snapshot per bucket and strike), plus the 5-minute candles of the
same session from market_snapshots, so the real heatmap drawing can be rendered offline in a browser. Usage: python hm_heatmap_dump.py <SYMBOL> <YYYY-MM-DD> <metric>"""
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
symbol, day, metric = sys.argv[1], sys.argv[2], sys.argv[3]
column = {"gamma": "net_gamma", "absolute_gamma": "absolute_gamma", "delta": "delta_exposure"}[metric]
open_et = datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=ET)
start, end = open_et - timedelta(hours=1), open_et + timedelta(hours=6, minutes=30)
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
    rows = s.execute(text(f"""
        SELECT DISTINCT ON (bucket, strike) bucket, time AS snapshot_time, strike, value FROM (
            SELECT date_bin(make_interval(secs => 120.0), time, TIMESTAMPTZ '2000-01-03 00:00+00') AS bucket, time, strike, {column} AS value
            FROM gamma_aggregate_items WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = :sym) AND view = 'tactical' AND time >= :a AND time < :b) AS binned
        ORDER BY bucket, strike, time DESC"""), {"sym": symbol, "a": start, "b": end}).fetchall()
    strikes = sorted({float(r.strike) for r in rows})
    idx = {k: i for i, k in enumerate(strikes)}
    cols, last = {}, {}
    for r in rows:
        v = cols.setdefault(r.bucket, [None] * len(strikes))
        v[idx[float(r.strike)]] = None if r.value is None else float(r.value)
        last[r.bucket] = max(last.get(r.bucket, r.snapshot_time), r.snapshot_time)
    columns = [{"as_of": last[b].isoformat(), "values": cols[b]} for b in sorted(cols)]
    vals = sorted(abs(x) for col in columns for x in col["values"] if x is not None)
    scale = vals[int(0.98 * (len(vals) - 1))] if vals else None
    snaps = s.execute(text("SELECT time, price FROM market_snapshots WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = :sym) AND time >= :a AND time < :b ORDER BY time"),
                      {"sym": symbol, "a": open_et, "b": open_et + timedelta(hours=6, minutes=30)}).fetchall()
    candles, cur = [], None
    for t, p in snaps:
        k = int(t.timestamp()) // 300 * 300
        p = float(p)
        if cur is None or cur["time"] != k:
            cur = {"time": k, "open": p, "high": p, "low": p, "close": p}
            candles.append(cur)
        else:
            cur["high"], cur["low"], cur["close"] = max(cur["high"], p), min(cur["low"], p), p
    s.rollback()
out = {"heatmap": {"schema_version": 1, "symbol": symbol, "view": "tactical", "metric": metric, "bucket_seconds": 120, "strikes": strikes, "columns": columns, "scale_p98": scale}, "candles": candles}
path = r"V:\Convexa\bench\hm_stage2\heatmap_dump_%s_%s_%s.json" % (symbol, day, metric)
json.dump(out, open(path, "w"))
print("columns", len(columns), "strikes", len(strikes), strikes[0] if strikes else None, strikes[-1] if strikes else None, "candles", len(candles), "scale_p98", scale, "->", path)
