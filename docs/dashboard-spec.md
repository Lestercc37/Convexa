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

**Aproximación necesaria, documentada explícitamente — Convexa no tiene datos tick a tick**: `market_snapshots` persiste un punto de precio cada 30 segundos (columnas `price` y `volume`, este último el volumen **acumulado de sesión**, mismo patrón ya resuelto con Whale Alerts), no OHLC por intervalo. Dos ajustes sobre la fórmula estándar, ninguno escondido:

- **Precio típico del intervalo ≈ `price` de esa lectura** — no hay high/low por ventana de 30s, solo el precio puntual. Es una aproximación razonable a esa resolución temporal, no la fórmula exacta de un candle de 1 minuto.
- **Volumen del intervalo = `volumen_actual - volumen_lectura_anterior`** — como `volume` es acumulado de sesión (se reinicia a 0 en cada apertura, mismo comportamiento que ya maneja `WhaleAlertsEngine` al detectar un delta negativo entre sesiones), la primera lectura después de las 9:30 ET no tiene lectura previa dentro de la sesión contra la cual restar: su propio `volume` ya es, por definición, el volumen acumulado desde la apertura hasta ese instante, así que se usa tal cual como volumen de ese primer intervalo (delta implícito contra el cero de apertura).

**Estado provisional**: requiere al menos 1 lectura después de la apertura para tener valor — antes de eso (o si el volumen acumulado del rango es 0, ej. justo a las 9:30:00), `provisional=true` y `value=null`. El campo `sample_count` expone cuántas lecturas de `market_snapshots` entraron al cálculo, para que el frontend pueda mostrar la confianza del nivel sin adivinar.

**Caso de uso**: proyección pura (`calculate_anchored_vwap`), no persiste el resultado — mismo patrón que `dealer_mode` (propiedad derivada) y `derived_metrics` (calculado on-demand desde storage). Lee el historial de `market_snapshots` del underlying desde la apertura de la sesión actual hasta la lectura más reciente vía el nuevo método de storage `get_price_history`, y se expone en `GET /api/v1/market/{symbol}` como el campo `anchored_vwap`.

**Nota de metodología**: al igual que con el histórico de Gamma (sección 2.1) y Vanna (`vendor-comparison.md`), si se compara este VWAP Anclado contra el de otra plataforma (ThinkOrSwim, NinjaTrader) los números pueden diferir levemente por la resolución de 30s vs. tick a tick de esas plataformas — no es un error de cálculo, es una diferencia de resolución de muestreo ya documentada arriba.

**Widget de frontend (implementado)**: el backend solo expone el valor *actual* de `anchored_vwap` en cada respuesta de `GET /api/v1/market/{symbol}` — no un historial. La línea continua "desde la apertura de la sesión" se construye **acumulando del lado del cliente**: cada poll de 30s que trae una lectura no provisional se agrega a un arreglo en memoria del navegador (mismo mecanismo ya aceptado para las velas del chart, sección 2.2), y se dibuja como una serie de línea continua (no punteada, para diferenciarla de los niveles de Gamma) sobre el `PriceChart`, en color ámbar (`#f3c969`, ya usado en el Dashboard como acento). **Misma limitación ya aceptada conscientemente que con las velas**: el historial de la línea se pierde si se refresca la página — la línea solo cubre desde que el usuario abrió el Dashboard, no necesariamente desde las 9:30 ET reales si se abrió después. La versión con historial persistido del lado del servidor (un endpoint de historial de VWAP) queda pendiente, no es parte de este PR. Toggle independiente para mostrar/ocultar el overlay junto a los controles del chart.

## 11. Rango Histórico Esperado (ATR)

Banda de precio esperado anclada a la apertura del día, basada en el rango de movimiento reciente del subyacente — True Range / ATR clásico de análisis técnico, matemática estándar de la industria y de dominio público. Construcción **propia** de Convexa: inspirada en observar el patrón de un indicador de terceros, pero reconstruida desde el concepto general de ATR, no una copia de ninguna fórmula propietaria.

