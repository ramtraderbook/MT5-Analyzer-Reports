# EA Analyzer Reports 1.0v — CLAUDE.md

Herramienta Flask local para analizar historial de trades de MetaTrader 5 (EAs).
El usuario exporta un `.xlsx` desde MT5 y el programa calcula métricas por EA,
genera gráficos Plotly y produce un export para su archivo "EA Validator" Excel.

---

## Arquitectura

| Archivo | Rol |
|---|---|
| `ea_analyzer.py` | Flask app: todas las rutas, cache de sesión, config |
| `parser.py` | Lee el .xlsx de MT5, une POSITIONS + ORDERS, retorna trades con EA name |
| `metrics.py` | Calcula todas las métricas: equity, drawdown, SQN, streaks, etc. |
| `templates/` | Jinja2: `base.html`, `upload.html`, `mapping.html`, `dashboard.html`, `strategy.html`, `export.html` |
| `static/style.css` | Dark theme con CSS custom properties |
| `static/charts.js` | Helpers Plotly: equity, drawdown, histograma, streaks, contribution |
| `config.json` | Persiste mapeos: magic number, alias, capital, active, instrument por EA |
| `cache_{uuid}.json` | Cache de parsed data por sesión (evita límite 4MB de cookies) |
| `.secret_key` | Clave Flask persistente (sesiones sobreviven reinicios) |

---

## Flujo de datos

```
.xlsx upload → parser.py → cache_{uuid}.json → /mapping (config magic/alias/capital)
    → /dashboard → metrics.calculate_all_metrics() → Jinja2 templates + /api/* → Plotly.js
```

---

## Reglas críticas de negocio

**P&L:** `net_pnl = Profit + Commission + Swap` (NUNCA usar solo `Profit`)

**Agrupación de trades:** Parser une POSITIONS (tiene trades, sin comentario) con ORDERS
(tiene comentario = nombre del EA). JOIN por `position_id == order_id`. Trades sin match → `"Unknown"`.

**Filtro de comentarios:** Se descartan comentarios que empiezan con `[` (ej: `[sl 1234.5]`,
`[tp ...]` son comentarios automáticos de MT5). Todo lo demás es nombre válido de EA.

**Equity curve:** Empieza en 0, acumula net_pnl. El eje Y muestra ganancias/pérdidas, no saldo absoluto.

**Max DD%:** `(peak_pnl - valley_pnl) / (capital + peak_pnl) × 100`
`capital` viene de `config["mappings"][ea_name]["capital"]` (default $5,000).
Portfolio capital = suma de capitals de todos los EAs activos.

**SQN:** `sqrt(N) × mean(net_pnl) / std(net_pnl, ddof=1)`. Nota "(orientativo)" si N < 20.

**EAs activos:** En `config.json`, `active: false` excluye el EA de métricas, portfolio,
sidebar y API. El filtro ocurre en `calculate_all_metrics()` antes de cualquier cálculo.

**Alias:** Campo opcional en mapping. Si vacío, se usa el nombre original del archivo.
El `label` final = `"magic - alias"` o solo `alias` si no hay magic.

---

## Columnas POSITIONS (xlsx MT5)

| Col | Campo | Nota |
|---|---|---|
| 1 | open_time | Hardcoded (duplicado "Time") |
| 2 | position_id | ID para JOIN con ORDERS |
| 3 | symbol | |
| 4 | direction | buy/sell |
| 5 | volume | string → float |
| 6 | open_price | Hardcoded (duplicado "Price") |
| 9 | close_time | Hardcoded |
| 10 | close_price | Hardcoded |
| 11 | commission | negativo |
| 12 | swap | |
| 13 | profit | crudo, SIN comisiones |

---

## Comandos

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar (abre browser automáticamente en localhost:5000)
python ea_analyzer.py

# Archivo de prueba esperado en:
test_data/ReportHistory-4000084439.xlsx
```

---

## Estado actual (v1.0 — 2026-03-04)

**Funcionando:**
- Parsing dinámico de secciones MT5 (sin row numbers hardcodeados)
- Mapeo con magic number, alias, capital, active checkbox
- Dashboard portfolio: 8 KPIs + stats row + tabla + 3 charts Plotly
- Strategy page: mismos KPIs + tabla de trades + histograma P&L + streak chart
- Export para EA Validator (copy-paste clipboard o CSV)
- Sidebar filtrada por EAs activos
- Config.json persistente entre sesiones
- Max DD% corregido: usa capital del config ($5,000 default), no $100,000

**Pendiente / posibles mejoras:**
- Validación cruzada con sección RESULTS del xlsx
- Soporte multi-archivo (comparar períodos)
- Tests unitarios para parser.py y metrics.py
