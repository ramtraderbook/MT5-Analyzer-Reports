# Frontend — Templates, CSS y Gráficas

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Motor de templates | Jinja2 (vía Flask) |
| Estilos | CSS custom en `static/style.css` (sin frameworks externos) |
| Gráficas | Chart.js (cargado desde CDN) |
| Scripts | JavaScript vanilla en `static/charts.js` + inline por template |
| Iconos | Emojis Unicode (sin dependencias de iconografía) |

---

## Estructura de templates

```
templates/
├── base.html                   ← Layout base: nav, sidebar, scripts
├── upload.html                 ← Página de carga de archivos
├── mapping.html                ← Formulario de mapeo EA (live)
├── dashboard.html              ← Dashboard portfolio live
├── strategy.html               ← Detalle de un EA (live)
├── export.html                 ← Tabla exportable para Validator
├── validator.html              ← Dashboard Live vs BT scoring
├── validator_input.html        ← Formulario datos BT por EA
├── incubation_mapping.html     ← Formulario de mapeo EA (incubación)
├── incubation_reference.html   ← Lista EAs con estado BT/MC/SPP
├── incubation_reference_edit.html ← Formulario datos BT/MC/SPP
├── incubation_dashboard.html   ← Dashboard screening incubación
└── incubation_strategy.html    ← Detalle EA en incubación
```

---

## base.html — Layout global

### Sidebar
La barra lateral se genera dinámicamente desde el contexto Jinja2:
- En modo **live**: itera `all_metrics.by_ea` (solo EAs activos)
- En modo **incubation**: itera `inc_ea_list` con badge de veredicto por color

El sidebar muestra: nombre del EA, instrumento, profit neto y badge de veredicto (solo incubación).

### Navegación superior
Botones: Dashboard · Validator / Incubation Screening · Exportar · modo activo badge.

### Scripts globales
`charts.js` se carga al final del `<body>` en `base.html`. Los templates individuales pueden agregar scripts adicionales en el bloque `{% block extra_scripts %}`.

---

## upload.html

### Selector de modo
Radio buttons para elegir `live` o `incubation` antes de subir. El valor se envía en el POST a `/upload` como campo `analysis_mode`.

### Historial de archivos
Dos secciones separadas con acentos de color:
- **Live** (azul): `loaded_files_live` de `config.json`
- **Incubación** (morado): `loaded_files_incubation` de `incubation_config.json`

Cada sección tiene dos botones:
- **Resetear trades**: POST a `/reset` o `/incubation/reset`
- **Reset completo**: POST a `/reset_all_live` o `/incubation/reset_all` con `confirm()` JS

Variables Jinja2 requeridas: `loaded_files_live`, `loaded_files_inc`, `total_trades_live`, `total_trades_inc`, `analysis_mode`

---

## dashboard.html

### KPI cards
Cada KPI es un `.kpi-card` con `.kpi-label` y `.kpi-value`. Los colores de estado se aplican con clases CSS: `.good`, `.warning`, `.danger`.

### Gráficas principales
Se renderizan en `<canvas>` elements con IDs predefinidos. Los datos se cargan via AJAX a las APIs:

| Canvas ID | API endpoint | Qué muestra |
|---|---|---|
| `equityChart` | `/api/equity_curves?days=N` | Curvas de equity multiEA + portfolio |
| `drawdownChart` | `/api/drawdown_curves?days=N` | Drawdown % multiEA |
| `contributionChart` | `/api/contribution` | Barras de contribución |
| `correlationChart` | (inline) | Heatmap de correlación |

### Filtro de días
Select con opciones: 30, 90, 180, 365, "Todo". Al cambiar, recarga las curvas via `fetchEquityCurves(days)` en `charts.js`.

### Tabla de EAs
Cada fila tiene link a `/strategy/<name>`. Las columnas numéricas se colorean según umbrales (verde/amarillo/rojo).

---

## strategy.html (detalle EA live)