Complementa (no reemplaza) a Movimiento Esperado (sección 6, basado en IV de opciones) y a VWAP Anclado (sección 10, basado en volumen intradía). Este es un **tercer enfoque**, basado solo en el rango histórico diario de precio — no necesita cadena de opciones ni, para el ATR en sí, snapshots intradía.

**Fórmula (ATR clásico, ventana de 14 días):**

```
True Range (por día) = max(
  high_hoy - low_hoy,
  |high_hoy - close_ayer|,
  |low_hoy - close_ayer|
)

ATR = promedio simple de True Range de los últimos 14 días

Banda del día (ancladas a la apertura de HOY):
  banda_externa_superior = apertura_hoy + ATR
  banda_externa_inferior = apertura_hoy - ATR
  banda_interna_superior = apertura_hoy + (ATR / 2)
  banda_interna_inferior = apertura_hoy - (ATR / 2)
```

**Corrección sobre el conteo de historial (detectada al implementar)**: calcular 14 valores de True Range requiere **15** días cerrados consecutivos, no 14 — cada True Range usa el cierre del día anterior, así que el día más antiguo de la ventana solo aporta ese cierre de referencia y no genera su propio True Range. El requisito de historial mínimo es **15 días de barras diarias** en `daily_bars`, no 14.

**Fuente de los 15 días cerrados**: tabla nueva `daily_bars` (`underlying_id, date, open, high, low, close`), un registro por día **cerrado** únicamente — nunca contiene una fila para la sesión de hoy en curso. Es la materia prima del True Range/ATR, sin dependencia de datos intradía.

**Fuente de `apertura_hoy` — reutiliza el mecanismo ya existente de VWAP Anclado, sin mecanismo nuevo**: `daily_bars` nunca tiene una fila de "hoy" (el high/low/close del día no existen hasta el cierre), así que la apertura de hoy es un dato intradía, tomado de `market_snapshots` — específicamente, la primera lectura de la sesión desde las 9:30 ET, el mismo dato que ya usa `calculate_anchored_vwap` para anclar el VWAP. El ATR en sí (basado en historial cerrado) es lo único que viene de `daily_bars`.

**Dos condiciones de `provisional` independientes, no colapsadas en una sola:**
1. `atr_provisional` — menos de 15 días de historial en `daily_bars` → sin valor de ATR (`atr: null`).
2. `bands_provisional` — todavía no hay ninguna lectura de `market_snapshots` para la sesión de hoy (antes de apertura, o el scheduler interno no ha corrido todavía) → sin valor de banda, **aunque el ATR de 14 días ya esté disponible** (`atr_provisional=false` pero `bands_provisional=true`). No hay apertura de hoy contra qué anclar todavía.

**Caso de uso**: proyección pura (`calculate_atr_range`), no persiste el resultado — mismo patrón que `anchored_vwap`. Se expone en `GET /api/v1/market/{symbol}` como el bloque `atr_range`, con `atr`, `atr_provisional`, `daily_bars_count`, `today_open`, `bands_provisional`, y las cuatro bandas (`outer_upper_band`, `outer_lower_band`, `inner_upper_band`, `inner_lower_band`).

**Widget de frontend (implementado)**: no existía ningún patrón previo de "banda semi-transparente" en el Dashboard (ni siquiera para Movimiento Esperado, sección 6, que solo se muestra como texto) — el diseño visual se construyó desde cero para este PR. A diferencia de VWAP Anclado, las bandas **no se acumulan del lado del cliente**: `atr_range` ya viene recalculado en cada poll de 30s con valores ancladas a la apertura de hoy y un ATR que no cambia intradía, así que se dibujan directo con los valores del último poll, sin arreglo histórico. Dos rectángulos semi-transparentes superpuestos al `PriceChart` (externo más tenue, interno más opaco), en violeta (`#a78bfa`, nueva variable `--atr`, elegido por ser distinguible de los demás colores ya usados en el Dashboard — calma/riesgo/acento/ámbar están todos tomados). Si `bands_provisional=true` (sin importar el estado de `atr_provisional`) no se dibuja nada, sin mensaje de error. Toggle independiente para mostrar/ocultar el overlay.

