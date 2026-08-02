# Convexa — Métricas Derivadas Propietarias

Estado: v1.0. Este documento define fórmulas de Convexa que **no son estándares de la industria** — a diferencia de GEX, Gamma Flip, Vega Exposure, etc. (que sí lo son y están documentados en `architecture.md`/`database-schema.md`), las métricas de este documento son compuestos propios. Cualquier pantalla que las muestre debe dejarlo claro (ver sección 3, "Cómo se presentan").

## 1. Dealer Impact Score (antes "Confidence: 78%" del mockup — renombrado y redefinido)

### Metodología base: GEX Percentile Rank

Metodología encontrada en la investigación (MenthorQ y otros la usan): en vez de decir "GEX es -$500K" sin contexto, se pregunta **qué tan extremo es ese número comparado con la propia historia del underlying**. Un GEX de -$500K puede ser normal para una acción y extremo para otra — el número crudo no dice nada por sí solo.

**Fórmula**:
```
GEX_percentile(t) = (# de días en la ventana trailing donde |NetGamma| ≤ |NetGamma(t)|) / (N días en la ventana) × 100
```

- Ventana default: **60 días de trading** (≈ 3 meses). Configurable por underlying.
- **Muestreo de un punto por día, no por snapshot de minuto**: se toma el `NetGamma` de un instante fijo cada día (ej. 09:35am, dentro de tu ventana de trading) para construir la distribución histórica. Comparar snapshots de distintas horas del día entre sí sería comparar cosas no equivalentes — el gamma varía sistemáticamente durante la sesión, no solo día a día.
- Interpretación: percentil alto (>70) = régimen de gamma inusualmente fuerte para este underlying, en cualquier dirección; percentil bajo (<30) = débil/neutral; medio (40-60) = normal.

**`Dealer Impact Score` = este percentil**, tal cual, sin capas adicionales. No es una probabilidad de acierto — es una medida de qué tan extrema es la lectura de hoy respecto a la propia historia del activo.

### Requisito de datos mínimos

No se muestra hasta tener al menos 20 días de historia acumulada (mínimo estadístico razonable; 60 es el ideal). Antes de eso, la UI muestra `Acumulando historial (X/20 días)` en vez de un número — esto es directamente lo que evita el problema que señalé al principio: precisión falsa en el día uno.

## 2. Signal Alignment Score (antes parte del "Confidence" genérico — separado y redefinido)

No mide qué tan extremo es el régimen (eso ya lo hace el Dealer Impact Score) — mide **qué tan de acuerdo están las señales independientes que ya calculamos**, todas disponibles desde el día uno sin necesitar historial.

### Componentes (los tres ya existen en el sistema, ninguno es nuevo)

| Componente | Fuente | Peso |
|---|---|---|
| Acuerdo de régimen | `dealer_mode_source` (`agree` vs `price_vs_flip`) — ver `api-contract.md` | 40% |
| Frescura del dato | `gamma_as_of` vs. `as_of` — qué tan reciente es el último recálculo de `GammaAggregate` | 30% |
| Extremidad del régimen | `Dealer Impact Score` (sección 1) — un percentil cercano a 0 o 100 es una lectura más clara que uno cercano a 50 | 30% |

### Fórmula

```
agreement_component  = 100 si dealer_mode_source == "agree", si no 40
freshness_component  = 100 si (as_of - gamma_as_of) < 60s
                        interpolación lineal a 0 conforme crece el desfase, hasta 0 en >5 min
extremity_component  = |Dealer_Impact_Score − 50| × 2      # 0 en percentil 50, 100 en percentil 0 o 100

Signal_Alignment_Score = 0.40 × agreement_component
                        + 0.30 × freshness_component
                        + 0.30 × extremity_component
```

