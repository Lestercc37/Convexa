# Whale Alerts: captura del código de condición (fase 1 multi-leg)

Estado: **borrador, no instalado.** Fase 1 de `docs/whale-alerts-multileg-condition.md` / informe 2026-10-08.
Decisión de Lester (2026-10-08): D3 "sí plan", fase 1 el jueves 10-15 después de las 16:16, solo guardar la
condición, con prueba de carga antes; la fase 2 (la regla) solo con su autorización.

## 1. Qué hace y qué NO hace

Hace:
- `parse_option_trade_message` lee `trade.condition` (entero; cualquier otra cosa → `None`) y lo pone en `FlowEvent.condition`.
- El relay local de ballenas (`whale_alerts_relay.py`) lo transporta (`cond`); un emisor viejo sin el campo se decodifica como `None`.
- `WhaleAlertsEngine.process_trade` suma la prima de cada operación por código en el cubo del minuto del contrato
  (`bucket_condition_premium`, clave `-1` = el mensaje no traía código) y guarda los mapas de los últimos 15 cubos para SUSTAINED_FLOW.
- Al emitir la alerta, `WhaleAlert.condition_premium` = `{"18": 812000.0, "130": 640000.0}`; WHALE/UNUSUAL = su cubo de un minuto,
  SUSTAINED_FLOW = los mismos 15 minutos de su `amount` (los valores suman el monto).
- Se guarda en la columna nueva `whale_alerts.condition_premium jsonb` (migración 0040, nullable, sin relleno).
- Interruptor: `QLL_WHALE_ALERTS_STORE_CONDITIONS=false` apaga la suma por operación (default: encendido).

NO hace (es la fase 2): firmar, excluir o ponderar por código; cambiar la presión neta, el volumen, los umbrales o la tarjeta;
leer la columna en ninguna consulta (`get_recent_whale_alerts` conserva su lista explícita de columnas); tocar la API o el frontend;
excluir cancelaciones (40-44, 148) del contador de volumen.

## 2. Archivos

| Archivo | Cambio |
|---|---|
| `backend/domain/entities/entities.py` | `FlowEvent.condition: int \| None = None` |
| `backend/adapters/providers/thetadata/stream_parsing.py` | `_trade_condition()` y lectura en `parse_option_trade_message` |
| `backend/core/whale_alerts_relay.py` | `cond` en `_encode_trade` / `_decode_trade` (tolerante a su ausencia) |
| `backend/domain/use_cases/flow.py` | `WhaleAlert.condition_premium`, estado por contrato, suma en `process_trade`, cierre de cubo, `_emit` |
| `backend/core/settings.py`, `backend/core/container.py` | `whale_alerts_store_conditions` y su cableado |
| `backend/adapters/storage/postgresql.py` | `save_whale_alert` escribe la columna (`CAST(:condition_premium AS jsonb)`) |
| `backend/db/migrations/0040_whale_alerts_conditions.py` | `ALTER TABLE whale_alerts ADD COLUMN IF NOT EXISTS condition_premium jsonb` |
| `tests/test_trade_condition_capture.py` | 10 pruebas (parseo, relay, motor, interruptor, "el código no cambia nada") |
| `backend/scripts/bench_trade_condition_capture.py` | micro-benchmark de `process_trade` con captura apagada/encendida |
| `backend/scripts/loadtest_stream_split.py` | las operaciones falsas ahora traen `condition` con la mezcla real |

## 3. Procesos que se reinician al instalar

`ConvexaStreamProcessor` y `ConvexaWhaleAlerts` (juntos; el orden da igual: el decodificador tolera el campo ausente).
No hace falta reiniciar `ConvexaBackendAPI`, `ConvexaFrontend`, `ConvexaScheduler` ni `ConvexaWorker` (el Worker solo reenvía tramas;
su ruta de respaldo sin procesador usa el mismo parser, pero esa ruta está inactiva mientras el procesador corre).
Reiniciar el procesador y el worker de ballenas borra el estado en memoria del día (presión neta acumulada, cubos del minuto): **después del cierre**.
El contador de volumen se retoma desde lo guardado desde las 00:00 (`_resume_cumulative_volume`).

Migración: `alembic upgrade head` primero contra `Convexa_test` (mostrando host y base antes, como la 0029/0039), luego contra `Convexa`.
Es un `ADD COLUMN` nullable sin valor por defecto: solo toca el catálogo de Postgres, no reescribe la tabla.

