# Convexa — Trading Playbook: Régimen de Gamma

Estado: v1.0 — primer documento de estrategia del proyecto (el resto de `docs/` es de ingeniería). Vive aquí porque justifica por qué el Dashboard (`dashboard-spec.md`) se diseñó como se diseñó — el badge de régimen no es un adorno, es la entrada de este playbook.

## Disclaimer obligatorio

Este documento no es asesoría financiera ni garantiza resultados. Un backtest independiente de 8 años sobre SPY encontró que, al controlar por VIX e IV ATM, el poder predictivo aislado del GEX sobre la volatilidad del día siguiente cae de una correlación fuerte a ruido estadístico (p=0.18). El gamma exposure es un marco de contexto estructural con mecanismo de mercado real y documentado (Barbon & Buraschi, 2020/2021), no una señal de entrada autónoma ni una fórmula predictiva validada de forma aislada. Este playbook lo usa exactamente para eso: **contexto que filtra qué operaciones evitar**, no un sistema que predice el próximo movimiento.

## Principio central

El gamma no es el gatillo de entrada. El footprint / volume delta / VWAP (tu herramienta ya existente en NinjaTrader) sigue siendo el gatillo. El régimen de gamma decide **qué tipo de jugada tiene sentido intentar** y **qué tan ajustado o amplio debe ir el stop** — es una capa de contexto montada sobre la herramienta de flujo, no un reemplazo.

Ventana de aplicación: sesión regular completa (9:30am-4:00pm ET) — decisión revisada: originalmente diseñado solo para la ventana personal del fundador (apertura-11:30am), extendido a la sesión completa porque el equipo de 5 personas no comparte la misma ventana — algunos operan opciones hasta el cierre, aprovechando dinámicas de Charm/pin que la ventana original excluía por diseño. Un solo marco cubre ambos casos: quien opera solo la mañana simplemente no usa las secciones de cierre; quien opera hasta las 4pm las tiene disponibles. Dentro de esta ventana, la porción de apertura sigue siendo donde el régimen de gamma tiene más tracción relativa (razón original de la ventana angosta) — eso no cambia, solo se deja de excluir el resto de la sesión.

## Los tres modos (aplican durante toda la sesión, régimen-dependientes)

### Modo 1 — Long Gamma, precio lejos del Flip (por encima): Mean-Reversion

**Condición**: `dealer_mode = long_gamma`, precio claramente por encima de `gamma_flip` (no en zona de transición).

**Jugada**: rebotes en Put Wall (largo), resistencia en Call Wall (corto / toma de ganancia). Absolute Gamma Strike como objetivo de "vuelta al centro" del rango.

**Gatillo de entrada**: tocar el nivel NO es la señal. La señal es la confirmación de flujo en ese nivel — absorción de venta en el Put Wall (delta se voltea, iceberg absorbiendo oferta) antes de entrar largo. El nivel dice dónde mirar; el footprint dice cuándo.

**Gestión de riesgo**: stops relativamente ajustados. El régimen amortigua el movimiento — si el precio se mueve en tu contra y no revierte rápido, es señal temprana de que la lectura de régimen o de flujo estaba equivocada, no momento de aguantar la posición.

### Modo 2 — Short Gamma, precio lejos del Flip (por debajo): Momentum/Ruptura

**Condición**: `dealer_mode = short_gamma`, precio claramente por debajo de `gamma_flip`.

**Jugada**: el marco se invierte respecto al Modo 1. **No fadear los walls, especialmente el Put Wall.** Las rupturas del Put Wall en este régimen tienden a ser violentas (hedging pro-cíclico), no rebotes — tratar una ruptura de Put Wall como señal de continuación, no de reversión.

**Gatillo de entrada**: ruptura del Put Wall confirmada por footprint mostrando venta agresiva sostenida, sin absorción — eso confirma seguir el momentum. Ausencia de esa confirmación (venta que sí encuentra absorción) es señal de no perseguir el nivel.

**Gestión de riesgo**: stops más amplios, tamaño de posición más conservador. Los movimientos en este régimen son más rápidos y recorren más distancia de lo que aparenta el rango reciente.

### Modo 3 — Precio cerca del Gamma Flip: Reducir exposición

**Condición**: precio dentro de una banda estrecha alrededor de `gamma_flip` (umbral exacto a definir en Etapa 5/7 con datos reales — punto de trabajo futuro, no cerrado todavía).

**Jugada**: ninguna por defecto. Es la zona de transición — el régimen puede cambiar dentro de la misma sesión de la mañana, y el marco de Modo 1 o Modo 2 puede volverse incorrecto a mitad de operación.

**Regla operativa**: exigir más confirmación de footprint de la habitual antes de entrar, y reducir tamaño de posición — no por prohibición, sino porque no se sabe todavía con cuál de los otros dos modos va a terminar el trade.

