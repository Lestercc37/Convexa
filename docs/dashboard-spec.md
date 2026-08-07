# Convexa — Dashboard Spec: Regime Badge & Gravitational Map

Alcance: diseño para la Etapa 5 (Dashboard). No se implementa todavía — este documento existe para que, cuando lleguemos ahí, no haya decisiones de UX improvisadas, igual que hicimos con el backend en la Etapa 0.

Regla (ya establecida en la sesión de PRs): el backend nunca decide colores, formas de dibujar ni layout — solo entrega datos consistentes (`MarketSnapshot`, ver `docs/api-contract.md`). Este documento es responsabilidad del frontend, consumiendo esa proyección tal cual.

**Fuente de precio del chart — actualizado**: originalmente se planeó Massive/Polygon vía WebSocket para el precio en vivo, con niveles de Gamma de FlashAlpha por separado. **Decisión revisada**: por ahora, el precio también viene de FlashAlpha (la misma llamada del Screener que ya trae los niveles, con el precio incluido gratis en la respuesta), a cadencia de 30 segundos — sin necesitar Massive todavía, ya que el equipo confirmó que no necesita movimiento tick-a-tick dentro de Convexa. Massive queda anotado en `roadmap-futuro.md` como mejora futura opcional (streaming más fino), no como requisito actual. El frontend debe consumir el precio desde el mismo `MarketSnapshot`/`GammaAggregate` que ya expone la API — no asumir una fuente WebSocket separada todavía; el canal WebSocket documentado en `websocket-contract.md` sigue siendo válido como mecanismo de entrega, solo cambia de dónde el backend obtiene el dato antes de emitirlo.

## 1. Regime Badge (elemento principal, visible sin scroll)

Lo primero que se ve al abrir el Dashboard para el símbolo activo. Objetivo: responder "¿en qué régimen estoy hoy?" en menos de dos segundos, antes de mirar cualquier otra cosa — este es el "chequeo de régimen de dos segundos" que motivó todo este documento.

**Contenido del badge:**
- Texto grande: `LONG GAMMA` o `SHORT GAMMA` (viene de `dealer_mode`).
- Color: verde/azul para `long_gamma` (mercado que amortigua), naranja/rojo para `short_gamma` (mercado que amplifica) — la paleta exacta se define en Etapa 5 con el resto del sistema de diseño, pero la semántica (calma vs. riesgo) es la que importa, no el hex exacto.
- Subtexto: precio actual vs. `gamma_flip` (ej. "SPY $549.10 — arriba del Flip ($548.50)").
- Si `dealer_mode_confirmed` es `false`: el badge muestra un indicador visual de menor confianza (ej. borde punteado o ícono de advertencia pequeño) — no se oculta el dato, se marca como transitorio. Tooltip: "El precio cruzó el Gamma Flip antes del último recálculo del agregado — régimen basado en precio."

**Por símbolo, no global**: cada underlying tiene su propio régimen. El badge corresponde al símbolo activo en el gráfico; si el Dashboard permite múltiples símbolos en paralelo (fuera del MVP, pero previsible), cada uno tiene su propio badge, no uno compartido.

## 2. Mapa de gravitación (debajo o al lado del badge)

Representación visual simple de los niveles clave en una sola línea horizontal (no un gráfico de velas todavía — eso es el chart principal, esto es un resumen):

```
Put Wall          Gamma Flip      Absolute Gamma      Call Wall
   540                548.5             550                555
    |------------------|------●----------|------------------|
                              549.10 (precio actual)
```

- `Put Wall` y `Call Wall` en los extremos — los límites de rango más probables (sección 2 de la investigación de gamma, turno anterior).
- `Gamma Flip` marcado dentro — el límite de régimen, no un borde de rango.
- `Absolute Gamma Strike` marcado como el imán más literal — si coincide o está muy cerca del Flip (es lo típico), se puede fusionar visualmente en vez de mostrar dos marcas casi superpuestas.
- Precio actual como punto móvil sobre la línea, actualizado por polling cada 30 segundos (ver sección 3 — decisión revisada, ya no WebSocket).