### Secciones
1. **Header**: nombre, magic, instrumento, fecha primer/último trade
2. **KPI cards**: profit neto, win rate, profit factor, max DD%, SQN, Sharpe
3. **Curvas**: equity + drawdown del EA via `/api/ea_equity/<name>?days=N`
4. **Monthly Performance**: heatmap de P&L por mes/año
5. **Distribución P&L**: histograma via `/api/ea_pnl_data/<name>`
6. **Streaks y Long/Short**: barras comparativas
7. **Métricas rodantes**: `/api/rolling_metrics/<name>?window=N`
8. **Tabla de trades**: con sorting JS via `makeSortable()`

---

## validator.html

### Card de veredicto por EA
Cada EA tiene una card con:
- Score total `/100` con color según umbral (≥70 verde, ≥45 amarillo, <45 rojo)
- Veredicto: CONTINUAR / MONITOREAR / ELIMINAR con badge de color
- Tabla de métricas con estado OK/ALERTA/FUERA por fila

### Cajas explicativas
Cada categoría (RIESGO, EDGE, CARÁCTER, DESV. ESTRUCTURAL) tiene una caja `.explain-box` con descripción y fórmula de la métrica principal.

---

## incubation_dashboard.html

### Tabla de resultados
Columnas: EA, Instrumento, Trades, Checkpoint, Veredicto, Score (solo CP3), Días en incubación.

### Cards de estado por EA
Cada EA muestra: badge de checkpoint (CP1/CP2/CP3), badge de veredicto con color, score numérico si CP3.

### Cajas explicativas de Checkpoints
Ubicadas **debajo** de la tabla de resultados (`.inc-explain-grid`). Explican CP1, CP2 (con las 7 métricas), CP3, SCORE y VEREDICTO con la misma estructura visual que el Validator live.

---

## incubation_strategy.html

### Card "Estado Actual" (`.inc-vcard`)
La pieza más compleja del frontend. Compuesta por:

1. **Top row**: badge de checkpoint + chip de veredicto + score numérico (solo CP3)
2. **Progress bar**: avance hacia el próximo checkpoint, calculado con `trade_count` (variable local renombrada para evitar colisión con la lista `trades` del contexto)
3. **Score bar** (solo CP3): gradiente rojo→amarillo→verde con marcadores en 45 y 65
4. **Category mini-bars** (solo CP3): Desviación BT/MC, Riesgo, Coherencia, Ajuste muestra
5. **Hard gates**: lista de gates que fallan con iconos ✗/✓
6. **Métricas fallidas**: lista de métricas deterioradas
7. **Anti-limbo warning**: banner rojo si escaló de CP2+CP3 OBSERVAR → ELIMINAR
8. **Mensaje contextual**: acción recomendada según veredicto

> **⚠️ Bug conocido resuelto**: La variable `{% set trades = m.total_trades %}` causaba colisión con la lista `trades` del route. Renombrada a `trade_count` para evitar `TypeError: object of type 'int' has no len()` en la tabla de trades.

### Curvas de equity y drawdown
Misma implementación que `strategy.html` live. Los datos provienen de `/api/incubation/ea_equity/<ea>`.

### Historial de Checkpoints
Timeline de evaluaciones pasadas: fecha, trades en ese momento, checkpoint, veredicto, score (si CP3). Datos provienen de `checkpoints` en `incubation_store.json`.

---

## charts.js — Funciones principales

### fetchEquityCurves(days)
Llama a `/api/equity_curves?days={days}`. Construye un gráfico Chart.js de tipo `line` con múltiples datasets (uno por EA + portfolio). La paleta de colores viene del servidor en `ea_colors`.

### fetchDrawdownCurves(days)
Llama a `/api/drawdown_curves?days={days}`. Área rellena negativa (DD es siempre ≤ 0).

### fetchContribution()
Llama a `/api/contribution`. Gráfico de barras horizontales con valor de `net_profit` por EA.

### fetchCorrelation()
Llama a `/api/correlation`. Construye un heatmap usando Chart.js con escala de color rojo-blanco-verde (escala R-G usada en el selector de modo de correlación).