## Dinámica de Cierre — Charm/Vanna (última hora de sesión, ~3:00pm-4:00pm ET)

**No es un cuarto modo que compite con los 3 anteriores — es una capa que se monta encima de cualquiera de ellos** cuando la sesión se acerca al cierre. Mecanismo distinto al de Gamma puro: mientras los 3 Modos leen la posición del precio respecto al Flip, esta capa lee **cuánto tiempo queda** (`time_to_close_pct` / `time_to_close_hours`, ya disponibles en el endpoint Zero-DTE de FlashAlpha) y el régimen de Charm/Vanna vigente.

**Por qué importa específicamente en esta ventana**: Charm (delta decay) impulsa el pinning de fin de día, concentrado sobre todo en la subasta de cierre — el gamma de 0DTE puede llegar a 10x+ un contrato equivalente de 7 días cerca del cierre. Vanna (sensibilidad a cambios de IV) se vuelve más relevante si hay movimiento de volatilidad en las últimas horas — un spike de IV puede forzar venta de delta del dealer incluso sin que el precio se mueva del Flip.

**Lectura práctica**: `charm_regime` (`time_decay_dealers_buy` / `time_decay_dealers_sell`) indica si el paso del tiempo está empujando a los dealers a comprar o vender — soporte o presión adicional, independiente del régimen de Gamma Largo/Corto ya identificado. `pin_risk.pin_score` (0-100) señala qué tan probable es que el precio quede "clavado" cerca de un strike específico hacia el cierre.

**Max Pain gana peso real en esta ventana** — a diferencia de la ventana de apertura, donde es solo informativo (es una teoría de precio de cierre, poco útil con horas de anticipación), conforme el `time_to_close_pct` baja, Max Pain se vuelve más relevante como referencia de hacia dónde puede converger el precio.

**Gestión de riesgo en esta ventana**: mayor gamma acelerada significa movimientos más rápidos y bruscos cerca del cierre — el ancho de stop debe ajustarse con el mismo criterio que Modo 2 (amplio), incluso si el régimen de fondo es Long Gamma, porque la mecánica de Charm puede sobreponerse al comportamiento típico de "amortiguar" cerca del final de la sesión.

## Resumen de decisión rápida (para el badge del Dashboard)

| `dealer_mode` | Posición vs Flip | Modo | Walls | Stop |
|---|---|---|---|---|
| `long_gamma` | Lejos, por encima | Mean-Reversion | Rebote/resistencia confiables (con confirmación de flujo) | Ajustado |
| `short_gamma` | Lejos, por debajo | Momentum | Rupturas probables, no rebotes — especialmente Put Wall | Amplio |
| cualquiera | Cerca del Flip | Reducir exposición | No usar como referencia — régimen inestable | N/A — menor tamaño |
| cualquiera | Última hora (~3-4pm ET) | + Dinámica de Cierre (overlay) | Max Pain gana peso; Charm/Vanna pueden sobreponerse al régimen base | Amplio, independiente del régimen de fondo |

## La ventaja real (sin adornos)

Este playbook no predice el próximo movimiento. Su función es evitar un tipo específico de error bien documentado: fadear una ruptura del Put Wall durante un régimen de gamma corto — la asimetría mejor respaldada de toda la investigación (rupturas de call wall ordenadas vs. rupturas de put wall violentas). El edge no viene de anticipar la dirección; viene de no pelear contra el régimen equivocado con la herramienta de flujo que ya sabes leer.

## Pendiente de definir (no bloquea el uso del playbook, pero queda anotado)

- Umbral exacto de "cerca del Flip" para el Modo 3 (¿0.3%? ¿0.5% del precio? — se calibra con datos reales una vez que el Gamma Engine esté corriendo, Etapa 7).
- Si vale la pena registrar el resultado de cada trade junto al `dealer_mode` vigente en ese momento, para poder validar (o refutar) este playbook con tus propios datos en vez de solo literatura de terceros — candidato natural para el módulo de Historical Analytics (Etapa 6+ en adelante). Con la ventana extendida, este registro debería incluir también si el trade ocurrió dentro de la ventana de Dinámica de Cierre, para poder evaluar esa capa por separado.

**Resuelto** — Umbral exacto de "última hora" para la Dinámica de Cierre: dinámico según `time_to_close_pct` (no fijo por reloj), `<= 15` (últimos ~15% de la sesión, ~58 minutos de una sesión de 6.5 horas). Calibración inicial, no definitiva — se ajustará con datos reales más adelante, mismo espíritu que los umbrales iniciales de Whale Alerts. Implementado en `calculate_closing_dynamics.py` (`CLOSING_WINDOW_THRESHOLD_PCT`), documentado en detalle en `dashboard-spec.md` sección 9.
