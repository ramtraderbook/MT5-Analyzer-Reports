# Pendientes conocidos

Hallazgos auditados, verificados ejecutando el código, y **deliberadamente no
corregidos**. Cada uno dice por qué se dejó y qué hace falta para cerrarlo.

Esto no es una lista de deseos: todo lo de acá está probado. Si algo se
corrige, borrar su entrada.

Origen: Judgment Day JD-2 (`metrics.py`, núcleo estadístico), JD-3
(`validator.py`, escalado del límite de DD) y JD-3 (`parser.py`, integridad de
datos).

---

## 1. Sobre-operar compra margen de drawdown — BLOQUEADO POR DATOS

**Severidad**: alta. Es la vía de parada inmediata (`dd_estado == "FUERA"` →
`ELIMINAR` en `validator.py`).

`dd_limit` escala con `sqrt(trades_live / trades_por_mes_BT)`. Eso es
estadísticamente correcto —la varianza se acumula por operación— pero implica
que un EA que opera muy por encima del ritmo de su backtest recibe un límite
de DD proporcionalmente mayor.

Medido, con el mismo drawdown real del 20% y `bt = 500 trades / 48 meses`:

| Trades | Ritmo vs BT | dd_limit | dd_estado | Score | Veredicto |
|---|---|---|---|---|---|
| 14 | 97% | 6.96 | FUERA | 79.0 | **ELIMINAR** |
| 30 | 208% | 10.18 | FUERA | 70.8 | ELIMINAR |
| 60 | 416% | 14.40 | ALERTA | 75.0 | CONTINUAR |
| 120 | 831% | 20.36 | OK | **83.8** | **CONTINUAR** |

El que opera al ritmo correcto se elimina; el que opera a 831% continúa, con
el mismo drawdown.

**Aritmética**: llegar a `dd_estado = OK` vale +17.5 puntos (`w_riesgo` 35 ×
`w_dd_escalado` 50). Perder `freq_estado` cuesta 8.25 (`w_caracter` 15 ×
`w_frecuencia` 55). Neto: **+9.25 a favor de portarse mal**.

`freq_estado` ya es de dos colas, así que el sobre-ritmo **se ve** — pero
verlo no alcanza para compensarlo.

**Solución acordada**: un ritmo live muy por encima del BT invalida la
referencia entera (el EA ya no es la estrategia que ese backtest describe) →
`SIN DATOS` nombrando la causa, según el contrato de
`docs/design/decision-engine-no-data-contract.md`. Se implementó y se probó
que cierra el agujero (208%/416%/831% → `SIN DATOS`), pero **se revirtió**:
con el umbral en 150% quedaban 11 tests en rojo, incluido el fixture canónico
de JD-1 (207.8% de ritmo). No se puede distinguir si el umbral está muy
ajustado o si esos fixtures son sintéticos sin coherencia de ritmo.

**Para destrabar**: hace falta un export real de MT5 + `validator_store` para
medir la distribución real de `freq_pct` en los EAs propios y elegir el umbral
con datos. Mover el umbral hasta que los tests pasen sería calibrar contra
fixtures inventados.

**Dos trampas ya identificadas para quien lo implemente**:

- El gate debe exigir `weeks_live >= 4.33` primero. `freq_pct` extrapola un
  ritmo *mensual*: sobre 2 días es ruido. Un scalper sano recién nacido con 6
  trades en 2 días extrapola a 831% y sería rechazado el día 2 — la misma
  falacia de muestra chica que hacía que el reloj de calendario ejecutara
  recién nacidos.
- El gate debe ser de **una sola cola** (solo el exceso). Mandar todo
  `freq_estado == FUERA` a `SIN DATOS` **rescataría** al sub-operador
  deteriorado: 6 trades en 20 semanas con `detcount = 4` hoy puntúa 43.2 →
  `ELIMINAR`. Sub-operar es la misma estrategia haciendo menos señales: la
  referencia le sigue aplicando por trade.

---

## 2. `detcount` ignora el sobre-ritmo

`validator.py` cuenta deterioro con `freq_pct < 70`, así que un EA a 413% del
ritmo BT no suma `detcount` ni levanta el flag `DESV`, aunque `freq_estado`
ya lo marque `FUERA`.

Es coherente con el diseño del bloque —`detcount` es un contador de
*deterioro*, y todas sus condiciones son de una sola cola (`wr < bt-5`,
`payout < bt*0.8`, `pf < bt*0.8`, `edge_erosion < -30`)— pero deja una
incoherencia con `freq_estado`, que ahora sí es de dos colas.

Se resuelve junto con el punto 1: si el sobre-ritmo pasa a `SIN DATOS`, no
hay score que ajustar.

---

## 3. Calibración del multiplicador 1.5 de ALERTA — NO VERIFICABLE

JD-2 corrigió `max_dd_pct` para que sea el máximo verdadero de la serie y no
el DD% del momento del peor DD en dólares. El valor nuevo es **siempre ≥ al
viejo**, así que el gate de DD quedó monotónicamente más estricto **sin
recalibrar** ni `worst_dd_1m`, ni el multiplicador 1.5 de ALERTA, ni las
referencias MC.

