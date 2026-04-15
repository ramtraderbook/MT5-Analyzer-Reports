# Backend — Arquitectura y Programación

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.10+ |
| Framework web | Flask 3.x |
| Parsing xlsx | openpyxl |
| Cálculo numérico | NumPy |
| Test estadístico (opcional) | SciPy (`binom.cdf`) |
| Servidor | Werkzeug (dev) · WSGI-compatible para producción |
| Sesiones | Flask session (cookie firmada con secret key) |

---

## Estructura de archivos

```
ea_analyzer.py          ← Flask app: rutas, sesión, cache, orquestación
parser.py               ← Parseo de .xlsx MT5
metrics.py              ← Cálculo de métricas (P&L, DD, SQN, etc.)
validator.py            ← Motor de scoring Live Validator
incubation_validator.py ← Motor de scoring Incubación (CP1/CP2/CP3)

config.json             ← Mapeos EA live (magic, alias, capital, active) + loaded_files_live
validator_store.json    ← Datos BT/MC/SPP ingresados por el usuario (vía /validator/edit)
incubation_config.json  ← Mapeos EA incubación + loaded_files_incubation
incubation_store.json   ← Datos BT/MC/SPP de incubación + checkpoints históricos

cache_{uuid}.json       ← Cache de trades live por sesión
incubation_cache_{uuid}.json ← Cache de trades incubación por sesión
```

---

## Modos de análisis

La app soporta dos modos mutuamente exclusivos, seleccionados al cargar el archivo:

| Modo | Clave sesión | Config | Cache prefix |
|---|---|---|---|
| `live` | `analysis_mode = "live"` | `config.json` | `cache_` |
| `incubation` | `analysis_mode = "incubation"` | `incubation_config.json` | `incubation_cache_` |

El modo activo se guarda en `session["analysis_mode"]` y persiste mientras dure la sesión Flask.

---

## Sesión y cache de trades

### Secret key persistente
Al arrancar por primera vez, `ea_analyzer.py` genera 24 bytes aleatorios y los guarda en `.secret_key`. En reinicios posteriores lee ese mismo archivo. Esto garantiza que las cookies de sesión sobreviven a reinicios del servidor.

### Cache de trades en disco
Los trades parseados **no** se guardan en la cookie (demasiado grandes). Se guardan en archivos JSON:
- `cache_{uuid}.json` para live
- `incubation_cache_{uuid}.json` para incubación

El UUID del archivo activo se guarda en `session["cache_key"]` o `session["incubation_cache_key"]`. Al leer datos, la app carga el archivo correspondiente.

### Cache de métricas en memoria
`_metrics_cache: dict` almacena resultados de `calculate_all_metrics()` con TTL de 120 segundos. La clave es el UUID del cache de trades. Esto evita recalcular métricas en cada request de la misma sesión.

```python
_metrics_cache = {
    "uuid-xxx": {
        "ts": 1712345678.0,       # timestamp UNIX
        "data": { ... }           # resultado de calculate_all_metrics()
    }
}
```

---

## Flujo de carga de datos

```
Usuario sube .xlsx
    │
    ▼
/upload POST
    ├── Detecta modo (live / incubation)
    ├── parse_mt5_report(filepath)       → ParsedData
    ├── get_parsed_data() / get_incubation_parsed_data()  ← carga cache existente
    ├── merge_trades(existing, new_trades)  ← dedup por position_id
    ├── Guarda cache_{uuid}.json
    ├── Actualiza config.json / incubation_config.json
    │   └── loaded_files_live / loaded_files_incubation
    └── redirect → /mapping
```

### Append mode
Cada carga de archivo **agrega** trades al cache existente (no reemplaza). `merge_trades()` elimina duplicados por `position_id`. El resultado está ordenado por `close_time` ascendente.

---

## Parseo del .xlsx (parser.py)

### Estructura del reporte MT5
Los reportes MT5 son libros Excel con múltiples secciones en la misma hoja activa. La detección es dinámica: se escanea la columna A buscando palabras clave (`Positions`, `Orders`, `Deals`, `Open Positions`, `Results`).

