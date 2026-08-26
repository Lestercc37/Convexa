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
