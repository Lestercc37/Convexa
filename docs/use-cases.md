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
