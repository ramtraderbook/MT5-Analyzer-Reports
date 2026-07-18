# EA Analyzer & Validator — AGENTS.md

Flask app para analizar historial de trades de MetaTrader 5. Aplica a `ea_analyzer.py`, `parser.py`, `metrics.py`, `validator.py` y los templates.

## Documentación detallada

Para no repetir información, los detalles técnicos están en `docs/`:

| Documento | Qué cubre |
|---|---|
| [`docs/backend.md`](docs/backend.md) | Arquitectura Flask, parseo xlsx, rutas, sesión/cache, flujo de datos |
| [`docs/frontend.md`](docs/frontend.md) | Templates Jinja2, CSS, Chart.js, APIs de gráficas, gotchas |
| [`docs/metrics-formulas.md`](docs/metrics-formulas.md) | Todas las fórmulas: P&L, DD, SQN, Sharpe, correlación, etc. |
| [`docs/decision-logic.md`](docs/decision-logic.md) | Scoring Live Validator + Incubation CP1/CP2/CP3, pesos, veredictos, anti-limbo |
| [`docs/known-issues.md`](docs/known-issues.md) | **Pendientes auditados y verificados, deliberadamente no corregidos. Leer antes de tocar el motor de decisión.** |

---

## Arquitectura

| Archivo | Rol |
|---|---|
| `ea_analyzer.py` | Flask app: rutas, cache de sesión, config |
| `parser.py` | Parsea .xlsx MT5, JOIN POSITIONS + ORDERS |
| `metrics.py` | Calcula métricas, equity curves, drawdown |
| `validator.py` | Motor de scoring EA Validator |
| `cache_store.py` | Capa de storage en disco (serialize, save/load, migración legacy, cleanup) — pura de Flask/session; `ea_analyzer` la envuelve atando `CACHE_DIR`/`APP_DIR`/prefijos |
| `config.json` | Mapeos: magic, alias, capital, active |
| `validator_store.json` | Datos de backtest por magic number |
| `cache_{uuid}.json` | Cache de trades por sesión |
| `templates/` | base.html, upload, mapping, dashboard, strategy, export, validator, validator_input |
| `static/` | style.css, charts.js |

---

## Reglas de negocio (críticas)

**P&L:** `net_pnl = Profit + Commission + Swap` — NUNCA usar solo `Profit`

**JOIN:** POSITIONS (sin comment) + ORDERS (con comment) por `position_id == order_id`

**Filtro comments:** Descartar los que empiezan con `[` (MT5 auto-comments). Todo lo demás = nombre válido de EA.

**Equity curve:** Empieza en 0, acumula `net_pnl`. No es saldo absoluto.

**Max DD%:** `(peak_pnl - valley_pnl) / (capital + peak_pnl) × 100` — capital del config, $5,000 default

**Portfolio capital:** Suma de capitals de EAs activos

**SQN:** `sqrt(N) × mean(net_pnl) / std(net_pnl, ddof=1)` — "(orientativo)" si N < 20

**EAs activos:** `active: false` en config.json los excluye de métricas, portfolio, sidebar y API

---

## Rutas principales

| Ruta | Descripción |
|---|---|
| `/` | Upload de .xlsx |
| `/upload` POST | Parsea archivo, merge trades, redirige a mapping |
| `/reset` POST | Limpia historial y cache |
| `/mapping` | Mapeo magic/alias/capital/instrument |
| `/mapping/save` POST | Guarda config.json |
| `/dashboard` | Portfolio + KPIs + charts + tabla |
| `/strategy/<name>` | Detalle por EA |
| `/export` | Tabla para EA Validator |
| `/validator` | Dashboard Live vs BT scoring |
| `/validator/edit/<magic>` | Formulario datos BT |
| `/validator/delete/<magic>` POST | Elimina datos BT |

---

## APIs (para charts.js)

| Endpoint | Params | Retorna |
|---|---|---|
| `/api/equity_curves` | `?days=N` | traces equity por EA + portfolio |
| `/api/drawdown_curves` | `?days=N` | traces drawdown % |
| `/api/contribution` | — | bars contribución por EA |
| `/api/ea_equity/<name>` | `?days=N` | equity + dd de un EA |
| `/api/ea_pnl_data/<name>` | — | histogram, streaks, weekday/hour, long/short |
| `/api/portfolio_analytics` | — | igual que ea_pnl_data pero para portfolio |
| `/api/rolling_metrics/<name>` | `?window=N` | expectancy/win_rate/PF rodante |
| `/api/correlation` | — | matriz correlación Pearson diaria entre EAs |