## 12. Fuera de alcance de este documento

- Vista de Options Chain, Flow — tienen su propio documento cuando lleguen esas etapas.
- Historial persistido del lado del servidor para la línea de VWAP Anclado (endpoint dedicado) — por ahora se acumula client-side, ver nota de implementación en la sección 10.

## 13. Layout estilo TradingView (implementado)

Migración del Dashboard al layout de referencia aprobado. Reorganiza únicamente estructura y paleta — reutiliza cada componente existente tal cual (mismos props, misma lógica interna), salvo dos excepciones puntuales documentadas abajo. Reemplaza la sección "paleta/tipografía... se resuelve en Etapa 5" que antes vivía en "Fuera de alcance": ya se resolvió, es esta sección.

**Paleta** (variables CSS en `globals.css`):

```
--tv-bg: #131722      --tv-panel: #1e222d   --tv-border: #2A2E39
--tv-text: #d1d4dc     --tv-text-dim: #787b86
--tv-up: #26A69A (velas alcistas — SOLO velas, nunca niveles de Convexa)
--tv-down: #EF5350 (velas bajistas — ídem)
```

**Colores de marca Convexa (`#00DC5A` verde / `#FA000A` rojo)** — decisión de alcance: se aplican a **todo lo que Convexa calcula** (`--calm`/`--risk`, reemplazando sus valores anteriores), no solo a los niveles de Gamma — cubre también `RegimeBadge` (long/short), `VolatilitySmile` (call/put) y el par Put Wall/Call Wall en `PriceChart`/`GravityMap`. Los overlays propios (VWAP ámbar `--warning`, ATR violeta `--atr`) no cambian — ya tenían "su propio color para diferenciarse" por diseño (secciones 10-11). Único corte fijo: las velas (`--tv-up`/`--tv-down`) nunca usan el par de marca, sin excepción.

**Estructura (flexbox, no CSS grid):**
1. Barra superior (~44px): logo, selector de símbolo, precio + OHLC de la vela más reciente, timeframe, `RegimeBadge` a la derecha.
2. Fila principal: toolbar lateral angosto decorativo (44px) | **centro dominante**: `PriceChart` con todos sus overlays (Gamma, VWAP Anclado, ATR Range) y su selector Estático/Histórico ya integrado en su propia barra de herramientas | panel derecho (~256px, apilado): `DerivedMetricsBar` (4 métricas, ahora en columna) + Charm/Vanna Exposure + `ExpectedMoveWidget` + `VolatilitySmile` + `QuickScreener`, todos compactados vía CSS contextual.
3. Franja inferior fija: panel de Alertas (Whale Alerts), nuevo — ver abajo.

**Decisiones de alcance tomadas durante la implementación, documentadas explícitamente:**

