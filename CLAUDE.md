# EA Analyzer & Validator — CLAUDE.md

Flask app para analizar historial de trades de MetaTrader 5. Aplica a `ea_analyzer.py`, `parser.py`, `metrics.py`, `validator.py` y los templates.

---

## Arquitectura

| Archivo | Rol |
|---|---|
| `ea_analyzer.py` | Flask app: rutas, cache de sesión, config |
| `parser.py` | Parsea .xlsx MT5, JOIN POSITIONS + ORDERS |
| `metrics.py` | Calcula métricas, equity curves, drawdown |
| `validator.py` | Motor de scoring EA Validator |
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
| `_calc_risk_of_ruin(net_pnl_list, capital)` | Monte Carlo RoR (5000 sims) |
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

**Veredictos:** CONTINUAR ≥ 70 · MONITOREAR ≥ 45 · ELIMINAR < 45

**DD_límite:** `Peor_DD_1Mes × sqrt(semanas_live / 4.33)`

**Datos Live:** auto-calculados desde `calculate_ea_metrics()`
**Datos BT:** ingresados por usuario en `/validator/edit/<magic>`

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