`Max Pain` **no aparece en el mapa principal por defecto** — su peso de gravitación es bajo temprano en la sesión (es una teoría de precio de cierre, poco útil con horas de anticipación) y aumenta conforme se acerca el cierre (~3:00pm-4:00pm ET). Con la ventana de trading extendida a la sesión completa (ver `trading-playbook.md` "Dinámica de Cierre"), Max Pain se promueve del panel secundario/informativo al mapa principal cuando `time_to_close_pct` baja de cierto umbral (a calibrar, mismo criterio que el resto de umbrales pendientes del playbook) — no es una exclusión permanente, es peso condicional al horario.

### 2.1 Modo Estático vs. Histórico (selector en la barra de herramientas del chart)

Dos formas de dibujar la misma serie de GammaAggregate que ya se
persiste — no son datos distintos, es la misma fuente, distinta
presentación. Selector junto a los botones de timeframe (1m/5m/15m/1h),
ej. "Niveles: Estático | Histórico". Cambiar entre ambos durante la
sesión no requiere recálculo — ambos leen la misma serie ya persistida.

- Estático (el ya implementado): líneas punteadas fijas mostrando solo
  el valor vigente de cada nivel. Incluye Absolute Gamma Strike.
- Histórico (inspirado en el chart principal de GEXBot): Call Wall,
  Gamma Flip y Put Wall dibujados como series de tiempo superpuestas al
  precio, mostrando cómo se movió cada nivel desde la apertura — no solo
  dónde está ahora. Absolute Gamma Strike NO se incluye en este modo —
  suele solaparse visualmente con Gamma Flip y no aporta lectura
  adicional en una serie de tiempo (mismo criterio que usa GEXBot en su
  propio chart histórico).

Nota de metodología, si se compara contra GEXBot u otra fuente externa:
GEXBot calcula su histórico con un modelo "GEX by volume (90d)", distinto
a la metodología basada en Open Interest que usa FlashAlpha. Los números
no van a coincidir entre plataformas — no es un error, es una diferencia
de convención.

### 2.2 Chart Principal de Velas — decisión de librería y fuente de datos (nueva, no implementada todavía)

**Librería**: Lightweight Charts (TradingView, código abierto, licencia Apache 2.0, gratis) — evaluada y decidida en una sesión anterior. Se autohospeda, permite dibujar overlays propios (los niveles de Gamma del Mapa de Gravitación, superpuestos sobre las velas) — a diferencia de un widget embebido de terceros, que no lo permite.

**Fuente de datos — decisión pragmática para esta etapa**: no existe todavía un scheduler real que persista precio cada 30 segundos en `market_snapshots` (solo hay datos de prueba insertados a mano), y `GET /api/v1/market/{symbol}/history` tampoco existe. En vez de bloquear el chart hasta tener esa pieza construida, **las velas se acumulan del lado del cliente**: cada vez que el frontend hace polling de `GET /api/v1/market/{symbol}` (cada 30s, ya implementado), agrega ese precio a un arreglo en memoria del navegador, y construye velas de 1 minuto a partir de esos puntos acumulados (2 muestras por vela, igual que ya calculamos el presupuesto de FlashAlpha). **Limitación aceptada conscientemente**: el historial se pierde si se refresca la página — es una versión funcional, no la definitiva. La versión con historial persistido del lado del servidor queda para cuando se construya el scheduler real (ya anotado como pendiente).

## 3. Actualización

El badge y el mapa se refrescan vía polling cada 30 segundos a `GET /api/v1/market/{symbol}` (decisión revisada — el diseño original planeaba WebSocket, pero el frontend ya implementado usa polling, consistente con la cadencia de datos del backend). El `gamma_as_of` de cada respuesta permite mostrar, si se desea, hace cuánto se recalculó el agregado (ej. "Gamma actualizado hace 40s") — útil para que el usuario sepa qué tan fresco es el régimen que está viendo, dado que el precio se actualiza más seguido que el agregado.

## 4. Referencia

