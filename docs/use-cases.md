# QLL — Casos de Uso

Cada caso de uso vive en `backend/domain/use_cases/` como una función/clase que orquesta ports (`IDataProvider`, `IStorage`, `IGreeksCalculator`, `INotificationService`) — nunca llama directamente a un adaptador concreto.

Se dividen en dos categorías según qué los dispara:

- **Orientados a cliente**: los dispara una petición REST o una suscripción WebSocket. Son de **solo lectura** desde la perspectiva del cliente — el cliente nunca "manda a calcular", solo consulta resultados ya calculados o persistidos.
- **Internos del motor**: los dispara un scheduler (cadencia fija) o el stream de datos entrante. No tienen endpoint propio — su resultado alimenta lo que los casos de uso orientados a cliente consultan.

---

## Orientados a cliente

### `GetOptionChain(underlying, expiration?)`
- **Disparador**: `GET /api/v1/chain/{symbol}`
- **Ports usados**: `IStorage.get_latest_chain_snapshot(...)`. Si no hay snapshot reciente (más viejo que el umbral de frescura configurado), cae a `IDataProvider.get_option_chain(...)` y persiste el resultado antes de responder.
- **Salida**: `OptionChain` (lista de `OptionContract` con `Greeks` embebidos).
- **Nota**: este es el único caso de uso "orientado a cliente" que puede tocar `IDataProvider` directamente (on-demand fetch), porque el chain crudo es la entrada de todo lo demás. El resto de casos de uso de cliente solo leen de `IStorage`.

### `GetGammaAggregate(underlying)`
- **Disparador**: `GET /api/v1/gamma/{symbol}` y canal WebSocket `gamma`.
- **Ports usados**: `IStorage.get_latest_gamma_aggregate(...)`.
- **Salida**: `GammaAggregate` con `dealer_position` derivado (signo de `net_gamma`, ver `docs/database-schema.md`).
- **No calcula nada** — solo lee el último `GammaAggregate` que el caso de uso interno `CalculateGammaAggregate` ya persistió.

### `GetGammaHistory(underlying, start, end)`
- **Disparador**: `GET /api/v1/gamma/{symbol}/history`
- **Ports usados**: `IStorage.get_gamma_history(...)`.
- **Salida**: lista de `GammaAggregate` en el rango.

### `GetFlow(underlying, since?, limit?)`
- **Disparador**: `GET /api/v1/flow/{symbol}`
- **Ports usados**: `IStorage.get_flow_events(...)`.
- **Salida**: lista de `FlowEvent` ya clasificados (persistidos por el caso de uso interno `ProcessFlow`).

### `BuildMarketSnapshot(underlying)`
- **Disparador**: `GET /api/v1/market/{symbol}` y canal WebSocket `market`.
- **Ports usados**: `IStorage.get_latest_price(...)` + `IStorage.get_latest_gamma_aggregate(...)` + `IStorage.get_recent_flow(...)`.
- **Salida**: `MarketSnapshot` — **proyección**, no se persiste (Domain Model v1.1). Compone tres fuentes con cadencias distintas: precio (alta frecuencia), `GammaAggregate` (baja frecuencia), Flow reciente.
- **Nota**: es el único caso de uso "orientado a cliente" que combina varias fuentes de `IStorage`, precisamente porque es una proyección — su trabajo es componer, no calcular de cero.

---

## Internos del motor (no tienen endpoint propio)

### `CalculateGammaAggregate(underlying)`
- **Disparador**: scheduler, cadencia configurable (default 1 min — ver `docs/architecture.md` sección 2).
- **Ports usados**: `IStorage.get_latest_chain_snapshot(...)` (lee el chain ya persistido, no vuelve a golpear al proveedor) → calcula Net Gamma, Gamma Flip, Call Wall, Put Wall, Dealer Position, Dealer Bias y demás métricas definidas por el proyecto → `IStorage.save_gamma_aggregate(...)`.
- **Incluye como sub-pasos** (no casos de uso separados con endpoint propio): `CalculateGammaFlip`, `CalculateCallPutWall` — son funciones internas del motor de cálculo, invocadas por este caso de uso, no expuestas individualmente.
- **Efecto secundario**: si el resultado cruza un umbral relevante (ej. cambio de `dealer_position`), llama a `INotificationService.notify(...)` — no-op hasta Etapa 8+.

