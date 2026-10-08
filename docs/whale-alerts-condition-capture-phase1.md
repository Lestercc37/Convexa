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
Es un `ADD COLUMN` nullable sin valor por defecto: solo metadatos en Postgres. Verificar antes si la hipertabla `whale_alerts` tiene
compresión activa en la versión de TimescaleDB instalada (añadir columnas a una hipertabla comprimida tiene restricciones según versión).

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
3. Migración sobre hipertabla: comprobar compresión antes (sección 3).
4. Solo cubre operaciones dentro de alertas. Para el porcentaje sobre TODO el tape haría falta una tabla agregada por símbolo/minuto/código
   (otro cambio: tabla nueva, tarea de vaciado cada minuto); no está en esta fase salvo que Lester lo pida.
5. Si ThetaData cambiara el campo `condition`, el código quedaría en `-1` (sin condición), sin romper nada.
