# Métricas y Fórmulas Estadísticas

> **Principio fundamental**: `net_pnl = profit + commission + swap` es la única fuente de verdad para P&L.  
> Nunca se usa `profit` solo. Las comisiones y swaps son costos reales de la operativa.

---

## 1. P&L básico

### Net Profit (beneficio neto total)
```
net_profit = Σ net_pnl(i)  para todos los trades i
           = gross_profit + gross_loss
```
- `gross_profit` = suma de todos los `net_pnl` > 0
- `gross_loss` = suma de todos los `net_pnl` ≤ 0 (número negativo)

### Win Rate (tasa de aciertos)
```
win_rate = (trades ganadores / total trades) × 100
```
- Un trade es **ganador** si `net_pnl > 0`
- Expresado en porcentaje (%)

### Average Win / Average Loss
```
avg_win  = gross_profit / total trades ganadores
avg_loss = gross_loss   / total trades perdedores   (resultado negativo)
```

### Expectancy (expectativa por trade)
```
expectancy = net_profit / total_trades
```
Representa el beneficio promedio esperado por cada trade cerrado, en unidades monetarias. Es la métrica más directa de "edge" del sistema.

---

## 2. Profit Factor (PF)

```
profit_factor = gross_profit / |gross_loss|
```
- Si `gross_loss = 0` y `gross_profit > 0` → PF = ∞
- Si ambos son 0 → PF = 0
- **PF > 1**: el sistema genera más de $1 por cada $1 que pierde
- **PF < 1**: el sistema pierde dinero en conjunto
- **Referencia**: PF ≥ 1.5 se considera aceptable; PF ≥ 2.0 es bueno

### Cuándo PF puede engañar
Un PF alto con wins pequeños y frecuentes pero losses raros y enormes puede tener buen PF y aun así ser destructivo. Por eso se complementa con Payout Ratio.

---

## 3. Payout Ratio

```
payout_ratio = avg_win / |avg_loss|
```
Mide la relación entre el tamaño promedio de ganancia y el de pérdida.
- **Payout > 1**: ganancias promedio mayores que pérdidas promedio
- **Payout < 1**: pérdidas promedio mayores que ganancias (típico en sistemas con alta win rate)

Un sistema puede tener payout < 1 y ser rentable si la win rate es suficientemente alta:  
Punto de equilibrio: `win_rate_break_even = |avg_loss| / (avg_win + |avg_loss|)`

---

## 4. Ret/DD Ratio (Recovery Factor)

```
ret_dd_ratio = net_profit / max_dd_dollar
```
Cuántos dólares de ganancia neta se generaron por cada dólar de drawdown máximo.
- También llamado **Recovery Factor** en la literatura de trading
- `ret_dd_ratio = None` si `max_dd_dollar = 0` (sin drawdown)
- **Referencia**: valores > 3 son generalmente aceptables

---

## 5. Equity Curve

### Construcción
La curva de equity **empieza en 0** y acumula `net_pnl`. NO representa el saldo absoluto de la cuenta; representa las ganancias/pérdidas relativas al inicio del período analizado.

```python
equity[0] = 0.0   (punto inicial: día anterior al primer trade)
equity[i] = equity[i-1] + net_pnl[i]
```

El punto inicial se coloca un día antes del primer trade para que la gráfica muestre claramente el inicio desde cero.

**Importante**: Un trade queda con `close_time = None` cuando `parser.py::_parse_date` no logra interpretar la fecha de cierre (formato inesperado en el xlsx). Ese trade **se excluye por completo** de la curva de equity: el `continue` en `_build_equity_curve()` ocurre antes de sumar su `net_pnl`, así que su P&L nunca llega a acumularse en la curva. Sin embargo, ese mismo `net_pnl` **sí** se cuenta en `net_profit`, SQN, Sharpe y streaks, porque esas métricas se calculan sobre la lista completa de trades, no sobre la curva de equity.

Esto significa que `equity_curve`, `max_dd`/`max_dd_pct` y `ret_dd` usan una **población de trades distinta** a la de `net_profit`, SQN, Sharpe y streaks — un trade con `close_time` no parseable puede sumar o restar de estas últimas sin afectar en absoluto a las primeras. Para que este descarte silencioso no oculte datos, `calculate_ea_metrics()` y `calculate_portfolio_metrics()` exponen el contador `untimed_trades` (número de trades con `close_time = None`), siguiendo el contrato SIN DATOS del repositorio: los datos faltantes deben nombrarse, nunca asumirse por defecto.

---

## 6. Drawdown (DD)

### Definición
El drawdown mide cuánto ha caído el equity desde su pico anterior. Es el riesgo realizado del sistema.

