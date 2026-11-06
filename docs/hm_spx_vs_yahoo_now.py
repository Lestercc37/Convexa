"""READ-ONLY. Convexa's stored SPX (last reading of each minute) vs Yahoo's ^GSPC and ES=F 1-minute closes today, last 20 minutes, plus SPY for scale."""
import json
import sys
import urllib.request
from datetime import datetime, time, timezone

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=5d"


def yahoo(symbol):
    req = urllib.request.Request(YAHOO.format(symbol=symbol), headers={"User-Agent": "Mozilla/5.0"})
    body = json.load(urllib.request.urlopen(req, timeout=15))["chart"]["result"][0]
    closes = body["indicators"]["quote"][0]["close"]
    return {datetime.fromtimestamp(t, timezone.utc).astimezone(EASTERN_TIME).strftime("%Y-%m-%d %H:%M"): v for t, v in zip(body["timestamp"], closes) if v is not None}


c = build_container()
now = datetime.now(EASTERN_TIME)
# optional: day HH:MM HH:MM (default: today from the open)
from datetime import date as _date
day = _date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else now.date()
h1, m1 = map(int, (sys.argv[2] if len(sys.argv) > 2 else "09:30").split(":"))
start = datetime.combine(day, time(h1, m1), EASTERN_TIME)
end = datetime.combine(day, time(*map(int, sys.argv[3].split(":"))), EASTERN_TIME) if len(sys.argv) > 3 else now
ours = {}
for h in c.storage.get_price_history("SPX", start, end):
    ours[h.as_of.astimezone(EASTERN_TIME).strftime("%Y-%m-%d %H:%M")] = float(h.price)
gspc, es = yahoo("%5EGSPC"), yahoo("ES%3DF")
print("now", now.strftime("%H:%M:%S"), "| Yahoo bars are labelled by their START minute; ours = last reading inside the minute")
for key in sorted(ours)[-40:]:
    y = gspc.get(key)
    e = es.get(key)
    print(f"{key} Convexa SPX {ours[key]:.2f} | Yahoo ^GSPC {y if y is None else round(y, 2)} | diff {None if y is None else round(ours[key] - y, 2)} | Yahoo ES=F {e if e is None else round(e, 2)}")