La lógica de qué hacer según el régimen que muestra el badge (no solo qué régimen es) está documentada en `docs/trading-playbook.md`, incluyendo el disclaimer estadístico sobre el poder predictivo real del GEX — el tooltip del badge debería enlazar ahí en vez de duplicar el texto.

## 5. Volatility Smile (panel Options Chain)

**Fuente de datos confirmada**: FlashAlpha entrega `implied_vol` por contrato (crudo, derivado de BSM) desde su tier gratis — suficiente para este panel tal como está diseñado (puntos individuales por strike, no curva suavizada). La versión SVI-suavizada es Alpha-tier, pero no la necesitamos — el diseño de esta sección usa puntos crudos, igual que GEXBot.

Vive en el panel de **Options Chain**, indexado por strike para un vencimiento específico — no se agrega entre vencimientos ni se superpone al chart de velas (eje X incompatible: strike vs. tiempo).

**Rol explícito — no es un nivel de gravitación.** A diferencia de Call Wall/Put Wall/Gamma Flip/Absolute Gamma (que reflejan presión mecánica de hedging de dealers sobre el precio), el Smile refleja **sentimiento de riesgo tasado en la prima** — no genera presión de compra/venta sobre el subyacente por sí mismo. No se etiqueta ni se trata en la UI como un nivel hacia el que el precio "gravita".

**Dos usos reales, ambos activos para este usuario (opera el futuro ES=F directamente con FlashAlpha — instrumento principal, NQ pasa a secundario/scalping sin niveles en vivo dentro de Convexa por ahora, ver `use-cases.md`; y compra Calls/Puts directamente):**
1. **Alerta temprana de régimen**: el skew se mueve antes de que el signo del Net Gamma cambie — un put skew que se empina de golpe en la apertura es señal de que el terreno se está poniendo menos favorable para el Modo 1 (mean-reversion), incluso antes de que el badge confirme el cambio a Short Gamma. Se usa como modificador de confianza sobre el modo ya activo, no como un cuarto modo.
2. **Selección de strike/vencimiento al comprar contratos**: un strike OTM con skew empinado está pagando una IV relativa más alta — mismo movimiento direccional esperado, prima más cara. Relevante directamente al elegir qué Call/Put comprar, no solo como contexto pasivo.

## 6. Movimiento Esperado (Expected Move)

Widget compacto, junto al Regime Badge — dato en texto, no gráfico (el gráfico es opcional/secundario). Formato: **"Movimiento esperado: ±$X.XX (X.XX%) — Rango: $bajo – $alto"**, con una segunda línea mostrando el remanente del día (`remaining_1sd_dollars`/`remaining_1sd_pct`), que se achica en tiempo real a medida que avanza la sesión.

**Fuente de datos — ya confirmada, sin trabajo adicional**: el endpoint Zero-DTE de FlashAlpha (Growth+, ya revisado en `use-cases.md`) devuelve esto directamente en el bloque `expected_move`:
```json
"expected_move": {
  "implied_1sd_dollars": 2.18,
  "implied_1sd_pct": 0.37,
  "remaining_1sd_dollars": 1.05,
  "remaining_1sd_pct": 0.18,
  "upper_bound": 591.47,
  "lower_bound": 589.37,
  "straddle_price": 1.62,
  "atm_iv": 0.123
}
```
No requiere cálculo propio — se consume tal cual. Metodología de referencia (por si se necesita calcular en algún punto sin este endpoint): `Movimiento Esperado (1 desviación estándar) = Precio × IV_ATM × √(DTE/365)`, o directamente desde el precio del straddle ATM para mayor precisión en plazos cortos.

**Relevancia directa para la ventana de trading del equipo**: el campo `remaining` se recalcula en tiempo real según `time_to_close_pct` — a las 9:35am se ve casi el movimiento esperado completo del día; conforme avanza la sesión se puede ver cuánto "presupuesto" de movimiento ya se consumió. Información accionable durante toda la sesión regular (9:30am-4:00pm ET, ver `trading-playbook.md`), no solo en la apertura — útil tanto para quien cierra posiciones a media mañana como para quien las deja correr hacia el cierre.

