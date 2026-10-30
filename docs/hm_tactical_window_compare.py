"""READ-ONLY (Lester 2026-10-30 "si"): the 0-2 DTE (tactical) window counts 2 CALENDAR days from today (gamma.py TACTICAL_WINDOW_DAYS), so Fridays get only 0DTE and
Thursdays two expirations, while the 1DTE / 2DTE tabs use LISTED order. Rebuilds the tactical view offline from the stored chain snapshot with (a) the current rule
and (b) the first 3 LISTED expirations from today, and prints ABS / Call Wall / Put Wall / Flip of both. Nothing is written.
Usage: python hm_tactical_window_compare.py DAY[,DAY...] [HH:MM ...]"""
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.market_hours import EASTERN_TIME

days = [date.fromisoformat(d) for d in sys.argv[1].split(",")]
times = sys.argv[2:] or ["10:00", "12:00", "15:00"]
c = build_container()
orch = c.calculate_gamma_exposure_orchestrator
print("server now", datetime.now(EASTERN_TIME).strftime("%Y-%m-%d %H:%M %Z"))

SQL = """
with snap as (
  select max(s.time) t from option_chain_snapshots s join underlyings u on u.id = s.underlying_id
  where u.symbol = :sym and s.time <= :at and s.time >= :at - interval '10 minutes'
)
select oc.occ_symbol, oc.strike, oc.expiration, oc.contract_type, s.bid, s.ask, s.last, s.volume, s.open_interest, s.iv,
       s.delta, s.gamma, s.theta, s.vega, s.charm, s.vanna, s.spot_price, s.time
from option_chain_snapshots s join option_contracts oc on oc.id = s.contract_id join underlyings u on u.id = s.underlying_id, snap
where u.symbol = :sym and s.time = snap.t and oc.expiration between :d0 and :d1
"""


def levels(g):
    flip = "-" if g.gamma_flip is None else f"{float(g.gamma_flip):.1f}"
    cw = "-" if g.call_wall is None else f"{float(g.call_wall):g}"
    pw = "-" if g.put_wall is None else f"{float(g.put_wall):g}"
    return (float(g.absolute_gamma_strike), cw, pw, flip)


tally = {"thu_fri": [0, 0, 0, 0, 0], "mon_wed": [0, 0, 0, 0, 0]}  # runs, abs, cw, pw, flip changed
moves = []
for sym in ("SPX", "NDX", "SPY", "QQQ"):
    bars = c.storage.get_daily_bars(sym, REQUIRED_DAILY_BARS)
    for day in days:
        for hhmm in times:
            at = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=EASTERN_TIME)
            with c.storage.session_factory() as s:
                s.execute(text("SET TRANSACTION READ ONLY"))
                s.execute(text("SET LOCAL statement_timeout = '90s'"))
                rows = s.execute(text(SQL), {"sym": sym, "at": at, "d0": day, "d1": day + timedelta(days=10)}).all()
                s.rollback()
            if not rows:
                print(f"{sym} {day} {hhmm}: no snapshot")
                continue
            contracts = [
                OptionContract(underlying=sym, strike=Decimal(k), expiration=exp, contract_type=ContractType.CALL if str(ct).upper().startswith("C") else ContractType.PUT,
                               occ_symbol=occ, bid=Decimal(bid), ask=Decimal(ask), last=Decimal(last or 0), volume=int(vol or 0), open_interest=int(oi or 0),
                               iv=Decimal(iv) if iv is not None else None,
                               greeks=Greeks(delta=Decimal(de or 0), gamma=Decimal(ga or 0), theta=Decimal(th or 0), vega=Decimal(ve or 0), charm=Decimal(ch or 0), vanna=Decimal(va or 0)))
                for occ, k, exp, ct, bid, ask, last, vol, oi, iv, de, ga, th, ve, ch, va, sp, t in rows
            ]
            chain = OptionChain(symbol=sym, as_of=rows[0][17], spot_price=Decimal(str(rows[0][16])), contracts=tuple(contracts))
            listed = sorted({k.expiration for k in contracts if k.expiration >= day})
            first3 = set(listed[:3])
            a = orch.build_tactical(sym, chain, bars)
            three = replace(chain, contracts=tuple(k for k in contracts if k.expiration in first3))
            b = orch._build_view(sym, three, bars, 30, day, "tactical")
            la, lb = levels(a), levels(b)
            group = "thu_fri" if day.weekday() in (3, 4) else "mon_wed"
            tally[group][0] += 1
            for i in range(4):
                tally[group][i + 1] += la[i] != lb[i]
            if la[0] != lb[0]:
                moves.append(abs(la[0] - lb[0]))
            in_window_a = sorted({k.expiration for k in contracts if day <= k.expiration <= day + timedelta(days=2)})
            print(f"{sym} {day:%a %m-%d} {hhmm} spot {float(chain.spot_price):.2f} | calendar {[str(d)[5:] for d in in_window_a]} -> ABS {la[0]:g} CW {la[1]} PW {la[2]} flip {la[3]} | "
                  f"listed {[str(d)[5:] for d in listed[:3]]} -> ABS {lb[0]:g} CW {lb[1]} PW {lb[2]} flip {lb[3]}" + ("" if la == lb else "  <-- differs"))
for group, (n, abs_, cw, pw, flip) in tally.items():
    print(f"SUMMARY {group}: {n} runs | changed ABS {abs_} CW {cw} PW {pw} flip {flip}")
if moves:
    moves.sort()
    print(f"ABS moved by: median {moves[len(moves) // 2]:g} pts, max {moves[-1]:g}")
