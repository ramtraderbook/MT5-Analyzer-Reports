# Lógica de Decisión — Scoring y Veredictos

## Visión general

El programa tiene dos motores de decisión independientes:

| Módulo | Archivo | Pregunta que responde |
|---|---|---|
| **Live Validator** | `validator.py` | ¿Mi EA en producción sigue operando como en el backtest? |
| **Incubation Screening** | `incubation_validator.py` | ¿Esta estrategia en paper trading merece pasar a producción? |

Ambos se nutren de los mismos datos live (calculados por `metrics.py`) pero comparan contra referencias distintas y tienen lógicas de veredicto diferentes.

---

## MÓDULO 1 — Live Validator

### Objetivo
Detectar si un EA en operativa real está **divergiendo** de su comportamiento esperado en backtest/análisis de sensibilidad. No evalúa si el sistema es bueno; evalúa si *sigue siendo el mismo sistema* que validamos.

### Inputs requeridos
- **Live**: datos automáticos desde `calculate_ea_metrics()` (trades MT5 cargados)
- **BT** (Backtest): ingresados manualmente por el usuario en `/validator/edit/<magic>`
- **MC Retest 95%**: Monte Carlo con Retest Methods
- **MC Trades 95%**: Monte Carlo con Trades Manipulation
- **SPP**: System Parameter Permutation (medianas)

### Estructura del score (100 puntos)

```
Score = (35 × S_Riesgo + 30 × S_Edge + 15 × S_Carácter + 20 × S_Desv) / 10
```

Cada subcategoría `S_X` tiene valor entre 0 y 10 (los sub-pesos internos suman 100).

---

### Categoría RIESGO (peso 35%)

| Métrica | Sub-peso | Fórmula de estado |
|---|---|---|
| DD% Escalado | 50% | Comparar DD_live vs límite dinámico |
| Max Consec Losses | 30% | Comparar racha_live vs racha_BT |
| Stagnation | 20% | Días desde último pico vs referencia BT |

#### DD% Escalado (la métrica más crítica)
El límite de DD se escala con el tiempo en live. A más tiempo operando, mayor tolerancia porque el DD acumulado sube naturalmente:

```
DD_limite = peor_DD_1mes_BT × √(weeks_live / 4.33)
```

El `peor_DD_1mes_BT` es el peor drawdown en cualquier ventana de 1 mes del backtest (ingresado por el usuario). Esta fórmula viene de la teoría de procesos estocásticos: el drawdown esperado crece con la raíz cuadrada del tiempo.

| Estado | Condición |
|---|---|
| OK | `DD_live ≤ DD_limite` |
| ALERTA | `DD_live ≤ DD_limite × 1.5` |
| FUERA | `DD_live > DD_limite × 1.5` |

**Fallback**: si no hay `peor_DD_1mes_BT`, se requieren **ambos** `MC Retest 95%` y `MC Trades 95%` (si falta alguno, `dd_estado = N/D`). El límite OK es `min(MC Retest 95%, MC Trades 95%)` y el límite ALERTA es `max(MC Retest 95%, MC Trades 95%)`.

**Si DD_estado = FUERA → veredicto inmediato ELIMINAR** (override de score).

#### Max Consec Losses
```
consec_ratio = consec_losses_live / consec_losses_BT
```

| Estado | Condición |
|---|---|
| OK | `live ≤ BT` |
| ALERTA | `live ≤ BT × 1.5` |
| FUERA | `live > BT × 1.5` |

#### Stagnation
| Estado | Condición (con BT referencia) |
|---|---|
| Normal → OK | `days ≤ BT_stagnation × 0.3` |
| Elevada → ALERTA | `days ≤ BT_stagnation × 0.6` |
| Alta → FUERA | `days > BT_stagnation × 0.6` |

Sin referencia BT, los umbrales son 60 días (Normal) y 120 días (Elevada).

---

### Categoría EDGE (peso 30%)

| Métrica | Sub-peso | Lógica |
|---|---|---|
| Win Rate | 25% | Diferencia absoluta live vs BT, tolerancia variable por N trades |
| Profit Factor | 30% | PF_live ≥ referencia |
| Payout Ratio | 20% | Variación % vs BT |
| Edge Erosion | 25% | Comparar expectancy_live vs SPP median |

#### Win Rate — Tolerancias dinámicas por número de trades
Con pocos trades, la variabilidad estadística es alta → tolerancias más amplias.

| Trades | OK | ALERTA | FUERA |
|---|---|---|---|
| < 30 | `|delta| ≤ 15pp` | `|delta| ≤ 20pp` | `|delta| > 20pp` |
| 30-49 | `|delta| ≤ 10pp` | `|delta| ≤ 15pp` | `|delta| > 15pp` |
| 50-99 | `|delta| ≤ 7pp` | `|delta| ≤ 12pp` | `|delta| > 12pp` |
| ≥ 100 | `|delta| ≤ 5pp` | `|delta| ≤ 10pp` | `|delta| > 10pp` |