### Cálculo del DD en dólares
```
peak_pnl = máximo acumulado de net_pnl hasta el momento t
dd_dollar[t] = peak_pnl - equity[t]
```
`dd_dollar` siempre es ≥ 0.

### Cálculo del DD en porcentaje
```
peak_abs = capital + peak_pnl
dd_pct[t] = (dd_dollar[t] / peak_abs) × 100
```
El denominador `capital + peak_pnl` es la base desde la cual se mide la caída. Usar solo `capital` o solo `equity` sería incorrecto: si el equity subió mucho antes de caer, la base debe incluir esa ganancia.

**La curva de DD se expresa como valores negativos** para visualización (eje Y debajo de cero).

### Max DD
```
max_dd_dollar = máximo de dd_dollar a lo largo de toda la serie
max_dd_pct    = máximo de dd_pct a lo largo de toda la serie
```
`max_dd_dollar` y `max_dd_pct` se rastrean **de forma independiente**, cada uno como el máximo corriente de su propia serie — NO es el `dd_pct` del punto donde ocurre `max_dd_dollar`. El denominador `peak_abs = capital + peak_pnl` cambia entre puntos, así que el peor drawdown porcentual puede darse en un punto distinto al peor drawdown en dólares. Ejemplo: con capital=5000 y trades -1000 / +6000 / -1010, el peor DD en dólares es $1010 (ocurre tras el tercer trade, con peak_pnl=5000), pero el peor DD porcentual es 20% (ocurre tras el primer trade, con peak_pnl=0 y peak_abs=5000 aún pequeño) — reportar solo el DD% del momento de peor DD$ subestimaría el riesgo real.

Se calcula en `_calc_max_drawdown()`. También retorna `last_peak_date`: la fecha del pico más reciente del equity (útil para calcular stagnation).

**Capital default**: $5,000 si no está configurado.

---

## 7. Stagnation (estancamiento)

```
stagnation_days = (hoy - fecha_del_ultimo_pico_equity).days
```
Mide cuántos días han pasado desde que el sistema marcó un nuevo máximo de equity. Un valor elevado indica que el sistema lleva tiempo sin generar nuevos beneficios.

El "pico" solo avanza ante un **nuevo máximo estricto** (`pnl > peak_pnl`), nunca ante un empate (`pnl == peak_pnl`). `_calc_max_drawdown()` y `_build_drawdown_curve()` usan ambas la comparación estricta `>` para mantener coherencia entre `last_peak_date` y la curva de DD. Si el equity vuelve a tocar (sin superar) un pico anterior, eso NO cuenta como nuevo pico y el stagnation sigue contando desde el pico previo.

- `stagnation_days = 0` si el último pico fue hoy
- Se recalcula cada vez que se calculan métricas (usa `date.today()`)

---

## 8. SQN — System Quality Number

```
SQN = √N × mean(net_pnl) / std(net_pnl, ddof=1)
```
Donde:
- `N` = total de trades
- `mean(net_pnl)` = expectativa media por trade
- `std(net_pnl, ddof=1)` = desviación estándar muestral (corrección de Bessel)

Desarrollado por Van K. Tharp para comparar sistemas de trading independientemente de su escala.

### Tabla de calificación SQN
| Valor | Calificación |
|---|---|
| ≥ 7.0 | Santo Grial |
| ≥ 5.0 | Sobresaliente |
| ≥ 3.0 | Excelente |
| ≥ 2.5 | Bueno |
| ≥ 2.0 | Promedio |
| ≥ 1.6 | Debajo promedio |
| < 1.6 | Pobre |

**Nota**: Si N < 20, el SQN se muestra con la nota "(orientativo)" porque con pocos trades la desviación estándar no es estadísticamente robusta.

### La etiqueta se retiene con muestra chica (N < 20)
Si `N < MIN_TRADES_FOR_SQN_LABEL` (= 20), se devuelve el **valor** de SQN con su nota "(orientativo)" pero la **etiqueta pasa a "N/A"**: no se emite calificación.

El motivo no es la varianza sino el tamaño de muestra: `√N × mean / std` no está acotado cuando N es chico, así que un EA que simplemente arranca con unos pocos wins puntúa como élite. `[5.00, 6.00]` — dos trades ganadores perfectamente normales — da SQN = 11.0, que en la tabla de arriba caería en "Santo Grial". Con 2, 3 o 5 trades no hay muestra que sostenga una calificación de calidad, y la nota "(orientativo)" sola no alcanza: lo que el usuario lee en el dashboard es la etiqueta.

Esto afecta a toda la etapa CP1 de incubación (`get_checkpoint_for_trades` clasifica como CP1 a todo EA con menos de 20 trades), que es justamente donde más EAs nuevos se acumulan.