### `ProcessFlow(underlying)`
- **Disparador**: continuo, sobre `IDataProvider.stream_trades(underlying)`.
- **Ports usados**: `IDataProvider.stream_trades(...)` → clasifica cada trade (sweep/block/unusual, vía la lógica de detección del Flow Engine) → `IStorage.save_flow_event(...)`.
- **Efecto secundario**: eventos que superan un umbral de relevancia disparan `INotificationService.notify(...)`.

#### Whale Alerts / ProcessFlow

Whale Alerts consume snapshots sucesivos de `OptionChain`, sin depender del proveedor que los
produzca. Por cada `occ_symbol` conserva el último volumen acumulado de sesión, calcula
`delta_volume = volumen_actual - volumen_anterior` y convierte cada lectura a dólares mediante
`monto = delta_volume × last × 100`. Una caída del acumulado se trata como reinicio de sesión:
se descarta toda ventana en curso (bucket del minuto, promedio de 5 y acumulado de 15) para evitar
falsos positivos.

**Granularidad real: bloques de 1 minuto, no cada lectura cruda.** Las lecturas (hoy disparadas
manualmente vía `/internal/trigger-calculation/{symbol}`, sin cadencia fija todavía) se agrupan en
buckets alineados al minuto calendario usando `chain.as_of` — mismo criterio de "floor al minuto"
que ya usa el frontend para las velas. Un bucket solo se clasifica (contra el umbral) cuando llega
una lectura del minuto *siguiente*, cerrándolo — la clasificación nunca ocurre sobre una lectura
parcial. `n1m` en la fórmula de abajo ya asumía "monto por minuto" (heredado del Pine Script
original); esto es la primera vez que el motor de Python lo hace cumplir de verdad.

La evaluación de Whale/Unusual empieza cuando existen cinco minutos anteriores completos — la
ventana móvil de 5 períodos ahora representa 5 minutos reales, no 2.5 minutos. El promedio usa solo
los montos de esos cinco minutos y excluye el actual, equivalente a `ta.sma(n1m[1], 5)`. Se emite
una sola alerta Whale/Unusual por minuto cerrado, dando prioridad a `WHALE`:

| Tipo | Monto mínimo | Múltiplo sobre promedio previo |
|---|---:|---:|
| `UNUSUAL` | $40,000 | 3.0× |
| `WHALE` | $150,000 | 6.0× |

**`SUSTAINED_FLOW`** es un tipo de alerta independiente, separado de Whale/Unusual: se dispara
cuando la suma de los últimos 15 minutos cerrados (mismo stream de montos-por-minuto, alimentando
un segundo `deque(maxlen=15)` en vez de un promedio) cruza `sustained_flow_min`. Se dispara una
sola vez mientras se mantenga por encima del umbral (flag `sustained_alerted` independiente por
contrato) y vuelve a estar disponible solo cuando el acumulado caiga por debajo y lo vuelva a
cruzar. A diferencia de Whale/Unusual, no hay precedente de un flag de "ya alertado" en el motor
de Python — este es nuevo, construido específicamente para este tipo de alerta.

| Tipo | Ventana | Monto mínimo |
|---|---:|---:|
| `SUSTAINED_FLOW` | 15 minutos acumulados | $500,000 |

Los valores son defaults configurables por símbolo y fueron calibrados originalmente para IWM
(Whale/Unusual) o son una calibración inicial sin datos reales todavía (`sustained_flow_min`).
Los demás símbolos deben comenzar con estos defaults, pero necesitan recalibración con datos
reales; no se presupone que una calibración de IWM sea válida, por ejemplo, para SPX.

El ciclo interno que obtiene y persiste el `OptionChain` entrega el mismo snapshot al motor. Las
alertas recientes se consultan sin disparar cálculo mediante `GET /api/v1/alerts/{symbol}`.