Medido en producción el 2026-10-08 (solo lectura): PostgreSQL 18.4; extensiones instaladas: solo `plpgsql` (**no hay TimescaleDB**, así que `whale_alerts`
**no es hipertabla y no hay compresión que la bloquee**); tabla de 9 MB + índice de 2.7 MB = 11 MB, ~83,700 filas; sin triggers, reglas ni vistas que dependan de ella;
sin transacciones abiertas de más de 30 s; 29 conexiones inactivas y 1 activa. Lo único que puede demorar el `ALTER TABLE` es esperar el bloqueo exclusivo breve
detrás de una transacción larga: la migración pone `SET LOCAL lock_timeout = '5s'` y, si no lo consigue, falla sin cambiar nada y se repite.

### Respaldo antes de migrar producción
1. `pg_dump -Fc -t whale_alerts -f V:\Convexa\hist\backups\whale_alerts_pre0040_<fecha>.dump` (~11 MB, segundos) y un `pg_dump -Fc --schema-only` de toda la base (pequeño);
   anotar `alembic_version` (hoy `0039_gamma_near_money_width`). Espacio libre en V: 210 GB.
2. Un volcado completo de la base (17 GB) no hace falta para este cambio (añadir una columna nullable y `alembic downgrade -1` la quita); si se quiere igualmente,
   fuera de horario y con espacio de sobra.
3. Restaurar solo esta tabla: `pg_restore -d Convexa -t whale_alerts --clean --if-exists <dump>`; o simplemente `alembic downgrade -1`.
La contraseña se lee del `.env` del servidor dentro del script; no se escribe en ningún archivo ni se imprime.

## 4. Costo en el camino caliente y plan de prueba de carga

Lo que se añade por operación:
1. Proceso del stream (`stream_processor_worker`): un `trade.get("condition")` + una comprobación de tipo; en el relay, un campo más en el JSON.
2. Proceso de ballenas (`process_trade`): un `dict.get` + una suma `Decimal` por operación; al cerrar el cubo, una referencia más en un `deque(maxlen=15)`.
   Memoria: un dict pequeño por contrato y minuto cerrado, hasta 15 por contrato.

Antecedente que obliga a medir: el 2026-09-15 un trabajo síncrono en el camino caliente perdió 912 mensajes en 67 s.

Plan (todo antes de instalar, nada toca Postgres ni el Terminal):
- **A. Micro-benchmark** `python -m backend.scripts.bench_trade_condition_capture --contracts 2000 --trades 400000 --rounds 5`
  (mezcla de códigos real; apagado vs encendido intercalados). Criterio fijado ANTES de medir: costo añadido < 10% del tiempo por operación y
  < 20 MB de memoria para 2,000 contratos; el número de alertas debe ser idéntico encendido/apagado.
  **Resultado (portátil, 300,000 operaciones, 2,000 contratos, mediana de 5 rondas): 8.33 → 9.41 µs por operación (+1.09 µs, +13.1%);
  memoria +17.3 MB; alertas 1,000 = 1,000.** La memoria cumple; el criterio relativo (< 10%) NO se cumple (13.1%). En términos absolutos,
  +1.09 µs por operación a 2,400 operaciones/s (el primer minuto de la apertura) son 2.6 ms de CPU por segundo, 0.26% de un núcleo.
  Se deja a decisión de Lester si el criterio relativo es el correcto o se pasa a uno absoluto; no se cambió el criterio después de medir.
- **B. Prueba de la cadena** `python -m backend.scripts.loadtest_stream_split --rates 10000,20000,40000 --seconds 30`
  en esta rama y en `main`, mismas tasas. Criterio: sin `queue_full`, sin pérdida entre etapas, latencia p99 de extremo a extremo y retraso del bucle
  del procesador no peores que `main` en más de 10%.
- **C. En el servidor, fuera de horario**: no se corre la prueba B (compite con el stream real); solo se repiten las pruebas unitarias.
- **D. Primera sesión tras la instalación** (viernes 10-16, 09:30-10:00, la más cargada): `frame mix` por minuto, `SLOW CONSUMER` en el log del Terminal,
  `queue full` en `stream_processor_worker.log` y `whale_alerts_worker.log`, memoria de ambos procesos contra los días anteriores.

**Resultado de B** (portátil, 20 s por tasa, mismo script en `main` y en la rama, 2 rondas cada uno, 2026-10-08):

| Tasa | main (ronda 1 / 2) | rama (ronda 1 / 2) |
|---|---|---|
| 10,000/s | sin pérdida; marcador p99 2.2 / 1.0 ms | sin pérdida; p99 1.8 / 1.1 ms |
| 20,000/s | sin pérdida; p99 5.6 / 3.2 ms | sin pérdida; p99 4.9 / 2.5 ms |
| 40,000/s | 0 / **4,106** tramas perdidas; p50 24 / 522 ms | **114** / 0 perdidas; p50 297 / 375 ms |

