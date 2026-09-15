# Whale Alerts: campo `condition` y clasificación multi-leg (SPX/SPY)

Investigación recapturada desde cero el 2026-09-15 — un hallazgo anterior (mencionaba "~5.5% del tape
de SPY 0DTE" marcado multi-leg) se perdió con una sesión de chat previa y nunca quedó documentado en
el repositorio. Este archivo existe para que eso no vuelva a pasar.

## 1. Tabla oficial de códigos `condition` (ThetaData)

Fuente exacta, confirmada en vivo visitando la página (no citada de memoria):
[`https://docs.thetadata.us/Articles/Errors-Exchanges-Conditions/Trade-Conditions.html`](https://docs.thetadata.us/Articles/Errors-Exchanges-Conditions/Trade-Conditions.html)
— tabla completa de 149 códigos (0-148), el estándar OPRA/CTA/UTP.

### Códigos multi-leg modernos (complex order book) — el conjunto usado en esta investigación

| Código | Nombre | Descripción (textual de la fuente) |
|---|---|---|
| 130 | MULTI_LEG_AUTOELEC_TRADE | "Transaction represents an electronic execution of a multi leg order traded in a complex order book." |
| 131 | MULTI_LEG_AUCTION | "...electronic multi leg order which was 'stopped' at a price and traded in a two sided auction mechanism...in a complex order book." |
| 132 | MULTI_LEG_CROSS | "...electronic multi leg order...crossing mechanism...Customer to Customer Cross and QCC with two or more options legs." |
| 133 | MULTI_LEG_FLOOR_TRADE | "...non-electronic multi leg order trade executed against other multi-leg order(s) on a trading floor." |
| 134 | ML_AUTO_ELEC_TRADE_AGSL | "...electronic execution of a multi Leg order traded against single leg orders/quotes." |
| 135 | STOCK_OPTIONS_AUCTION | "...electronic multi leg stock/options order...auction mechanism...complex order book." |
| 136 | ML_AUCTION_AGSL | "...multi leg order...auction mechanism...trades against single leg orders/quotes." |
| 137 | ML_FLOOR_TRADE_AGSL | "...non-electronic multi leg order trade executed on a trading floor against single leg orders/quotes." |
| 138 | STK_OPT_AUTO_ELEC_TRADE | "...electronic execution of a multi leg stock/options order traded in a complex order book." |
| 139 | STOCK_OPTIONS_CROSS | "...electronic multi leg stock/options order...crossing mechanism...Customer to Customer Cross." |
| 140 | STOCK_OPTIONS_FLOOR_TRADE | "...non-electronic multi leg order stock/options trade executed on a trading floor in a Complex order book." |
| 141 | STK_OPT_AE_TRD_AGSL | "...electronic execution of a multi Leg stock/options order traded against single leg orders/quotes." |
| 142 | STK_OPT_AUCTION_AGSL | "...multi leg stock/options order...auction mechanism...trades against single leg orders/quotes." |
| 143 | STK_OPT_FLOOR_TRADE_AGSL | "...non-electronic multi leg stock/options order trade executed on a trading floor against single leg orders/quotes." |
| 144 | ML_FLOOR_TRADE_OF_PP | "...proprietary product non-electronic multi leg order with at least 3 legs. The trade price may be outside the current NBBO." |

**Rango usado para el % medido abajo: códigos 130-144 inclusive.**

### Códigos legacy relacionados (NO incluidos en el % medido — reportados aparte)

| Código | Nombre | Descripción (textual) |
|---|---|---|
| 35 | SPREAD | "Spread between 2 options in the same options class." |
| 36 | STRADDLE | "Straddle between 2 options in the same options class." |
| 37 | BUY_WRITE | "This is the option part of a covered call." |
| 38 | COMBO | "A buy and a sell in 2 or more options in the same class." |
| 124 | QUALIFIED_CONTINGENT_TRADE | "...two or more component orders...execution of one component is contingent upon the execution of all other components at or near the same time..." |

Estos aparecieron con frecuencia insignificante en la captura real (no se observaron en los conteos
finales de abajo) — se documentan como candidatos legacy, no como parte de la definición operativa de
"multi-leg" que se terminó usando.

## 2. Confirmación de que `condition` viaja en el payload real

Confirmado con captura en vivo del WebSocket (`ws://127.0.0.1:25520/v1/events`), no por lectura de
código. `ThetaStreamHub._handle_option_trade` (`backend/adapters/providers/thetadata/provider.py`) ya
recibe `condition` en el mensaje crudo `trade` — simplemente nunca se leía antes de esta investigación
(solo se extraían `size`, `sequence`, `price`).

Ejemplos reales capturados en vivo (2026-09-15, ~09:39 ET, apertura de mercado):
```
root=SPY occ=SPY260915C00760000 trade={'ms_of_day': 34787596, 'sequence': 127612803, 'size': 1, 'condition': 125, 'price': 1.15, 'exchange': 4, 'date': 20260915}
root=SPY occ=SPY260915C00762000 trade={'ms_of_day': 34787625, 'sequence': 127614589, 'size': 5, 'condition': 18, 'price': 0.41, 'exchange': 4, 'date': 20260915}
root=SPXW occ=SPXW260915C07605000 trade={'ms_of_day': 34787681, 'sequence': 127620733, 'size': 2, 'condition': 125, 'price': 15.1, 'exchange': 5, 'date': 20260915}
```
`condition=125` = SINGLE_LEG_AUCTION_NON_ISO, `condition=18` = AUTO_EXECUTION — ambos códigos válidos
de la tabla oficial, ninguno multi-leg (esperado: la mayoría del tape es single-leg).

## 3. Porcentaje real medido — ventana limpia de 60 minutos

**Ventana:** 2026-09-15, 11:23:25–12:23:33 ET (mercado abierto, día de trading normal). Muestreo 1-de-5
mensajes (no el 100%, margen de seguridad adicional — ver sección de diseño abajo). Símbolos: SPY y
SPX (roots SPX+SPXW combinados en un solo conteo "SPX").

| Símbolo | Trades muestreados | Multi-leg (130-144) | % multi-leg |
|---|---|---|---|
| **SPY** | 23,213 | 1,385 | **5.97%** |
| **SPX** (SPXW) | 27,938 | 8,930 | **31.96%** |

### Comparación contra el ~5.5% mencionado en la investigación perdida

El número de SPY (**5.97%**) es consistente con el ~5.5% recordado — dentro de un margen razonable
dado que son mediciones de días distintos con volumen real distinto, no la misma muestra.

**SPX resultó mucho más alto (31.96%) que SPY** — confirma la hipótesis original de Lester: SPX, con
mayor actividad institucional, tiene una proporción de multi-leg varias veces mayor que SPY, que es más
retail. Esto no se había medido antes (el hallazgo perdido solo mencionaba SPY).

Distribución de códigos individuales más comunes en la muestra final (además de 130-144): `18`
(AUTO_EXECUTION) y `125` (SINGLE_LEG_AUCTION_NON_ISO) dominan el tape single-leg en ambos símbolos,
como es de esperar.

## 4. Diseño del mecanismo de captura (para referencia futura)

**El primer intento de esta captura causó un incidente real** (912 mensajes de SPY perdidos en 67s,
2026-09-15 ~09:45 ET) por hacer trabajo síncrono (incremento de dict anidado + logging periódico
pesado) directamente en el hot path del Trade Stream (`ThetaStreamHub._handle_option_trade`).

El diseño final (usado para la medición de arriba) corrige la causa raíz, no el síntoma:
- **Costo en el hot path:** un chequeo `is not None` cuando está desactivado; un `put_nowait()` de una
  tupla mínima `(symbol, condition)` en una cola dedicada (nunca la cola real de Whale Alerts) cuando
  está activo, con muestreo 1-de-5.
- **Agregado pesado** (Counter, sort, logging) en una tarea `asyncio` completamente separada, cada 5
  minutos — nunca en el bucle de lectura principal.
- **Circuit breaker de dos capas:** cualquier drop CRITICAL real de producción (cualquier símbolo)
  desactiva la captura al instante; límite duro de 90 minutos sin importar qué pase.
- Resultado: **cero drops de producción durante toda la ventana de 60 minutos** de esta medición final.

El código de captura es temporal (marcado `TEMPORARY ... remove before merging` en el propio código) —
no está pensado para quedar en producción de forma permanente.

## 5. No implementado

Esta investigación confirma que hay una base de datos real y suficiente para evaluar la etiqueta visual
de "Multi-leg" en Whale Alerts — pero **no se implementó ningún cambio de producción todavía**. Ver
también la investigación separada (pausada) sobre cómo exponer `condition` desde el backend hasta el
frontend, que sigue pendiente de una decisión de diseño aparte.