`delta = win_rate_live - win_rate_BT` (en puntos porcentuales)

#### Profit Factor
| Trades | OK | ALERTA | FUERA |
|---|---|---|---|
| < 30 | `PF ≥ 0.8` | `PF ≥ 0.5` | `PF < 0.5` |
| 30-49 | `PF ≥ 1.0` | `PF ≥ 0.8` | `PF < 0.8` |
| ≥ 50 | `PF ≥ PF_BT` | `PF ≥ 1.0` | `PF < 1.0` |

**Override**: Si `PF_live < 1.0` con 50+ trades → veredicto inmediato ELIMINAR.

#### Payout Ratio
```
payout_var = ((payout_live - payout_BT) / payout_BT) × 100
```
Tolerancias: ±40% (< 30 trades), ±30% (30-49), ±25% (≥ 50).

#### Edge Erosion
```
edge_erosion = ((expectancy_live - spp_expectancy_median) / spp_expectancy_median) × 100
```
| Estado | Condición |
|---|---|
| OK | `edge_erosion ≥ -30%` |
| ALERTA | `edge_erosion ≥ -60%` |
| FUERA | `edge_erosion < -60%` |

---

### Categoría CARÁCTER (peso 15%)

| Métrica | Sub-peso | Qué mide |
|---|---|---|
| Frecuencia de trades | 55% | ¿Opera al mismo ritmo que en BT? |
| Avg Bars/Trade | 45% | ¿Las operaciones duran lo mismo que en BT? |

#### Frecuencia
```
bt_freq_per_month  = bt_trades / bt_months
live_freq_per_month = trades_live / (weeks_live / 4.33)
freq_pct = (live_freq_per_month / bt_freq_per_month) × 100
```
| Estado | Condición |
|---|---|
| OK | `freq_pct ≥ 70%` |
| ALERTA | `freq_pct ≥ 50%` |
| FUERA | `freq_pct < 50%` |

> En incubación, la frecuencia es solo un WARNING (no elimina), pero en Live Validator contribuye al score.

#### Avg Bars/Trade
```
bars_var = ((avg_bars_live - avg_bars_BT) / avg_bars_BT) × 100
```
Tolerancias: ±50% (< 30 trades), ±30% (≥ 30 trades).

---

### Categoría DESVIACIÓN ESTRUCTURAL (peso 20%)

Detecta si múltiples métricas se deterioran **simultáneamente**, lo que indica un cambio de régimen real (no ruido estadístico).

Se cuentan los siguientes deterioros simultáneos:
1. `win_rate_live < win_rate_BT - 5pp`
2. `payout_live < payout_BT × 0.8`
3. `pf_live < pf_BT × 0.8`
4. `edge_erosion < -30%`
5. `freq_pct < 70%`

| Deterioros | S_Desv |
|---|---|
| 0 | 10 |
| 1 | 8 |
| 2 | 5 |
| ≥ 3 | 0 · Flag "DESV" activado |

---

### Puntuación final y veredictos

```
S_Riesgo  = DD_pts × 0.50 + Consec_pts × 0.30 + Stagn_pts × 0.20
S_Edge    = WR_pts × 0.25 + PF_pts × 0.30 + Payout_pts × 0.20 + Edge_pts × 0.25
S_Carácter= Freq_pts × 0.55 + Bars_pts × 0.45
```
Donde cada `_pts` es: OK=10, ALERTA=5, FUERA=0.

```
Score = (35 × S_Riesgo + 30 × S_Edge + 15 × S_Carácter + 20 × S_Desv) / 10
```

| Veredicto | Condición |
|---|---|
| **ELIMINAR** | DD_estado = FUERA, O PF < 1 con ≥ 50 trades, O Score < 45 |
| **MONITOREAR** | Score ∈ [45, 70) |
| **CONTINUAR** | Score ≥ 70 |

Las condiciones de ELIMINAR se evalúan **antes** del score (override).

---

## MÓDULO 2 — Incubation Screening

### Objetivo
Decidir objetivamente, mediante checkpoints progresivos, si una estrategia en paper trading o incubación tiene suficiente evidencia estadística para aprobar a operativa real o debe ser descartada. Elimina la selección subjetiva.

### Sistema de checkpoints

La estrategia avanza por checkpoints según el número de trades cerrados:

```
PRE_CP1: < 5 trades     → Sin evaluación (insuficiente data)
CP1:    5–19 trades     → Hard gates binarios
CP2:   20–39 trades     → Comparación probabilística vs bandas MC
CP3:   ≥ 40 trades      → Scoring ponderado completo
```

---

