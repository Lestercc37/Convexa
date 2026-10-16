"""ONE ThetaData request through the provider's own transport (its request slots): does the Terminal accept max_dte on /v3/option/snapshot/greeks/first_order with expiration=*, and what does it return?
Authorized by Lester 2026-10-15 ('si, haz la peticion'). Run outside 09:25-11:00 ET. Exactly one request (SPXW root, strike_range=100 like the provider, max_dte=45); stops on any error/empty body.
Saves the raw response compressed under V:/Convexa/hist/raw/max_dte_test/ and prints: HTTP result, seconds, number of contracts and expirations, the largest days-to-expiration returned, and the JSON size."""
import gzip
import json
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

today = datetime.now(EASTERN_TIME).date()
provider = build_container().market_data_provider
started = time.monotonic()
try:
    body = provider._get_json(  # noqa: SLF001 -- one deliberate probe; the strict variant: any non-200 raises, a 472 is NOT swallowed
        "/v3/option/snapshot/greeks/first_order", symbol="SPXW", expiration="*", strike_range=100, max_dte=45, format="json"
    )
except Exception as exc:
    print("STOP: la peticion fallo:", type(exc).__name__, str(exc)[:300])
    sys.exit(1)
elapsed = time.monotonic() - started
rows = body.get("response", [])
if not rows:
    print("STOP: respuesta vacia, elapsed %.1f s, keys %s" % (elapsed, list(body.keys())))
    sys.exit(1)
out_dir = Path("V:/Convexa/hist/raw/max_dte_test")
out_dir.mkdir(parents=True, exist_ok=True)
raw = json.dumps(body).encode("utf-8")
path = out_dir / ("greeks_first_order_SPXW_max_dte45_%s.json.gz" % datetime.now(EASTERN_TIME).strftime("%Y%m%d_%H%M%S"))
path.write_bytes(gzip.compress(raw))
exps = Counter(r["contract"]["expiration"] for r in rows)
dtes = sorted((date.fromisoformat(e) - today).days for e in exps)
print("OK http 200 | %.1f s | contratos %d | vencimientos %d | dias al vencimiento: min %d max %d | JSON %.1f MB (%s, %.1f MB comprimido)" % (
    elapsed, len(rows), len(exps), dtes[0], dtes[-1], len(raw) / 1e6, path.name, path.stat().st_size / 1e6))
print("contratos por vencimiento (primeros 12):", [(e, n) for e, n in sorted(exps.items())[:12]])
print("max_dte respetado (<= 45):", dtes[-1] <= 45)