### JOIN POSITIONS + ORDERS
Este es el punto crítico del parser:

```
POSITIONS → trades cerrados, SIN comment del EA
ORDERS    → mapeo order_id → comment (nombre del EA)

JOIN: position_id == order_id
```

Los comentarios del tipo `[sl 1234.5]` o `[tp 1234.5]` son generados automáticamente por MT5 y se descartan (`SYSTEM_COMMENT_PREFIX = "["`). Solo se conservan los comentarios que no empiecen por `[`.

### Columnas duplicadas en POSITIONS
Las columnas `Time` y `Price` aparecen **dos veces** en POSITIONS (open/close). `_get_column_map()` solo almacena la última ocurrencia, por eso las posiciones de estas columnas se hardcodean:

```python
time_open_col   = 1    # primera columna
price_open_col  = 6    # columna 6
time_close_col  = 9    # columna 9
price_close_col = 10   # columna 10
```

### Trades sin match en ORDERS
Si un `position_id` no tiene entrada en el `order_map`, el trade queda con `comment = "Unknown"`. Esto indica trades manuales o automáticos sin nombre de EA.

### Resultado de parse_mt5_report()
```python
{
    "account": { "name", "number", "currency", "broker", "report_date" },
    "closed_trades": [ { ...campos trade... } ],
    "open_positions": [ { ...campos trade... } ],
    "ea_names": [ "EA_1", "EA_2", ... ],  # únicos, excluye Unknown
    "results_validation": { ... },
    "unknown_trades": int,
    "total_closed": int,
    "total_open": int,
}
```

### Trade dict (campos)
```python
{
    "position_id": int,
    "symbol": str,
    "direction": "buy" | "sell",
    "volume": float,
    "open_time": datetime,
    "close_time": datetime | None,
    "open_price": float,
    "close_price": float,
    "sl": float | None,
    "tp": float | None,
    "commission": float,
    "swap": float,
    "profit": float,
    "net_pnl": float,       # profit + commission + swap
    "duration_hours": float,
    "comment": str,         # nombre del EA (del JOIN con ORDERS)
}
```

---

## Rutas Flask

### Modo Live

| Ruta | Método | Función |
|---|---|---|
| `/` | GET | `index()` — Upload page + historial de archivos |
| `/upload` | POST | Parseo, merge, guarda cache |
| `/reset` | POST | Borra trades + cache, conserva mappings y BT data |
| `/reset_all_live` | POST | Reset completo: trades + mappings + validator_store |
| `/mapping` | GET | Formulario de mapeo magic/alias/capital/instrument |
| `/mapping/save` | POST | Guarda config.json |
| `/dashboard` | GET | Portfolio + KPIs + charts |
| `/strategy/<name>` | GET | Detalle por EA |
| `/export` | GET | Tabla exportable para Validator |
| `/validator` | GET | Dashboard Live vs BT scoring |
| `/validator/edit/<magic>` | GET/POST | Formulario datos BT |
| `/validator/delete/<magic>` | POST | Elimina datos BT |
| `/switch_mode/<mode>` | GET | Cambia entre live/incubation |

### Modo Incubación

| Ruta | Método | Función |
|---|---|---|
| `/incubation/mapping` | GET | Mapeo EAs incubación |
| `/incubation/mapping/save` | POST | Guarda incubation_config.json |
| `/incubation/reference_data` | GET | Lista EAs con estado BT/MC/SPP |
| `/incubation/reference_data/edit/<ea>` | GET/POST | Formulario datos de referencia |
| `/incubation/reference_data/save/<ea>` | POST | Guarda en incubation_store.json |
| `/incubation/reference_data/delete/<ea>` | POST | Elimina datos de referencia |
| `/incubation/dashboard` | GET | Dashboard screening |
| `/incubation/strategy/<ea>` | GET | Detalle por EA con scoring CP |
| `/incubation/force_evaluate/<ea>` | POST | Re-evalúa con trades actuales |
| `/incubation/reset_checkpoints/<ea>` | POST | Limpia historial CP del EA |
| `/incubation/reset` | POST | Borra trades incubación |
| `/incubation/reset_all` | POST | Reset completo incubación |