- **Panel de Alertas (Whale Alerts) — no existía, se construyó en este PR.** El plan original asumía que ya existía un componente de frontend para reposicionar; verificado contra el código, solo existía el endpoint backend `GET /api/v1/alerts/{symbol}` (`WhaleAlertsResponse`: `symbol`, `contract`, `type` (`WHALE`/`UNUSUAL`), `amount`, `timestamp`), sin consumidor en el frontend. Se construyó `AlertsPanel` (fila horizontal desplazable de tarjetas, una por alerta, color distinto por tipo), consultando los símbolos activos vía la misma lista que ya usa el selector del Dashboard (`getUnderlyings`, sin duplicar la fuente), con el mismo polling de 30s que el resto del Dashboard (`frontend/lib/polling.ts`, nueva constante compartida para evitar duplicar el valor).
- **`GravityMap` retirado del Dashboard compuesto.** La lista de "Estructura" del layout de referencia no le asigna ninguna zona — su función (mostrar Put Wall/Gamma Flip/Call Wall en una franja horizontal) queda cubierta por los mismos niveles ya superpuestos al precio en `PriceChart` (modo Estático, sección 2.1), ahora elemento central dominante en vez de una tarjeta pequeña. El componente y sus tests (`gravity-map.tsx`/`gravity-map.test.tsx`) quedan intactos y siguen pasando de forma standalone — la decisión es reversible con un solo import si se prefiere mantenerlo visible.
- **`RegimeBadge` en la barra superior es una compactación vía CSS, no un componente nuevo.** Mismo componente, mismos props (`gamma`, `market`) — un selector contextual (`.tv-topbar-right .regime-badge`) reduce tamaño de fuente y oculta el eyebrow/meta line para caber en una barra de ~44px, sin tocar `regime-badge.tsx`.
- **Precio + OHLC en la barra superior** se arma con datos que el Dashboard ya tiene (`market.price` + la última vela de `candles`, ya calculada por `aggregateMinuteCandles`) — no es una llamada ni componente nuevo.
- **Timeframe (1m/5m/15m/1h) y el toolbar lateral son decorativos**, igual que ya indicaba el propio plan para el toolbar lateral. Solo existen velas de 1 minuto (sección 2.2); no se inventó agregación real de 5m/15m/1h — los botones de otros timeframes están deshabilitados visualmente, sin lógica detrás, hasta que exista una fuente de datos multi-timeframe real.
- **Charm/Vanna Exposure en el panel derecho no requirió backend nuevo.** `GET /api/v1/gamma/{symbol}` ya devolvía `charm_exposure`/`vanna_exposure` (junto con `max_pain`, `net_gamma`, `vega_exposure`, `theta_exposure`) — el tipo `GammaResponse` del frontend simplemente no los declaraba todavía. Se completó el tipo para reflejar la respuesta real y se agregó un bloque compacto que los muestra.
- **`PriceChart` gana altura responsiva** (adaptación mínima, no lógica de negocio nueva): el `ResizeObserver` que ya ajustaba el ancho del chart en cada resize ahora también ajusta el alto (mismo mecanismo, una dimensión más), necesario para que el chart pueda ser el "centro dominante" del layout en vez de quedar con una altura fija de 420px sin importar el espacio disponible.