---

## Funciones clave en metrics.py

| Función | Qué hace |
|---|---|
| `calculate_ea_metrics(ea_name, trades, config)` | Métricas completas de un EA |
| `calculate_all_metrics(parsed_data, config)` | Portfolio + todos los EAs activos |
| `calculate_portfolio_metrics(all_trades, config)` | Portfolio completo |
| `_build_equity_curve(trades_sorted)` | Curva equity desde 0, acumula net_pnl |
| `_build_drawdown_curve(equity_curve, capital)` | Curva DD% desde equity curve |
| `_calc_max_drawdown(equity_curve, capital)` | Max DD$ y DD% |
| `_calc_sqn(net_pnl_list)` | SQN score + label |
| `_calc_sharpe(net_pnl_list)` | Sharpe simplificado |
| `_calc_streaks(net_pnl_list)` | Rachas max y promedio wins/losses |
| `_calc_stagnation(last_peak_date)` | Días desde último pico equity |
| `_calc_rolling_metrics(trades, window)` | Métricas rodantes sobre ventana N trades |
| `_weeks_operating(trades_sorted)` | Semanas desde primer a último trade |

---

## Funciones clave en parser.py

| Función | Qué hace |
|---|---|
| `parse_mt5_report(filepath)` | Entry point — parsea xlsx, retorna dict |
| `merge_trades(existing, new_trades)` | Append mode — merge por position_id |
| `_find_section_rows(ws)` | Detecta secciones dinámicamente |
| `_parse_positions(ws, ...)` | Parsea trades cerrados |
| `_parse_orders(ws, ...)` | Mapeo order_id → comment |
| `_parse_open_positions(ws, ...)` | Posiciones abiertas |
| `_parse_results(ws, ...)` | Sección results para validación |

---

## Validator (validator.py)

**4 categorías ponderadas (100%):**
- RIESGO 35% → DD% Escalado (50%) + Max Consec Losses (30%) + Stagnation (20%)
- EDGE 30% → Win Rate (25%) + PF (30%) + Payout (20%) + Edge Erosion (25%)
- CARÁCTER 15% → Frecuencia (55%) + Avg Bars/Trade (45%)
- DESV. ESTRUCTURAL 20% → Conteo métricas deterioradas simultáneamente

**Veredictos:** CONTINUAR ≥ 70 · MONITOREAR ≥ 45 · ELIMINAR < 45 · SIN DATOS (guard)

**DD_límite:** `Peor_DD_1Mes × sqrt(trades_live / (bt_trades / bt_months))`

**Datos Live:** auto-calculados desde `calculate_ea_metrics()`
**Datos BT:** ingresados por usuario en `/validator/edit/<magic>`

**Guard de datos mínimos (min_trades=5, max_wait_weeks=8):**
- `trades_live >= 5` → evaluar normalmente
- `trades_live < 5` y `weeks_live < 8` → **SIN DATOS** (muy pronto para evaluar)
- `trades_live < 5` y `weeks_live >= 8` → **ELIMINAR** (deadline cumplido, EA perdió frecuencia/edge)

El resultado incluye siempre `sin_datos: bool`. Si `True`, `score=None` y no se muestran sub-scores.

---

## Comandos

```bash
pip install -r requirements.txt
python ea_analyzer.py          # localhost:5000
python -m pytest                # tests (si existen)
```

---

## Convenciones de código

- `net_pnl` es la única fuente de verdad para P&L
- Trades sin match en ORDERS → `"Unknown"`
- `capital` default = $5,000
- Fechas en cache: ISO strings (no datetime objects)
- Colors EAs: paleta fija de 12 + HSL dinámico si > 12
- Filter EAs inactivos ANTES de cualquier cálculo en `calculate_all_metrics()`

---

## Modo Incubation Screening

**Propósito:** Evaluar si estrategias en paper trading/incubación se comportan conforme a sus backtests/MC/SPP.

**Archivos nuevos:**
| Archivo | Rol |
|---|---|
| `incubation_validator.py` | Motor de scoring por checkpoints |
| `incubation_store.json` | Datos de referencia BT/MC/SPP por EA |
| `incubation_config.json` | Config EAs en incubación (magic, alias, capital, active) |
| `runtime_cache\incubation_cache_{uuid}.json` | Cache de trades de incubación por sesión |

