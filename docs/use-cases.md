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
`delta_volume = volumen_actual - volumen_anterior` y convierte el período a dólares mediante
`monto = delta_volume × last × 100`. Una caída del acumulado se trata como reinicio de sesión:
se descarta ese período y se reinicia su ventana para evitar falsos positivos.

La evaluación empieza cuando existen cinco períodos anteriores completos. El promedio usa solo
los montos de esos cinco períodos y excluye el actual, equivalente a `ta.sma(n1m[1], 5)`. Se emite
una sola alerta por período, dando prioridad a `WHALE`:

| Tipo | Monto mínimo | Múltiplo sobre promedio previo |
|---|---:|---:|
| `UNUSUAL` | $40,000 | 3.0× |
| `WHALE` | $150,000 | 6.0× |

Los valores son defaults configurables por símbolo y fueron calibrados originalmente para IWM.
Los demás símbolos deben comenzar con estos defaults, pero necesitan recalibración con datos
reales; no se presupone que una calibración de IWM sea válida, por ejemplo, para SPX.

El ciclo interno que obtiene y persiste el `OptionChain` entrega el mismo snapshot al motor. Las
alertas recientes se consultan sin disparar cálculo mediante `GET /api/v1/alerts/{symbol}`.

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