**Tests**: `dashboard.test.tsx` incluye un test de cambio de símbolo repetido (mismo escenario que causó los crashes corregidos en los PRs #54 y #55 — remount completo de `PriceChart` vía su `key`), con datos no-provisionales de VWAP/ATR para ejercitar sus efectos de limpieza en el layout nuevo, confirmando que los guards siguen protegiendo correctamente.

## 14. Diagnóstico: overlays durante pan y rango de tiempo del chart

Dos reportes de UX tras usar el Dashboard en vivo, investigados con evidencia antes de tocar código — uno resultó ser una limitación ya documentada (no un bug), el otro sí era un bug real, aislado y corregido.

**"El chart solo muestra ~1 hora, sin importar el volumen de datos" — NO es un bug, es la sección 2.2 en acción.** `PriceChart` nunca lee `market_snapshots` histórico — `pricePoints`/`candles` se acumulan exclusivamente del lado del cliente vía polling de 30s desde que la página está abierta (limitación ya documentada en la sección 2.2, "el historial se pierde si se refresca la página"). Verificado dos veces: (1) consulta directa a la base de datos de desarrollo — 16 filas de `market_snapshots` para SPY, repartidas en varios días distintos (2, 3 y 7 de agosto), ninguna de las cuales el frontend lee para construir velas; (2) el propio `refresh()` de `dashboard.tsx` solo llama a `getMarket()` (la lectura *más reciente*), nunca a un endpoint de historial. La ventana visible del chart es, por diseño actual, del tamaño de la sesión del navegador — no de lo que exista en Postgres. `shiftVisibleRangeOnNewBar` (Lightweight Charts, `true` por defecto — confirmado en `node_modules/lightweight-charts/dist/typings.d.ts`) hace que la ventana visible avance sola con cada vela nueva mientras el usuario no haya hecho pan hacia atrás, así que el rango sí crece con el tiempo de sesión real. No se tocó ningún código del chart para esto — es exactamente la limitación de la sección 2.2, ahora también visible en el eje de tiempo, no solo en "se pierde al refrescar".

**"Los niveles se pierden o dejan de seguir al precio al hacer pan" — bug real, encontrado y corregido.** Los overlays nativos de Lightweight Charts (líneas de precio de Gamma vía `createPriceLine`, y las series de VWAP/Gamma histórico vía `addSeries`+`setData`) son manejados enteramente por la librería — siempre correctos ante pan/zoom por construcción, no hay nada que corregir ahí. El único overlay manual del proyecto son las bandas de ATR (`<div>` posicionados con `series.priceToCoordinate()`), que ya se suscribían correctamente a `subscribeVisibleLogicalRangeChange` (el evento documentado para esto). La causa real no estaba en esa suscripción: `PriceChart` creaba el chart con `width`/`height` fijos y los actualizaba a mano en un `ResizeObserver` propio (`chart.applyOptions({ width, height })`). Con el layout nuevo (sección 13), la altura de `.price-chart` ya no viene de un CSS fijo (`height: 420px`, como en el layout anterior) sino de una cadena de `flex` que se resuelve más tarde — una condición de carrera plausible entre el montaje síncrono de `createChart()` y el resize handler, capaz de dejar el buffer interno del canvas (no el CSS visible) en un tamaño equivocado, mientras cualquier `priceToCoordinate()` posterior — exactamente lo que usan las bandas de ATR — seguiría calculando sobre ese sistema de coordenadas incorrecto.

Corrección: se reemplazó el `ResizeObserver` manual + `chart.applyOptions({width, height})` por `autoSize: true` (opción nativa de Lightweight Charts, pensada exactamente para este caso — ver `typings.d.ts` línea 933 y siguientes) y `chart.timeScale().subscribeSizeChange(...)` para seguir recalculando las bandas de ATR en cualquier cambio de tamaño, no solo en pan. Se confirmó, llamando directamente a la instancia real de la librería (`chart.resize(...)` con `autoSize` desactivado), que el pipeline de resize en sí funciona correctamente una vez disparado — el problema estaba específicamente en cuándo/cómo se disparaba, no en el cálculo de coordenadas.

**Límite honesto de esta verificación**: el entorno de navegador automatizado usado para probar esto en vivo no compone frames reales (confirmado por el propio error de la herramienta de captura de pantalla), lo cual impide confirmar con una captura visual que el pan funciona correctamente end-to-end, y pudo haber afectado si `ResizeObserver` disparaba o no durante las pruebas interactivas en ese entorno específico. Lo que sí se confirmó con evidencia directa (no suposición): (1) los 16 registros reales en la base de datos y que el frontend nunca los consume para el chart; (2) el comportamiento documentado de `shiftVisibleRangeOnNewBar` en el código fuente de la librería instalada; (3) que el pipeline de resize de Lightweight Charts funciona correctamente cuando se dispara explícitamente; (4) que `autoSize`+`subscribeSizeChange` recalculan correctamente las bandas de ATR ante un cambio de tamaño, cubierto ahora por un test de componente. Recomendado: confirmar visualmente el pan en un navegador real tras este PR.

### 14.1 Bug de seguimiento: bandas de ATR colapsadas visualmente al mismo nivel — confirmado y corregido

Reportado por el usuario después de validar el fix de pan de la sección 14: las dos bandas de ATR aparecían siempre en el mismo nivel de precio (~552), sin importar cuántas veces se refrescara la página — es decir, consistente, no intermitente, y sin relación con el gesto de pan.

**Diagnóstico**: los valores numéricos SIEMPRE fueron correctos — el backend devuelve `outer_upper_band`/`outer_lower_band`/`inner_upper_band`/`inner_lower_band` genuinamente distintos, y `bandRect()` los posiciona con `top`/`height` correctos y proporcionalmente anidados (proporción 2:1 exacta, ATR vs ATR/2), verificado con `getBoundingClientRect()` sobre el DOM real. El problema era puramente de **apilamiento visual (z-index)**: `.atr-band` usaba `z-index: 1`, pero Lightweight Charts asigna `z-index: 2` a varias de sus propias capas de canvas internas (confirmado con `getComputedStyle` sobre los 7 canvases que la librería crea por chart) — ambos grupos comparten el mismo contexto de apilamiento (`.price-chart-frame`, `position: relative`), así que las capas del chart con z-index más alto se pintaban encima de las bandas, tapándolas casi por completo, independientemente del orden en el DOM.

**Corrección**: `.atr-band` sube a `z-index: 3` — el mínimo entero que supera el máximo actual de la librería (2) — con un comentario en `globals.css` explicando el número exacto y advirtiendo que deberá subir de nuevo si una futura versión de `lightweight-charts` incrementa sus propios z-index internos (no existe una API pública de "siempre encima" para overlays custom sobre esta librería).

**Nota metodológica, para no repetir el error**: la primera verificación de este fix usando `elementFromPoint`/`elementsFromPoint` (la misma técnica usada para diagnosticar) dio un falso negativo — seguía mostrando el canvas ganando el hit-test incluso con `z-index: 999999` en las bandas. La causa: `.atr-band` tiene `pointer-events: none` (intencional, son overlays puramente visuales), y esa propiedad excluye al elemento del hit-testing por completo, sin importar el z-index — `elementFromPoint` no mide orden de pintado para elementos con `pointer-events: none`, mide orden de hit-testing. La verificación correcta requiere activar `pointer-events: auto` temporalmente solo para la medición (revertido de inmediato después), momento en el cual ambas bandas sí ganan correctamente sobre las dos capas de canvas en cada punto de muestra.

**Test**: `app/globals.css.test.ts` lee el CSS fuente y confirma que `.atr-band` declara un z-index mayor a 2 — un guardia simple contra que alguien baje ese valor sin saber por qué existe, ya que el entorno de tests (jsdom sin `css: true`) no aplica el CSS real y no puede verificar esto por renderizado.

### 14.2 Bug de seguimiento: chart cubierto de morado tras el fix de z-index — bandas con rango de precio degenerado

Reportado por el usuario tras el merge del fix de z-index de la sección 14.1: el chart aparecía completamente cubierto de morado, ocultando los niveles de Gamma. Hipótesis del usuario a verificar primero: el fix de z-index (bandas de 1 a 3) pudo haber **expuesto**, no introducido, un bug de tamaño ya existente en los `<div>` de las bandas — invisible antes porque quedaban detrás del canvas del chart.

**Confirmado con evidencia, mismo método (`getBoundingClientRect()`) que la sección 14.1**: `.price-chart-frame` medía 1112px de alto; `.atr-band-outer` medía `top: -17649px`, `height: 36807px` — unas 33 veces la altura real del contenedor, extendiéndose miles de píxeles por encima y por debajo del viewport visible. No es un problema de opacidad/color — el ancho y la posición horizontal eran correctos, solo `top`/`height` estaban gravemente mal.

**Causa raíz, confirmada inspeccionando el estado real de React (`candles` vía fiber)**: en el momento en que `bandRect()` se ejecuta, el chart tenía exactamente **una vela**, con `open = high = low = close = 552.25` — variación de precio cero (el segundo poll en vivo todavía no había llegado, limitación ya documentada en la sección 2.2 de acumulación de precio del lado del cliente). Con datos de variación cero, el auto-escalado del eje vertical (precio) de Lightweight Charts colapsa a un rango casi nulo — `series.priceToCoordinate()` entonces mapea los límites reales de la banda de ATR (unos pocos dólares de distancia) a coordenadas miles de píxeles fuera del contenedor, produciendo una `height` (la diferencia entre dos de esas coordenadas) ~33,000px en vez de unas pocas decenas.

**Distinto del bug de la sección 14.1**: ese fix corrigió el apilamiento visual (z-index) de bandas que ya estaban bien dimensionadas. Este es un bug de dimensionamiento real que **siempre existió** en el estado de una sola vela plana — el fix de z-index no lo introdujo, solo dejó de ocultarlo detrás del canvas. Se intentó forzar un resize/pan manual para descartar que fuera un problema de temporización de layout (la misma categoría que la sección 14) — no cambió nada, consistente con que el disparador real es la *forma de los datos* (variación de precio cero), no el tamaño del contenedor.

**Corrección**: `bandRect()`/`recompute()` en `price-chart.tsx` ahora no calculan ni renderizan las bandas de ATR mientras las velas visibles no tengan variación de precio real (`hasPriceRange()`, nueva función que compara el máximo `high` contra el mínimo `low` de todas las velas). En cuanto llega una vela con variación real (el `useEffect` ya depende de `candles`, así que se recalcula en cada actualización), las bandas vuelven a aparecer con el mismo `bandRect()` de siempre, ahora sobre un eje de precio ya asentado.

**Test**: `price-chart.test.tsx` — nuevo caso reproduce el rango degenerado exacto (una vela con `open=high=low=close`) junto con un `priceToCoordinate` simulado que reproduce la misma explosión de coordenadas, confirmando que las bandas no se renderizan. Los tres tests existentes que sí esperaban bandas visibles pasaban `candles={[]}` (un arreglo vacío es, si acaso, un caso *más* degenerado que una sola vela plana) — se actualizaron para usar velas con variación de precio real, ya que ese era el escenario que realmente pretendían cubrir. Verificado también en navegador real contra `uvicorn`+`next dev`: con la vela plana inicial de SPY, las bandas ahora no se renderizan (antes cubrían todo el chart); la lógica de auto-recuperación en cuanto hay variación real está cubierta por los tests existentes de reposicionamiento (sección 14).

## 15. Rename: "Eagle Contracts" → "Whale Alerts"

El motor de detección de flujo inusual/institucional (antes `EagleContractsEngine`, sección 2.2 y `use-cases.md`) se renombra a **`WhaleAlertsEngine`**, decisión del usuario — sin cambios de lógica ni de comportamiento, solo de nombres. Mapeo completo: `EagleContractsEngine`→`WhaleAlertsEngine`, `EagleAlert`→`WhaleAlert`, `EagleAlertType`→`WhaleAlertType`, `EagleAlertResponse`/`EagleAlertsResponse`→`WhaleAlertResponse`/`WhaleAlertsResponse`, `build_eagle_contracts_engine`→`build_whale_alerts_engine`, campo del container `eagle_contracts_engine`→`whale_alerts_engine`, `tests/test_eagle_contracts.py`→`tests/test_whale_alerts.py`, y el texto visible en `AlertsPanel` ("Eagle Contracts" → "Whale Alerts" en el eyebrow).

`EagleThresholds` (dataclass interna de `flow.py`, config en memoria del engine) se renombra a **`WhaleAlertThresholds`**, no a "WhaleThresholds" — para no quedar peligrosamente parecido a la entidad de dominio ya existente `WhaleThreshold` (la tabla persistida `whale_thresholds`, con su propio significado: overrides de umbral por símbolo), que este PR explícitamente no toca.

**Explícitamente sin renombrar** (fuera de alcance de este PR):
- La tabla/entidad `whale_thresholds`/`WhaleThreshold` — ya tenía el nombre correcto.
- El endpoint `GET /api/v1/alerts/{symbol}` — ya era genérico.
- El componente `AlertsPanel`/`alerts-panel.tsx` — ya estaba bien nombrado (solo se actualizaron los tipos `Eagle*` que importaba).
- `docs/vendor-comparison.md`, mención de "Eagle Contracts" — es la atribución al script de Pine Script de terceros (TradingView) que inspiró el diseño original de `ProcessFlow`, no el nombre de nuestro sistema. Se deja intacta a propósito.
- Todas las referencias a "QLL Eagle Platform" (`pyproject.toml`, `alembic.ini`, `backend/main.py`, `backend/core/settings.py`, `tests/test_health.py`, `qll_eagle.db`) — es el nombre viejo del **proyecto completo**, un rename distinto y no solicitado en este PR (ver `resumen-proyecto.md` para el catálogo completo de esas referencias, de una investigación anterior).