### Hard Gates (aplican en CP1, CP2 y CP3)

Los hard gates son condiciones que **eliminan inmediatamente** sin importar el score. Se verifican antes que cualquier scoring.

#### Gate 1: DD Extremo
```
DD_threshold = MC95_max_dd_pct × 1.5
passed = (DD_live ≤ DD_threshold)
```
Si el drawdown live supera 1.5× el peor caso Monte Carlo al 95%, es señal de peligro grave.

#### Gate 2: Win Rate Binomial
```
p_valor = P(X ≤ wins | N, p_BT)    [distribución binomial]
passed = (p_valor ≥ 0.03)
```
Si la probabilidad de observar tan pocos wins por azar es menor al 3%, la win rate real es significativamente peor que el backtest.

#### Gate 3: Max Consec Losses
```
passed = (max_consec_losses_live ≤ MC95_max_consec_losses)
```

#### Gate 4: Frecuencia (solo WARNING, no elimina)
```
ratio = actual_monthly / expected_monthly
passed = (0.25 ≤ ratio ≤ 3.0)
```
Fuera de este rango: estado WARNING. No elimina pero se muestra en el card de Estado Actual.

---

### CP1 (5–19 trades): Hard Gates Binarios

Solo se verifican los 3 hard gates que eliminan.

| Resultado | Condición |
|---|---|
| **CONTINUAR** | Los 3 gates pasan |
| **ELIMINAR** | Al menos 1 gate falla |

Score = `None` (no hay suficientes datos para calcular score significativo).

---

### CP2 (20–39 trades): Bandas Monte Carlo

Se comparan 7 métricas live contra las bandas MC (peor caso entre Trades Manipulation y Retest Methods).

Las 7 métricas evaluadas son:

| Métrica | Dirección | Live key |
|---|---|---|
| Win Rate | Higher is better | `win_rate` |
| Profit Factor | Higher is better | `profit_factor` |
| Expectancy | Higher is better | `expectancy` |
| Max DD% | Lower is better | `max_dd_pct` |
| Max Consec Losses | Lower is better | `max_consec_losses` |
| Payout Ratio | Higher is better | `payout_ratio` |
| Avg Trade | Higher is better | `expectancy` (misma fuente) |

#### Status por métrica
```
Si higher_is_better:
    good       → live ≥ MC50
    acceptable → live ≥ MC95
    failing    → live < MC95

Si lower_is_better:
    good       → live ≤ MC50
    acceptable → live ≤ MC95
    failing    → live > MC95
```

#### Ajuste SPP en CP2
Si una métrica está en estado `failing` pero el SPP tiene confianza > 1.3 (la mediana SPP es ≥ 130% de la original) Y el valor live está dentro de la mediana SPP, el estado se mejora a `acceptable`.

**Orientación de `spp_confidence` (dependiente de la dirección de la métrica):**
```
Si higher_is_better: spp_confidence = mediana_SPP / original_BT
Si lower_is_better:  spp_confidence = original_BT / mediana_SPP
```
Con esta orientación, `spp_confidence > 1.3` significa uniformemente "la permutación típica es ≥30% mejor que la corrida original" sin importar si la métrica es higher- o lower-is-better — la mediana SPP mide robustez del parámetro: una mediana muy por debajo (o, invertido, muy por encima en métricas lower-is-better) de la original sugiere que la corrida original fue un outlier con overfitting. `spp_confidence` se calcula directamente desde las claves planas `spp.median_*` y `backtest.*` almacenadas — no desde `compute_spp_ratios`, que sigue siendo solo para presentación (su etiqueta ya se lee como "original vs mediana", orientación inversa a la usada aquí).

#### Veredicto CP2
```
failing_count ≤ 1 → CONTINUAR
failing_count = 2 → OBSERVAR
failing_count ≥ 3 → ELIMINAR
```

---

### CP3 (≥ 40 trades): Scoring Ponderado Completo

#### Función de scoring por métrica

Para cada métrica, se calcula un score de 0 a 100 comparando el valor live contra cuatro referencias (BT, MC50, MC95):

```
Si higher_is_better:
    live ≥ BT           → score = 100
    MC50 ≤ live < BT    → score = 65 + 35 × (live - MC50) / (BT - MC50)
    MC95 ≤ live < MC50  → score = 25 + 40 × (live - MC95) / (MC50 - MC95)
    live < MC95         → score = max(0, 25 × live / MC95)

Si lower_is_better (invertir):
    live ≤ BT           → score = 100
    BT < live ≤ MC50    → score = 65 + 35 × (MC50 - live) / (MC50 - BT)
    MC50 < live ≤ MC95  → score = 25 + 40 × (MC95 - live) / (MC95 - MC50)
    live > MC95         → score = max(0, 25 × MC95 / live)
```