### makeSortable(tableId)
Añade sorting clickable a tablas con clase `data-table`. Detecta si la columna es numérica o string y ordena correspondientemente.

### IntersectionObserver — dash-reveal
Los elementos con clase `dash-reveal` comienzan con `opacity: 0`. El `IntersectionObserver` activa `opacity: 1` con transición cuando el elemento entra en el viewport. **Cada template que use `dash-reveal` debe incluir su propio IntersectionObserver** (no es global en charts.js).

---

## style.css — Clases importantes

### Layout
| Clase | Uso |
|---|---|
| `.dash-grid` | Grid 2-columnas para KPIs |
| `.kpi-card` | Card de métrica individual |
| `.section-title` | Título de sección con borde izquierdo |
| `.data-table` | Tabla con hover y sorting |
| `.explain-box` | Caja informativa con fondo sutil |

### Estados y colores
| Clase | Color | Umbral |
|---|---|---|
| `.good` / `.text-good` | Verde `#66BB6A` | Métrica OK |
| `.warning` / `.text-warning` | Amarillo `#FFA726` | Métrica ALERTA |
| `.danger` / `.text-danger` | Rojo `#FF5252` | Métrica FUERA |

### Incubation Strategy card
| Clase | Descripción |
|---|---|
| `.inc-vcard` | Contenedor principal del card Estado Actual |
| `.inc-vcard-top-row` | Fila superior: checkpoint + veredicto + score |
| `.inc-vcard-progress-wrap` | Progress bar hacia próximo checkpoint |
| `.inc-vcard-score-bar` | Barra de score CP3 con gradiente |
| `.inc-vcard-score-bar-fill` | Relleno de la barra de score |
| `.inc-vcard-cat-bars` | Mini-barras de categorías CP3 |
| `.inc-vcard-gates` | Sección de hard gates |
| `.inc-vcard-failing` | Lista de métricas fallidas |
| `.inc-vcard-antilimbo` | Banner rojo de escalación anti-limbo |
| `.inc-vcard-next-msg` | Mensaje contextual de acción recomendada |

### Upload page historial
| Clase | Descripción |
|---|---|
| `.history-strip` | Contenedor de historial de archivos |
| `.history-strip--live` | Variante con acento azul |
| `.history-strip--inc` | Variante con acento morado |
| `.history-mode-badge` | Badge "LIVE" / "INCUBACIÓN" |
| `.btn-reset-all` | Botón de reset completo (rojo oscuro) |

### Botones de acción en incubation_strategy
| Clase | Descripción |
|---|---|
| `.inc-action-btn-group` | Wrapper botón + descripción |
| `.inc-btn-hint` | Texto explicativo debajo del botón |

---

## Colores de EAs

`metrics.py` define una paleta fija de 12 colores para EAs:
```python
EA_COLORS = ["#4FC3F7", "#FF7043", "#66BB6A", "#AB47BC", "#FFA726",
             "#26C6DA", "#EC407A", "#8D6E63", "#78909C", "#D4E157",
             "#5C6BC0", "#FF8A65"]
```
Si hay más de 12 EAs, los colores adicionales se generan con HSL distribuido uniformemente: `hsl({hue}, 70%, 60%)`.

---

## Gotchas de Jinja2

### Scoping de variables
`{% set variable = value %}` dentro de `{% if %}` / `{% elif %}` crea una variable **local al bloque**. No persiste al scope exterior. Para resolver:

```jinja2
{% set ns = namespace(bar_pct=100) %}
{% if condition %}
    {% set ns.bar_pct = 50 %}
{% endif %}
{{ ns.bar_pct }}   {# funciona correctamente #}
```

Alternativamente, renombrar la variable local para evitar colisión con variables del contexto del route (como se hizo con `trade_count` vs `trades`).

### Filtros útiles
- `{{ value|default("N/A") }}` — valor por defecto si es None/undefined
- `{{ value|round(2) }}` — redondeo
- `{{ value|int }}` — conversión a entero
- `{{ value|abs }}` — valor absoluto
- `{{ loop.index }}` — índice 1-based en bucles `{% for %}`