EAs que antes leían OK pueden leer ALERTA/FUERA con datos idénticos. Eso es
intencional (el valor viejo subestimaba el riesgo), pero la calibración de los
umbrales quedó sin revisar.

**Por qué no se puede verificar**: `worst_dd_1m` lo tipea el operador desde
SQX y no hay importador en el repo. No hay forma de confirmar si SQX define su
"max DD %" con la misma semántica (máximo de la serie de dd_pct) que
`metrics.py`. Si las dos definiciones difieren, los dos lados de la
comparación miden cosas distintas.

**Para destrabar**: confirmar qué reporta exactamente SQX en ese campo.

---

## 4. Fallback MC: la zona ALERTA desaparece si las dos referencias coinciden

Cuando `mc_r_dd == mc_t_dd`, la frontera OK (`min`) y la frontera ALERTA
(`max`) coinciden: el gate de tres estados colapsa a dos y **un EA cuyos dos
métodos MC coinciden recibe un gate más duro que uno cuyos métodos discrepan**.
Verificado: con 22/22, un DD de 21 es OK y uno de 23 ya es FUERA; ningún valor
cae en ALERTA.

Ensancharlo a la convención 1.5× del camino BT se probó y se **rechazó**: mueve
el DD en `(max(mc), 1.5*min(mc)]` de FUERA —veto duro— a ALERTA, aflojando la
vía de parada en un rango ancho, y rompe las fronteras intencionales que fija
`test_dd_estado_both_mc_present_fallback_boundaries`.

Es una decisión de política, no un bug. Está pinneado por
`test_dd_estado_mc_fallback_alerta_zone_is_empty_when_mc_values_equal`, que
registra la conducta real y falla si alguien la cambia sin querer.

**Relacionado**: el fallback MC no escala con el tiempo ni con los trades,
mientras que el camino BT sí. Un mismo EA con 20% de DD da ELIMINAR con
`worst_dd_1m` cargado y CONTINUAR sin cargarlo. Escalar el MC **no** es la
solución: sus cifras son del percentil 95 sobre el backtest completo, y
escalarlas a un EA joven (`22 * sqrt(6/500) = 2.42%`) haría que un DD normal
del 3% leyera FUERA, reintroduciendo la ejecución del recién nacido.

---

## 5. `bt_months` degenerado pasa la guarda

`bt_months = 0.0001` supera el chequeo `> 0` y produce
`dd_limit = 0.02%` → `FUERA` → `ELIMINAR`. Es un valor degenerado-pero-presente,
justo la clase que el contrato SIN DATOS existe para atrapar, y ni la compuerta
de completitud (que solo chequea `None`) ni la guarda `> 0` lo cazan.

Input implausible, pero no hay cota de sanidad.

---

## 6. `_nd_result` solo blanquea `dd_limit`

En una fila `SIN DATOS`, `wr_delta`, `payout_var`, `bars_var` y `edge_erosion`
sobreviven con valores confiados al lado de estados `N/D` — el mismo patrón de
número silencioso y auto-contradictorio que se corrigió para `dd_limit`.

Pre-existente. `dd_limit` se corrigió porque el cambio del reloj de trades lo
volvió alcanzable en un caso nuevo.

---

## 7. `metrics.py` — menores verificados

- **SQN sin el cap de Tharp**: Van K. Tharp acota el multiplicador en
  `sqrt(100) = 10`; el código usa `sqrt(N)` sin cota, así que el número de
  trades por sí solo infla la calificación (con `mean/std = 0.108` y N=2500 da
  SQN 5.42 "Sobresaliente" contra 1.08 "Pobre" con el cap). El doc §8 define la
  fórmula sin cap, así que código y doc **coinciden**; la divergencia es contra
  el estándar. Solo display.
- **Acantilado del guard de varianza**: con CV = 0.0101 y N ≥ 20, el SQN
  todavía puede dar 1393 y etiqueta "Santo Grial". Cualquier umbral fijo tiene
  esa discontinuidad; se aceptó porque la banda CV 0.01–1.0 no contiene ningún
  EA realista (los reales miden CV 2.9–6.0) y el retén de etiqueta por muestra
  chica (N < 20) cubre los casos plausibles.
- **`profit_factor` / `payout_ratio` devuelven el string `"∞"`**: mal olor de
  tipos, pero **verificado inocuo** — todos los consumidores lo guardan
  (`_safe_float` en `validator.py` e `incubation_validator.py`, `isinstance` en
  el export, `!= '∞'` en Jinja). El único que se degrada es el ordenamiento de
  tablas en `static/charts.js`, que lo ordena como texto.
- **`capital <= 0`** hace `peak_abs <= 0` y el DD% cae silenciosamente a 0.0,
  enmascarando el drawdown real. La config no valida capital no positivo.