**Repetición a 40,000/s (pedida por Lester, 2026-10-08): 8 corridas por versión, intercaladas, 20 s cada una, en tres tandas.** La mediana de tramas
perdidas es **0 en ambas versiones en las tres tandas** (la mayoría de corridas no pierde nada y unas pocas pierden miles, así que la mediana no distingue);
lo que sí informa es en cuántas corridas hubo pérdida, la magnitud y la mediana de la latencia del marcador:

| Tanda | Código de la rama | main: corridas con pérdida (tramas perdidas, procesador + eventos) | rama: corridas con pérdida | mediana p50 del marcador main / rama |
|---|---|---|---|---|
| 1 | con función auxiliar; **confundida**: yo corría otras cosas en la misma máquina | 1/8 (938 + 938) | 3/8 (22,286 + 22,276; 14,620 + 14,867; 3,808 + 4,218) | 57 / 210 ms |
| 2 | con función auxiliar; limpia (sin nada más corriendo) | 1/8 (1,143 + 1,142) | 2/8 (6,425 + 6,741; 7,652 + 7,737) | 39 / 209 ms |
| 3 | **con la comprobación en línea (este commit)**; limpia | 1/8 (314 + 314) | **0/8** | 80 / 59 ms |

Con la función auxiliar la rama salía peor a 40k (5 de 16 corridas con pérdida contra 2 de 16, latencia mediana 4-5 veces mayor). Se midió la etapa del
procesador aparte (200,000 tramas por `_handle_raw_frame` + la codificación del relay): **+4.7% por trama con la función auxiliar, +1.5% con la comprobación
en línea** (5.80 contra 5.72 us/trama; solo operaciones: 17.67 contra 17.48 us). Se dejó la versión en línea. Con 8 corridas por tanda y pérdidas
esporádicas en `main` también, la tanda 3 NO prueba equivalencia: dice que ya no se ve la diferencia de las tandas 1-2.

A 10 y 20 mil por segundo no hay diferencia. A 40 mil (el "codo" medido el 2026-10-04) ambas variantes oscilan entre sin pérdida y pérdida
de un orden parecido, así que ahí la prueba no distingue la rama de `main`. Límites: una sola máquina compartida (portátil), 2 rondas, 20 s,
y esta prueba **no incluye el motor de ballenas** (su consumidor es un `RelayDataProvider`): el costo del motor sale solo del benchmark A.

## 5. Verificación posterior (primera sesión)

```sql
-- alertas nuevas con el desglose, y que el desglose suma el monto
SELECT alert_type, count(*), count(condition_premium) AS con_desglose,
       count(*) FILTER (WHERE abs((SELECT sum(value::numeric) FROM jsonb_each_text(condition_premium)) - amount) < 1) AS suma_ok
FROM whale_alerts WHERE time >= now() - interval '1 day' GROUP BY 1;
-- prima por grupo de código, por símbolo y día (SPX, NDX): multi-leg = 130-144
```

Esto da, por primera vez, el porcentaje de prima de alertas multi-leg por símbolo y por día con datos guardados.
Límite: son solo las operaciones que terminan dentro de una alerta, no todo el tape (ver "tabla agregada" abajo).

## 6. Reversión

- Apagar sin tocar código: `QLL_WHALE_ALERTS_STORE_CONDITIONS=false` y reiniciar `ConvexaWhaleAlerts`.
- Revertir el PR y reiniciar los dos procesos. La columna puede quedarse (inofensiva) o borrarse con `alembic downgrade -1`.

## 7. Riesgos

1. Camino caliente (sección 4): medido, pero la prueba B no incluye el motor de ballenas con su base de datos real.
2. Memoria del worker de ballenas: hasta 15 dicts por contrato con estado; medida en el benchmark A, no en producción.
3. Migración: tabla común de 11 MB sin TimescaleDB (medido 2026-10-08); riesgo residual = esperar el bloqueo exclusivo (límite de 5 s, se repite).
4. Solo cubre operaciones dentro de alertas. Para el porcentaje sobre TODO el tape haría falta una tabla agregada por símbolo/minuto/código
   (otro cambio: tabla nueva, tarea de vaciado cada minuto); no está en esta fase salvo que Lester lo pida.
5. Si ThetaData cambiara el campo `condition`, el código quedaría en `-1` (sin condición), sin romper nada.
