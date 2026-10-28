"""READ-ONLY (plus the provider's one cached SOFR rate read). Own IV engine phase 1, shadow comparison on STORED chain snapshots of one day:
for SPX / NDX / SPY / QQQ / IWM at a few times, (a) our IV (backend/domain/use_cases/own_iv.py) vs ThetaData's IV per contract of the 0-2 DTE
expirations: share differing by more than 1 vol point, by bucket (near the money OTM, OTM wings, ITM), contracts where ThetaData's IV is 0 and ours
exists; (b) the levels (Flip / Call Wall / Put Wall / ABS) of the 0-2 DTE and 0DTE views rebuilt offline with gamma / vanna / charm re-priced from our
IV vs the same views from the stored greeks. Nothing is written. Usage: python hm_own_iv_compare.py 2026-10-27 [HH:MM ...]"""
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.adapters.providers.thetadata.provider import _time_to_expiration_years
from backend.core.container import build_container
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.calculate_bsm_greeks import calculate_bsm_greeks
from backend.domain.use_cases.market_hours import EASTERN_TIME
try:
    from backend.domain.use_cases.own_iv import QuoteInput, own_iv_by_contract
except ImportError:  # the engine is not on the server yet: a copy next to this script
    from own_iv import QuoteInput, own_iv_by_contract

day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(EASTERN_TIME).date()
times = sys.argv[2:] or ["10:00", "12:00", "15:00"]
c = build_container()
orch = c.calculate_gamma_exposure_orchestrator
rate = float(c.market_data_provider.risk_free_rate())
print(f"day {day} rate {rate:.4f}")

SQL = """
with snap as (
  select max(s.time) t from option_chain_snapshots s join underlyings u on u.id = s.underlying_id
  where u.symbol = :sym and s.time <= :at and s.time >= :at - interval '10 minutes'
)
select oc.occ_symbol, oc.strike, oc.expiration, oc.contract_type, s.bid, s.ask, s.last, s.volume, s.open_interest, s.iv,
       s.delta, s.gamma, s.theta, s.vega, s.charm, s.vanna, s.spot_price, s.time
from option_chain_snapshots s join option_contracts oc on oc.id = s.contract_id join underlyings u on u.id = s.underlying_id, snap
where u.symbol = :sym and s.time = snap.t and oc.expiration between :d0 and :d2
"""


def levels(g):
    return f"flip {float(g.gamma_flip or 0):.1f} cw {float(g.call_wall or 0):g} pw {float(g.put_wall or 0):g} abs {float(g.absolute_gamma_strike):g}"


for sym in ["SPX", "NDX", "SPY", "QQQ", "IWM"]:
    bars = c.storage.get_daily_bars(sym, REQUIRED_DAILY_BARS)
    for hhmm in times:
        at = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=EASTERN_TIME)
        with c.storage.session_factory() as s:
            s.execute(text("SET TRANSACTION READ ONLY"))
            s.execute(text("SET LOCAL statement_timeout='60s'"))
            rows = s.execute(text(SQL), {"sym": sym, "at": at, "d0": day, "d2": day + timedelta(days=4)}).all()
            s.rollback()
        if not rows:
            print(f"{sym} {hhmm}: no snapshot")
            continue
        snap_time = rows[0][17].astimezone(EASTERN_TIME)
        spot = float(rows[0][16])
        contracts, by_exp = [], {}
        for occ, k, exp, ct, bid, ask, last, vol, oi, iv, de, ga, th, ve, ch, va, sp, t in rows:
            is_call = str(ct).upper().startswith("C")
            contracts.append(OptionContract(underlying=sym, strike=Decimal(k), expiration=exp, contract_type=ContractType.CALL if is_call else ContractType.PUT,
                                            occ_symbol=occ, bid=Decimal(bid), ask=Decimal(ask), last=Decimal(last or 0), volume=int(vol or 0), open_interest=int(oi or 0),
                                            iv=Decimal(iv) if iv is not None else None,
                                            greeks=Greeks(delta=Decimal(de or 0), gamma=Decimal(ga or 0), theta=Decimal(th or 0), vega=Decimal(ve or 0), charm=Decimal(ch or 0), vanna=Decimal(va or 0))))
            by_exp.setdefault(exp, []).append(QuoteInput(occ, exp, float(k), is_call, float(bid), float(ask)))
        own, forwards = {}, {}
        for exp, quotes in by_exp.items():
            years = float(_time_to_expiration_years(exp, snap_time))
            ivs, fwd = own_iv_by_contract(quotes, spot, years, rate)
            own.update(ivs)
            forwards[exp] = fwd
        # (a) IV comparison
        buckets = {"near OTM": [0, 0], "wing OTM": [0, 0], "ITM": [0, 0]}
        theta_zero_filled = theta_zero = no_own = 0
        diffs = []
        for k in contracts:
            fwd = forwards.get(k.expiration) or spot
            strike = float(k.strike)
            itm = (k.contract_type == ContractType.CALL and strike < fwd) or (k.contract_type == ContractType.PUT and strike > fwd)
            mine = own.get(k.occ_symbol)
            theirs = float(k.iv) if k.iv is not None else 0.0
            if theirs <= 0:
                theta_zero += 1
                theta_zero_filled += mine is not None
                continue
            if mine is None:
                no_own += 1
                continue
            bucket = "ITM" if itm else ("near OTM" if abs(strike - fwd) / fwd < 0.01 else "wing OTM")
            d = (mine - theirs) * 100
            diffs.append(d)
            buckets[bucket][0] += 1
            buckets[bucket][1] += abs(d) > 1
        fwd0 = forwards.get(min(by_exp)) if by_exp else None
        print(f"\n{sym} {snap_time:%H:%M} spot {spot:.2f} fwd(0DTE) {fwd0 if fwd0 is None else round(fwd0, 2)} contracts {len(contracts)} | "
              + " ".join(f"{b}: {v[1]}/{v[0]} >1pt" for b, v in buckets.items())
              + f" | ThetaData IV=0: {theta_zero} (ours fills {theta_zero_filled}) | no own IV: {no_own}"
              + (f" | median diff {sorted(diffs)[len(diffs) // 2]:+.2f} pt" if diffs else ""))
        # (b) levels rebuilt offline
        base_chain = OptionChain(symbol=sym, as_of=rows[0][17], spot_price=Decimal(str(spot)), contracts=tuple(contracts))
        repriced = []
        for k in contracts:
            mine = own.get(k.occ_symbol)
            if mine is None:
                repriced.append(k)
                continue
            years = _time_to_expiration_years(k.expiration, snap_time)
            bsm = calculate_bsm_greeks(Decimal(str(spot)), k.strike, Decimal(str(rate)), Decimal(str(mine)), years)
            repriced.append(replace(k, iv=Decimal(str(mine)), greeks=replace(k.greeks, gamma=bsm.gamma, charm=bsm.charm, vanna=bsm.vanna)))
        own_chain = replace(base_chain, contracts=tuple(repriced))
        for view in ("tactical", "0dte"):
            if view == "tactical":
                a, b = orch.build_tactical(sym, base_chain, bars), orch.build_tactical(sym, own_chain, bars)
            else:
                a, b = orch.build_expiration_view(sym, base_chain, bars, view), orch.build_expiration_view(sym, own_chain, bars, view)
            print(f"   {view:8s} ThetaData IV: {levels(a)} | own IV: {levels(b)}")
