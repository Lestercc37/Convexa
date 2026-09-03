# QLL — Casos de Uso

Cada caso de uso vive en `backend/domain/use_cases/` como una función/clase que orquesta ports (`IDataProvider`, `IStorage`, `IGreeksCalculator`, `INotificationService`) — nunca llama directamente a un adaptador concreto.

Se dividen en dos categorías según qué los dispara:

- **Orientados a cliente**: los dispara una petición REST o una suscripción WebSocket. Son de **solo lectura** desde la perspectiva del cliente — el cliente nunca "manda a calcular", solo consulta resultados ya calculados o persistidos. **Única excepción, explícita:** `PATCH /api/v1/whale-thresholds/{symbol}` — ver la sección de Whale Alerts más abajo para la justificación.
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

**Bulk Volume Classification (BVC) — reemplazada por Lee-Ready como método principal; se mantiene
como fallback honesto para proveedores sin streaming en vivo.** Todo lo que sigue en esta subsección
describe `process()` tal como sigue funcionando HOY, sin cambios, para `MockDataProvider`, para los
tests, y para cualquier proveedor REST-only futuro — nunca se borró, porque sigue siendo la única
clasificación disponible cuando no hay Trade Stream + Quote Stream en vivo detrás. Ver más abajo
("Lee-Ready — clasificación real por operación individual") para el mecanismo nuevo, el que
`ThetaDataProvider` usa en su lugar. Cada alerta (Whale, Unusual o Sustained Flow) incluye
`estimated_buy_volume` y `estimated_sell_volume`, etiquetados explícitamente como estimación — el
motor nunca ve el lado real (compra o venta) de una operación, solo infiere una probabilidad a partir
del movimiento de precio. Método académico real, no una fórmula propia:

> Easley, D., López de Prado, M., O'Hara, M. (2012). "Flow Toxicity and Liquidity in a
> High-Frequency World." *Review of Financial Studies*, 25(5), 1457-1493.

Por cada `occ_symbol`, a nivel de CONTRATO individual (cada opción tiene su propio precio y su
propia clasificación, nunca el subyacente):

1. `ΔP = precio_actual − precio_anterior` — precio (`contract.last`) de la lectura actual contra la
   lectura inmediatamente anterior del mismo contrato.
2. `σ` = desviación estándar poblacional de `ΔP` sobre una ventana móvil de los últimos 10 minutos
   reales (`_PRICE_VOLATILITY_WINDOW`, timedelta — **no** un conteo de lecturas). Originalmente era
   `deque(maxlen=20)` (últimas 20 lecturas), correcto solo mientras las lecturas llegaran a cadencia
   más o menos estable (~30s, ~10 minutos reales para 20 lecturas). Al investigar la arquitectura para
   un futuro proveedor por streaming (ThetaData, push en vez de pull) quedó claro que esa suposición se
   rompe con lecturas a intervalos irregulares: 20 lecturas podrían representar 2 segundos en una
   ráfaga o 20 minutos en una calma, cambiando qué mide realmente σ sin que el código lo note. Se ancló
   a tiempo transcurrido real en su lugar — mismo criterio que ya usan `previous_amounts`/
   `sustained_amounts`, anclados a minutos calendario finalizados, no a conteo de lecturas.
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

**Lee-Ready — clasificación real por operación individual, para proveedores con streaming en vivo.**
Reemplaza a BVC como método principal de clasificación compra/venta en `ThetaDataProvider`. Mientras
BVC infiere una *probabilidad* a partir del movimiento de precio agregado de un período, Lee-Ready
clasifica *cada operación* contra la cotización bid/ask realmente vigente en ese instante — una señal
real de microestructura de mercado, no un proxy estadístico. Requiere datos por operación individual
(precio + una cotización contemporánea), algo que BVC nunca tuvo disponible porque se construyó
cuando el proyecto solo tenía datos OHLC agregados de FlashAlpha. Método académico real:

> Lee, C. M. C., & Ready, M. J. (1991). "Inferring Trade Direction from Intraday Data." *The Journal
> of Finance*, 46(2), 733-746.

*Investigación previa, confirmada antes de escribir código:*

1. **¿ThetaData tiene un Quote Stream real, separado del Trade Stream?** Confirmado contra la
   documentación pública de ThetaData v3
   (`Streaming/US-Options/Quote-Stream`, `docs.thetadata.us`) — sí, y es un espejo estructural exacto
   del Trade Stream ya implementado: mismo `msg_type`/`sec_type`/forma de `contract`, solo
   `req_type: "QUOTE"` en vez de `"TRADE"`, y un objeto `quote` (`bid`, `ask`, `bid_size`,
   `ask_size`, `ms_of_day`, ...) en vez de `trade`. No existía ninguna implementación de esto en el
   proyecto antes de este PR — solo un comentario en esta misma página (ver la sección
   `ThetaDataProvider` más abajo) que documentaba haber confirmado su existencia durante la
   investigación de PR #76, sin construirlo entonces porque nada lo necesitaba todavía.
2. **Mecanismo de sincronización bid/ask ↔ operaciones.** Exactamente el propuesto antes de
   implementar: una suscripción paralela al nuevo Quote Stream, actualizando un
   `dict[occ_symbol, LatestQuote]` en memoria (sin historial indexado por tiempo — solo el último
   valor recibido por contrato), consultado por el clasificador cada vez que llega un trade. Esto
   coincide, sin necesidad de construir nada más preciso, con la precisión de timestamping que ya
   tiene el resto del proyecto: `FlowEvent.as_of` ya se marca al momento de recepción local
   (`utc_now()`), no con el timestamp real de la bolsa — "vigente en ese instante" en un stream en
   vivo se reduce, en la práctica, a "el más reciente recibido hasta ahora".
3. **Qué se reutiliza de `flow.py`, qué se reemplaza.** El bucketing por minuto, el promedio de 5
   minutos, la ventana de Sustained Flow de 15 minutos y el flag `sustained_alerted` son
   clasificador-agnósticos — nunca les importó *cómo* se calculó el split compra/venta de una
   lectura, solo que exista como par `(buy_volume, sell_volume)` para acumular. Se extrajo ese tramo
   compartido a `WhaleAlertsEngine._finalize_bucket()`, usado tanto por `process()` como por el nuevo
   `process_trade()`. Lo que sí cambia por completo es el cálculo mismo de la clasificación
   (`calculate_bvc_split` → `classify_trade_side`, `calculate_lee_ready.py`) y las entradas que lo
   alimentan (`ΔP`/`σ` sobre una ventana móvil → precio de la operación + bid/ask vigente + precio de
   la operación anterior, sin ventana móvil de ningún tipo).
4. **Caso borde: operación sin ninguna cotización conocida todavía** (ej. justo al arrancar, antes de
   que llegue el primer mensaje del Quote Stream para ese contrato). Mismo criterio documentado que
   usa `calculate_bvc_split` para `σ = 0` y `calculate_iv_rank` para una ventana de IV plana: split
   neutral 50/50, no una adivinanza. Toma prioridad incluso cuando el tick rule sí tendría una
   dirección disponible.

**El algoritmo, en `classify_trade_side` (`calculate_lee_ready.py`), función pura:**

1. **Quote rule** (regla principal): si el precio de la operación está por encima del punto medio
   `(bid + ask) / 2` de la cotización vigente → iniciada por el comprador. Por debajo → iniciada por
   el vendedor.
2. **Tick rule** (desempate, solo si la operación imprime exactamente en el punto medio): comparar
   contra el precio de la operación anterior del mismo contrato — más alto que la anterior = 
   comprador, más bajo = vendedor, exactamente igual ("zero tick") = mantener la clasificación de la
   operación anterior en vez de adivinar.
3. **Sin cotización vigente** (`bid`/`ask` desconocidos): neutral, ver caso borde #4 arriba.

**`process_trade(event: FlowEvent, quote: LatestQuote | None)` — nuevo método del motor, estado
separado de `process()`.** Alimentado por `StreamWhaleAlertsUseCase`
(`backend/domain/use_cases/stream_whale_alerts.py`), que consume `IDataProvider.stream_trades()` y el
nuevo `IDataProvider.stream_quotes()` concurrentemente, mantiene el `dict[occ_symbol, LatestQuote]`
descrito arriba, y llama a `engine.process_trade(trade_event, ultima_cotización_conocida)` por cada
operación. Deliberadamente **NO comparte** `_states` con `process()` — usa su propio diccionario
(`_trade_states`), reutilizando la misma `_ContractState` como forma de datos (con un campo nuevo,
`previous_side`, que `process()`/BVC nunca lee ni escribe) pero en una instancia completamente
separada por contrato. Esto es a propósito: bajo `ThetaDataProvider`, el scheduler REST sigue
consultando greeks/IV/OI en su cadencia habitual (`docs/use-cases.md`, sección `ThetaDataProvider`
más abajo — nada de eso tiene equivalente por streaming), así que `process(chain)` puede seguir
ejecutándose para el mismo `occ_symbol` al mismo tiempo que `process_trade()` — con estado
compartido, el mismo volumen se contaría dos veces en dos buckets independientes. `price` se recupera
de `event.premium / (event.size × 100)` (deshaciendo exactamente la multiplicación que
`ThetaTradeStream._handle_trade` ya hace) en vez de agregar un campo nuevo a `FlowEvent` — ese
dataclass también se reconstruye desde una fila de Postgres sin columna de precio propia
(`PostgreSQLStorage.get_flow_events`), fuera del alcance de este cambio.

**Limitación conocida, fuera de alcance de este PR:** a diferencia de `process()`, `process_trade()`
no tiene una detección de reinicio de sesión equivalente (un stream continuo de operaciones no tiene
un contador de volumen que pueda "bajar" para señalizarlo) — las ventanas móviles de 5 y 15 minutos
no se limpian explícitamente al cruzar la medianoche/apertura de sesión. Reemplazar el mecanismo de
clasificación era el único objetivo de este cambio; el manejo de reinicio de sesión para el camino de
streaming queda como un problema separado a revisar más adelante.

**`ThetaQuoteStream` — nueva clase en `backend/adapters/providers/thetadata/provider.py`, análoga
directa a `ThetaTradeStream`.** Misma dureza de reconexión (heartbeat `STATUS`, backoff exponencial
2s/4s/8s.../60s tope) — sin la reconciliación periódica contra REST que sí tiene el Trade Stream,
porque las cotizaciones no tienen ningún concepto de "volumen acumulado" contra el cual reconciliar.
Usa **una conexión WebSocket separada** de `ThetaTradeStream`, no una que multiplexe ambas
suscripciones (`TRADE` y `QUOTE`) sobre el mismo socket — el protocolo de ThetaData sí soporta eso,
pero mantener el Trade Stream ya validado (con dos incidentes reales de desconexión documentados en
su propio docstring) completamente intacto valió más que ahorrarse una conexión adicional, liviana,
hacia un Theta Terminal local (no un servidor remoto con límite de tasa). `IDataProvider` gana
`stream_quotes(underlying) -> AsyncIterator[QuoteEvent]` junto a `stream_trades`, y
`MockDataProvider.stream_quotes` es el mismo patrón `if False: yield` que ya usa `stream_trades` — un
generador async válido, inmediatamente agotado.

**Conectado de punta a punta, no solo implementado.** La brecha que dejó PR #76 ("`stream_trades()`
... sin consumidor todavía", documentado explícitamente en la sección `ThetaDataProvider` de esta
misma página) se cierra aquí: `WhaleAlertsStreamManager`
(`backend/core/whale_alerts_stream.py`) crea una tarea `asyncio.Task` de larga vida por cada símbolo
de `ACTIVE_UNDERLYINGS`, cada una corriendo `StreamWhaleAlertsUseCase.run(symbol)` — mismo patrón
`start()`/`stop()` que ya usa `UnderlyingRefreshScheduler`, pero con N tareas concurrentes de larga
vida en vez de un solo ciclo periódico (las suscripciones de streaming nunca retornan por sí solas, a
diferencia de un poll REST programado, así que no pueden recorrerse secuencialmente como sí hace el
ciclo del scheduler). Se arranca/detiene en `backend/main.py`, con el mismo flag
`enable_scheduler` que ya usa el scheduler — `tests/conftest.py` ya pone ese flag en `False` para
cualquier test que abra `TestClient(app)`, así que ningún test existente empieza a abrir conexiones de
red reales por este cambio. Es un no-op inofensivo bajo `MockDataProvider` (sus dos streams son un
generador async inmediatamente agotado), consistente con el mismo criterio de "agnóstico al proveedor
concreto" que ya sigue el scheduler.