Esta escala da:
- 100: tan bueno o mejor que el BT
- ~65: al nivel de la mediana MC (comportamiento normal esperado)
- ~25: en el límite del peor caso MC95
- 0: peor que el peor caso MC95

#### Ajuste SPP en CP3
Si `spp_confidence > 1.3`:
```
score_final = score_mc × 0.85 + score_spp × 0.15
```
Blend del 15% con el score calculado usando el SPP como referencia.

#### Categorías y pesos CP3

| Categoría | Peso | Métricas incluidas |
|---|---|---|
| Desviación vs BT/MC | 45% | Win Rate (15%), PF (20%), Expectancy (20%), Avg Trade (15%), Payout (15%), Ret/DD (15%) |
| Riesgo Observado | 30% | Max DD% (45%), Max Consec Losses (30%), Stagnation (25%) |
| Coherencia Operativa | 15% | Frecuencia mensual |
| Ajuste Muestra | 10% | Penalización por pocos trades |

#### Coherencia Operativa (frecuencia)
```
ratio = actual_monthly / expected_monthly
0.5 ≤ ratio ≤ 2.0  → coherence_score = 100
0.25 ≤ ratio < 0.5
o 2.0 < ratio ≤ 3.0 → coherence_score = 50
otro               → coherence_score = 10
```

#### Ajuste por tamaño de muestra
| Trades | Sample Score |
|---|---|
| ≥ 80 | 100 |
| 60-79 | 80 |
| 40-59 | 60 |
| < 40 | 40 |

#### Score final CP3
```
Score = Desviación × 0.45 + Riesgo × 0.30 + Coherencia × 0.15 + Muestra × 0.10
```

#### Veredictos CP3
```
Score ≥ 65 Y sin métricas below MC95 → APROBAR
Score ≥ 45                           → OBSERVAR
Score < 45                           → ELIMINAR
```

---

### Regla Anti-Limbo (escalación)

Previene que una estrategia quede en estado OBSERVAR indefinidamente:

```
Si CP2_veredicto = OBSERVAR Y CP3_veredicto = OBSERVAR → ELIMINAR (escalación)
```

Una estrategia que no mejora al pasar de CP2 a CP3 (con más datos) pero tampoco empeora lo suficiente para eliminar directamente, demuestra que no tiene la consistencia necesaria para producción.

---

### Dual Monte Carlo — Worst Case

Incubación usa **dos métodos de Monte Carlo**:
- **Trades Manipulation**: altera el orden y selección de trades
- **Retest Methods**: re-ejecuta con diferentes condiciones de entrada

Para cada métrica, se toma el peor caso entre ambos:

```
Si higher_is_better:
    worst = min(mc_manipulation_value, mc_retest_value)
    dominant = el método que dio el valor más bajo

Si lower_is_better:
    worst = max(mc_manipulation_value, mc_retest_value)
    dominant = el método que dio el valor más alto
```

La vista detalle de cada EA muestra qué método fue "dominante" para cada métrica (qué MC fue más conservador).

---

## Comparativa entre módulos

| Aspecto | Live Validator | Incubation Screening |
|---|---|---|
| ¿Cuándo se usa? | EA ya en producción real | EA en paper trading / incubación |
| Objetivo | Detectar divergencia del backtest | Decidir si pasa a producción |
| Checkpoints | Único scoring continuo | 3 checkpoints progresivos (CP1/CP2/CP3) |
| Score visible | Siempre (si hay BT data) | Solo en CP3 (≥ 40 trades) |
| Datos de referencia | BT + MC + SPP en validator_store.json | BT + dual MC + SPP en incubation_store.json |
| Tolerancias WR | Dinámicas por N trades | Fijas (test binomial) |
| Veredictos | CONTINUAR / MONITOREAR / ELIMINAR | APROBAR / OBSERVAR / ELIMINAR (CP3) |
| Anti-limbo | No aplica | CP2 OBSERVAR + CP3 OBSERVAR → ELIMINAR |
| Frecuencia | Contribuye al score (15%) | Solo WARNING (no elimina) |

---

## Historial de checkpoints

Cada vez que se evalúa un EA en incubación, el resultado se guarda en `incubation_store.json`:

```json
"checkpoints": [
    {
        "checkpoint": "CP1",
        "verdict": "CONTINUAR",
        "score": null,
        "timestamp": "2026-03-01T10:00:00",
        "total_trades": 8
    },
    {
        "checkpoint": "CP2",
        "verdict": "OBSERVAR",
        "score": null,
        "timestamp": "2026-03-15T10:00:00",
        "total_trades": 25
    }
]
```

El historial permite visualizar la evolución de la estrategia y detectar el patrón de dos OBSERVARs consecutivos (anti-limbo).

La evaluación se dispara automáticamente al cargar datos o manualmente via "Forzar Evaluación".