### Guard de varianza degenerada
El SQN no se calcula si `std(net_pnl, ddof=1) <= |mean(net_pnl)| × MIN_COEFFICIENT_OF_VARIATION` (con `MIN_COEFFICIENT_OF_VARIATION = 0.01`). Un guard que solo comprueba `std == 0` no es suficiente: un EA con take-profit fijo (p. ej. `[5.00, 5.01]`) tiene una desviación estándar minúscula pero no nula, y el SQN calculado explota a valores absurdos (>1000, etiqueta "Santo Grial") aunque la muestra sea, en la práctica, un pago fijo y no una distribución real. Comprobar el coeficiente de variación (std relativo a la media) en vez de una desviación exactamente cero detecta también estos casi-degenerados.

- `std_r == 0` exacto → SQN = None, nota "(desviación cero)"
- `0 < std_r ≤ |mean_r| × 0.01` → SQN = None, nota "(varianza degenerada)"
- En ambos casos, `label = "N/A"`

---

## 9. Sharpe Ratio (simplificado)

```
Sharpe = mean(net_pnl) / std(net_pnl, ddof=1)
```
Versión simplificada sin tasa libre de riesgo. Mide cuántas desviaciones estándar de beneficio se obtienen por unidad de riesgo (en términos de variabilidad de retornos). Es básicamente el SQN sin el factor √N.

- `Sharpe = None` si N < 2
- `Sharpe = None` si `std(net_pnl, ddof=1) <= |mean(net_pnl)| × MIN_COEFFICIENT_OF_VARIATION` (mismo guard de varianza degenerada que el SQN — ver sección 8)
- Valores positivos indican edge; cuanto mayor, más consistente el sistema

---

## 10. Streaks (rachas)

### Cálculo
Se recorre la lista de `net_pnl` en orden cronológico:
- Si `net_pnl > 0`: incrementa racha de wins, resetea racha de losses
- Si `net_pnl ≤ 0`: incrementa racha de losses, resetea racha de wins

### Métricas derivadas
```
max_consec_wins   = máxima racha de trades ganadores consecutivos
max_consec_losses = máxima racha de trades perdedores consecutivos
avg_consec_wins   = promedio de todas las rachas ganadoras
avg_consec_losses = promedio de todas las rachas perdedoras
```

`max_consec_losses` es el input más crítico para el Live Validator y los hard gates de incubación, ya que una racha prolongada de pérdidas puede destruir la cuenta incluso si el sistema es rentable a largo plazo.

---

## 11. Métricas rodantes (Rolling Metrics)

Calculadas sobre una ventana deslizante de `window` trades consecutivos:

```
Para cada posición i desde window-1 hasta N-1:
    chunk = trades[i-window+1 : i+1]
    expectancy[i] = mean(net_pnl del chunk)
    win_rate[i]   = (ganadores en chunk / window) × 100
    PF[i]         = gross_profit_chunk / |gross_loss_chunk|
```

Visualizan cómo evolucionan las métricas a lo largo del tiempo, revelando deterioros graduales que el promedio global oculta.

---

## 12. Correlación entre EAs

### Método
1. Se construye una serie diaria de P&L para cada EA (sum de `net_pnl` de ese día)
2. Se alinean las series en fechas comunes (unión o intersección)
3. Se calcula la correlación de Pearson entre cada par de series

```
r(X, Y) = Σ[(Xi - X̄)(Yi - Ȳ)] / √[Σ(Xi - X̄)² × Σ(Yi - Ȳ)²]
```

**Rango**: -1 (perfectamente inverso) a +1 (perfectamente correlado). 0 = sin correlación lineal.

**Visualización**: heatmap rojo-blanco-verde donde:
- Rojo oscuro: correlación negativa fuerte (ideal para diversificación)
- Blanco: sin correlación
- Verde oscuro: correlación positiva fuerte (EAs se comportan igual)

---

## 13. Weeks Operating

```
weeks_operating = (hoy - fecha_cierre_primer_trade).days / 7
```

**El minuendo es `hoy`, no la fecha de cierre del último trade.** Esto es intencional: usar `hoy` en vez del último trade permite detectar EAs que dejaron de operar — un EA con trades antiguos y ninguna actividad reciente debe seguir mostrando un `weeks_operating` creciente, porque el Live Validator usa este valor para decidir si un EA lleva demasiado tiempo inactivo. Si se calculara con la fecha del último trade, un EA que dejó de operar hace meses aparentaría llevar operando el mismo tiempo que uno activo.

El resultado se acota con `max(0.0, ...)`: como el minuendo es "hoy a las 00:00" y `fecha_cierre_primer_trade` suele llevar una hora intradía, un trade cerrado hoy mismo produciría un valor ligeramente negativo sin el clamp. No se puede escalar un límite de DD sobre semanas negativas, así que 0.0 es el piso correcto (equivalente a "recién empezó").