**Los pesos (40/30/30) son un punto de partida razonado, no un resultado calibrado estadísticamente** — no existe todavía el historial de resultados propios para optimizarlos (es el mismo punto pendiente que ya anotamos en `trading-playbook.md`). Se documentan aquí precisamente para que, cuando haya datos reales, se puedan ajustar de forma consciente y no se pierda el rastro de por qué empezaron en estos valores.

**Antes de tener el mínimo de 20 días para `extremity_component`**: el score se calcula solo con los dos primeros componentes (`agreement` 60%, `freshness` 40%), y la UI lo marca como `provisional`.

## 3. Market Bias

**Origen**: pendiente desde la tabla de decisión de la sección 4 (era una línea suelta, sin fórmula ni ubicación — se formaliza aquí). No confundir con el "Dealer Mode" (Long/Short Gamma) del Regime Badge — ese describe **comportamiento de volatilidad** (amortigua vs. amplifica), este describe **sesgo direccional del posicionamiento de opciones**. Son ejes distintos, no se combinan en una sola etiqueta.

**Por qué no es un indicador técnico de precio (a diferencia de "Trend Strength", ya descartado)**: se deriva enteramente de datos de opciones (Put/Call Ratio, skew de IV) — la ventaja de dato que realmente tenemos — no de velas ni EMAs, que es lo que hace NT8 mejor con order flow. Evaluado explícitamente contra los widgets de "Bias/Strength" típicos de TradingView (basados en % de movimiento de vela o cruces de EMA) — esos quedan fuera por ser puro price action, redundante con NT8.

### Metodología: mismo patrón de percentil que Dealer Impact Score, dos insumos

```
PC_percentile(t)    = percentil de hoy del Put/Call OI Ratio vs. los últimos 60 días del propio underlying
Skew_percentile(t)  = percentil de hoy del skew 25-delta (put IV − call IV) vs. los últimos 60 días

# Invertido: P/C ratio alto y skew alto = más miedo/protección = bajista.
# Percentil alto de cualquiera de los dos empuja el score hacia bajista, no alcista.
Market_Bias_Score = 0.5 × (100 − PC_percentile) + 0.5 × (100 − Skew_percentile)

Bullish  si Market_Bias_Score > 65
Bearish  si Market_Bias_Score < 35
Neutral  en cualquier otro caso
```

Los umbrales (65/35) son un punto de partida razonado, mismo criterio que los pesos de Signal Alignment Score — no calibrados estadísticamente todavía, documentados para ajustarse conscientemente cuando haya historial propio.

**Requisito de datos mínimos**: igual que Dealer Impact Score — no se muestra hasta 20 días de historial (idealmente 60). Antes de eso, `Acumulando historial (X/20 días)`.

**Reutilización de infraestructura, no tabla nueva**: `daily_gamma_reference` (`database-schema.md`) ya muestrea un punto diario a hora fija para el percentil de gamma — se le agregan dos columnas (`pc_oi_ratio`, `skew_25d`) en vez de crear una tabla aparte.

**Ubicación en el Dashboard**: junto a Put/Call Ratio en la barra inferior (`dashboard-spec.md`), como una etiqueta corta (Bullish/Bearish/Neutral) al lado del número — no en el Regime Badge, para no repetir la confusión que ya corregimos dos veces entre régimen de gamma y dirección de mercado.

## 4. Cómo se presentan (regla de UI, no negociable)

Ninguna métrica de este documento (incluyendo Market Bias) se presenta como un número o etiqueta sin calificar. Todas llevan:
- Un tooltip o nota que dice explícitamente "métrica propia de Convexa, no un estándar de mercado" (o equivalente).
- El estado `provisional` visible mientras no se cumple el mínimo de historial.
- Nunca se redondea a un lenguaje que implique probabilidad de acierto de un trade (ej. nunca "78% de probabilidad de que el precio suba") — todas describen el estado de las señales, no un pronóstico.

## 5. Resto de métricas del mockup — decisión

