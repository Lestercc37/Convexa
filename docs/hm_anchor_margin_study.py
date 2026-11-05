"""READ-ONLY (READ ONLY transaction). ES / NQ anchor margin study (2026-11-05): for every day with an anchor, offset = anchor - median of the index's
first minute (09:30:00-09:30:59, as backend/adapters/storage/futures_anchor_queries.py), its day-to-day change, its distance to the median of the previous
5 days' offsets (the auto-save check), the index's first-minute range, and the anchor's source. Then the distribution of |distance| per future.
usage: hm_anchor_margin_study.py"""
import statistics
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
PAIRS = {"ES": "SPX", "NQ": "NDX"}
c = build_container()


def pct(values, q):
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    cols = {r[0] for r in s.execute(text("select column_name from information_schema.columns where table_name = 'future_price_anchors'")).all()}
    has_source = "source" in cols
    for future, index in PAIRS.items():
        rows = s.execute(text(
            "select a.session_date, a.anchor_price" + (", a.source" if has_source else ", null") + " from future_price_anchors a join underlyings u on u.id = a.underlying_id "
            "where u.symbol = :f order by a.session_date"), {"f": future}).all()
        print(f"===== {future} vs {index}: {len(rows)} anchors")
        print("date        anchor     source       idx_med    idx_rng  offset   d/d     med5   dist")
        history, dists, dds, ranges = [], [], [], []
        prev = None
        for day, anchor, source in rows:
            open_ = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
            prices = [float(r[0]) for r in s.execute(text(
                "select m.price from market_snapshots m join underlyings u on u.id = m.underlying_id where u.symbol = :s and m.time >= :a and m.time < :b"),
                {"s": index, "a": open_, "b": open_ + timedelta(minutes=1)}).all()]
            if not prices:
                print(f"{day}  {float(anchor):9.2f}  {str(source):11}  (no index readings in the first minute)")
                continue
            med, rng = statistics.median(prices), max(prices) - min(prices)
            offset = float(anchor) - med
            med5 = statistics.median(history[-5:]) if history else None
            dist = offset - med5 if med5 is not None else None
            dd = offset - prev if prev is not None else None
            if dist is not None:
                dists.append(abs(dist))
            if dd is not None:
                dds.append(abs(dd))
            ranges.append(rng)
            print(f"{day}  {float(anchor):9.2f}  {str(source):11}  {med:9.2f}  {rng:6.2f}  {offset:+7.2f}  "
                  + (f"{dd:+6.2f}" if dd is not None else "     -") + "  " + (f"{med5:+7.2f}" if med5 is not None else "      -") + "  "
                  + (f"{dist:+6.2f}" if dist is not None else "     -") + f"  n={len(prices)}")
            history.append(offset)
            prev = offset
        for name, vals in (("|dist to med5|", dists), ("|day-to-day|", dds), ("first-minute range", ranges)):
            if vals:
                print(f"  {name:20} n={len(vals):3}  median {statistics.median(vals):6.2f}  p90 {pct(vals, .9):6.2f}  p95 {pct(vals, .95):6.2f}  max {max(vals):6.2f}")