Usado en el Live Validator para escalar el límite de DD:

```
DD_limite = peor_DD_1mes × √(weeks_live / 4.33)
```

4.33 = número promedio de semanas en un mes (52/12).

**⚠️ Acoplamiento conocido a resolver por separado**: como `weeks_operating` crece con el tiempo aunque el EA esté inactivo, un EA dormido (sin trades nuevos) ve crecer su `weeks_live` indefinidamente, lo que **agranda** `DD_limite` — cuanto más tiempo lleva quieto, más laxo se vuelve su gate de DD. Esto es una consecuencia no deseada de reutilizar `weeks_operating` (pensado para detectar inactividad) como input directo de un cálculo que premia la inactividad con más margen de riesgo. Se deja señalado aquí; no se modifica `validator.py` en este cambio.

---

## 14. Monthly Frequency (frecuencia mensual)

```
monthly_frequency = total_trades / meses_transcurridos
```

Donde `meses_transcurridos = días_desde_primer_trade / 30.44`.

Para el **backtest**: se calcula a partir de `bt_period` (ej: "2020.01.01 - 2024.12.31") y el total de trades.

Para el **live/incubación**: se calcula desde la fecha del primer trade hasta hoy.

La frecuencia es crítica para detectar si un EA está "operando menos de lo normal", lo que puede indicar que el broker no está ejecutando las señales, condiciones de mercado desfavorables, o que el EA está en un período atípico.

---

## 15. Avg Bars per Trade

Aproximación del número de barras (velas) que dura cada trade en promedio:

```
avg_bars_live = avg_duration_hours / timeframe_hours
```

Donde `timeframe_hours` se deriva del timeframe del backtest:

| Timeframe | Horas |
|---|---|
| M1 | 1/60 |
| M5 | 5/60 |
| M15 | 15/60 |
| M30 | 30/60 |
| H1 | 1.0 |
| H4 | 4.0 |
| D1 | 24.0 |
| W1 | 168.0 |

Si el avg_bars_live difiere mucho del backtest, puede indicar slippage elevado o cambio en las condiciones de ejecución.

---

## 16. Walk-Forward Efficiency (WFE)

```
BT_profit_per_month  = bt_expectancy × bt_trades / bt_months
Live_profit_per_month = live_expectancy × live_trades / live_months
WFE = (Live_profit_per_month / BT_profit_per_month) × 100
```

Mide qué porcentaje de la rentabilidad del backtest se está materializando en live.

| WFE | Estado |
|---|---|
| > 120% | ALERTA (mejor que BT → posible sobreoptimización del BT) |
| 70-120% | OK |
| 50-70% | ALERTA |
| 30-50% | ALERTA |
| < 30% | FUERA |

---

## 17. Edge Erosion

```
edge_erosion = ((expectancy_live - spp_expectancy_median) / spp_expectancy_median) × 100
```

Compara la expectativa live contra la mediana del SPP (System Parameter Permutation). Si `edge_erosion < -30%`, el sistema está perdiendo su ventaja estadística.

El SPP actúa como una estimación más conservadora del "edge real" del sistema: si el sistema funciona en un rango amplio de parámetros (no solo en los optimizados), la mediana SPP refleja ese edge robusto.

---

## 18. Significancia estadística (trades live)

| Trades | Nivel |
|---|---|
| ≥ 100 | Alta |
| ≥ 50 | Media |
| ≥ 30 | Baja |
| < 30 | Muy baja |

Con menos de 30 trades, los resultados son estadísticamente poco confiables. Los umbrales de tolerancia en el Live Validator se amplían automáticamente a menor número de trades.

---

## 19. Test binomial para Win Rate (incubación)

Utilizado en los hard gates de CP1/CP2/CP3 para verificar si la tasa de aciertos observada podría ser simplemente mala suerte o es estadísticamente significativa como deterioro real.

```
p_valor = P(X ≤ wins | N, p_BT)
```

Donde:
- `wins` = trades ganadores observados en live
- `N` = total trades live
- `p_BT` = win rate del backtest / 100 (probabilidad esperada)

Se usa la CDF de la distribución binomial. Si `scipy` está disponible, usa `binom.cdf(wins, N, p_BT)`. Si no, aproximación normal:

```
z = (wins - N×p) / √(N×p×(1-p))
p_valor ≈ 0.5 × (1 + erf(z / √2))
```

**Hard gate**: si `p_valor < 0.03`, la win rate es tan baja que hay menos del 3% de probabilidad de que sea solo mala suerte → se activa el gate de eliminación.
