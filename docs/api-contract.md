# Convexa — Contrato API REST

Base path: `/api/v1`. Formato: JSON. Toda respuesta incluye `schema_version` (ver `docs/nt8-contract.md` para por qué esto es una regla del proyecto, no solo de NT8).

## `GET /api/v1/underlyings`
Lista de underlyings soportados, con flag `is_priority`.

## `GET /api/v1/chain/{symbol}`
Cadena de opciones actual (último snapshot persistido).

Query params: `expiration` (opcional, filtra por vencimiento).

Respuesta (resumen):
```json
{
  "schema_version": 1,
  "symbol": "SPY",
  "as_of": "2026-07-21T14:32:00Z",
  "contracts": [
    { "occ_symbol": "...", "strike": 550, "type": "call", "bid": 1.2, "ask": 1.25,
      "iv": 0.18, "delta": 0.42, "gamma": 0.03, "open_interest": 12000, "volume": 3400 }
  ]
}
```

## `GET /api/v1/gamma/{symbol}`
Último snapshot de Gamma Exposure. Query param opcional `?scope=0dte|next_expiries` (default `0dte` para subyacentes prioritarios, que tienen ambas filas disponibles; ignorado para el resto del universo, que solo tiene `nearest`).

Respuesta (resumen):
```json
{
  "schema_version": 1,
  "symbol": "SPY",
  "as_of": "2026-07-21T14:32:00Z",
  "expiration_scope": "0dte",
  "zero_dte_pct_of_total": 62.4,
  "gamma_flip": 548.5,
  "call_wall": 555,
  "put_wall": 540,
  "absolute_gamma_strike": 550,
  "max_pain": 550,
  "net_gamma": -1250000,
  "dealer_position": "short_gamma"
}
```
`dealer_position` es un campo **derivado** (signo de `net_gamma`), calculado al construir la respuesta — no existe como columna en la base de datos (ver `docs/database-schema.md`, tabla `gamma_aggregates`). `zero_dte_pct_of_total` solo aparece cuando `expiration_scope=0dte` — indica qué proporción del GEX total representa el 0DTE, tomado directo de la fuente de datos.

### Métricas derivadas (`derived_metrics`, ver `docs/derived-metrics.md` para las fórmulas)

Se agregan como un objeto anidado en la misma respuesta de `GET /api/v1/gamma/{symbol}`:

```json
"derived_metrics": {
  "dealer_impact_score": { "value": 78, "provisional": false, "days_accumulated": 60 },
  "signal_alignment_score": { "value": 62, "provisional": false, "days_accumulated": 60 },
  "market_bias": { "score": 71.2, "label": "bullish", "provisional": false, "days_accumulated": 60 },
  "volatility_regime": { "iv_rank": 42.5, "label": "moderate", "provisional": false, "days_accumulated": 60 }
}
```

Mientras `days_accumulated < 20`: `value`/`score`/`label` son `null`, `provisional: true`. `Signal Alignment Score` es la única excepción parcial — se calcula desde el día uno con los dos componentes que no requieren historial (`agreement` + `freshness`, re-ponderados 60/40), y ya no está en `null` incluso con `provisional: true`, hasta llegar a 20 días donde se incorpora el tercer componente y `provisional` pasa a `false`.

Regla de presentación (obligatoria, ver `derived-metrics.md` secciones 4 y 6, no negociable en el frontend): `dealer_impact_score`, `signal_alignment_score` y `market_bias` nunca se muestran sin la nota "métrica propia de Convexa, no un estándar de mercado" — la API no repite esa nota como texto en cada respuesta (sería ruido en el payload), es responsabilidad del frontend mostrarla junto a esos tres campos. `volatility_regime` es distinto: IV Rank sí es un concepto estándar de industria — lo que debe indicarse en su lugar, si se muestra la ventana usada, es "60 días" explícitamente (no sugerir el estándar de 52 semanas/252 días).

## `GET /api/v1/gamma/{symbol}/history`
Serie histórica de Gamma Exposure. Query params: `start`, `end`.

## `GET /api/v1/flow/{symbol}`
Últimos eventos de flujo clasificados (sweeps/blocks). Query params: `since`, `limit`.

## `GET /api/v1/market/{symbol}`
Proyección `MarketSnapshot` (compuesta, no persistida — ver `docs/architecture.md` sección 2 y `docs/use-cases.md`, caso de uso `BuildMarketSnapshot`). Combina precio (alta frecuencia) + último `GammaAggregate` (baja frecuencia) + confirmación de régimen.

**Confirmación de régimen (Dealer Mode)**: se calcula con dos chequeos independientes, ambos derivados, ninguno persistido:
1. Signo de `net_gamma` (del último `GammaAggregate`).
2. Posición del precio actual respecto a `gamma_flip` (por encima → régimen largo; por debajo → régimen corto).

Normalmente concuerdan. Si divergen (el precio se movió después del último snapshot de `GammaAggregate` y cruzó el flip antes de que el motor recalculara), el chequeo por precio (`gamma_flip`) tiene prioridad — es más reciente que el agregado completo, que depende de la cadencia de recálculo (default 1 min).

Respuesta (resumen):
```json
{
  "schema_version": 1,
  "symbol": "SPY",
  "as_of": "2026-07-21T14:32:05Z",
  "price": 549.10,
  "gamma_flip": 548.5,
  "call_wall": 555,
  "put_wall": 540,
  "absolute_gamma_strike": 550,
  "dealer_mode": "long_gamma",
  "dealer_mode_source": "price_vs_flip",
  "dealer_mode_confirmed": true,
  "gamma_as_of": "2026-07-21T14:32:00Z"
}
```
- `dealer_mode`: `long_gamma` | `short_gamma` — el valor final ya resuelto (con la prioridad de precio-vs-flip si hubo divergencia).
- `dealer_mode_source`: `agree` (ambos chequeos coinciden) | `price_vs_flip` (hubo divergencia y ganó el precio).
- `dealer_mode_confirmed`: `false` cuando hubo divergencia — el cliente puede usarlo para mostrar el badge con un estado de menor confianza.
- `gamma_as_of`: timestamp del último `GammaAggregate` usado, distinto de `as_of` (que es el del precio) — hace explícito que son dos frecuencias distintas.

## Errores

Formato uniforme:
```json
{ "schema_version": 1, "error": { "code": "PROVIDER_UNAVAILABLE", "message": "..." } }
```

Códigos base: `NOT_FOUND`, `PROVIDER_UNAVAILABLE`, `INVALID_PARAMS`, `INTERNAL_ERROR`.

## Versionado

Cambios incompatibles → nueva versión de path (`/api/v2/...`), nunca se rompe `/v1` en producción mientras exista un cliente activo (incluyendo el indicador NT8). Cambios aditivos (campos nuevos opcionales) no incrementan `schema_version`.