**Bulk Volume Classification (BVC) — estimación de compra/venta, no dato confirmado.** Cada alerta
(Whale, Unusual o Sustained Flow) incluye `estimated_buy_volume` y `estimated_sell_volume`,
etiquetados explícitamente como estimación — el motor nunca ve el lado real (compra o venta) de una
operación, solo infiere una probabilidad a partir del movimiento de precio. Método académico real,
no una fórmula propia:

> Easley, D., López de Prado, M., O'Hara, M. (2012). "Flow Toxicity and Liquidity in a
> High-Frequency World." *Review of Financial Studies*, 25(5), 1457-1493.

Por cada `occ_symbol`, a nivel de CONTRATO individual (cada opción tiene su propio precio y su
propia clasificación, nunca el subyacente):

1. `ΔP = precio_actual − precio_anterior` — precio (`contract.last`) de la lectura actual contra la
   lectura inmediatamente anterior del mismo contrato.
2. `σ` = desviación estándar poblacional de `ΔP` sobre una ventana móvil de las últimas 20 lecturas
   (`deque(maxlen=20)`, ajustable — ventana inicial del rango 20-30 sugerido).
3. `Z = ΔP / σ`.
4. `fracción_compra = Φ(Z)` — CDF normal estándar. Sin dependencia de `scipy` (no es dependencia del
   proyecto en ningún otro lado): `Φ(z) = 0.5 × (1 + erf(z/√2))` vía `math.erf` de la librería
   estándar — relación exacta, no una aproximación de `erf` en sí.
5. `volumen_compra_estimado = volumen_del_período × fracción_compra`,
   `volumen_venta_estimado = volumen_del_período × (1 − fracción_compra)`.

**Caso borde de `σ = 0`** (ventana vacía o sin varianza todavía): `fracción_compra = 0.5` — mismo
criterio documentado que usa `calculate_iv_rank` para una ventana de IV plana.

**Cadencia: por lectura cruda, no por minuto cerrado.** A diferencia de Whale/Unusual/Sustained Flow
(que operan sobre montos ya agregados por minuto), BVC clasifica cada lectura individualmente
—ΔP y σ se actualizan en cada llamada a `process()`— y el resultado de cada lectura se acumula
dentro del bucket del minuto en curso (`bucket_buy_volume`/`bucket_sell_volume`, mismo patrón que
`bucket_amount`). Al cerrar un minuto, el split de compra/venta reportado es la suma de todas las
lecturas individuales de ese minuto, no un único Z de "fin de minuto" aplicado a todo el volumen —
consistente con la metodología del paper (clasificar cada barra, agregar sobre cualquier ventana
mayor sumando). Para `SUSTAINED_FLOW`, el split reportado es la suma de los mismos 15 minutos
cerrados que ya alimentan su monto acumulado (dos `deque(maxlen=15)` adicionales, uno por lado).

`previous_price` y la ventana de `ΔP` viven en la misma estructura de estado en memoria por
contrato que ya mantenía el motor (`_ContractState`) — no se creó ninguna tabla nueva; el motor de
Whale Alerts nunca persistió su estado en base de datos, solo los umbrales configurados
(`whale_thresholds`).

---

## Resumen de mapeo a contratos existentes

| Caso de uso | REST | WebSocket |
|---|---|---|
| `GetOptionChain` | `GET /api/v1/chain/{symbol}` | canal `chain` |
| `GetGammaAggregate` | `GET /api/v1/gamma/{symbol}` | canal `gamma` |
| `GetGammaHistory` | `GET /api/v1/gamma/{symbol}/history` | — (histórico no se transmite por streaming) |
| `GetFlow` | `GET /api/v1/flow/{symbol}` | canal `flow` |
| `BuildMarketSnapshot` | `GET /api/v1/market/{symbol}` | canal `market` |
| `CalculateGammaAggregate` | — (interno) | — |
| `ProcessFlow` | — (interno) | — |

Ningún caso de uso interno tiene endpoint propio — confirma la regla del principio de esta sección: el cliente consulta, nunca dispara cálculo.