**Distinción importante — no es lo mismo que Call Wall/Put Wall, son complementarios**: los Walls son presión mecánica de hedging por concentración de Open Interest; el Movimiento Esperado es una medida estadística derivada de la volatilidad implícita (vía el straddle ATM). Dos tipos de límite con origen distinto — se muestran juntos en el Dashboard, uno no reemplaza al otro.

**Visualización opcional (no prioritaria)**: dos líneas de referencia punteadas discretas en el chart principal (`upper_bound`/`lower_bound`), distintas visualmente de las líneas de Call Wall/Put Wall/Gamma Flip ya definidas en la sección 2, para no confundir ambos tipos de nivel.

## 7. Market Bias (barra inferior de métricas)

Etiqueta corta (Bullish/Bearish/Neutral) junto al Put/Call Ratio en la barra inferior de métricas del Dashboard (ya vista en los mockups: Open Interest, Put/Call Ratio, IV Rank, etc.) — no en el Regime Badge principal, para no repetir la confusión ya corregida dos veces entre régimen de gamma (Long/Short Gamma, comportamiento de volatilidad) y dirección de mercado.

**Fórmula completa, umbrales, y requisito de historial mínimo**: documentados en `derived-metrics.md` sección 3 — percentil de Put/Call OI Ratio + skew 25-delta contra los últimos 60 días del propio underlying, mismo patrón que Dealer Impact Score. Lleva el mismo tooltip de "métrica propia de Convexa" y el mismo estado `provisional` mientras no se cumple el mínimo de 20 días.

## 8. Preparación Pre-Sesión (vista dedicada, sin dato nuevo)

Vista separada del chart principal en vivo — para revisar la noche anterior o antes de la apertura (9:30am), no durante la sesión.

**Fuente de datos — ya confirmada, sin trabajo adicional**: usa el mismo snapshot de `GammaAggregate` que FlashAlpha recalcula una sola vez al día, después del cierre (~8:30 PM ET), sobre OI oficial de la OCC — ya documentado en `use-cases.md` como el endpoint base de GEX (distinto del endpoint `/flow` que sí se actualiza intradía). Es literalmente "los niveles de mañana, congelados desde el cierre de hoy" — no requiere ningún dato que no tengamos ya contemplado.

**Visualización**: barras de magnitud de gamma por strike, normalizadas (0 a 1), con Gamma Flip y Max Pain marcados como líneas de referencia — mismo formato que el "GEX Profile" de GEXBot ya evaluado en `vendor-comparison.md` (implementable con datos de OI, sin depender de la pregunta pendiente de trade tape granular). Etiqueta clara de "Congelado desde el cierre de [fecha]" para que no se confunda con datos en vivo.

**Propósito**: dar un vistazo rápido de los niveles clave antes de que abra el mercado, sin necesidad de esperar a que la sesión en vivo actualice nada — útil para preparación, no para decisiones intradía (esas siguen viviendo en el chart principal con el mapa de gravitación de la sección 2).

## 9. Dinámica de Cierre (panel condicional, más prominente cerca del cierre)

Panel que traduce la sección "Dinámica de Cierre" de `trading-playbook.md` a elementos visibles del Dashboard. No es exclusivo de quienes operan hasta las 4pm — está siempre presente, pero su prominencia visual escala con `time_to_close_pct` (ya disponible en el endpoint Zero-DTE de FlashAlpha): discreto/secundario temprano en la sesión, promovido a posición prominente conforme se acerca el cierre.

**Contenido**:
- `charm_regime` (`time_decay_dealers_buy` / `time_decay_dealers_sell`) — traducido a lenguaje simple: "el paso del tiempo está empujando a los dealers a comprar/vender".
- `vanna_interpretation` — mismo criterio, en lenguaje simple, no el valor crudo.
- `pin_risk.pin_score` (0-100) — barra visual simple, mismo estilo que Dealer Impact Score/Signal Alignment Score (`derived-metrics.md`), con la etiqueta de "no es estándar de mercado" si aplica.
- Max Pain, promovido desde el panel secundario cuando el umbral de cercanía al cierre se cumple (ver sección 2).

