# Por qué colapsó el split de stream processing — 2026-10-02

Intento: mover el parsing/clasificación de QUOTE/TRADE a un proceso nuevo (`ConvexaStreamProcessor`), con un relay bidireccional (frames crudos worker→procesador, eventos clasificados de vuelta). Desplegado 1:30:46pm, colapsó y se desconectó solo a 1:33:29pm (2m 41s de vida). Detenido manualmente, sistema vuelto al estado seguro (procesamiento en el mismo proceso, 100-150ms de atraso conocido). **No se reintentó nada — esto es solo causa raíz para diseñar una v2.**

## Línea de tiempo exacta

- `13:30:48.134` — el procesador se conecta al relay.
- `13:30:50.425` — **primer mensaje descartado por cola llena — 2.29 segundos después de conectar.**
- `13:33:29.156` — el procesador se desconecta solo (probablemente colapsado por el propio volumen de logging: 229MB en <3 min).
- Total de mensajes descartados solo del lado de `worker.py`: **1,025,239** en 2m 41s.

---

## Pregunta 1: ¿el viaje de ida y vuelta es necesario para TODO, o solo para parte?

**No es necesario para nada, en realidad — fue una decisión de diseño mía, no un requisito real.**

Repasé qué necesita cada pieza que depende de lo que el procesador clasifica:

- **`_cumulative_volume`**: hoy se actualiza en memoria dentro de `ThetaStreamHub` (worker.py), porque `StreamStateExporter` (que corre EN worker.py) lo exporta a Postgres cada 15s para que el Scheduler lo lea. Pero no hay ninguna razón estructural para que esto tenga que pasar por `ThetaStreamHub` específicamente — el procesador podría escribir el delta de volumen **directo a Postgres** (su propio `StreamStateExporter`-equivalente), igual que `whale_alerts_worker.py` ya exporta su propio estado de forma independiente hoy.
- **El feed del chart** (`PriceNotificationHub`) y **Whale Alerts** (`WhaleAlertsRelayPublisher`): ambos leen de las colas internas de `ThetaStreamHub` en worker.py — pero eso es porque así los conecté yo. Si el procesador en vez de "contestarle a worker.py" **publicara directamente** a un relay de precio y a Whale Alerts (el mismo patrón que `whale_alerts_relay.py` ya usa, uno-a-muchos, sin esperar respuesta), no haría falta que nada vuelva a worker.py en absoluto.

**Conclusión**: diseñé un patrón de ida-y-vuelta (request/response) cuando el problema en realidad pedía un patrón de publicación unidireccional (fire-and-forget) — el procesador debería ser el dueño final de la clasificación y publicar hacia adelante, no reportarle de vuelta a worker.py. Lo único que estructuralmente DEBE quedarse en worker.py es la lectura cruda del WebSocket (Theta Terminal solo permite una conexión) y la publicación unidireccional de esos frames hacia el procesador — nada más.

## Pregunta 2: ¿el tamaño de cola (20,000) explica el colapso casi inmediato?

**Parcialmente, pero no es la causa única — el patrón bidireccional la agrava.**

Matemática: a 17,000-23,000 msgs/seg, una cola de 20,000 representa **~1 segundo de buffer** si nada la está drenando. El colapso real tomó 2.29 segundos, no 1 — significa que el procesador SÍ estaba drenando algo al principio, pero mucho más lento de lo que llegaba, acumulando déficit hasta reventar.

Dato importante para contexto: **el mismo tamaño de cola (20,000) en el relay de Whale Alerts (`whale_alerts_relay.py`) NO colapsó durante todo el día de hoy** bajo el mismo volumen real — la única vez que se llenó fue esta mañana, cuando el proceso receptor estaba literalmente colgado (0% CPU, el incidente de las 3:34am). Eso sugiere que 20,000 SÍ alcanza para un relay **unidireccional** bien drenado a este volumen — el problema no es solo el tamaño de la cola, es que mi diseño le pedía al procesador hacer el trabajo DOBLE (leer + reclasificar + volver a mandar) en el mismo tiempo que el relay de Whale Alerts solo necesita hacer la mitad (recibir y listo).

**Conclusión**: el tamaño de cola es insuficiente *dado* el patrón bidireccional, pero probablemente sería suficiente para un patrón unidireccional puro (que es lo que la Pregunta 1 ya señala como el diseño correcto).

## Pregunta 3: ¿existe un patrón de referencia para esto?

**Sí — ya está en este mismo proyecto, funcionando, y no lo seguí lo suficientemente literal.**

`backend/core/whale_alerts_relay.py` (PR #179) es exactamente el patrón "un proceso de alto volumen descarga trabajo a otro proceso sin esperar respuesta": `WhaleAlertsRelayPublisher` publica eventos ya armados hacia `whale_alerts_worker.py`, nunca espera nada de vuelta, y el consumidor hace su propio trabajo pesado de forma completamente aislada. Es el patrón clásico de **pub/sub fire-and-forget** (ampliamente documentado en la industria para separar un hilo/proceso de I/O crítico de trabajo pesado downstream — mismo principio detrás de Disruptor de LMAX, o de cualquier cola de mensajes tipo "producer never waits for consumer").

No existe ningún patrón de ida-y-vuelta (request/response) en todo este codebase — ni uno. Construí algo sin precedente en el proyecto en vez de replicar literalmente el patrón ya probado, que es justo lo que el usuario me pidió evitar ("no inventes una arquitectura nueva, usa la que ya funciona"). Ese fue el error de diseño real.

---

## Recomendación para la v2 (no implementada, para la próxima sesión)

Rediseñar como **unidireccional puro**, replicando `whale_alerts_relay.py` literalmente:
1. `worker.py` sigue siendo el único dueño de la conexión WS real — sin cambios ahí.
2. El procesador nuevo recibe frames crudos (igual que hoy) y, en vez de contestarle a worker.py, **publica directamente**:
   - El evento clasificado (QuoteEvent/FlowEvent/UnderlyingTradeEvent) a quien realmente lo consume — posiblemente un relay nuevo hacia el proceso que sirve el chart, y/o reemplazando directamente la fuente de `WhaleAlertsRelayPublisher`.
   - El delta de `cumulative_volume` directo a Postgres, con su propio exportador periódico (como ya hace `whale_alerts_worker.py`).
3. El watchdog de silencio se queda exactamente como está hoy (ya es local a worker.py, nunca necesitó el viaje de vuelta).

Esto elimina el viaje de regreso por completo — el procesador deja de ser "el ayudante que le reporta a worker.py" y pasa a ser un publicador de primera clase, igual que `whale_alerts_worker.py` ya lo es.