**Umbrales editables en caliente, sin reiniciar el backend.** Hasta este punto,
`WhaleAlertsEngine` construía su `thresholds_by_symbol` una sola vez, al arrancar
(`build_container()`), a partir de `storage.get_whale_thresholds()` — cualquier edición hecha
directamente en la base de datos requería reiniciar el proceso para tener efecto. Se investigaron
tres mecanismos antes de elegir uno:

- *Reconstruir el motor completo por request* — descartado: `WhaleAlertsEngine` también mantiene
  `_states` (la memoria de ventana por contrato — minuto en curso, promedio de 5, acumulado de 15,
  ventana de precio de BVC) y `_alerts` (historial). Reconstruir el motor las borraría, rompiendo
  la ventana móvil por completo.
- *Caché con invalidación por tiempo* — descartado: complejidad real (reloj, ventana de staleness,
  invalidación) para una tabla de 11 filas consultada por un endpoint (`/internal/trigger-calculation`)
  disparado manualmente hoy, no por un scheduler de alta frecuencia — resuelve un problema de
  rendimiento que todavía no existe.
- **Elegido: el motor lee `storage.get_whale_thresholds()` en vivo, dentro de cada llamada a
  `process()`.** `IStorage` es un `Protocol` de dominio (`backend/domain/ports/interfaces.py`), no
  un adaptador — varios casos de uso ya reciben `storage: IStorage` directamente
  (`get_flow`, `BuildMarketSnapshot`), así que esto es consistente con el patrón ya establecido, no
  uno nuevo. `WhaleAlertsEngine.__init__` ahora recibe `storage` en vez de un diccionario congelado
  de umbrales; `_states`/`_alerts` no cambian. Costo: un `SELECT` adicional, barato, por cada
  llamada a `process()` — aceptable dado el volumen actual.