**Por qué escala con el tiempo, no es on/off**: mismo espíritu que el resto del Dashboard — el dato existe siempre, pero se le da el peso visual que corresponde al momento de la sesión, en vez de mostrar u ocultar secciones completas de forma abrupta.

**Relevancia por usuario**: como el equipo no comparte la misma ventana de trading, este panel es más relevante para quienes operan hasta el cierre — pero no se oculta para el resto, ya que cualquiera podría querer verlo (ej. para evaluar si dejar correr una posición hacia el final del día).

## 10. VWAP Anclado (Anchored VWAP)

Nivel donde el precio suele reaccionar, calculado con matemática estándar de industria — confirmado contra un script de ThinkOrSwim revisado en sesión anterior. La fórmula es de dominio público, no propietaria de ningún proveedor. Se ancla en la apertura de la sesión (9:30 ET) y se reinicia cada día a esa hora, igual que el script original.

**Fórmula (estándar):**

```
Precio típico (por lectura) = (high + low + close) / 3

VWAP_anclado = Σ(precio_típico × volumen_del_intervalo) desde la apertura de HOY (9:30 ET) hasta ahora
               ÷
               Σ(volumen_del_intervalo) desde la apertura de HOY hasta ahora
```

**Aproximación necesaria, documentada explícitamente — Convexa no tiene datos tick a tick**: `market_snapshots` persiste un punto de precio cada 30 segundos (columnas `price` y `volume`, este último el volumen **acumulado de sesión**, mismo patrón ya resuelto con Eagle Contracts), no OHLC por intervalo. Dos ajustes sobre la fórmula estándar, ninguno escondido:

- **Precio típico del intervalo ≈ `price` de esa lectura** — no hay high/low por ventana de 30s, solo el precio puntual. Es una aproximación razonable a esa resolución temporal, no la fórmula exacta de un candle de 1 minuto.
- **Volumen del intervalo = `volumen_actual - volumen_lectura_anterior`** — como `volume` es acumulado de sesión (se reinicia a 0 en cada apertura, mismo comportamiento que ya maneja `EagleContractsEngine` al detectar un delta negativo entre sesiones), la primera lectura después de las 9:30 ET no tiene lectura previa dentro de la sesión contra la cual restar: su propio `volume` ya es, por definición, el volumen acumulado desde la apertura hasta ese instante, así que se usa tal cual como volumen de ese primer intervalo (delta implícito contra el cero de apertura).

**Estado provisional**: requiere al menos 1 lectura después de la apertura para tener valor — antes de eso (o si el volumen acumulado del rango es 0, ej. justo a las 9:30:00), `provisional=true` y `value=null`. El campo `sample_count` expone cuántas lecturas de `market_snapshots` entraron al cálculo, para que el frontend pueda mostrar la confianza del nivel sin adivinar.

**Caso de uso**: proyección pura (`calculate_anchored_vwap`), no persiste el resultado — mismo patrón que `dealer_mode` (propiedad derivada) y `derived_metrics` (calculado on-demand desde storage). Lee el historial de `market_snapshots` del underlying desde la apertura de la sesión actual hasta la lectura más reciente vía el nuevo método de storage `get_price_history`, y se expone en `GET /api/v1/market/{symbol}` como el campo `anchored_vwap`.

**Nota de metodología**: al igual que con el histórico de Gamma (sección 2.1) y Vanna (`vendor-comparison.md`), si se compara este VWAP Anclado contra el de otra plataforma (ThinkOrSwim, NinjaTrader) los números pueden diferir levemente por la resolución de 30s vs. tick a tick de esas plataformas — no es un error de cálculo, es una diferencia de resolución de muestreo ya documentada arriba.

## 11. Fuera de alcance de este documento

- Paleta de colores exacta, tipografía, sistema de diseño general — se resuelve en Etapa 5 junto con el resto del Dashboard, no aquí (evita sobre-especificar antes de tener el resto de la UI para dar contexto).
- Vista de Options Chain, Flow — tienen su propio documento cuando lleguen esas etapas.
- Widget de frontend para VWAP Anclado (sección 10) — el backend/API de este PR es la base; el widget visual queda para un PR aparte.
