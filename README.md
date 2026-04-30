# MT5 EA Analyzer Reports

Herramienta Flask local para analizar el historial de trades de MetaTrader 5. Subes un `.xlsx` exportado desde MT5 y el programa calcula métricas por EA, genera gráficos interactivos y te da un dashboard completo con scoring de validación.

---

## Características

### Dashboard portfolio y por estrategia
- Métricas clave: Net P&L, Win Rate, Profit Factor, SQN, Expectancy, Max Drawdown %, RRR
- Curva de Equity con rango temporal configurable (7D · 14D · 1M · 3M · 6M · 1Y · ALL)
- Curva de Drawdown con selector de rango temporal
- Histograma de P&L por trade
- Gráfico de rachas (win/loss streaks)
- Contribución por estrategia al portfolio

### Módulo EA Validator v1.1
Sistema de scoring ponderado 0–100 que compara resultados **Live (MT5)** vs **Backtest** para cada EA:

| Categoría | Ponderación |
|---|---|
| Riesgo | 35% |
| Edge | 30% |
| Carácter | 15% |
| Desviación Estructural | 20% |

**Veredictos:** CONTINUAR ≥ 70 · MONITOREAR ≥ 45 · ELIMINAR < 45

- DD% escalado con fórmula `sqrt(semanas_live / 4.33)`
- Edge Erosion: Live Expectancy vs SPP Mediana
- Umbrales dinámicos por cantidad de trades (< 30 / 30–49 / 50–99 / 100+)
- Datos de backtest persistidos en `validator_store.json` (local, fuera del repo)

### Flujo de datos

```
.xlsx MT5 → parser.py (une POSITIONS + ORDERS por position_id)
    → cache_{uuid}.json → /mapping (configura magic/alias/capital)
    → /dashboard → metrics.py → Jinja2 templates + Plotly.js
    → EA Validator (scores + verdicts)
```

---

## Requisitos

- Python 3.10+
- Flask
- Pandas
- Plotly
- openpyxl (lectura de .xlsx)

---

## Instalación

```bash
pip install -r requirements.txt
python ea_analyzer.py
```

Abre `http://localhost:5000` en tu navegador.

En el primer arranque, la app crea archivos locales ignorados (`config.json`, `validator_store.json`, `incubation_config.json`, `incubation_store.json`) a partir de las plantillas `*.example.json`.

---

## Archivos principales

| Archivo | Rol |
|---|---|
| `ea_analyzer.py` | App Flask: rutas, cache de sesión, config |
| `parser.py` | Lee .xlsx MT5, une POSITIONS + ORDERS |
| `metrics.py` | Calcula equity, drawdown, SQN, streaks, etc. |
| `validator.py` | Motor de scoring EA Validator |
| `templates/` | Vistas Jinja2 (dashboard, strategy, validator...) |
| `static/` | CSS (dark theme) + Charts.js (Plotly helpers) |

---

## Archivos locales (no subidos al repo)

| Archivo | Qué contiene |
|---|---|
| `config.json` | Magic numbers, alias, capital por EA |
| `.secret_key` | Clave Flask para sesiones |
| `validator_store.json` | Datos de backtest ingresados |
| `incubation_config.json` | Mapping local de EAs en incubación |
| `incubation_store.json` | Referencias BT/MC/SPP locales |
| `runtime_cache/` | Cache de trades parseados por sesión (live + incubación) |
| `uploads/*.xlsx` | Archivos MT5 subidos |
| `_debug_*.py` | Scripts temporales de depuración local |

---
