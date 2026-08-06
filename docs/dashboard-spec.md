# Dashboard

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