- **Trades con el mismo timestamp**: el orden estable del sort conserva el orden
  del archivo, así que `max_dd_pct` puede dar 9.09 o 10.0 para la misma
  historia según cómo vino el export.
- **`untimed_trades`** se expone en el dict pero ninguna plantilla ni ruta lo
  muestra: el contrato SIN DATOS se cumple a nivel API, no en la UI.
- **Trades con P&L exactamente 0 cuentan como pérdidas** (`net_pnl <= 0`),
  inflando `losing_trades` y las rachas perdedoras. El doc §10 lo documenta así,
  o sea que código y doc coinciden.

---

## 8. El JOIN POSITIONS↔ORDERS es válido solo si la orden de apertura está en el rango exportado

Confirmado por los dos jueces de JD-3 (`parser.py`). El JOIN es
`position_id == order_id`, y eso funciona porque MT5 asigna al `position_id` el
ticket de la **orden que abrió** la posición. La consecuencia: si el usuario
exporta un rango de fechas que **no incluye la apertura**, la orden nunca entra
al `order_map` y el trade queda en `"Unknown"`.

Forma de entrada: posición abierta el 2024-04-28 por la orden 111 (comment
`"EA-A"`), cerrada el 2024-05-02; el usuario exporta solo mayo → la sección
POSITIONS trae la posición 111 pero ORDERS no trae la orden 111 → el trade se
atribuye a `"Unknown"` y queda **excluido del análisis**
(`ea_names` filtra `"Unknown"`).

**Por qué se dejó**: no hay nada en el archivo con qué reparar el JOIN — la
información simplemente no fue exportada. Un fallback por
`open_time`+`symbol`+`volume` sería adivinar, y adivinar mal la atribución de
un EA es peor que decir `"Unknown"`.

**Para destrabar**: el contador `unknown_trades` ya existe y es honesto. Falta
que la UI lo muestre de forma visible al cargar el archivo, con el consejo de
re-exportar incluyendo la fecha de apertura. Es un cambio de UI, no de parser.

---

## 9. Una celda de dinero ilegible sigue devolviendo 0.0 en silencio

Ésta es la queja original de la auditoría —el silencio— y quedó **cerrada solo
a medias**. `_to_float()` ahora entiende todos los formatos plausibles
(miles, decimal europeo, negativos entre paréntesis, símbolos de moneda,
`nan`/`inf`), pero una celda genuinamente ilegible (por ejemplo `"###"`)
todavía cae al `default = 0.0` sin ninguna señal.

Verificado ejecutando: `_to_float("###")` → `0.0`. Un trade con Profit ilegible
se registra como breakeven y entra a las métricas como un trade real.

`unknown_trades` **no** cubre esto: solo cuenta fallos del JOIN con ORDERS, no
fallos de parseo numérico.

**Por qué se dejó**: cerrarlo no es un fix, es un cambio de contrato —hace
falta un contador nuevo en el dict que devuelve `parse_mt5_report()`
(`malformed_cells` o similar), que la UI lo muestre, y decidir si un archivo
con celdas ilegibles se rechaza o se carga con aviso. Eso es diseño, y se
sale del alcance de una auditoría de integridad.

**Para destrabar**: definir la política primero (¿rechazar o avisar?), después
implementar. El patrón a seguir es el contrato SIN DATOS de
`docs/design/decision-engine-no-data-contract.md`: un número silencioso y
confiado es peor que una ausencia declarada.

---

## 10. `parser.py` — menores verificados

- **`sl`/`tp` con cero: incoherente según el tipo de celda**. `"sl": _to_float(sl_val) if sl_val else None`
  — una celda numérica `0.0` es falsy → `None` (correcto: en MT5 `S/L = 0`
  significa "sin stop"), pero el string `"0"` es truthy → `0.0`, y se trata como
  un precio real. Pre-existente, sin cambios en esta auditoría. Impacto bajo:
  `sl`/`tp` son campos de display, no entran en ningún cálculo.
- **Un comment de EA puramente numérico puede perderse en un export malformado**.
  El escaneo de fallback de `_parse_open_positions` (solo se activa si el header
  **no** tiene columna `Comment`) ahora saltea celdas numéricas para no volver a
  leer el Profit como nombre de EA. El costo: un EA cuyo comment sea el magic
  number pelado (`"1104"`, que `trade_matching.py` sí matchea contra el magic)
  queda en `"Unknown"` en ese caso degradado. Se eligió esa dirección a
  propósito: un EA fantasma llamado `"150.75"` contaminando `ea_names` es peor
  que un `"Unknown"` honesto.
- **Moneda: whitelist de tres**. `_parse_header` solo reconoce `USD`/`EUR`/`GBP`;
  una cuenta en `JPY`/`CHF`/`AUD` queda etiquetada `"USD"` por default. Solo
  display, pero mal etiquetado.
- **`_parse_results` corta en el primer par por fila**. Los reportes MT5 suelen
  poner varios pares label/valor en la misma fila; solo entra el primero. Afecta
  a `results_validation`, que hoy no se usa para decidir nada.