**El primer endpoint de escritura de toda la API — justificación explícita.** `PATCH
/api/v1/whale-thresholds/{symbol}` actualiza `unusual_min`, `whale_min`, `unusual_multiplier`,
`whale_multiplier` y `sustained_flow_min` para un símbolo (los 5 campos juntos, reemplazo completo,
no actualización parcial — `IStorage.save_whale_threshold` persiste una fila `WhaleThreshold`
completa). Rompe deliberadamente la regla de "solo lectura" de arriba porque los umbrales de Whale
Alerts no son un dato de mercado ni un resultado calculado — son una **calibración del operador**
(ya documentada arriba como "defaults... necesitan recalibración con datos reales; no se presupone
que una calibración de IWM sea válida para SPX"). Editarlos ajusta cómo el motor (de solo lectura)
clasifica actividad futura; no es el cliente "mandando a calcular" nada. Antes de esta pieza, la
única forma de ajustar un umbral era SQL manual en pgAdmin — el endpoint reemplaza eso, no introduce
una capacidad nueva de negocio. Valida que los 5 campos sean números positivos (`Field(gt=0)`) y que
el símbolo sea uno de los 11 ya configurados en `ACTIVE_UNDERLYINGS` (404 si no, para no crear una
fila de `underlyings` nueva por accidente vía el upsert interno de `_ensure_underlying`).
`GET /api/v1/whale-thresholds` (los 11 símbolos con sus umbrales vigentes) se agregó junto con el
PATCH — no existía forma de leer los umbrales actuales vía API antes de esto, necesaria para que el
panel de edición del frontend pueda mostrar los valores vigentes antes de editarlos.

**Screener Presets — criterios de filtro editables.** Los 5 presets de `get_screener_preset`
(`backend/domain/use_cases/screener_presets.py`) resultaron tener criterios muy distintos entre sí
al auditarlos — ninguno tenía un mecanismo de configuración previo:

- `unusual-options-activity`: sin filtro propio — muestra las alertas que `WhaleAlertsEngine` ya
  emitió; su configuración real ya vive en `whale_thresholds` (arriba). No se duplica nada aquí.
- `negative-gamma-board`: el único con un umbral real, hardcodeado como `net_gamma < 0`.
- `max-pain-key-levels`: sin filtro — devuelve todos los subyacentes persistidos, incondicional.
- `vanna-exposure-leaders` / `charm-decay-pressure`: sin filtro ni tope — solo ordenan por
  `abs(vanna_exposure)`/`abs(charm_exposure)` descendente.

Decisión, confirmada antes de implementar: de los 5, solo 3 obtienen parámetros editables —
`negative-gamma-board` (`net_gamma_max`, reemplaza el `0` hardcodeado), `vanna-exposure-leaders` y
`charm-decay-pressure` (`min_magnitude` y `limit`/top-N cada uno, ambos opcionales). Los otros 2 no
tienen ningún escalar sensato que exponer.

**Esquema: una tabla JSONB flexible, con un dataclass congelado por preset validando en el dominio.**
`screener_preset_settings (preset text PRIMARY KEY, parameters jsonb NOT NULL)` — una sola fila por
preset configurable, con la forma del JSON dependiendo de cuál preset es. Deliberadamente **no** un
blob sin tipar: `NegativeGammaBoardSettings` y `ExposureLeadersSettings`
(`backend/domain/entities/entities.py`, junto a `ScreenerPreset` — movido ahí desde el use case para
evitar el ciclo de imports `ports → use_cases → ports`, mismo motivo por el que `WhaleThreshold` ya
vivía en `entities.py`) son `@dataclass(frozen=True)` que validan sus propios campos en
`__post_init__` (decimal finito, `min_magnitude` no negativo, `limit` entero positivo) — el JSON es
solo el mecanismo de persistencia, nunca el tipo con el que trabaja el dominio.
`_screener_preset_settings_to_json`/`_from_json` en `PostgreSQLStorage`
(`backend/adapters/storage/postgresql.py`) hacen la conversión; los `Decimal` se serializan como
strings dentro del JSON (nunca como número JSON), mismo criterio de precisión que el resto del
proyecto usa en cada round-trip a base de datos. Un preset sin fila persistida (o sin settings del
tipo esperado) cae al valor por defecto que reproduce el comportamiento de antes de este PR:
`NegativeGammaBoardSettings()` tiene `net_gamma_max=0` (el preset es inherentemente sobre gamma
negativo, así que "sin configurar" nunca puede significar "sin tope"); `ExposureLeadersSettings()`
tiene ambos campos en `None` (los dos presets de exposición eran un ranking incondicional, así que
"sin configurar" debe seguir siéndolo).

**Mismo patrón ya probado en `whale_thresholds`: lectura en vivo, no cacheada al arrancar.**
`get_screener_preset` llama a `storage.get_screener_preset_settings(preset)` en cada invocación —
una edición vía `PATCH` tiene efecto en la siguiente consulta, sin reiniciar el backend, exactamente
igual que `WhaleAlertsEngine` con `whale_thresholds`.

**Segunda y tercera excepción a la API de solo lectura, misma justificación que `whale_thresholds`.**
`PATCH /api/v1/screener-preset-settings/{preset_name}` — igual que los umbrales de Whale Alerts, esto
no es el cliente "mandando a calcular" nada: es calibración de cómo un preset (de solo lectura) filtra
resultados futuros. `GET /api/v1/screener-preset-settings` devuelve los 3 presets configurables con
sus valores vigentes (forma plana/unioned, mismo criterio que `ScreenerPresetResponse` ya usa para la
heterogeneidad de campos entre presets — solo los campos que aplican a cada preset vienen no-`null`).
El `PATCH` es reemplazo completo por preset (no parcial), con una particularidad nueva frente a
`WhaleThresholdUpdateRequest`: distingue "campo enviado explícitamente como `null`" (válido — limpia
ese filtro, aplica solo a `vanna-exposure-leaders`/`charm-decay-pressure`) de "campo no enviado en
absoluto" (rechazado con 422) vía `body.model_fields_set` de Pydantic, en la ruta
(`backend/api/routes/screener_presets.py`) — necesario porque `min_magnitude`/`limit` son ambos
genuinamente opcionales (a diferencia de los 5 campos de `WhaleThresholdUpdateRequest`, todos
obligatorios). `unusual-options-activity` y `max-pain-key-levels` devuelven 404 en el `PATCH` — no
tienen fila de configuración que editar.

**Scheduler Automático de Cálculo — reemplaza el disparo manual periódico.** Hasta este PR, la única
forma de refrescar snapshot + gamma + métricas derivadas para un símbolo era `POST
/internal/trigger-calculation/{symbol}` (endpoint interno, sin UI, uno por vez) — sin ningún proceso
que lo disparara solo. Se investigaron tres cosas antes de escribir código:

1. **¿Existe ya un chequeo de horario de mercado reutilizable?** La premisa inicial asumía que sí
   (del PR de bucketing de 1 minuto de Whale Alerts) — resultó ser falsa. `WhaleAlertsEngine.process()`
   (`backend/domain/use_cases/flow.py`) bucketiza por `chain.as_of` pero no tiene ningún gate de
   horario de sesión; no existía ningún `is_market_open` en el proyecto. Sí hay precedente de usar
   `ZoneInfo("America/New_York")` en la capa de dominio (`calculate_anchored_vwap.py`,
   `calculate_derived_metrics.py`, `calculate_expected_move.py`), pero todos calculan el ancla de
   sesión (9:30 ET), no un booleano de horario — se construyó `is_market_open` desde cero, siguiendo
   ese mismo estilo (`zoneinfo`, dominio puro).
2. **¿`trigger_calculation` maneja errores por símbolo?** No — cero `try`/`except`, cualquier
   excepción en cualquiera de sus 6 pasos se propaga sin aislarse (el único handler es el `QllError`
   global de `backend/main.py`, que no distingue por símbolo). El scheduler tiene que aislar cada
   símbolo por su cuenta.
3. **¿Qué mecanismo de tarea periódica?** No había ninguna infraestructura previa (sin APScheduler, sin
   `BackgroundTasks`, sin Celery) — se eligió un loop `asyncio` nativo (`asyncio.create_task` +
   `asyncio.sleep`), arrancado/detenido dentro del `lifespan` ya existente de `backend/main.py`, en vez
   de agregar una dependencia como APScheduler para un caso de 11 símbolos cada 30 segundos que no
   necesita jobstores, persistencia ni varios triggers concurrentes.

**Extracción a un caso de uso compartido, no duplicación.** El cuerpo de `trigger_calculation` (6
pasos: traer cadena de opciones, persistirla, alimentar Whale Alerts, traer/persistir precio y barras
diarias, recalcular exposición gamma y métricas derivadas) se extrajo a
`RefreshUnderlyingSnapshotUseCase` (`backend/domain/use_cases/refresh_snapshot.py`) — mismo patrón que
`capture_daily_gamma_reference`: una función/clase de dominio reutilizada por dos llamadores. El
endpoint manual y el scheduler la llaman por igual; ninguno de los dos invoca al otro (se descartó
explícitamente que el scheduler llamara al endpoint por HTTP interno — más indirecto y frágil que
compartir la función de dominio directamente). El endpoint mantiene el mismo contrato externo
(`TriggerCalculationResponse` sin cambios).

**`UnderlyingRefreshScheduler` (`backend/core/scheduler.py`) — infraestructura, no dominio.** Vive en
`backend/core/` (junto a `container.py`), no en `backend/domain/`, porque coordina `asyncio`, logging
y el `Container` completo — son responsabilidades de composición/infraestructura, no lógica de
negocio. Un único `asyncio.Task` corre durante toda la vida del proceso, arrancado en el `lifespan` de
`backend/main.py` justo después de construir el container, y cancelado limpiamente en su bloque
`finally`. Cada ciclo (cada 30s, solo si `is_market_open(datetime.now(UTC))`) recorre
`ACTIVE_UNDERLYINGS` y llama a `RefreshUnderlyingSnapshotUseCase.execute(symbol)` por cada uno dentro
de `asyncio.to_thread(...)` — hoy sobre `MockDataProvider` (síncrono, en memoria) esto no cambia nada
observable, pero mantiene al scheduler agnóstico del proveedor: el día que se conecte FlashAlpha real
(HTTP síncrono), una llamada lenta ya no congelaría el event loop ni el resto de la API mientras un
ciclo está en curso. Cada símbolo se envuelve en su propio `try`/`except Exception` con
`logger.exception(...)` — un fallo de un símbolo no interrumpe a los demás ni tira abajo el ciclo. Al
cerrar cada ciclo se loggea un resumen (símbolos exitosos/fallidos, con los símbolos fallidos
nombrados) — para poder diagnosticar sin adivinar, como pidió la tarea.

**Deshabilitado durante tests, no solo "no llamado".** `Settings.enable_scheduler` (default `True`)
se fuerza a `False` en el fixture `autouse` de `tests/conftest.py` para todo test no marcado
`integration` — cualquier test que abra `TestClient(app)` dispara el `lifespan` real, y sin este flag
arrancaría un scheduler de verdad (intervalo de 30s, iteraciones inmediatas si el reloj real cae en
horario de mercado) corriendo de fondo durante ese test, con efectos secundarios no deterministas
según la hora real de ejecución de la suite. Mismo mecanismo ya usado por ese fixture para forzar
SQLite en memoria en vez de la base de datos real del desarrollador.

**Límite conocido y aceptado explícitamente: sin calendario de feriados bursátiles.**
`is_market_open` (`backend/domain/use_cases/market_hours.py`) solo verifica día hábil (lunes-viernes)
y horario (9:30am-4:00pm ET, intervalo semiabierto `[9:30, 16:00)`) — ningún feriado bursátil
(Acción de Gracias, Navidad, etc.) está contemplado, porque no existe ningún calendario de feriados en
el proyecto. El scheduler intentará correr en un feriado entre semana; documentado en el docstring de
la función y en `docs/dashboard-spec.md` (sección 21) como limitación conocida y deliberada, no un
descuido — construir un calendario de feriados completo queda fuera de alcance de este PR.

**Tests:** `tests/test_market_hours.py` (abierto en sesión regular, exactamente en el límite de
apertura/cierre —intervalo semiabierto—, cerrado fuera de horario, cerrado en fin de semana, acepta
cualquier timezone de entrada y convierte a ET, y un test que documenta explícitamente la limitación
de feriados en vez de asumir una corrección que la función no ofrece), `tests/test_scheduler.py` (un
ciclo procesa los 11 símbolos activos, un ciclo continúa con los 10 restantes si uno falla, el loop no
corre ningún ciclo fuera de horario de mercado, el loop sí corre durante horario de mercado,
`start()`/`stop()` son seguros de llamar repetidamente o antes de arrancar).

**`ThetaDataProvider` — proveedor real, conectado a un Theta Terminal v3 local.** Reemplaza a
`MockDataProvider` cuando `QLL_DATA_PROVIDER=thetadata` (default: `mock`) — mecanismo de configuración
elegido específicamente para poder volver a Mock en cualquier momento sin cambiar código, si hace
falta diagnosticar algo sin gastar cupo real. Toda la investigación previa a este PR se hizo con
llamadas reales contra `http://localhost:25503` (REST) y `ws://127.0.0.1:25520/v1/events` (streaming),
con una cuenta real en tier Options Standard — no se adivinó ninguna forma de respuesta. (`FlashAlpha`
se investigó primero como proveedor candidato, con hallazgos equivalentes en varios puntos — pero
`FlashAlphaDataProvider` nunca se implementó; se optó por ThetaData antes de llegar a esa etapa.)

Fuente real por campo, confirmada en vivo antes de escribir el adaptador:

- **bid/ask/delta/theta/vega/IV/underlying_price**: una sola llamada a
  `GET /v3/option/snapshot/greeks/first_order` — Options Standard ya la incluye completa.
- **open_interest**: `GET /v3/option/snapshot/open_interest`.
- **gamma/vanna/charm**: el endpoint que los trae directo (`greeks/second_order`) exige tier
  Professional (confirmado con un 403 real) — se calculan en su lugar vía Black-Scholes-Merton
  (`backend/domain/use_cases/calculate_bsm_greeks.py`), sin dividendos:
  ```
  d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)
  d2 = d1 - σ√T
  Gamma = φ(d1) / (S σ √T)
  Vanna = -φ(d1) · d2 / σ
  Charm = -φ(d1) · (2rT - d2·σ√T) / (2T·σ√T)
  ```
  > Black, F., & Scholes, M. (1973). "The Pricing of Options and Corporate Liabilities." *Journal of
  > Political Economy*, 81(3), 637-654.
  > Merton, R. C. (1973). "Theory of Rational Option Pricing." *Bell Journal of Economics and
  > Management Science*, 4(1), 141-183.
  > Haug, E. G. (2007). *The Complete Guide to Option Pricing Formulas* (2nd ed.). McGraw-Hill —
  > fórmulas cerradas de vanna/charm.

  Verificado a mano contra un caso de texto conocido (S=K=100, r=5%, σ=20%, T=1 año →
  gamma=0.018762, vanna=-0.281430, charm=-0.065667, reproducidos exactos) antes de confiar en la
  implementación, y contra el `delta` real que ThetaData ya reporta para 7 contratos reales (0DTE y a
  3 semanas, en dos momentos del día) usando la misma convención de tiempo.
  - **Tasa libre de riesgo**: SOFR real vía `GET /v3/interest_rate/history/eod?symbol=SOFR` (tier
    FREE), cacheada una vez por día.
  - **Tiempo a vencimiento**: `T = segundos reales hasta las 4:00pm ET / 86400 / 365` (ACT/365
    calendario real hasta el cierre) — confirmado con datos reales dos veces en el mismo día de
    mercado (mañana y tarde), en 7 strikes reales, con error total de 0.0002-0.0003 contra el `delta`
    que ThetaData reporta directamente. Un conteo de días calendario completos (el otro candidato
    considerado) da `T=0` exacto para contratos 0DTE y rompe la fórmula — descartado con evidencia,
    no por intuición. Los timestamps de ThetaData son hora de Nueva York (ET), no UTC — confirmado
    comparando un timestamp en vivo contra el reloj real del sistema durante horario de mercado real;
    convertirlos como si fueran UTC produce un `T` incorrecto (fue el error real de una investigación
    previa en este mismo proyecto, corregido antes de escribir el adaptador).
- **`last` (precio de última operación)**: ThetaData no tiene ese campo en el snapshot — se usa el
  punto medio `(bid+ask)/2`, documentado explícitamente como aproximación.
- **`occ_symbol`**: tampoco viene en la respuesta — se construye igual que
  `MockDataProvider._contract()` (`{symbol}{expiración:%y%m%d}{C|P}{strike*1000:08d}`).
- **Volumen acumulado** (para la detección por delta de `WhaleAlertsEngine`): el snapshot REST de
  `/trade` solo da el tamaño de la última operación individual, no un acumulado — se construye sumando
  cada mensaje del Trade Stream (WebSocket) desde que arranca el proveedor. Validado con evidencia
  real contra la apertura de mercado del 2026-08-31 (9:32-10:22 AM ET, la ventana históricamente más
  riesgosa para este proveedor — dos incidentes reales documentados de desconexión/pérdida de datos,
  uno durante una apertura): 100.6-100.8% de cobertura contra el volumen/cantidad de operaciones que
  reportó independientemente `GET /v3/option/history/ohlc` para la misma ventana, cero desconexiones
  en 50 minutos de captura real.
- **Volumen del subyacente** (para Anchored VWAP) **y snapshot en vivo de índices**: las suscripciones
  de Stocks/Indices no estaban activas al momento de esta investigación (confirmado con 403 reales en
  ambos endpoints) — `MarketSnapshot.volume` queda en `0`, limitación conocida y documentada, mismo
  patrón ya usado en el proyecto. **No afecta al precio del subyacente** — ese ya viene gratis desde
  `greeks/first_order`, incluso para símbolos tipo índice (SPX/VIX tienen su propia cadena de
  opciones).
- **`get_daily_bars`**: `GET /v3/stock/history/eod` (equities) o `/v3/index/history/eod` (índices) —
  ambos confirmados funcionando con datos reales, sin necesitar la suscripción de Stocks/Indices en
  vivo. No se encontró ningún endpoint equivalente para futuros (`/v3/future/history/eod` → 404
  confirmado) — ES devuelve lista vacía, limitación conocida y documentada.
- **`atm_iv`/`pc_oi_ratio`** en `MarketSnapshot`: aproximados desde la misma cadena near-the-money ya
  traída para la cadena de opciones (IV promedio; ratio de open interest put/call) — documentado
  explícitamente como aproximación, no el IV ATM real ni el put/call ratio de mercado completo.
  `skew_25d` queda en `0`: calcularlo de verdad necesitaría strikes a 25-delta específicos, fuera del
  rango near-the-money que este adaptador ya trae.

**Manejo de conexión del streaming — no opcional, dados los incidentes reales documentados de
ThetaData.** `ThetaTradeStream` (dentro del mismo módulo del adaptador) monitorea el mensaje `STATUS`
como heartbeat (uno por segundo, según la documentación) — sin uno reciente, o con un estado distinto
de `CONNECTED`, se fuerza una reconexión. Las reconexiones usan backoff exponencial (2s, 4s, 8s...
tope de 60s) e incrementan el `id` de la solicitud, seguido del patrón de ThetaData. Cada mensaje
loggea su `sequence` como red de seguridad adicional para detectar huecos — informativo, no una
garantía por sí solo, porque los números de secuencia de OPRA son globales entre contratos/exchanges,
no un contador simple por contrato (confirmado en la investigación). Cada 20 minutos se reconcilia el
volumen acumulado contra `GET /v3/option/history/ohlc` para el mismo contrato/día — una discrepancia
mayor al 10% se loggea como advertencia clara, nunca falla en silencio.

**`stream_trades()` — el método del puerto que ya existía sin usar, implementado en este PR sin
consumidor todavía; ya tiene uno, ver la sección Lee-Ready más arriba.** Confirmado antes de escribir
el adaptador original (PR #76): ningún caso de uso construía `FlowEvent` ni llamaba `stream_trades()`
en ningún lugar del proyecto — era infraestructura declarada en el puerto desde antes, nunca
conectada. `aggressor_side` sigue en `Side.UNKNOWN` y `event_type` en `FlowEventType.UNUSUAL` en el
`FlowEvent` que emite el Trade Stream (una operación cruda de OPRA no trae esa clasificación por sí
sola) — eso no cambió; lo que sí cambió es que ahora existe un consumidor real:
`StreamWhaleAlertsUseCase` clasifica cada `FlowEvent` con Lee-Ready por su cuenta, en vez de esperar
que el propio evento ya viniera clasificado.

**`start()`/`stop()` — nuevo en el puerto `IDataProvider`, no específico de este adaptador.** Ganchos
de ciclo de vida para un proveedor con una conexión persistente que abrir/cerrar junto con el proceso
— `MockDataProvider` los implementa como no-operaciones. Viven en el puerto (no en `ThetaDataProvider`
únicamente) para que `backend/main.py` y el `Container` sigan siendo agnósticos del proveedor
concreto, mismo criterio que ya mantiene a `UnderlyingRefreshScheduler` sin saber qué proveedor tiene
detrás. El scheduler en sí **no necesitó ningún cambio de rol** — sigue llamando a
`RefreshUnderlyingSnapshotUseCase.execute(symbol)` en su cadencia habitual, porque greeks/IV/OI no
tienen ningún equivalente por streaming (confirmado en la investigación previa a PR #76: el Trade
Stream y el Quote Stream traen exactamente los mismos campos fragmentados que sus equivalentes REST,
nada más completo) — lo único que deja de necesitar una llamada de red en el momento de pedirlo es el
volumen, servido desde el estado que el stream ya mantiene en memoria. El Quote Stream mencionado ahí
sí se terminó construyendo — como `ThetaQuoteStream`, ver la sección Lee-Ready más arriba — una vez
que Lee-Ready le dio un motivo real para existir.

#### Ancho dinámico de la cadena filtrada near-the-money, anclado a ATR Range

Reemplaza el ancho fijo de la cadena filtrada (antes `strike_range=1`, es decir 3 strikes: 1 abajo +
ATM + 1 arriba, heredado sin revisar de la era de FlashAlpha con tope de 2,500 solicitudes/día) por un
ancho dinámico anclado al ATR Range de cada símbolo (`calculate_atr_range`, ya construido y validado),
en vez de un número fijo o una tabla de porcentajes a mano — el ATR ya refleja la volatilidad real de
cada símbolo automáticamente.

**Fórmula:** `ancho_en_precio = ATR Range del símbolo × 1.5` (`calculate_near_the_money_width.py`,
`ATR_WIDTH_MULTIPLIER`). Como ThetaData no tiene un parámetro nativo de "distancia de precio" (solo
`strike_range=n`, que da `2n+1` strikes alrededor del spot), el adaptador sobre-pide con
`NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE=100` (201 strikes) y filtra del lado del cliente a los
contratos dentro de `spot ± ancho_en_precio` — confirmado con el usuario antes de implementar que 100
da margen real incluso en un escenario de estrés (ATR de 14 días subiendo a ~$150 en SPX, un promedio
extendido, no un solo día atípico, ya que ATR es una media móvil que no reacciona a una sola sesión) y
que el costo de sobre-pedir es solo un payload JSON más grande en UNA llamada REST — sin costo de
cuota adicional (confirmado en la investigación previa a este PR) ni de más suscripciones de streaming
(esas solo se crean para lo que sobrevive al filtro por precio, no para lo sobre-pedido).

**Bootstrap en frío — confirmado que funciona en la práctica, no solo en teoría.** `calculate_atr_range`
recibe `daily_bars` y `session_readings` como parámetros independientes; el campo `atr` en sí se
calcula únicamente a partir de `daily_bars` (líneas 34-52 de ese archivo) — `session_readings` solo se
usa para `today_open`, necesario para las bandas de precio, no para `atr`. Esto significa que
`calculate_atr_range(daily_bars, [])` (con `session_readings` vacío) devuelve un `atr` real siempre
que haya 15+ días — sembrado enteramente desde `get_daily_bars()`, independiente de cualquier cadena
de opciones en vivo, confirmado leyendo la lógica real de la función, no asumido.

**Dos casos especiales de ancho fijo, con razones distintas — no una sola excepción genérica**
(`FIXED_WIDTH_BY_SYMBOL`):

- **VIX ($6, fijo):** revierte a la media con picos abruptos de cambio de régimen (15 → 40 en un día
  durante estrés) — un ATR de 14 días es una señal de tamaño pobre específicamente para VIX: demasiado
  angosto justo antes de un pico, o artificialmente ancho por semanas después de uno, porque los
  valores elevados de True Range durante el pico siguen inflando el promedio incluso después de que
  VIX ya se calmó. Lester no opera VIX directamente, solo lo mira como referencia de sentimiento para
  NQ, así que la precisión aquí no vale la pena perseguir.
- **ES ($100, fijo):** no tiene historial de daily bars del cual derivar un ATR, nunca —
  `ThetaDataProvider.get_daily_bars()` devuelve `[]` incondicionalmente para futuros (no existe un
  endpoint de EOD de futuros que funcione en ThetaData, ver el comentario propio de ese método). Ancho
  fijo del mismo orden de magnitud que SPX, ya que ES sigue al S&P 500 en puntos de índice.

**Caso borde adicional — sin relación con VIX/ES, defensivo únicamente:** si `atr` resulta `None` por
historial insuficiente (menos de 15 días), el ancho cae a `spot_price × 0.02`
(`INSUFFICIENT_DATA_WIDTH_FRACTION`) — escalado al precio del símbolo en vez de un monto fijo en
dólares, para no reutilizar un ancho pensado para un símbolo de un orden de magnitud de precio
completamente distinto. No se espera que esto se dispare para ninguno de los 11 símbolos activos hoy
(todos tienen años de historial vía ThetaData) — existe para un símbolo futuro con poco historial, o
un hueco de datos transitorio.

**Caché por símbolo por día, no por poll.** ATR solo cambia cuando se agrega un día *cerrado* al
historial — recalcularlo en cada ciclo de ~30s del scheduler sería puro desperdicio.
`ThetaDataProvider._width_cache: dict[str, tuple[date, Decimal]]` sigue el mismo patrón ya establecido
por `_rate_cache` (la tasa libre de riesgo) en el mismo archivo.

**Garantía de cobertura mínima.** Si el filtrado por ancho no deja ningún contrato (un ATR
inusualmente bajo, más angosto que el propio espaciado de strikes del símbolo), se usan en su lugar
los `MINIMUM_NEAR_THE_MONEY_ENTRIES=6` strikes más cercanos al spot — nunca una cadena vacía.

**Logging real, para confirmar contra la tabla ilustrativa de la investigación.** Cada vez que el
ancho se recalcula (una vez por símbolo por día, no en cada poll — ver el caché arriba),
`ThetaDataProvider._resolve_width` loggea el ancho real en dólares junto con el spot del momento —
confirmado con el usuario que esto reemplaza, con números reales de producción, la tabla de ATR
ilustrativa (no verificada en vivo) que acompañó la investigación previa a este PR.

**Impacto de suscripciones/cómputo recalculado con el nuevo mecanismo:** incluso con anchos
ilustrativos de referencia (no verificados en vivo — ver el logging real arriba como la fuente de
verdad definitiva), el total de suscripciones de streaming a través de los 11 símbolos activos queda
muy por debajo de los topes documentados de Options Standard (10,000 en Quote Stream, 15,000 en Trade
Stream) — con margen real incluso si los anchos ilustrativos estuvieran equivocados por 2-3x. Max Pain
(ya confirmado `O(strikes²)` en la investigación previa) es el término a vigilar si los anchos reales
resultan más amplios que lo ilustrativo, no el conteo de suscripciones de streaming.

#### Límite real de concurrencia REST de ThetaData (4 por cuenta) y deduplicación de llamadas redundantes

Investigación previa confirmó un límite real, documentado por ThetaData, no investigado hasta
entonces: la concurrencia de solicitudes REST es **por cuenta completa**, no por endpoint ni por
símbolo, y no se suma entre suscripciones — el nivel más alto entre todas las suscripciones del
usuario determina el límite total. Con Options Standard como la suscripción más alta de Convexa, el
límite real es **4 solicitudes REST concurrentes para todo el backend**. Confirmado también: la
seguridad de hoy (concurrencia real ~1, casi nunca 2) es un efecto secundario accidental del scheduler
siendo secuencial (`for symbol in symbols: await asyncio.to_thread(...)`), no una protección diseñada
— si ese loop se paralelizara algún día por rendimiento, nada impediría exceder el límite real.

**A. Semáforo real, `threading.Semaphore(4)` — no `asyncio.Semaphore`.** Las llamadas REST de
`ThetaDataProvider` corren de forma síncrona dentro de hilos de worker (vía `asyncio.to_thread` desde
el scheduler, o el threadpool de Starlette para las rutas síncronas de la API) — nunca en el event
loop en sí, así que un primitivo de `asyncio` no coordinaría nada real entre esos hilos. El semáforo
se instancia una sola vez en `ThetaDataProvider.__init__` (`THETADATA_MAX_CONCURRENT_REQUESTS = 4`) y
envuelve la única llamada HTTP real dentro de `_get_json` (`self._client.get(...)`) — el chokepoint
que ya atraviesan las 4 rutas de llamada (cadena de opciones, snapshot del subyacente, daily bars,
open interest/tasa) — cubriendo todo de una sola vez sin tocar cada método individualmente.

**B. Deduplicación de las 2 llamadas REST redundantes por ciclo — implementada dentro del adaptador,
no en `RefreshUnderlyingSnapshotUseCase`.** El plan original proponía eliminar la llamada redundante
reescribiendo `RefreshUnderlyingSnapshotUseCase.execute()` para derivar el `MarketSnapshot`
directamente del `OptionChain` ya obtenido (`OptionContract` ya carga `iv`/`open_interest` por
contrato, suficiente en teoría para recalcular `atm_iv`/`pc_oi_ratio` sin ninguna llamada nueva). Se
descartó al confirmar que `MockDataProvider.get_underlying_snapshot()` devuelve valores de fixture
completamente independientes y no derivados de su propia cadena (`volume=1_250_000`,
`pc_oi_ratio=Decimal("1.10")`, `skew_25d=Decimal("0.04")`, `atm_iv=Decimal("0.22")`, hardcodeados) —
hacer que el caso de uso, agnóstico de proveedor, dejara de llamar a `get_underlying_snapshot()`
habría cambiado el comportamiento observable de Mock silenciosamente, violando la regla explícita de
"sin cambio de funcionalidad" de esta tarea.

La deduplicación real vive en `ThetaDataProvider._fetch_near_the_money`/`_fetch_open_interest`: un
caché en memoria de corta duración (`NEAR_THE_MONEY_CACHE_TTL_SECONDS = 10.0`), con clave
`(symbol, expiration)`, que ambos métodos ya comparten como argumentos — confirmado que
`get_option_chain()` y `get_underlying_snapshot()` piden exactamente los mismos datos cercanos al
precio (`_fetch_near_the_money(symbol, expiration=None)` y `_fetch_open_interest(symbol,
chain.expiration)`) cuando se llaman uno después del otro para el mismo símbolo, como ya hace
`RefreshUnderlyingSnapshotUseCase.execute()`. El TTL queda muy por debajo del intervalo de 30s del
scheduler — nunca podría abarcar dos ciclos distintos — y por encima del tiempo real que toma la
secuencia completa `get_option_chain → get_underlying_snapshot` dentro de un mismo ciclo. `IDataProvider`
como puerto no cambia — `MockDataProvider` y cualquier otro llamador de `get_underlying_snapshot()`
en aislamiento (sin una llamada previa a `get_option_chain()` para el mismo símbolo) siguen
funcionando exactamente igual, ya que el caché simplemente no tiene nada que reutilizar en ese caso
(fallback correcto: una consulta real, igual que hoy).

**Llamadas por ciclo, antes/después (símbolo en estado estable, con los cachés de tasa/ancho ya
calientes del ciclo anterior):**

| | Antes | Después |
|---|---|---|
| `get_option_chain` (near-the-money + open interest) | 2 | 2 |
| `get_underlying_snapshot` (near-the-money + open interest) | 2 | 0 (caché) |
| `get_daily_bars` directo (guardado de daily bars) | 1 | 1 |
| **Total por símbolo** | **5** | **3** |

Con los 11 símbolos activos reales hoy: **55 → 33 llamadas/ciclo** (22 ahorradas). Con el universo de
15 símbolos referenciado en la tarea: **75 → 45 llamadas/ciclo** — la cifra de 75 coincide exactamente
con 15 × 5 llamadas antes del cambio.

**Tests:** `tests/test_thetadata_provider.py` — `TestRequestConcurrencyLimit` (un handler que bloquea
hasta ser liberado, con 6 hilos concurrentes, confirma que nunca más de
`THETADATA_MAX_CONCURRENT_REQUESTS` entran al handler a la vez); `TestNearTheMoneyCaching` (confirma
que `get_underlying_snapshot()` tras `get_option_chain()` para el mismo símbolo genera 1 sola llamada
a cada endpoint, no 2; confirma que los valores del snapshot con caché caliente son idénticos al caso
sin caché ya probado en `TestGetUnderlyingSnapshot`; confirma que el caché no se filtra entre
símbolos distintos).

#### Scheduler paralelo (no secuencial) y caché de Open Interest ampliado a 20 minutos

Investigación previa (2026-09) midió en vivo, contra el ancho de cadena dinámico de ATR ya en
producción (SPX en ~32 strikes), que un ciclo completo secuencial del scheduler para los 11 símbolos
activos reales tomaba **~30 segundos** — prácticamente el mismo `REFRESH_INTERVAL_SECONDS` (30s) del
propio scheduler. Como `_run()` duerme el intervalo **después** de que el ciclo termina (`await
self._run_cycle(); await asyncio.sleep(interval)`), la cadencia real de refresco terminaba siendo
más cercana a **~60 segundos**, no los 30 previstos — y empeora con un universo de símbolos más grande.
La misma investigación confirmó que el semáforo de 4 (sección anterior) nunca era el cuello de botella
real: como el scheduler despachaba un símbolo a la vez, la concurrencia real nunca llegaba ni a 2.

**Cambio 1 — scheduler paralelo, semáforo como única protección real.**
`UnderlyingRefreshScheduler._run_cycle()` reemplaza el `for symbol in symbols: await
asyncio.to_thread(...)` secuencial por `asyncio.gather(*(self._refresh_symbol(symbol) for symbol in
symbols))` — los 11 símbolos se despachan de una sola vez. La seguridad real contra el límite de
ThetaData sigue siendo el `threading.Semaphore(4)` que ya vive dentro de `ThetaDataProvider`
(sección anterior) — es el único chokepoint real que atraviesa cada llamada REST sin importar qué
hilo de símbolo la generó, así que el scheduler despachando todo a la vez es seguro por construcción y
ya no necesita preocuparse por ese límite. `_refresh_symbol(symbol) -> bool` reemplaza el manejo de
excepciones que antes vivía inline en el loop — cada símbolo captura su propia excepción y devuelve
`False` en vez de dejarla propagar, para que el fallo de uno nunca cancele a los demás bajo
`asyncio.gather` (mismo comportamiento de "un fallo no detiene el ciclo" que ya existía, preservado
explícitamente).

Se dejó fuera de este PR, a propósito, el reordenamiento/prioridad de símbolos (ej. mover SPX al
frente) — confirmado que deja de aportar algo real una vez que el scheduler no es secuencial: con
todos los símbolos despachándose a la vez, la posición de cualquiera en la lista ya no determina
cuánto espera.

**Cambio 2 — caché de Open Interest ampliado de 10s a 20 minutos.** `OPEN_INTEREST_CACHE_TTL_SECONDS`
(nueva constante, separada de `NEAR_THE_MONEY_CACHE_TTL_SECONDS`, que se queda en 10s) — confirmado
en la investigación previa, con datos reales, que Open Interest no cambia intradía: comparando los 64
contratos de SPX entre dos lecturas reales separadas por ~70 segundos (con una llamada REST nueva
confirmada de por medio, no una caché), 0 de 64 valores de `open_interest` cambiaron, mientras que
bid/ask/IV cambiaron en los 64 — descartando un feed obsoleto como explicación. Consistente con el
hecho de mercado bien documentado (no un detalle específico de ThetaData): el Open Interest de
opciones en EE.UU. lo calcula y publica la OCC una vez al día, no continuamente. 20 minutos sigue
siendo conservador frente a "cambia una vez al día", no un límite ajustado al mínimo.

**Tests:** `tests/test_scheduler.py` — `test_run_cycle_dispatches_symbols_concurrently_not_sequentially`
(11 símbolos con 0.15s de delay cada uno terminan en <0.6s, no en los ~1.65s que tomaría secuencial);
`test_semaphore_still_caps_real_rest_concurrency_under_parallel_dispatch` (un `RefreshUnderlyingSnapshotUseCase`
real, con un `ThetaDataProvider` real de transporte simulado —no un stub—, confirma que incluso con
los 11 símbolos despachados a la vez por el scheduler paralelo, nunca más de
`THETADATA_MAX_CONCURRENT_REQUESTS` llamadas REST están en vuelo al mismo tiempo). Los dos tests
existentes que verificaban el orden exacto de símbolos procesados (`stub.calls == ACTIVE_SYMBOLS`) se
ajustaron a comparación por conjunto (`sorted(...) == sorted(...)`) — el orden ya no está garantizado
bajo despacho concurrente, un cambio de comportamiento esperado y correcto, no una regresión.

#### Precio del subyacente por streaming (Stock Trade Stream) — aditivo al scheduler REST, en borrador

Reemplaza (en el sentido de "complementa, sin quitar nada") el único mecanismo que hoy alimenta el
precio del chart de velas — el sondeo REST del scheduler cada 30s — con el Trade Stream del subyacente
de ThetaData en tiempo real, siguiendo exactamente la misma arquitectura ya construida para Whale
Alerts (PR #80): un stream WebSocket propio en el adaptador, un caso de uso de dominio que lo consume,
y un manager en `backend/core/` que corre una tarea por símbolo durante la vida del proceso.

**⚠️ El plan Stocks de ThetaData (necesario para este stream) no estaba activo al momento de este PR
— se activa en los próximos días.** Este PR se entrega en borrador, sin verificación contra el stream
real. Ver el desglose explícito de qué quedó verificado vs. qué no, al final de esta sección.

**1. Arquitectura reutilizada de Whale Alerts, confirmada antes de escribir código — no un patrón
nuevo:** `ThetaTradeStream`/`ThetaQuoteStream` (conexión WebSocket persistente, heartbeat STATUS,
backoff exponencial 2s→60s, payload de suscripción, parseo de mensajes) → `StreamWhaleAlertsUseCase`
(caso de uso de dominio que consume el stream vía el puerto `IDataProvider`, nunca un adaptador
concreto) → `WhaleAlertsStreamManager` (una tarea de `asyncio` por símbolo activo, `try/except
asyncio.CancelledError: raise / except Exception: logger.exception(...)` por tarea) → cableado en
`backend/main.py`'s lifespan, con la misma bandera `enable_scheduler` que ya gatea el scheduler REST
(para que un `TestClient(app)` de pruebas nunca arranque tareas de fondo reales). El nuevo consumidor
sigue esta misma cadena exactamente, con nombres análogos: `ThetaUnderlyingTradeStream` →
`StreamUnderlyingPriceUseCase` → `UnderlyingPriceStreamManager`.

**2. Formato de mensajes citado de la documentación pública de ThetaData, no asumido**
([Trade Stream | ThetaData v3](https://docs.thetadata.us/Streaming/US-Stocks/Trade-Stream.html),
consultada 2026-09). Payload de suscripción:
```json
{"msg_type": "STREAM", "sec_type": "STOCK", "req_type": "TRADE", "add": true, "id": 0, "contract": {"root": "AAPL"}}
```
Mucho más simple que el de opciones (`ThetaTradeStream`/`ThetaQuoteStream`) — el "contract" de una
acción es solo su símbolo raíz, sin expiración/strike/right. Mensaje de evento (ejemplo real de la
propia documentación, usado tal cual como fixture de test):
```json
{"header": {"type": "TRADE", "status": "CONNECTED"},
 "contract": {"security_type": "STOCK", "root": "AAPL"},
 "trade": {"ms_of_day": 38437607, "sequence": 12150295, "size": 500, "condition": 0, "price": 184.5099, "exchange": 57, "date": 20240503}}
```
Se descartó deliberadamente el "Full Trade Stream" (`msg_type: "STREAM_BULK"`) — trae **todos** los
símbolos del mercado a la vez, el equivalente stock del patrón que `ThetaTradeStream` ya evita para
opciones (suscripción explícita por símbolo, no un firehose).

**Corrección real encontrada durante la implementación, no solo en teoría — `sec_type: "STOCK"` no
sirve para todos los símbolos activos.** El diseño inicial asumía un único `sec_type` para todo el
universo de símbolos. Investigando antes de dar el PR por terminado, se confirmó que ThetaData tiene
un **stream de índices genuinamente separado**
([Price Stream | US-Indices](https://docs.thetadata.us/Streaming/US-Indices/Price-Stream.html),
consultada 2026-09), con su propia suscripción ("Index Standard", distinta del plan Stocks) — SPX y
VIX (`UnderlyingKind.INDEX`) necesitan `sec_type: "INDEX"`, no `"STOCK"`. La forma del mensaje de
`trade` es idéntica entre ambos (mismos campos: `ms_of_day`, `sequence`, `size`, `condition`, `price`,
`exchange`, `date` — confirmado comparando ambas páginas de documentación palabra por palabra), con
una sola diferencia real: para índices, `size` siempre se reporta en `0` (la documentación de
ThetaData lo dice explícitamente: "only the price field is updated"). `ThetaUnderlyingTradeStream`
elige el `sec_type` correcto por símbolo según su `UnderlyingKind` — `register_symbol(symbol, kind)`,
no solo `register_symbol(symbol)` como en el primer borrador.

Para ES (`UnderlyingKind.FUTURE`) no se encontró ninguna documentación de un stream de futuros —
mismo precedente ya establecido para `get_daily_bars` (que tampoco tiene un endpoint REST de futuros
que funcione) — así que `ThetaDataProvider.start()` lo excluye explícitamente de este stream en vez
de adivinar un `sec_type` sin evidencia.

**3. `ThetaUnderlyingTradeStream` — más simple que `ThetaTradeStream` en dos aspectos deliberados:**
sin reconciliación (no existe en este proyecto un endpoint REST de OHLC intradía para acciones contra
el cual reconciliar — `get_daily_bars` solo trae velas *diarias*), y se suscribe directamente por
símbolo del subyacente (`register_symbol`), no por contrato de opción descubierto. Misma conexión
WebSocket separada de `ThetaTradeStream`/`ThetaQuoteStream` (mismo razonamiento ya documentado en
esas dos: no tocar sus conexiones ya endurecidas, a costa de una conexión liviana más a un Theta
Terminal local, no a un servidor remoto con límite de tasa).

**4. Nueva entidad `UnderlyingTradeEvent`** (symbol, as_of, price, size) — ni `FlowEvent` ni
`QuoteEvent` sirven, ambas requieren `occ_symbol` (concepto de opciones que un tick de acción no
tiene) y `FlowEvent` no carga un precio crudo, solo `premium` derivado (precio × tamaño × 100).

**5. Aditivo, no reemplazo — confirmado explícitamente antes de implementar.** El diseño inicial que
se consideró (que `RefreshUnderlyingSnapshotUseCase` dejara de llamar a `get_underlying_snapshot()` y
derivara el `MarketSnapshot` directamente del `OptionChain` ya obtenido) se descartó por la misma
razón que ya descartó un diseño similar en la sección de deduplicación de llamadas REST arriba:
`MockDataProvider.get_underlying_snapshot()` devuelve valores de fixture completamente
independientes, no derivados de su propia cadena. `StreamUnderlyingPriceUseCase` en cambio solo
persiste `MarketPrice` — la misma entidad que el scheduler REST ya escribe — de forma **más
frecuente**, sin tocar el scheduler ni ningún caso de uso de Gamma/GEX/OI en absoluto.

**Sin escritura ilimitada:** `IStorage.save_market_price()` acumula un historial en memoria sin
límite de tamaño (`InMemoryStorage._price_history`, confirmado leyendo el código) — persistir cada
operación cruda de un stream de una acción líquida (potencialmente varias por segundo) crecería ese
historial sin control. `StreamUnderlyingPriceUseCase` aplica un debounce de
`MIN_WRITE_INTERVAL_SECONDS = 1.0` por símbolo — sigue siendo ~30x más fresco que los 30s del
scheduler, manteniendo el crecimiento del historial en un múltiplo deliberado y acotado de lo que ya
existe hoy, no un firehose sin control.

**6. Degradación con gracia — mismo precedente ya usado para Whale Alerts, no uno nuevo.** Si el
stream no está disponible (plan Stocks inactivo, símbolo no soportado, conexión caída tras agotar los
reintentos), `UnderlyingPriceStreamManager._run_symbol` captura la excepción por tarea, la loggea, y
esa tarea simplemente termina — sin tocar el resto de tareas, sin tocar el proceso, sin ningún error
visible para el usuario. El chart sigue funcionando exactamente como hoy, alimentado por las
escrituras del scheduler REST — no porque `StreamUnderlyingPriceUseCase` tenga lógica de fallback
explícita, sino porque simplemente nunca escribe nada si el stream nunca entrega nada, dejando el
`MarketPrice` más reciente en storage exactamente donde el scheduler ya lo dejó.

**Fuera de alcance de este PR, a propósito:** ningún cambio a Gamma/GEX/OI; ningún cambio al frontend
(el chart ya sondea `GET /market/{symbol}` cada 30s — este PR hace que el valor almacenado esté más
fresco entre ciclos del scheduler, pero **la cadencia de sondeo del navegador sigue siendo 30s** —
para que el usuario vea actualizaciones genuinamente más frecuentes que 30s haría falta un mecanismo
de push al frontend, ej. WebSocket propio expuesto por el backend, deliberadamente fuera de esta
tarea); acumular un volumen real de la acción a partir del `size` de cada operación del stream (el
`volume=0` de `MarketSnapshot` es una limitación ya documentada de `ThetaDataProvider`, sin fuente en
vivo — este stream podría cerrar ese hueco en un PR futuro, no en este).

**Tests — construidos con mensajes simulados basados en la documentación oficial, no inventados,
dejado explícito en el código (`TestUnderlyingTradeStream`'s propio docstring en
`tests/test_thetadata_provider.py`) que deben revalidarse contra el stream real en cuanto el plan
esté activo:** `TestUnderlyingTradeStream` (parseo de mensajes, filtrado por símbolo entre
suscriptores, mensajes incompletos ignorados, payload de suscripción exacto, backoff exponencial,
ciclo de vida start/stop) — todo en `tests/test_thetadata_provider.py`;
`tests/test_stream_underlying_price.py` (persiste en cada tick, debounce dentro de la ventana,
persiste de nuevo tras la ventana, debounce independiente por símbolo, `run()` completa de inmediato
para un proveedor sin nada que transmitir); `tests/test_underlying_price_stream.py` (una tarea por
símbolo activo, `start()` idempotente, `stop()` limpia todas las tareas, el fallo de un símbolo no
tumba a los demás) — mismos 3 niveles de test que ya existen para Whale Alerts, mismo patrón.

**Qué quedó verificado con confianza vs. qué sigue genuinamente sin probar hasta activar el plan
Stocks — el desglose que pidió el usuario explícitamente:**

- ✅ **Verificado con confianza:** la arquitectura completa (stream → caso de uso → manager → cableado
  en `main.py`), el parseo de mensajes contra el formato exacto documentado por ThetaData, el
  mecanismo de degradación con gracia (una tarea falla, el resto sigue, el chart sigue funcionando
  off el scheduler), la reutilización fiel del patrón de Whale Alerts, el debounce del historial de
  precios, y que el scheduler REST/Gamma/GEX/OI quedan completamente intactos — todo esto probado con
  pytest usando mensajes/proveedores simulados, sin depender de una conexión real.
- ❌ **Genuinamente sin probar hasta que los planes Stocks/Index estén activos:** que ThetaData
  realmente acepte estas suscripciones (`sec_type: "STOCK"` para EQUITY, `"INDEX"` para SPX/VIX — la
  distinción en sí ya está resuelta y confirmada contra la documentación, ver el hallazgo arriba, pero
  nunca ejercitada contra una conexión real) y devuelva mensajes con exactamente esta forma para una
  cuenta con ambos planes activos (los fixtures citan la documentación pública, pero ThetaData ya ha
  tenido incidentes reales de desconexión no documentados de antemano — ver el propio historial de
  `ThetaTradeStream`); el comportamiento real de reconexión contra un Theta Terminal real bajo esos
  planes; y el impacto real en latencia/frescura que el usuario observaría en la práctica.

#### Manejo explícito de REQ_RESPONSE en los 3 streams de ThetaData, y cierre del gap de `MarketSnapshot.volume = 0`

Dos hallazgos independientes de horario de mercado, confirmados en una investigación en vivo contra el
Theta Terminal real (planes Stocks+Index ya activos, a diferencia del PR anterior que se entregó en
borrador exactamente por no tener esos planes activos aún) — ninguno de los dos requirió tocar la
re-prueba de entrega de TRADE en horario de mercado, deliberadamente fuera de este PR.

**A. `REQ_RESPONSE` se descartaba en silencio — un mensaje real, no hipotético.** ThetaData manda un
mensaje `REQ_RESPONSE` inmediatamente después de cada suscripción, confirmando si fue aceptada o
rechazada:
```json
{"header": {"type": "REQ_RESPONSE", "status": "CONNECTED", "response": "SUBSCRIBED", "req_id": 7}}
```
Confirmado en vivo que `ThetaTradeStream`, `ThetaQuoteStream` y `ThetaUnderlyingTradeStream` —
mismo bucle `_connect_and_consume`, ya documentado como idéntico entre las 3 — solo distinguían
`header.get("type") == "STATUS"` y `"TRADE"`/`"QUOTE"`; cualquier otro tipo, incluido `REQ_RESPONSE`,
caía por la rama sin clasificar sin dejar rastro. Si una suscripción fuera rechazada alguna vez, se
vería idéntico a "todavía no hay operaciones" — indistinguible sin este cambio.

Nueva función compartida `_log_req_response(stream_name, message)` (un solo punto, no una clase base
nueva — las 3 clases ya comparten el bucle de parseo, no justificaba un refactor mayor) llamada desde
una nueva rama `elif header.get("type") == "REQ_RESPONSE":` en las 3 clases. Loggea a nivel `debug` si
`response == "SUBSCRIBED"`, y a nivel `error` (visible, con el mensaje completo) para cualquier otro
valor — incluyendo un mensaje malformado sin el campo `response` en absoluto, no solo motivos de
rechazo conocidos. Deliberadamente loggea en vez de lanzar una excepción: que un símbolo entre muchos
sea rechazado no debería tumbar una conexión que sigue sirviendo correctamente todo lo demás a lo que
sí se suscribió.

**B. `MarketSnapshot.volume` dejaba de estar bloqueado — la razón original ya no aplica.** El gap
documentado (`volume=0` fijo, sin fuente por falta de suscripción viva a Stocks/Indices) se cerró:
`GET /v3/stock/snapshot/ohlc` (equities/ETFs) y `/v3/index/snapshot/ohlc` (índices) devuelven volumen
real de sesión sin necesitar ninguna suscripción de streaming — confirmado en vivo justo antes de
implementar:
```json
// GET /v3/stock/snapshot/ohlc?symbol=SPY
{"response": [{"volume": 16396508, "symbol": "SPY", "high": 766.430, "low": 761.730, "count": 262994,
  "close": 765.200, "open": 762.450, "timestamp": "2026-09-02T19:59:54.391"}]}
// GET /v3/index/snapshot/ohlc?symbol=SPX — un índice no tiene volumen de acciones propio
{"response": [{"volume": 0, "symbol": "SPX", "high": 7681.19, "low": 7633.62, "count": 0,
  "close": 7666.60, "open": 7634.58, "timestamp": "2026-09-02T16:05:35.000"}]}
```
Nuevo método `_fetch_underlying_volume(symbol, kind)` en `ThetaDataProvider`, cableado en
`get_underlying_snapshot()` junto al resto de valores que ya arma esa función. Deliberadamente **sin
caché** — a diferencia de `_fetch_near_the_money`/`_fetch_open_interest`, el volumen cambia
continuamente durante la sesión (no es estático intradía como el open interest) y esta llamada ya
ocurre a lo sumo una vez por invocación de `get_underlying_snapshot()`, sin la duplicación
back-to-back que sí justifica el caché de la cadena near-the-money. Para futuros (`ES`,
`UnderlyingKind.FUTURE`) devuelve `0` sin hacer ninguna petición — mismo precedente documentado ya en
`get_daily_bars` (no se encontró endpoint de futuros que funcione). Si la petición falla por
cualquier motivo, cae a `0` (el mismo valor que ya tenía este campo hasta hoy) en vez de propagar la
excepción — para que un problema transitorio con este único campo no tumbe todo el ciclo de refresh
del snapshot.

**Auditoría de dependencias antes de tocar el valor, tal como pidió el usuario — no se encontró
ninguna ruptura.** Se rastreó cada lugar donde `MarketSnapshot.volume` fluye:
`ThetaDataProvider.get_underlying_snapshot()` → `RefreshUnderlyingSnapshotUseCase.execute()`
(`backend/domain/use_cases/refresh_snapshot.py:44`, lo copia tal cual a un `MarketPrice.volume`) →
persistido vía `IStorage.save_market_price()` → leído de vuelta por `calculate_anchored_vwap()`
(`backend/domain/use_cases/calculate_anchored_vwap.py`) como el volumen acumulado de sesión, restando
contra la lectura anterior para obtener el volumen del intervalo. El propio docstring de esa función
ya documentaba exactamente esta forma esperada ("`volume` en cada lectura es el total acumulado de la
sesión... el contador de volumen de sesión se resetea a cero a las 9:30 ET") — el valor real de
`/v3/*/snapshot/ohlc` es semánticamente compatible sin cambiar el contrato de esa función, solo deja
de estar permanentemente `provisional=True`. `capture_daily_gamma_reference` (la otra función que
recibe el `MarketSnapshot` completo) solo lee `pc_oi_ratio`/`skew_25d`/`atm_iv` de él, nunca `volume`
— sin impacto. No se encontró ningún `if volume == 0` ni condición equivalente en ningún punto del
código que tratara el `0` como un valor centinela con significado propio, más allá de los tests que
ya lo asumían como el gap documentado (actualizados en este PR).

**Fuera de alcance de este PR, a propósito:** ninguna re-prueba de entrega de TRADE en horario de
mercado (tarea separada, explícitamente pospuesta por el usuario); acumular el volumen de la acción a
partir del `size` de cada operación del `ThetaUnderlyingTradeStream` en vez de este REST — el gap que
`StreamUnderlyingPriceUseCase` ya documentaba como "un seguimiento natural, deliberadamente fuera de
[ese] PR" sigue sin cerrarse ahí (ese caso de uso sigue escribiendo `volume=0` en cada tick del
stream), solo se cerró el gap del lado del scheduler REST.

**Tests — ambos casos de REQ_RESPONSE probados con mensajes simulados, tal como pidió el usuario,
ya que forzar un rechazo real arriesgaría el Terminal compartido:** `TestReqResponseHandling` en
`tests/test_thetadata_provider.py` — aceptado (`"SUBSCRIBED"`) loggea a `debug` sin ningún `error`;
rechazado (`"SYMBOL_NOT_FOUND"`) loggea un único `error` visible con el `stream_name`, el motivo y el
`req_id`; un mensaje sin el campo `response` en absoluto también se trata como rechazo, no se ignora;
y una prueba que lee el código fuente compilado de las 3 clases confirma que la rama
`REQ_RESPONSE` existe en las 3, no solo que la función compartida funciona de forma aislada.
`TestFetchUnderlyingVolume` — equities enrutan a `/v3/stock/snapshot/ohlc`, índices a
`/v3/index/snapshot/ohlc`, futuros devuelven `0` sin ninguna petición, una respuesta HTTP 472 (el
código propio de ThetaData para "sin datos", ya confirmado en la investigación previa) cae a `0` sin
propagar la excepción, y una respuesta 200 sin filas también cae a `0`. Los 3 tests existentes de
`TestGetUnderlyingSnapshot`/`TestNearTheMoneyCaching` que ejercitan `get_underlying_snapshot("SPY")`
completo se actualizaron para reflejar el volumen real en vez del `0` documentado como gap.
272 tests, suite completa, en verde.

**Qué quedó verificado con confianza vs. qué siguió simulado:**

- ✅ **Verificado con confianza, en vivo:** que ambos endpoints REST
  (`/v3/stock/snapshot/ohlc`, `/v3/index/snapshot/ohlc`) existen, responden 200 con la forma exacta
  usada en el código, y devuelven volumen real de sesión para un símbolo activo (SPY:
  `16,396,508`) sin necesitar ninguna suscripción de streaming — confirmado justo antes de escribir el
  código de este PR. Que ningún otro cálculo del código dependía de `MarketSnapshot.volume == 0` como
  centinela — confirmado leyendo cada punto de consumo, no solo grep superficial.
- ❌ **Genuinamente simulado, no probado contra el Terminal real:** el caso de rechazo de
  `REQ_RESPONSE` — nunca observado en la práctica, y forzarlo arriesgaría el Terminal compartido de
  producción, tal como pidió el usuario evitar. Los fixtures del caso de aceptación sí replican el
  formato exacto confirmado en vivo (`"response": "SUBSCRIBED"`), pero el propio bucle
  `_connect_and_consume` recibiendo un `REQ_RESPONSE` real de rechazo en producción sigue sin
  ejercitarse end-to-end.

#### Re-prueba de TRADE en horario de mercado real, y fin del `volume=0` que el stream escribía sobre el del scheduler

Dos tareas que dependían de que el mercado estuviera abierto para probarse de verdad — ambas
confirmadas hoy en vivo (2026-09-03, jueves, mercado abierto, ~11:40am ET), a diferencia del intento
anterior que corrió después del cierre (9:59pm ET, cero mensajes en 45s, sin poder distinguir "no
opera" de "no funciona").

**A. Re-prueba de `ThetaUnderlyingTradeStream` — confirmada en vivo para los 5 símbolos pedidos, con
un hallazgo real no esperado.** Se corrió la clase real (no una reimplementación) contra el Terminal
real, registrando SPY/TSLA (`STOCK`) y SPX/VIX/NDX (`INDEX`), escuchando 45-75s por corrida:

- ✅ Los 5 símbolos recibieron mensajes `TRADE` genuinos: SPY (3264 en 60s), TSLA (10961 en 60s), SPX
  (211 en 60s / 126 índice + 13 opción en otra corrida de 45s), VIX (24 en 60s), NDX (60 en 60s). El
  formato coincide exactamente con lo que el código ya asumía — mismos campos (`ms_of_day`,
  `sequence`, `size`, `condition`, `price`, `exchange`, `date`), y `size` confirmado siempre `0` para
  mensajes `INDEX`, tal como documenta ya `ThetaUnderlyingTradeStream`.
- ✅ **Bono no pedido pero relevante:** `REQ_RESPONSE` también se confirmó en vivo — las 5
  suscripciones devolvieron `"SUBSCRIBED"`, logueado correctamente a `debug` vía
  `_log_req_response` (el fix del PR anterior), confirmando que ese arreglo funciona en producción,
  no solo con mensajes simulados.
- ⚠️ **NDX no está en `ACTIVE_UNDERLYINGS`** (`backend/domain/underlyings.py`) — el código de la
  aplicación no lo registra en ningún lado hoy. Se pudo probar igual suscribiéndolo directamente al
  stream (fuera del flujo normal de `ThetaDataProvider.start()`), y funcionó, pero si el usuario
  espera que NDX aparezca en el dashboard como los demás, hace falta agregarlo a esa lista — fuera de
  alcance de esta tarea, solo reportado.
- ❌ **Hallazgo real, no esperado, confirmado en vivo — no simulado:** `_handle_trade` en
  `ThetaUnderlyingTradeStream` (y estructuralmente lo mismo en `ThetaTradeStream`) filtra los mensajes
  entrantes solo por `contract.root`, nunca por `contract.security_type`. El Theta Terminal local
  **no** aísla la entrega por conexión — retransmite **todo** símbolo/contrato con alguna suscripción
  activa en *cualquier* cliente conectado a ese mismo Terminal, a *todos* los clientes conectados. Una
  conexión que solo pidió `sec_type: "INDEX"` para `"VIX"` igual recibió mensajes
  `security_type: "OPTION"` con el mismo root — filtrados desde el backend real que ya está corriendo
  en este entorno (puerto 8000, con `ThetaTradeStream` suscrito al chain near-the-money de VIX para
  Gamma Exposure). Como `_handle_trade` no distingue `security_type`, trata la prima de una opción
  (~$0.40–$1.57) como si fuera el propio precio del índice VIX (~$14.87–$14.89 real, confirmado
  simultáneamente vía `GET /v3/index/snapshot/price`). Cuantificado en vivo: 60% de los mensajes
  `root="VIX"` en 60s eran contaminación de opciones (9 de 15); ~9% para `root="SPX"` (13 de 139);
  `root="SPY"`/`"TSLA"`/`"NDX"` no mostraron ninguna en las ventanas muestreadas, pero el mismo riesgo
  latente aplica a cualquier símbolo cuyas opciones también estén suscritas en otro lado del mismo
  Terminal — es más severo en VIX precisamente porque las operaciones genuinas del índice son
  relativamente raras comparadas con sus opciones. **No corregido en esta tarea** — verificación, no
  arreglo, es lo que se pidió; documentado como gap conocido en el docstring de
  `ThetaUnderlyingTradeStream`, con una prueba (`test_option_trades_sharing_the_same_root_are_not_filtered_out`)
  que fija el comportamiento actual (buggy) con el mensaje real capturado en vivo, para quien retome el
  arreglo — el fix natural es que `_handle_trade` también verifique `contract.security_type` contra el
  `UnderlyingKind` registrado antes de aceptar un mensaje como genuino.
- **Sobre distinguir "no opera ahora" de "el stream no funciona":** los 5 símbolos pedidos sí
  operaron durante la ventana de prueba, así que no hubo un caso real de "cero mensajes" que probar
  directamente. La distinción ya existe arquitectónicamente sin necesidad de cambios: el heartbeat
  `STATUS` (ya implementado, independiente de si algún símbolo específico tuvo operaciones) sigue
  llegando mientras la conexión esté sana — silencio de un símbolo mientras `STATUS` sigue fluyendo =
  "no opera en este momento"; que el propio `STATUS` se vuelva stale (`STATUS_STALE_AFTER_SECONDS`) es
  lo que ya distingue "el stream no funciona".

**B. `StreamUnderlyingPriceUseCase` ya no pisa el volumen real del scheduler con `0`.** Antes de
tocar el código, se confirmó cómo escribe hoy `save_market_price()`: en `InMemoryStorage` es una
asignación de diccionario completa (`self._prices[symbol] = price`, reemplaza el objeto entero, no
actualiza campos); en `PostgresqlStorage` es un `INSERT` de una fila nueva en `market_snapshots` que
se vuelve "la más reciente" por tiempo (confirmado leyendo `get_latest_price()` de ambos backends). Y
`MarketPrice.volume` es un campo `int` requerido, sin default y sin `None` — **no existe la opción de
"no incluirlo en el payload"** que planteaba el usuario como posible arreglo; hay que enviar algún
valor sí o sí. El arreglo real: en vez de escribir `volume=0` a mano, `_maybe_persist` ahora llama a
`self._storage.get_latest_price(event.symbol)` y **reenvía el volumen que ya estaba ahí** (el que puso
el scheduler REST la última vez, o el que un tick de stream anterior ya reenvió) — el stream nunca
calcula ni decide un volumen propio, solo lo repite hacia adelante entre ciclos del scheduler. Si
nunca se ha escrito nada para ese símbolo (arranque en frío), cae a `0` — el mismo valor que ya tenía
este campo antes, no una regresión.

**Tests:** `test_a_trade_carries_forward_the_volume_already_in_storage` (siembra un `MarketPrice` con
volumen real, confirma que un tick de precio del stream lo preserva intacto) y
`test_volume_survives_several_stream_ticks_in_a_row` (el mismo volumen sobrevive 3 ticks seguidos del
stream sin que el scheduler vuelva a escribir) — ambos en `tests/test_stream_underlying_price.py`.
`_FakeStorage` (ese archivo) y `_StubStorage` (`tests/test_underlying_price_stream.py`) se actualizaron
para implementar `get_latest_price`, ya requerido por `IStorage` y ahora también llamado por el caso de
uso. 275 tests, suite completa, en verde. `ruff check`: mismos 5 `FURB157` preexistentes antes y
después (confirmado por `git stash`), ninguno nuevo.

**Fuera de alcance de esta tarea, a propósito:** el arreglo del filtrado por `security_type` que reveló
la Parte A — solo se pidió verificar y reportar, no corregir; agregar NDX a `ACTIVE_UNDERLYINGS`.

#### Arreglo urgente: filtro por `security_type` en `ThetaUnderlyingTradeStream`, y evaluación del daño ya hecho

Bug de producción activo, confirmado en la tarea anterior con mensajes reales: el Theta Terminal local
no aísla la entrega por conexión — retransmite todo símbolo/contrato con alguna suscripción activa (de
cualquier cliente conectado a ese Terminal, incluido el backend real que ya corre en este entorno) a
todos los clientes. `_handle_trade` en `ThetaUnderlyingTradeStream` solo filtraba por `contract.root`,
nunca por `contract.security_type` — consecuencia confirmada: primas de opciones de VIX (~$0.40–$1.57)
se guardaban en `MarketPrice` como si fueran el precio del índice VIX (~$14.87 real).

**A. El arreglo — ya aplicado.** `_handle_trade` ahora también verifica `contract.security_type` contra
el `UnderlyingKind` registrado (`"INDEX"` para índices, `"STOCK"` para acciones) antes de aceptar un
mensaje; si no coincide, se descarta. Un símbolo sin registrar (`self._symbols.get(symbol)` es `None`)
también se descarta — sin un `UnderlyingKind` registrado no hay `security_type` esperado contra el cual
validar, así que no se adivina.

- La prueba que ya dejamos documentando el bug (`test_option_trades_sharing_the_same_root_are_not_filtered_out`)
  se convirtió, no se borró: ahora es `test_option_trades_sharing_the_same_root_are_filtered_out`, usa el
  mismo mensaje real de opción de VIX capturado en vivo, y confirma que la cola queda vacía. Se agregaron
  además `test_genuine_index_trade_for_a_registered_symbol_still_publishes` (el arreglo no debe ser
  excesivo — un trade genuino del índice sigue publicándose normal), `test_option_trade_for_an_equity_root_is_also_filtered_out`
  (mismo arreglo, lado STOCK) y `test_trade_for_an_unregistered_symbol_is_dropped_not_guessed`.
- **Confirmado en vivo, no solo con pruebas simuladas:** se corrió `ThetaUnderlyingTradeStream` (la
  clase real, ya arreglada) contra el Terminal real durante 60s, suscrita solo a VIX. Llegaron 30
  mensajes `OPTION` + 6 `INDEX` con `root="VIX"` en el cable (mismo patrón de contaminación que antes),
  pero los eventos publicados a la cola fueron exactamente los 6 genuinos (`14.68, 14.68, 14.68, 14.67,
  14.67, 14.67`) — cero contaminación llegó al consumidor.
- `ThetaTradeStream`/`ThetaQuoteStream` (las clases hermanas) se revisaron y **no necesitan el mismo
  arreglo** — ya son inmunes estructuralmente: ambas requieren `expiration`/`strike` (u otros campos que
  solo existen en mensajes de `OPTION`) antes de procesar nada, así que un mensaje `STOCK`/`INDEX` que
  se filtre por la misma vía ya cae en su chequeo de "campos incompletos" existente, sin necesidad de un
  chequeo nuevo de `security_type`.

**B. Evaluación del daño — sin borrar ni limpiar nada, solo reportado.** El proceso real corriendo en
este entorno (puerto 8000, PID 13460, `uvicorn backend.main:app --reload`) arrancó el 2026-09-01
21:41:48 ET — desde entonces usa Postgres real (`DATABASE_URL` en `.env`), no memoria. Se consultó
`market_snapshots` de forma **solo lectura**, acotado a esa ventana de uptime:

- **VIX:** 40 de 992 filas (4.03%) con precio fuera del rango plausible del índice (banda de control:
  $5–$100), todas con precios entre $0.31 y $4.05 — consistente con primas de opciones, no con el nivel
  real del índice (~$14.6–15.0 confirmado en paralelo vía REST). Todas concentradas en una sola ventana
  de ~20 minutos hoy: 2026-09-03 15:39:06–15:58:58 UTC (~11:39am–11:58am ET).
- **SPX:** 12 de 1312 filas (0.91%) fuera de banda ($3,000–$12,000), precios entre $28.04 y $108.49,
  misma ventana de ~20 minutos que VIX.
- **SPY, TSLA:** 0 filas fuera de banda en toda la ventana de uptime (814 y 1483 filas respectivamente)
  — consistente con lo observado en vivo en la tarea anterior (ninguna contaminación de opciones
  capturada en las muestras para estos dos símbolos).
- **NDX:** 0 filas en `market_snapshots` — no está en `ACTIVE_UNDERLYINGS`, nunca se persistió nada para
  él (ya reportado en la tarea anterior).
- **Caveat honesto, no ocultado:** esa ventana de ~20 minutos coincide fuertemente con la propia sesión
  de pruebas en vivo de la tarea anterior (varios scripts abriendo conexiones WebSocket adicionales al
  mismo Terminal compartido, en el mismo rango horario). No se puede descartar con certeza que esa
  actividad de investigación haya contribuido a la tasa/momento exacto de la contaminación observada —
  el mecanismo de fondo (Terminal compartido + filtro ciego a `security_type`) es estructural e
  independiente de esas pruebas, pero la correlación temporal es demasiado fuerte para no mencionarla.
- **¿Sigue pasando ahora mismo?** No se detectó ninguna fila contaminada nueva desde las 15:58:58 UTC —
  las filas más recientes revisadas (hasta 16:07:47 UTC, varios minutos después de aplicar el arreglo)
  están todas limpias. El proceso corre con `--reload`, que reinicia automáticamente al detectar cambios
  en los archivos fuente — muy probablemente ya recogió el arreglo sin intervención manual, pero esto
  **no se pudo confirmar con certeza total desde fuera del proceso** (no hay acceso a su consola). Se
  recomienda confirmar en la consola del servidor o reiniciarlo manualmente para tener certeza absoluta.

**Impacto en cálculos derivados — rastreado explícitamente, no asumido:**
- **Gamma/GEX/OI/`DailyGammaReference`: sin impacto.** `capture_daily_gamma_reference` y todo el
  pipeline de Gamma usan `MarketSnapshot` del scheduler REST (`get_underlying_snapshot()`, con
  `underlying_price` que viene del propio endpoint de opciones), nunca `MarketPrice`/el stream — rutas
  de datos completamente separadas, confirmado leyendo el código, no solo por la arquitectura documentada.
- **Anchored VWAP (VIX/SPX): sin impacto en el valor.** Todas las filas de `market_snapshots` para VIX y
  SPX tienen `volume=0` (100% — es lo esperado para índices, `/v3/index/snapshot/ohlc` siempre reporta 0
  ahí, no es parte de este bug). `calculate_anchored_vwap` pondera cada intervalo por su volumen
  (`interval_volume = max(reading.volume - previous_volume, 0)`) — con volumen siempre 0, el VWAP se
  queda permanentemente `provisional=True, value=None` para VIX/SPX sin importar qué precio tuviera cada
  fila; los precios contaminados nunca llegaron a ponderarse en ningún cálculo real.
- **Bandas de ATR (`today_open`): sin impacto.** Se ancla a la lectura *más temprana* de la sesión
  (`min(session_readings, key=as_of)`) — confirmado que la primera lectura de hoy para VIX ($15.02,
  13:30:08 UTC) y SPX ($7701.70, 13:30:11 UTC) son genuinas, muy anteriores a la ventana de
  contaminación de las 15:39–15:58 UTC.
- **`snapshot.price` / `closing_dynamics` mostrados en vivo: posible impacto transitorio, no
  cuantificable con certeza.** `build_market_snapshot()` usa `storage.get_latest_price()` — la fila más
  reciente en el momento exacto de cada request — para `snapshot.price` y como entrada de
  `calculate_closing_dynamics()`. No se persiste nada derivado de esto; solo se calcula al vuelo por
  request. Si el dashboard consultó `GET /market/VIX` (o SPX) exactamente durante esos ~20 minutos,
  pudo haber mostrado un precio equivocado y un `closing_dynamics` calculado sobre él — no hay logs de
  acceso HTTP disponibles para confirmar si esto realmente ocurrió.

**Hallazgo aparte, no relacionado con este bug, no investigado a fondo — solo reportado:** filas mucho
más antiguas en `market_snapshots` (2026-08-03 y 2026-08-11) muestran el mismo precio exacto ($552.25)
simultáneamente para VIX, SPX, SPY y TSLA — un valor que coincide con un fixture usado en las pruebas de
este repo, no con ningún dato de mercado real. Parece contaminación de datos de prueba/mock en la misma
base de datos de producción, de origen anterior a esta investigación y sin relación con el bug de
`security_type`. No se tocó, no se investigó más — fuera de alcance de esta tarea.

**Actualización — cierre de la misma sesión:** las 52 filas contaminadas identificadas arriba, y un
segundo hallazgo (datos de prueba/mock mezclados en producción, mucho más extenso de lo que esta misma
sección sugería) se resolvieron en la tarea de cierre siguiente — ver la sección inmediatamente
posterior a esta.

---

#### Cierre de sesión: borrado de datos contaminados, mock data, NDX registrado, y reinicio

Última tarea del día — cuatro pasos, en orden, con el reinicio al final para que quede corriendo con
todo aplicado de una vez.

**A. Las 52 filas de VIX/SPX contaminadas por el bug de `security_type` — borradas.** Antes de borrar,
se volvió a confirmar el conteo exacto contra los mismos criterios (símbolo + rango de precio + ventana
de tiempo) del reporte anterior. La primera reconfirmación dio 50, no 52 — pero no por un cambio real en
los datos: fue un bug propio de la consulta de reconfirmación, no del dato. Postgres almacena `price`
como `numeric` (decimal exacto); la consulta pasó los límites de la banda (`0.31`, `4.05`, `28.04`,
`108.49`) como `float` de Python, y `asyncpg` los envía como `double precision` — la representación
binaria de `108.49`/`4.05` en punto flotante no es exactamente igual al decimal, así que dos filas cuyo
precio caía justo en el límite superior de su banda (`VIX` a `4.05`, `SPX` a `108.49`) quedaban excluidas
del `BETWEEN` por una diferencia de punto flotante invisible al ojo. Repitiendo la consulta con los
límites como `Decimal` exactos (en vez de `float`) confirmó **52 exactas** (40 VIX + 12 SPX), idénticas
en cada timestamp y precio al reporte original, sin ninguna fila nueva desde entonces. Borradas dentro de
una transacción con `RETURNING`, verificando que el conteo borrado fuera exactamente 52 antes de
confirmar (de lo contrario, rollback automático).

**B. Mock data mezclado en producción — mucho más grande de lo que la descripción original sugería, tal
como pidió el usuario confirmar antes de asumir nada.** La firma exacta (`price=552.25` Y `volume=1250000`
simultáneos) es literalmente el fixture hardcodeado de `MockDataProvider`
(`backend/adapters/providers/mock/provider.py:36,44-45`) — confirmado leyendo el código fuente, no
inferido. El hallazgo real: **15,878 filas** (51% de las 30,985 filas totales de `market_snapshots`),
en los **11 símbolos activos de ese momento** (no solo los 4 mencionados de pasada), del **2026-08-07 al
2026-09-01 19:59:32 UTC** — terminando justo antes de que arrancara el backend real actualmente en
ejecución (2026-09-01 21:41:48 ET), consistente con que alguien corrió la app con
`QLL_DATA_PROVIDER=mock` apuntando a esta misma base de datos real durante más de 3 semanas, hasta que
se cambió a `thetadata`. Cero filas con `price=552.25` pero `volume` distinto — sin ambigüedad, sin
coincidencia con datos reales.

Dado el salto de escala respecto a lo descrito originalmente, el clasificador de permisos bloqueó el
primer intento de borrado masivo; se confirmó explícitamente con el usuario antes de proceder. Se
exportaron las 15,878 filas a CSV como respaldo (`symbol, underlying_id, time, price, volume`, entregado
al usuario) antes del `DELETE`, dentro de una transacción que verifica el conteo borrado antes de
confirmar, igual que en la Parte A.

**C. NDX registrado en `ACTIVE_UNDERLYINGS`** (`backend/domain/underlyings.py`), como `UnderlyingKind.INDEX`,
`is_priority=True` — mismo tratamiento que los otros símbolos activos (ahora 12 en total, no 14 como
mencionó el usuario — conteo real verificado, no asumido). Se investigó exhaustivamente (agente de
búsqueda dedicado) si algún otro lugar del código declara el conjunto de símbolos por separado:

- **Backend**: cero listas independientes — `scheduler.py`, `whale_alerts_stream.py`,
  `underlying_price_stream.py`, las rutas de la API, `InMemoryStorage`, y `ThetaDataProvider` derivan
  todos dinámicamente de `ACTIVE_UNDERLYINGS`/`ACTIVE_UNDERLYINGS_BY_SYMBOL` — NDX se propaga solo con
  este único cambio.
- **Frontend**: no existe ningún dropdown ni lista de símbolos hardcodeada — `dashboard.tsx` llama
  `getUnderlyings()` → `GET /underlyings`, que el backend sirve dinámicamente; nada que actualizar ahí.
- **Hallazgo real que sí requería acción, no cubierto por el simple cambio a `underlyings.py`:** las
  migraciones de Alembic (`backend/db/migrations/0010_seed_active_underlyings.py`,
  `0012_whale_thresholds.py`) sembraron las tablas `underlyings`/`whale_thresholds` de una base de datos
  Postgres ya migrada a partir de una foto de `ACTIVE_UNDERLYINGS` en su momento — las migraciones no se
  vuelven a ejecutar solas. Sin una migración nueva, NDX jamás habría aparecido en esas tablas de la base
  real, exactamente el mismo problema que la migración `0015` ya resolvió una vez para `ES` ("missing
  from the original PR #44 seed"). Se agregó `0018_seed_ndx.py`, siguiendo el mismo patrón de `0015`
  para `underlyings` (re-ejecuta el upsert idempotente completo contra el `ACTIVE_UNDERLYINGS` actual —
  seguro, `kind`/`is_priority` no son personalizables vía ninguna API). Para `whale_thresholds` se fue
  más quirúrgico que `0012`/`0015`: existe `PATCH /whale-thresholds/{symbol}`
  (`backend/api/routes/whale_thresholds.py`) que permite personalizar umbrales por símbolo en tiempo
  real — repetir el upsert de `0012` con los valores por defecto para *todos* los símbolos habría
  podido pisar silenciosamente cualquier personalización real hecha desde entonces. La migración nueva
  solo inserta la fila de NDX (`ON CONFLICT DO NOTHING`), sin tocar ninguna fila de otro símbolo.
  Aplicada contra la base real: `alembic upgrade head` corrió limpio (`0017` → `0018`), verificado que
  los 12 símbolos aparecen en `underlyings` y que NDX tiene su fila en `whale_thresholds` con los
  mismos valores por defecto que el resto (`unusual_min=40000, whale_min=150000,
  unusual_multiplier=3.0, whale_multiplier=6.0, sustained_flow_min=500000`).
- Se corrigieron dos comentarios/pruebas que hardcodeaban el conteo "11" como si fuera una expectativa
  actual (`test_scheduler.py::test_cycle_processes_all_11_active_symbols`, renombrada y con la
  aserción ahora derivada de `len(ACTIVE_UNDERLYINGS)` en vez de un literal; un comentario en el mismo
  archivo). Dos menciones más de "11 símbolos" en `scheduler.py`/`test_scheduler.py` se dejaron
  intactas a propósito — son mediciones históricas reales ("un ciclo secuencial contra estos mismos 11
  símbolos tomó ~30s", investigación 2026-09), no una afirmación sobre el conteo actual; cambiarlas
  falsearía la medición.
- `tests/test_active_underlyings.py::test_ensure_underlying_preserves_unconfigured_symbol_metadata`
  usaba NDX como ejemplo de "símbolo sin configurar" — ya no aplica una vez que NDX se registra, así
  que se cambió a `AAPL` (un símbolo real pero genuinamente no rastreado) para seguir probando lo que
  la prueba dice probar, en vez de coincidir por casualidad con los valores ahora configurados.

**D. Reinicio del backend, al final — confirmado, con certeza real, no "probablemente".** El proceso
anterior (PID 13460 + su worker 50212, `uvicorn --reload` desde 2026-09-01 21:41:48 ET) se detuvo por
completo, y se arrancó uno nuevo (`.claude/launch.json`, entrada `backend-dev`, mismo comando) — un
proceso Python recién iniciado importa el contenido actual de los archivos en disco, sin ambigüedad
posible sobre si "recogió" un cambio: no es una pregunta de probabilidad, es una garantía estructural
de cómo funciona un arranque en frío.

- **Fix de `contract.security_type`: activo, confirmado dos veces.** (1) El nuevo proceso arrancó
  después de que el archivo con el fix ya estaba en disco — garantía de código, no inferencia. (2)
  Empíricamente: cero filas nuevas de VIX/SPX fuera de rango desde el reinicio (12:31:46 ET en
  adelante), verificado contra la base real.
- **NDX activo tras el reinicio, confirmado vía la API real:** `GET /api/v1/underlyings` devuelve los
  12 símbolos, incluido `{"symbol":"NDX","kind":"index","is_priority":true}`. El log de arranque
  también lo confirma: descubrió el chain near-the-money de NDX (`Near-the-money width for NDX:
  $491.42 (spot $29440.95)`, datos reales) y "Scheduler cycle starting for **12** symbols" (no 11).
- **Tablas limpias, confirmado por consulta directa tras el reinicio:** 0 filas con
  `price=552.25 AND volume=1250000` (mock data), 0 filas de VIX/SPX en la ventana original
  15:39-15:59 UTC dentro de sus respectivas bandas contaminadas. `market_snapshots` pasó de 30,985 a
  16,619 filas tras ambos borrados (la diferencia frente a 30,985-52-15,878=15,055 son escrituras
  nuevas y legítimas del scheduler/stream entre los borrados y el reinicio, no un error de conteo).
- Arranque limpio, sin errores en los logs del nuevo proceso.

---

#### AAPL, MSFT y DIA registrados — misma receta de NDX

Mismo patrón que NDX, ya establecido: `ACTIVE_UNDERLYINGS` (`backend/domain/underlyings.py`) suma
`AAPL`, `MSFT`, `DIA`, los tres `UnderlyingKind.EQUITY` (`DIA` es el ETF SPDR Dow Jones, una acción/ETF,
no un índice — a diferencia de NDX). Migración `0019_seed_aapl_msft_dia.py`, mismo diseño que `0018`:
re-ejecuta el upsert completo de `underlyings` contra el `ACTIVE_UNDERLYINGS` actual (seguro para los 15
símbolos, `kind`/`is_priority` no personalizables por API), pero solo inserta filas nuevas en
`whale_thresholds` para los 3 símbolos nuevos (`ON CONFLICT DO NOTHING`, vía `WHERE symbol = ANY(:symbols)`),
sin tocar ninguna fila existente — mismo cuidado que `0018` por el mismo motivo (`PATCH
/whale-thresholds/{symbol}` permite personalización real en producción). Aplicada contra la base real
(`alembic upgrade head`, `0018` → `0019`, limpio).

Se repitió la misma verificación que la vez pasada de si hace falta el mismo tipo de ajuste en algún
otro punto: grep de `AAPL`/`MSFT`/`DIA` en todo `backend/`/`frontend/` no encontró ninguna referencia
previa que entrara en conflicto ni ninguna lista independiente que necesitara el mismo cambio — la
investigación exhaustiva de la tarea de NDX (agente de búsqueda dedicado) ya había confirmado que el
único punto de fricción estructural es exactamente el que las migraciones ya resuelven, no algo que
dependa del símbolo específico.

**Confirmado en vivo, sin reinicio manual esta vez:** a diferencia de la sesión anterior (donde el
proceso viejo, corriendo desde antes de esta sesión, no daba certeza sobre si `--reload` había
recogido el cambio), el proceso actual se inició *dentro* de esta misma sesión con `uvicorn --reload`
rastreado — el archivo se guardó, y `GET /api/v1/underlyings` ya devolvía los 15 símbolos (incluidos
`AAPL`, `MSFT`, `DIA`) sin necesidad de reiniciar el proceso a mano, confirmando que el watcher de
`--reload` sí está funcionando como se esperaba en este proceso.

278 tests, suite completa, en verde (sin pruebas nuevas — los conteos ya se derivan dinámicamente de
`ACTIVE_UNDERLYINGS` desde el ajuste de la tarea de NDX). `ruff check` sin violaciones. Se actualizó
`test_active_underlying_classification` con los 3 símbolos nuevos, y
`test_ensure_underlying_preserves_unconfigured_symbol_metadata` cambió su símbolo de ejemplo de `AAPL`
(ya configurado ahora) a `XOM` — mismo motivo que forzó el cambio de `NDX` a `AAPL` la vez anterior.

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