### APIs para gráficas (JSON)

| Endpoint | Parámetros | Uso |
|---|---|---|
| `/api/equity_curves` | `?days=N` | Curvas equity por EA + portfolio |
| `/api/drawdown_curves` | `?days=N` | Curvas drawdown % |
| `/api/contribution` | — | Barras contribución por EA |
| `/api/ea_equity/<name>` | `?days=N` | Equity + DD de un EA |
| `/api/ea_pnl_data/<name>` | — | Histograma, streaks, weekday/hour, long/short |
| `/api/portfolio_analytics` | — | Igual que ea_pnl_data para portfolio |
| `/api/rolling_metrics/<name>` | `?window=N` | Expectancy/Win Rate/PF rodante |
| `/api/correlation` | — | Matriz correlación Pearson diaria |
| `/api/incubation/ea_equity/<ea>` | — | Equity + DD para incubación |
| `/api/incubation/ea_pnl_data/<ea>` | — | Distribución P&L incubación |

---

## Archivos de persistencia

### config.json (live)
```json
{
  "mappings": {
    "EA_Name": {
      "magic": 12345,
      "instrument": "EURUSD",
      "capital": 5000.0,
      "active": true,
      "alias": "EURUSD 12345"
    }
  },
  "last_file": "reporte.xlsx",
  "last_updated": "2026-04-15",
  "loaded_files_live": [
    { "filename": "reporte.xlsx", "loaded_at": "2026-04-15T12:00:00" }
  ]
}
```

### incubation_store.json
```json
{
  "1104": {
    "backtest": { "win_rate": 55.0, "profit_factor": 1.8, ... },
    "mc_manipulation": {
      "confidence_95": { "max_dd_pct": 12.0, ... },
      "confidence_50": { "max_dd_pct": 8.0, ... }
    },
    "mc_retest": {
      "confidence_95": { ... },
      "confidence_50": { ... }
    },
    "spp": { "median_avg_trade": 45.0, "orig_vs_median_pct": { ... } },
    "checkpoints": [
      { "checkpoint": "CP1", "verdict": "CONTINUAR", "timestamp": "..." }
    ]
  }
}
```

---

## Funciones de orquestación en ea_analyzer.py

| Función | Descripción |
|---|---|
| `get_parsed_data()` | Carga cache live de sesión, retorna ParsedData o `{}` |
| `get_incubation_parsed_data()` | Ídem para incubación |
| `get_metrics()` | Wrapper con cache TTL 120s sobre `calculate_all_metrics()` |
| `_incubation_verdict_card(ea_name, live_m, store_data)` | Construye el dict del card Estado Actual |
| `_trade_matches_ea(trade, ea_name, config)` | Normaliza y compara trade.comment con magic/alias/key |

### _trade_matches_ea
Normaliza ambos lados (quita no-alfanuméricos, minúsculas) y compara 3 variantes:
1. Clave del config (`"Strategy_1_23_192"`)
2. Alias (`"GDAXI 22212"`)
3. Magic number (`"22212"`)

Esto permite que trades con comment `"USDJPY 1104"` coincidan con el EA configurado con magic `1104`.

---

## Apertura automática del navegador

Al arrancar (`python ea_analyzer.py`), un thread separado espera 1.5 segundos y llama a `webbrowser.open("http://localhost:5000")`. Así el usuario no necesita abrir el browser manualmente.

---

## Convenciones críticas

- **`net_pnl = profit + commission + swap`** — NUNCA usar solo `profit`
- EAs con `active: false` en config se excluyen **antes** de cualquier cálculo
- Fechas en cache JSON: strings ISO 8601 (no objetos datetime)
- Trades sin match en ORDERS → `comment = "Unknown"` — se excluyen del análisis
- Capital default: $5,000 si no está en config