| Métrica del mockup | Decisión | Fuente |
|---|---|---|
| Vega Exposure | Se agrega a `GammaAggregate` — mismo patrón que GEX (`Vega × OI × 100`, por 1% de cambio en IV). Sube Theta/Vega de "opcional" a obligatorio en Greeks MVP (Domain Model v1.1) | Estándar de industria, Polygon |
| Theta Exposure | Igual que Vega Exposure, con Theta | Estándar de industria, Polygon |
| Volatility Regime | Bucket de IV Rank: <30 Low, 30-70 Moderate, >70 High — fórmula completa en sección 6 | Ya teníamos IV Rank en el bottom bar |

## 6. IV Rank y Volatility Regime (formalización — antes solo mencionado como elemento visual del mockup, sin fórmula)

**Adaptación consciente respecto a la convención más común de la industria**: la mayoría de plataformas calculan IV Rank sobre una ventana de 52 semanas (~252 días). Aquí se usa la misma ventana de 60 días / mínimo 20 días ya establecida para Dealer Impact Score y Market Bias — no por ser "más correcto", sino por consistencia con el resto del sistema y porque una ventana de 252 días tardaría casi un año en dejar de mostrar `provisional` en una plataforma nueva, lo cual la haría inútil durante todo ese tiempo. Se documenta esta desviación explícitamente para que no se confunda con el estándar de mercado si algún día se compara contra otra plataforma.

**Fórmula**:
```
IV_Rank(t) = (atm_iv(t) − min(atm_iv, ventana trailing)) /
             (max(atm_iv, ventana trailing) − min(atm_iv, ventana trailing)) × 100

Volatility_Regime = "low"      si IV_Rank < 30
                     "moderate" si 30 ≤ IV_Rank ≤ 70
                     "high"     si IV_Rank > 70
```

**Caso borde — ventana sin rango**: si `max(atm_iv) - min(atm_iv) == 0`,
`IV_Rank = 50` y `Volatility_Regime = "moderate"`. Cuando todos los valores
son iguales no existe base para clasificar el dato actual como alto o bajo;
el punto medio es una convención explícita, no un resultado calculado.

- Ventana: mismo default de 60 días / mínimo 20 días que el resto de métricas de este documento.
- **Fuente de `atm_iv`**: se agrega como columna nueva a `daily_gamma_reference` (mismo patrón que `pc_oi_ratio`/`skew_25d` del PR de Métricas Derivadas) — muestreado a la misma hora fija (09:35 ET), desde el mismo snapshot del proveedor que ya entrega esos otros dos campos.
- **Requisito de datos mínimos**: igual que el resto — antes de 20 días de historial, `provisional: true`, sin valor numérico.
- **No es una métrica propietaria en el sentido de sección 4** — IV Rank es un concepto estándar de la industria; lo que sí es una adaptación propia de Convexa es la ventana de 60 días en vez de 252. La regla de presentación de sección 4 (nota "no es estándar de mercado") no le aplica de la misma forma que a Dealer Impact Score/Signal Alignment Score/Market Bias — en su lugar, si se muestra la ventana usada, debe indicarse "60 días" explícitamente para no sugerir el estándar de 52 semanas.
| Liquidity Regime | Bucket de bid-ask spread relativo + volumen vs. promedio histórico del underlying | Polygon (spread, volumen) |
| Market Bias | Formalizado — ver sección 3 (percentil de Put/Call OI Ratio + skew, mismo patrón que Dealer Impact Score) | FlashAlpha (`pc_ratio_oi`, `skew_25d`, ya confirmados disponibles en Growth) |
| Trend Strength | **Fuera de alcance** — es un indicador técnico de precio (tipo ADX), no de opciones/gamma. No entra al dominio de Convexa; se solapa con lo que tu footprint de NT8 ya te da | — |
| Dealer Impact Score | Redefinido — sección 1 de este documento | GEX Percentile, propio |
| Confidence | Redefinido y renombrado a Signal Alignment Score — sección 2 | Propio |