**Rutas de incubación:**
| Ruta | Descripción |
|---|---|
| `/incubation/mapping` | Mapping de EAs en incubación |
| `/incubation/mapping/save` POST | Guarda incubation_config.json |
| `/incubation/reference_data` | Lista EAs con estado de datos BT/MC/SPP |
| `/incubation/reference_data/edit/<ea>` | Formulario datos BT/MC/SPP |
| `/incubation/reference_data/save/<ea>` POST | Guarda datos de referencia |
| `/incubation/reference_data/delete/<ea>` POST | Elimina datos de referencia |
| `/incubation/dashboard` | Dashboard principal incubación |
| `/incubation/strategy/<ea>` | Detalle por EA con scoring |
| `/incubation/force_evaluate/<ea>` POST | Re-evaluar EA manualmente |
| `/incubation/reset_checkpoints/<ea>` POST | Limpiar checkpoints de EA |
| `/incubation/reset` POST | Limpiar cache incubación |
| `/incubation/reset_all` POST | Limpiar todo dato de incubación |
| `/switch_mode/<mode>` | Cambiar entre live e incubation |

**APIs de incubación:**
| Endpoint | Retorna |
|---|---|
| `/api/incubation/ea_equity/<ea>` | equity + drawdown de EA en incubación |
| `/api/incubation/ea_pnl_data/<ea>` | histogram, streaks, weekday/hour, long/short |

**Sistema de Checkpoints:**
- PRE_CP1 (< 5 trades): sin evaluación — aplica deadline de frecuencia
- CP1 (5-20 trades): hard gates binarios → CONTINUAR/ELIMINAR
- CP2 (20-40 trades): comparación probabilística contra bandas MC → CONTINUAR/OBSERVAR/ELIMINAR
- CP3 (40+ trades): scoring ponderado completo → APROBAR/OBSERVAR/ELIMINAR

**Deadline de frecuencia (PRE_CP1):**
- `deadline_days = (5 / bt_monthly × 30.44) × 3` — triple del tiempo esperado para acumular 5 trades
- `trades < 5 AND days < deadline` → PENDING
- `trades < 5 AND days >= deadline` → ELIMINAR (frecuencia/edge perdidos)
- Resultado incluye siempre: `freq_deadline`, `deadline_days`, `bt_monthly`, `actual_monthly`

**Hard Gates (aplican en todos los checkpoints):**
- DD > MC95 × 1.5 → ELIMINAR
- Win Rate binomial p < 0.03 → ELIMINAR
- Max Consec Losses > MC95 → ELIMINAR
- Frecuencia fuera de rango → WARNING (no elimina)

**Señal "esperar vs actuar" (3B):** `trades_to_winrate_significance()` proyecta el
win rate observado hacia adelante y estima cuántos trades MÁS faltan para que el
gate binomial recién sea concluyente. Se muestra en la verdict card
(`trades_to_call`) solo como aviso positivo cuando la EA va por debajo del backtest
pero aún no llegó a un kill firme. No cambia ningún veredicto.

**Scoring CP3 (pesos):**
- DESVIACIÓN vs BT/MC: 45% (WR, PF, Expectancy, Avg Trade, Payout, Ret/DD)
- RIESGO OBSERVADO: 30% (Max DD%, Max Consec Losses, Stagnation)
- COHERENCIA OPERATIVA: 15% (Frecuencia mensual)
- AJUSTE MUESTRA: 10% (penalización si < 80 trades)

**Veredictos CP3:** APROBAR ≥ 65 Y sin métricas below MC95 · OBSERVAR ≥ 45 · ELIMINAR < 45

**Regla anti-limbo:** OBSERVAR en CP2 + OBSERVAR en CP3 → ELIMINAR

**Datos de referencia por EA:**
- Backtest: 12 métricas obligatorias
- Monte Carlo Trades Manipulation 95%: 10 métricas obligatorias
- Monte Carlo Trades Manipulation 50%: 10 métricas opcionales
- Monte Carlo Retest Methods 95%: 10 métricas obligatorias
- Monte Carlo Retest Methods 50%: 10 métricas opcionales
- SPP: 8 métricas opcionales (modifica scoring si spp_confidence > 1.3)

**Lógica dual MC:**
- Para scoring se usa el peor caso entre Trades Manipulation y Retest Methods
- Higher-is-better: worst = min(manipulation, retest)
- Lower-is-better: worst = max(manipulation, retest)
- La vista detalle muestra ambos MC y cuál dominó cada métrica
