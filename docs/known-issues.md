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

## 5. `bt_months` degenerado pasa la guarda — ✅ RESUELTO

`bt_months = 0.0001` supera el chequeo `> 0` y produce
`dd_limit = 0.02%` → `FUERA` → `ELIMINAR`. Es un valor degenerado-pero-presente,
justo la clase que el contrato SIN DATOS existe para atrapar, y ni la compuerta
de completitud (que solo chequea `None`) ni la guarda `> 0` lo cazan.

Input implausible, pero no hay cota de sanidad.

**✅ RESUELTO**: `validator.py` ahora impone una cota de sanidad
`BT_MONTHS_SANITY_FLOOR = 0.5` (`validator.py:57`); un `bt_months` por debajo del
piso ya no produce un `dd_limit` degenerado sino un `SIN DATOS` que nombra
`bt.months` (`validator.py:462`, `:675`, `:689`), según el contrato de
`docs/design/decision-engine-no-data-contract.md` — ausencia declarada en vez de
un número confiado y equivocado. También se cerró el gemelo B3 de underflow del
cociente (`bt_trades/bt_months` que hace underflow a `0.0` aun con operandos
normales, `validator.py:457-467`, `:562-563`). Pinneado por un test de regresión.

---

## 6. `_nd_result` solo blanquea `dd_limit` — ✅ RESUELTO

En una fila `SIN DATOS`, `wr_delta`, `payout_var`, `bars_var` y `edge_erosion`
sobreviven con valores confiados al lado de estados `N/D` — el mismo patrón de
número silencioso y auto-contradictorio que se corrigió para `dd_limit`.

Pre-existente. `dd_limit` se corrigió porque el cambio del reloj de trades lo
volvió alcanzable en un caso nuevo.

**✅ RESUELTO**: `_nd_result` (`validator.py:244`) ahora pone en `None` **todos**
los numéricos derivados de la fila —`wr_delta`, `payout_var`, `bars_var`,
`edge_erosion`, `freq_pct` y los echoes crudos de live/bt—, no solo `dd_limit`,
de modo que una fila `SIN DATOS` no arrastra ningún número confiado al lado de
sus estados `N/D`, según el contrato de
`docs/design/decision-engine-no-data-contract.md`. Pinneado por un test de
regresión. (Es el mismo hallazgo que §14-C5 — ver referencia cruzada.)

---

## 7. `metrics.py` — menores verificados

- **`calculate_psr` existe y está deliberadamente SIN CABLEAR**: Probabilistic
  Sharpe Ratio (Bailey & López de Prado, 2012) — la probabilidad de que el
  Sharpe verdadero supere un umbral `sr_benchmark`, ajustando por tamaño de
  muestra, skew y kurtosis. `PSR(0) > 0.95` = "el Sharpe es positivo al 95% de
  confianza". Fórmula verificada contra el paper original; ver
  `docs/research/prior-art.md` §5.2. Producción usa `math.erf` + momentos numpy
  (SIN scipy, mismo patrón que el binomial `math.comb`); el oráculo
  `tests/oracle/test_diff_metrics.py` valida contra `scipy.stats` (norm.cdf,
  skew, kurtosis) a tolerancia de redondeo (4dp).
  **Convenciones — son ELECCIONES documentadas, no las fija el paper** (su
  derivación es asintótica): SR con `std(ddof=1)` (igual que `_calc_sharpe`, así
  la PSR es sobre el Sharpe que reportamos); skew biased/poblacional; **kurtosis
  NO-excess (normal=3)** — el punto exacto donde quantstats resta 3 dos veces e
  infla la PSR (su bracket es siempre el correcto menos `0.75·SR²`); denominador
  `n−1` (Bessel, per el paper original — algunas fuentes secundarias usan `n`,
  diferencia O(1/n)). La PSR es atemporal y unitless: **nunca se anualiza**
  (quantstats la multiplica por `√252`, devolviendo "probabilidades" > 1).
  Devuelve la forma estructurada `{"available": ...}` como el bootstrap; no
  estimable con: vacío/None, `n < MIN_TRADES_FOR_PSR` (20, mismo piso que SQN y
  bootstrap — skew/kurt son ruido debajo), valores no finitos, varianza
  degenerada (misma guarda de CV que `_calc_sharpe`), bracket de varianza
  cero en el borde degenerado de dos puntos (Cauchy-Schwarz `kurt >= skew²+1`
  impide que sea negativo, así que NO es por skew/kurt "extremos"; el borde
  vuelve `se=0` y dividiría por cero), o momentos que hacen under/overflow con
  entradas de magnitud extrema (denormales ~1e-160 o ~1e160; cazado en la
  fuente en `_standardized_moments`, no por un chequeo de `isfinite` aguas
  abajo — la propiedad Hypothesis encontró el caso).
  **Por qué sin cablear**: mismas tres razones que el bootstrap (costo aquí es
  trivial, pero cablearla a la etiqueta "Significancia" del `validator.py`
  cambiaría el comportamiento y **el contrato de 41 claves** de
  `calculate_ea_metrics`; y qué umbral de PSR gatea qué es una decisión de
  política que necesita datos reales). **Diferido**: MinTRL (Minimum Track
  Record Length, "cuántos trades hasta que el Sharpe sea significativo") —
  necesita el cuantil normal inverso (`norm.ppf`) y **no hay `erfinv` en la
  stdlib**; agregarlo requeriría una aproximación racional (Acklam) con su
  propio test contra scipy. PSR sola es stdlib pura y es la pieza de valor.
- **`calculate_bootstrap_risk` existe y está deliberadamente SIN CABLEAR**:
  bootstrap iid sobre el P&L por trade (`rng.choice(..., replace=True)`,
  `np.random.default_rng`) que devuelve bandas percentiles de max DD% y
  probabilidad de brecha contra `RUIN_THRESHOLDS_PCT`. Nadie en producción la
  llama; solo los tests. **Esto NO es el mismo caso que `_calc_risk_of_ruin`**,
  que se eliminó por dead code con cero call sites (pinneado en
  `tests/test_metrics.py:397-401`) — aquella era privada, sin racional y nadie
  sabía por qué estaba. Las tres razones de esta, todas verificadas:
  1. **Costo medido**: la cifra de memoria original de esta entrada
     (**"~160 MB de pico con 2000"**) estaba **mal por 7x** — medido con
     `tracemalloc`, la versión sin chunking pico en **1121 MB con n=2000,
     iterations=10000** (seis locals vivos de forma `(iterations, n+1)`
     — `sims`, `cum`, `peak`, `dd_dollar`, `peak_abs`, `dd_pct` — más el
     temporal de `np.concatenate`, todos co-residentes hasta el `return`).
     Se corrigió procesando los paths en chunks acotados por
     `BOOTSTRAP_MEMORY_BUDGET_MB=64` (ver `metrics.py`, comentario de la
     constante): re-medido con `tracemalloc` tras el chunking, tres corridas
     cada uno con varianza cero entre corridas, el pico es **29 MB en n=50,
     71 MB en n=200, 77 MB en n=500, 77 MB en n=2000** — deja de crecer con
     `n` y nunca se acerca a los 1121 MB previos (~14x menor en n=2000). El
     resultado es
     numéricamente **idéntico** al de antes para el mismo seed (mismo `rng`
     creado una sola vez antes del loop, mismo stream de draws), verificado
     directamente contra la formulación sin chunking. Tiempo: 10 ms con 50
     trades, 104 ms con 500, 428 ms con 2000 — sin cambio material respecto
     a la medición original — por EA. `calculate_ea_metrics` corre en el path
     de request de Flask; hacer que cada cálculo de métricas pague eso por
     una capacidad que nadie consume todavía no se justifica. El chunking
     acota la memoria, no el tiempo: el costo temporal sigue creciendo
     linealmente con `n * iterations`, así que un caller que pase un `n` o
     `iterations` enorme paga en wall-clock, no en un salto de memoria —
     razón por la cual no se agregó ningún tope arbitrario a ninguno de los
     dos (ver comentario en `calculate_bootstrap_risk`).
  2. **Contrato de claves**: `tests/oracle/test_char_metrics.py:56` fija
     igualdad de conjuntos sobre las 41 claves de salida de
     `calculate_ea_metrics`. Agregar una clave es un cambio deliberado de
     contrato, no un efecto colateral de agregar una capacidad.
  3. **Política**: cablearla a `validator.py` cambiaría veredictos
     ELIMINAR/CONTINUAR sobre EAs reales. La §1 de este mismo documento está
     bloqueada por datos para exactamente esa clase de calibración.
  **Para cablearla** hace falta: decidir el punto de ruina (hoy se reportan
  cuatro umbrales justamente para no elegirlo), decidir dónde vive el costo
  (¿cacheado? ¿on-demand?), actualizar el contrato de 41 claves, y —si alguna
  vez alimenta un veredicto— calibrar contra un export real de MT5.
  **Valor de tenerla**: el gate de DD hoy se apoya en números tipeados desde
  SQX cuya semántica la §3 admite **NO VERIFICABLE**. Un bootstrap sobre
  nuestros propios trades no necesita ese acuerdo semántico: misma definición
  de los dos lados, por construcción. Ver `docs/research/prior-art.md` §5.1.

- **SQN sin cap en N=100 — la atribución a Tharp está sin verificar**: la
  atribución de ese cap a Van Tharp **no pudo confirmarse ni descartarse**
  (`docs/research/prior-art.md` §2.2): `vantharpinstitute.com` devuelve 403 en
  todas sus URLs, `vantharp.com` redirige al mismo muro, archive.org no estuvo
  disponible, y *Definitive Guide to Position Sizing* (2008) — el libro donde
  se introduce SQN y donde supuestamente vive el cap — está paywalled sin cita
  citable. El cap rastrea a solo dos paráfrasis de terceros, ninguna cita a
  Tharp ni página ("as a work around he suggests traders use 'N=100'" en un
  blog; "one way he suggests to cope" en un foro), y ambas describen un
  remedio informal para muestras grandes, no un término de la fórmula
  publicada. Contraevidencia: Jonathan Kinlay deriva SQN sin cap; un usuario de
  Wealth-Lab que sí implementa `Math.Min(10, ...)` lo describe explícitamente
  como **su propia** modificación, no la de Tharp. Origen probable de la
  confusión: el propio "Market SQN" de Tharp aplica la fórmula sobre una
  ventana FIJA de 100 días, donde `sqrt(100)=10` es una constante de
  normalización, no un clamp. **Veredicto honesto**: el cap no forma parte de
  la fórmula de SQN según el material accesible de Tharp y parece ser una
  convención de la comunidad; la fuente primaria (su libro) está paywalled y
  esto no pudo saldarse. El código usa `sqrt(N)` sin cota, así que el número de
  trades por sí solo infla la calificación (con `mean/std = 0.108` y N=2500 da
  SQN 5.42 "Sobresaliente" contra 1.08 "Pobre" con el cap). El doc §8 define la
  fórmula sin cap, así que código y doc coinciden. Solo display. Ver también la
  divergencia de R-multiples más abajo, mejor evidenciada que ésta.
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
- **`capital <= 0` — ✅ RESUELTO** (§14-C2). Antes hacía `peak_abs <= 0` y el DD%
  caía silenciosamente a 0.0, enmascarando el drawdown real. Ahora
  `capital <= 0` da `max_dd_pct = None` (SIN DATOS, ausencia declarada),
  siguiendo el precedente del bootstrap en vez del 0.0 silencioso
  (`metrics.py:285-286`, `:324`).
- **Trades con el mismo timestamp**: el orden estable del sort conserva el orden
  del archivo, así que `max_dd_pct` puede dar 9.09 o 10.0 para la misma
  historia según cómo vino el export.
- **`untimed_trades`** se expone en el dict pero ninguna plantilla ni ruta lo
  muestra: el contrato SIN DATOS se cumple a nivel API, no en la UI.
- **Trades con P&L exactamente 0 — ✅ RESUELTO / OBSOLETO**. Antes los breakeven
  (`net_pnl <= 0`) contaban como pérdidas, inflando `losing_trades` y las rachas
  perdedoras. Ahora el breakeven (P&L == 0) se excluye de **ambas** particiones
  —ni gana ni pierde— y se expone aparte como `breakeven_trades`: una ganancia es
  estrictamente `p > 0` y una pérdida estrictamente `p < 0`
  (`metrics.py:781-783`). `docs/metrics-formulas.md` §10 se actualizó para
  reflejar la nueva conducta, o sea que código y doc siguen coincidiendo.
- **SQN sobre P&L crudo, no sobre R-multiples**: la propia definición del
  Tharp Institute (recuperada vía la reproducción atribuida de TradingView de
  la página con 403) dice: *"SQN measures the relationship between the mean
  (expectancy) and the standard deviation of **the R-multiple distribution**
  generated by a trading system. It also makes an adjustment for the number
  of trades involved."* — nótese "an adjustment for the number of trades"
  **sin ningún techo mencionado**. SQN se define sobre R-multiples (retorno
  normalizado por el riesgo inicial de cada trade), no sobre P&L crudo en
  moneda. `_calc_sqn` (`metrics.py:394-425`) recibe `net_pnl` crudo; lo mismo
  hacen backtrader, backtesting.py y vectorbt (tres implementaciones
  verificadas, ninguna usa R-multiples).

  Por qué importa: los R-multiples hacen que el SQN sea comparable entre
  tamaños de posición distintos; el P&L crudo no — un EA que varía el lotaje
  tiene un SQN que mide en parte su gestión de tamaño, no su edge. Factible en
  principio: `parser.py:425` y `:557` sí capturan `sl`, y `open_price`/`volume`
  están disponibles, así que el riesgo inicial es calculable.

  Confianza: la definición de R-multiples es de alta confianza pero descansa
  en el mismo sitio con 403 y el mismo libro paywalled que el cap de arriba —
  mejor evidenciada, no confirmada de forma independiente. Ver
  `docs/research/prior-art.md` §2.2.

  **Para destrabar**: eran dos bloqueadores; **uno ya está resuelto**.
  (1) El bug de manejo de cero en `sl`/`tp` (§10) — el string `"0"` se trataba
  como precio real `0.0` mientras el `0.0` numérico daba `None` — está
  **ARREGLADO** vía `_to_price_or_none` (`parser.py`): todo cero es ahora `None`,
  así que `sl="0"` ya no inyectaría un riesgo inicial fabricado como
  denominador del SQN. (2) Sigue pendiente: los EAs que usan SL virtual/oculto
  (gestionado en código, nunca enviado al bróker) reportan `sl=None`, así que el
  R-multiple sería incalculable para ellos y hace falta una política explícita
  (¿excluirlos? ¿caer a P&L crudo con etiqueta?) antes de adoptar R-multiples.

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

## 9. Una celda de dinero ilegible sigue devolviendo 0.0 en silencio — ✅ RESUELTO

Ésta es la queja original de la auditoría —el silencio— y quedó **cerrada solo
a medias**. `_to_float()` ahora entiende todos los formatos plausibles
(miles, decimal europeo, negativos entre paréntesis, símbolos de moneda,
`nan`/`inf`), pero una celda genuinamente ilegible (por ejemplo `"###"`)
todavía cae al `default = 0.0` sin ninguna señal.

Verificado ejecutando: `_to_float("###")` → `0.0`. Un trade con Profit ilegible
se registra como breakeven y entra a las métricas como un trade real.

`unknown_trades` **no** cubre esto: solo cuenta fallos del JOIN con ORDERS, no
fallos de parseo numérico.

**✅ RESUELTO** con la política "cargar con aviso": `_money_float` en `parser.py`
cuenta un nuevo `malformed_cells` en `parse_mt5_report()` —mismo patrón que
`unknown_trades`— y `templates/mapping.html` muestra el contador con un aviso de
re-exportar. Un número silencioso y confiado se reemplaza así por una ausencia
declarada, en línea con el contrato SIN DATOS de
`docs/design/decision-engine-no-data-contract.md`.

**Follow-up pendiente (NO resuelto)**: el contador solo se muestra en la página
de mapping. No persiste al dashboard, a strategy ni al export, así que la señal
de calidad **desaparece al avanzar** más allá del mapping. Falta propagar
`malformed_cells` aguas abajo para que el aviso sobreviva a la navegación.

---

## 10. `parser.py` — menores verificados

- **`sl`/`tp` con cero: incoherente según el tipo de celda — RESUELTO**. El guard
  viejo `_to_float(sl_val) if sl_val else None` testeaba el valor **crudo**: el
  `0.0` numérico (falsy) daba `None` pero el string `"0"` (truthy) parseaba a
  `0.0` y se trataba como precio real. Arreglado con el helper compartido
  `_to_price_or_none` (`parser.py`), que parsea primero y conserva solo precios
  estrictamente positivos — en MT5 `S/L = 0` es un centinela de ausencia, no un
  precio de cero, y un precio válido siempre es `> 0`. Ahora `0.0`, `"0"`,
  `"0.0"`, `"0,0"`, vacío y `None` dan todos `None`; los negativos (celda
  malformada) también se tratan como unset en vez de propagar un precio
  inválido. Pineado por `test_to_price_or_none_*` y
  `test_parse_positions_sl_zero_string_becomes_none_end_to_end` en
  `tests/test_parser.py`. **Nota**: este era el bloqueador de parseo que la nota
  de R-multiples (§7) señalaba — con `sl` ahora coherente, `sl="0"` ya no
  inyectaría un riesgo inicial falso de cero. Queda pendiente lo otro de esa
  nota: los EAs con SL virtual (gestionado en código) reportan `sl=None`, así
  que el R-multiple seguiría siendo incalculable para ellos.
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

## 11. Backend Flask — hallazgos auditados y no corregidos (JD-4)

Los cuatro puntos siguientes los reportó **un solo** juez en la auditoría JD-4 del
backend Flask. El protocolo solo corrige lo que dos jueces confirman, así que
quedaron registrados en vez de aplicados. No son especulativos: el código hace lo
que se describe. Lo que falta es la corroboración independiente que justifique
tocarlos.

- **`GET /incubation/strategy/<ea_name>` escribe en disco — ✅ RESUELTO**. Antes,
  cuando la referencia estaba completa, la ruta persistía el resultado de la
  evaluación (`store[ea_name] = bundle["entry"]; save_incubation_store(store)`)
  durante un simple GET —re-disparable por prefetchers del navegador y por
  cualquier recarga, y un read-modify-write del store entero sin lock que bajo
  WSGI pisaba escrituras de **otros** EAs—. Ahora el GET es **compute-and-return**:
  la evaluación se calcula en memoria y no persiste nada
  (`ea_analyzer.py:1494-1513`).
- **Carrera de nombres en `uploads/` (solo WSGI) — ✅ RESUELTO**. Antes todas las
  subidas caían en una carpeta compartida con el nombre que devuelve
  `secure_filename`; como los exports de MT5 suelen traer el mismo nombre por
  default, dos usuarios concurrentes competían entre `file.save()` y
  `parse_mt5_report()` y uno podía parsear el archivo del otro; además los
  archivos nunca se limpiaban. Ahora cada request aísla su subida en un subdir
  único `req_<token>` (`secrets.token_hex`) y lo borra en un `finally`
  (`ea_analyzer.py:886-912`), así que ni colisionan ni quedan huérfanos.
- **`cache_key` se interpola en rutas de archivo sin validar — ✅ RESUELTO**. Antes
  `_cache_file_path` y sus pares construían `f"{prefix}{cache_key}.json"` sin
  filtrar separadores; era defensa en profundidad (la clave siempre es un UUID
  del servidor) pero valía como cinturón si el `.secret_key` alguna vez se
  filtraba. Ahora la clave se valida en la frontera de `cache_store` con
  `_is_safe_cache_key` (`cache_store.py:43`), que rechaza separadores y `..`
  antes de tocar el disco.
- **Carrera del `.secret_key` al importar (solo WSGI multi-worker). ✅ RESUELTO
  (fix 1A)**. Antes el bootstrap era un check-then-write en tiempo de import: dos
  workers podían generar y escribir claves distintas, o uno leer el archivo a
  medio escribir. Ahora **importar no escribe nada**: `_resolve_secret_key()`
  resuelve la clave por precedencia `EA_ANALYZER_SECRET_KEY` (env) → archivo
  existente → efímera, y solo el arranque real (`__main__`) persiste una clave
  nueva. Para WSGI multi-worker, exportar `EA_ANALYZER_SECRET_KEY` da una clave
  estable entre workers sin tocar el disco — la forma idiomática y sin carrera.
  Tests: `test_flask_hardening.py::test_resolve_secret_key_never_writes_unless_persisting`.

Contexto de despliegue: `docs/backend.md` contempla explícitamente un despliegue
WSGI, y por eso los puntos marcados "solo WSGI" siguen abiertos en vez de
descartados. La app tal como se ejecuta hoy (`app.run` en 127.0.0.1, monoproceso,
sin `threaded=True`) no los alcanza.

## 12. Capa de view-model de incubación — hallazgos auditados y no corregidos (JD-5)

La auditoría JD-5 revisó la capa que traduce el veredicto del motor a lo que ve el
usuario: `_build_incubation_dashboard`, las secciones de datos de referencia y el
parseo/validación de su formulario. Los ocho defectos que podían mostrar una
conclusión distinta a la que calculó el motor —o borrar datos obligatorios en
silencio— se corrigieron. Lo que sigue quedó registrado y no tocado.

- **Las secciones MC y SPP no se pueden vaciar desde el formulario**. `mc_*` y
  `spp` se guardan con `payload or existing`, así que si el usuario borra a
  propósito una sección para eliminar datos cargados por error, el dict vacío es
  falsy y los valores viejos reviven. El guardado no avisa nada y esos datos
  siguen alimentando veredictos que el usuario cree haber quitado. El único
  camino de borrado hoy es el botón Delete, que elimina la entrada entera. Es el
  reverso exacto de C1: ahí el problema era que el backtest **no** se preservaba;
  acá es que MC/SPP se preservan cuando no deberían. Cerrarlo bien pide distinguir
  "campo no enviado" de "campo vaciado a propósito", que es un cambio de contrato
  del formulario, no un parche.
- **`incubation_reference_data` marca "datos cargados" sin MC50**. La lista usa un
  chequeo propio (`has_bt and (manip.confidence_95 or retest.confidence_95)`) que
  duplica a mano la regla de `reference_ready()`. Por eso una entrada sin MC50
  aparece como completa y recién se convierte en SIN DATOS cuando la EA llega a 20
  trades y el motor exige las claves `mc50.*`. Además trata como alternativas
  (OR) las dos secciones MC95 que AGENTS.md declara obligatorias por separado. El
  arreglo natural es llamar a `reference_ready()` —ya importado en el archivo— pero
  eso cambia qué EAs figuran como listas en la UI y conviene decidirlo con datos
  reales a la vista.
- **La columna "Días" usa otro reloj que el motor**. El dashboard calcula los días
  con `days_since_first_trade()` (primer trade cerrado) mientras el motor cuenta
  desde `date_added` (design C7). Para una EA agregada hace 60 días cuyo primer
  trade cerró hace 5, la fila muestra 5 al lado de un veredicto que el motor
  calculó con `days_incubating = 60`, y la página de estrategia muestra las dos
  cifras a la vez. La evaluación ya trae `days_incubating`; unificar es fácil, pero
  cambia una cifra visible en todas las filas y merece confirmarse antes.
- **Claves legacy y helpers duplicados**. `entry.get("mc_manipulation") or
  entry.get("monte_carlo")` está copiado textual en tres lugares
  (`incubation_reference_data`, `..._edit`, `..._save`), y cada guardado sigue
  reescribiendo la clave vieja `monte_carlo` por compatibilidad. `fmt_dt` está
  definido tres veces: las copias de `incubation_strategy` y `strategy` son
  idénticas, y la del dashboard live derivó (le falta la rama `isinstance(...,
  datetime)`). Hoy no rompe nada porque el cache guarda fechas como strings ISO
  (AGENTS.md), pero es deriva esperando pasar.
- **Contadores muertos en el view-model**. `_build_incubation_dashboard` calcula
  `observar_count`, `continuar_count` y `pending_count`, pero la ruta solo pasa
  cinco contadores a la plantilla y esos tres no se renderizan. `pending_count`
  además mezcla dos significados: EAs sin datos de referencia y EAs con veredicto
  PENDING.

## 13. Frontend — hallazgos auditados y no corregidos (JD-6)

La auditoría JD-6 revisó `templates/*.html` y `static/charts.js` con dos jueces
ciegos (`style.css` quedó fuera de alcance). Se corrigieron el escape XSS del
chip de correlación, el colapso `0 or ''` en `validator_input.html`, el
formulario de borrado anidado dentro del de guardado, la contradicción de la
leyenda CP2, el Jinja crudo dentro de manejadores de eventos JS inline, los
gráficos que quedaban obsoletos con rangos vacíos, la carrera de respuestas
fuera de orden, el PF rolling en 0.0 renderizado como hueco, el "Score: None",
el centinela de PF en 1e9 y la leyenda de veredicto que omitía los overrides
duros. Lo que sigue quedó registrado y no tocado.

- **"+ Agregar EA" fue eliminado, no arreglado**. El botón en
  `templates/validator.html` apuntaba a
  `url_for('validator_edit', magic='nuevo')`; `validator_edit` indexa el store
  exclusivamente por el parámetro de ruta de la URL
  (`store[str(magic)] = new_entry`, `ea_analyzer.py:2483`), y el formulario no
  tiene ningún campo `magic`, así que todo lo que el usuario tipeaba caía en
  `validator_store.json['nuevo']` y nunca lo mostraba la tabla del validador
  (que solo busca entradas del store por el magic entero de un mapping).
  Verificado: cero inputs `name="magic"` en el formulario. Como JD-6 estaba
  acotada a frontend, el punto de entrada roto se **eliminó** para que no siga
  comiéndose datos en silencio.

  **Para destrabar**: `validator_edit` tiene que leer el magic de un campo del
  formulario (`request.form.get('magic')`) y validarlo contra los mappings
  existentes; recién ahí pueden volver el botón y un input de magic. La
  funcionalidad de "agregar EA a mano" hoy está **ausente** de la UI — es un
  trade-off deliberado y reversible, y es el punto más importante de esta
  sección.

- **Los puntos de color del sidebar nunca se pintan**
  (`templates/base.html:109-111`). La plantilla lee `ea.color` para
  `--ea-color` y el fondo del punto, pero `build_sidebar_eas`
  (`ea_analyzer.py:495-510`) solo emite `name`/`label`/`url`/`active` — `color`
  nunca está en el dict, así que el punto se renderiza sin color en toda
  página con sidebar. `all_metrics` ya calcula `ea_colors`
  (`ea_analyzer.py:1743`) pero nunca lo mergea. Requiere un cambio de backend,
  fuera del alcance de JD-6.

- **Inyección de fórmulas en el export** (`templates/export.html:71-109`).
  `copyExportTable`/`downloadCSV` exportan el texto crudo de las celdas,
  incluido el nombre del EA, que viene del comment del trade en el archivo
  subido, sin sanitizar (`parser.py:551`). Un nombre que empiece con `=`, `+`,
  `-` o `@` se convierte en fórmula viva al pegarlo o abrirlo en Excel. El
  camino CSV entrecomilla pero no neutraliza el operador inicial. Se dejó
  porque el arreglo pertenece a una decisión más amplia sobre sanitizar
  nombres de EA en el borde del parser, no en cada sink por separado.

  **Para cerrarlo**: prefijar esas celdas con `'` o quitar el operador inicial
  al exportar.

- **El export copia 12 columnas pero la instrucción dice 10**
  (`templates/export.html:8-9` vs `:43-54`). La página dice "Copia estos
  valores en las columnas AC–AL" (10 columnas) mientras el botón de copiar
  copia 12 (incluye Magic y Nombre), así que seguir la instrucción al pie de
  la letra desalinea cada métrica dos columnas. Cosmético pero engañoso;
  necesita una decisión de producto sobre cuál de las dos es la correcta.

- **El ordenamiento de tablas rompe las fechas** (`static/charts.js`,
  `sortTableByColumn`). La función quita `[$%,+∞]` y hace `parseFloat` de la
  celda, así que una fecha `dd/mm/yyyy HH:MM`
  (`strategy.html:386-387`, `incubation_strategy.html:497-498`, formateada por
  `ea_analyzer.py:1850-1858`) se interpreta como su día del mes —
  `02/09/2025` ordena antes que `16/03/2025`. Las celdas `∞` y `N/A` además
  caen a un `localeCompare` contra celdas numéricas, un comparador
  inconsistente.

  **Para cerrarlo**: emitir valores ISO en atributos `data-*` y ordenar sobre
  esos.

- **Los dos selectores de rango de `strategy.html` mienten**
  (`templates/strategy.html:453-458`, lo mismo en
  `incubation_strategy.html:563-568`). Los gráficos de equity y drawdown
  tienen cada uno su propio selector de rango, pero ambos callbacks
  re-renderizan LOS DOS gráficos (`renderEAEquity` escribe en los divs de
  equity y de dd), mientras que la clase "activo" solo se setea en el
  selector clickeado (`charts.js`). Al clickear 7D en uno cambian los dos
  gráficos, pero el otro selector sigue marcando ALL — su propio estado
  activo contradice a su propio gráfico. Necesita una decisión: un selector
  compartido, o dos genuinamente independientes.

- **`if (!res.ok) return;` se traga todos los errores HTTP** (`static/charts.js`,
  ~9 sitios de llamada). Un fetch fallido o con sesión expirada deja el
  gráfico en blanco u obsoleto sin nada mostrado al usuario; el propio
  `400 "No hay datos cargados"` del backend (`ea_analyzer.py:1986-1988`)
  nunca se muestra. JD-6 arregló el caso de DATOS vacíos (ahora hay un mensaje
  explícito "sin datos en este rango") pero dejó deliberadamente el caso de
  ERROR vacío, porque un buen arreglo necesita una decisión consistente de
  superficie de error para todos los gráficos.

- **Umbrales de negocio duplicados en las plantillas**. Los dos jueces lo
  marcaron de forma independiente. Las tarjetas explicativas hardcodean
  constantes que viven en el motor: los cortes de color de SQN 2.0/1.6
  (`dashboard.html:126-142`, `strategy.html:130-146`,
  `incubation_strategy.html:263-268` vs `metrics.py:77-85`); los umbrales de
  trades de checkpoint 5/20/40 y los cortes de CP3 65/45
  (`incubation_strategy.html:44-49,75-76` e
  `incubation_dashboard.html:88-153` vs `incubation_domain.py:353-360`,
  `incubation_validator.py:830-835`); los hard gates "DD > MC95 × 1.5" y
  "p < 0.03" (`incubation_validator.py:342-346`). Todos coinciden HOY con el
  motor — JD-6 verificó cada uno — pero nada los mantiene sincronizados. La
  leyenda de CP2 que corrigió JD-6 es exactamente esta clase de bug ya
  habiendo derivado una vez, que es la razón por la que esto queda en la
  lista.

  **✅ RESUELTO EN PARTE (fix 4D)**: un context processor
  `inject_display_thresholds` (`ea_analyzer.py`) inyecta un dict `TH` como
  fuente única. Los cortes de veredicto del validador (70/45) se **leen en vivo**
  de `validator.CONFIG` (no pueden derivar); los gates de checkpoint (5/20/40) se
  reflejan en `TH` y quedan **pinneados** al comportamiento real de
  `get_checkpoint_for_trades` por un test anti-drift
  (`test_frontend_contracts.py::test_injected_thresholds_match_the_engine`); los
  cortes de color SQN 2.0/1.6 y PF 1.5/1.0 (cosméticos, sin equivalente en el
  motor) quedan definidos una sola vez en `TH`. Las plantillas colorean por
  `TH.*` (`dashboard.html`, `strategy.html`, `validator.html`,
  `incubation_strategy.html`). **No se tocó el motor** (respeta "motor = fuente
  de verdad"): las constantes siguen en su módulo y el test garantiza la sync.

  **Queda fuera de 4D** (follow-up): los cortes CP3 65/45, el `DD > MC95 × 1.5`
  y el `p < 0.03` son constantes de decisión del motor que **no aparecen como
  lógica de color en ninguna plantilla** — solo como prosa en las tarjetas
  explicativas de `incubation_dashboard.html`; y el `120/30` del ratio live-vs-BT
  en `validator.html`, cuyo color ya viene del motor (`live_vs_bt_profit_status`)
  y solo duplica el umbral en el texto de una nota. Migrar la prosa explicativa
  y ese texto de nota es cosmético y de menor valor.

- **RESUELTO** — la banda "50%" del ratio live-vs-BT que no existía en el
  código (`templates/validator.html`, tarjeta de info) se corrigió: la
  leyenda inventaba un límite "Aceptable/Degradación" en 50% e invertía la
  señal (calificaba 50-70% como "Aceptable" cuando el motor levanta ALERTA
  ahí), y además omitía el caso `>120 → ALERTA`. La tarjeta ahora lista las
  cuatro bandas reales (`70-120 OK`, `30-70 ALERTA`, `<30 FUERA`, `>120
  ALERTA`, `validator.py`) y la fila vestigial 50/30 de
  `docs/metrics-formulas.md` §16 (que probablemente originó el invento) se
  eliminó.

- **Campos que el backend manda y el frontend ignora**. `ea.label` se envía
  pero la tabla de EAs del dashboard renderiza el `ea.name` crudo
  (`dashboard.html:313` vs `ea_analyzer.py:1753`) — inconsistente con el
  sidebar y los gráficos, que usan el alias. `streak_data[].color` se envía y
  se recalcula del lado del cliente (`charts.js:475`). `long_wins`/`short_wins`
  se envían y nunca se usan. `observar_count`/`continuar_count`/`pending_count`
  se calculan y nunca se pasan a la plantilla (ya registrado en §12).

- **Menores verificados**: `templates/incubation_reference_data.html` es
  código muerto — ninguna ruta lo renderiza (el endpoint
  `incubation_reference_data` renderiza `incubation_reference.html`,
  `ea_analyzer.py:1332-1340`). `validator.html:409` usa `colspan="11"` en una
  tabla de 12 columnas. `incubation_strategy.html:444` renderiza el timestamp
  ISO crudo con microsegundos mientras toda otra fecha de la página es
  `dd/mm/yyyy`. `base.html:10,183` hace cache-busting de css/js con
  `?v={{ range(10000,99999)|random }}`, regenerado en cada request, lo que
  anula el cacheo del navegador de forma permanente. `charts.js:586`
  hardcodea la etiqueta del eje "Hora (UTC)" mientras las horas vienen de
  datetimes naive del servidor del broker sin ninguna conversión de zona
  horaria en todo el pipeline — la etiqueta afirma una zona horaria que el
  dato no garantiza (no se puede probar solo con el código; hace falta el
  offset del broker).
  También: `static/style.css:3489-3494` sigue cargando las reglas
  `.val-add-bt-link`, ya muertas, que dejó la eliminación del punto 1 —
  `style.css` estaba fuera del alcance de JD-6.

## 14. Oráculo ejecutable (P-A) — hallazgos revelados y NO corregidos

Arnés en `tests/oracle/` (8 archivos, ~3.500 líneas): caracterización +
propiedades (Hypothesis) + diferencial contra oráculo independiente
(`empyrical` para Sharpe, `scipy.stats.binom.cdf` para el binomial, oráculos
derivados a mano para el resto). El arnés nació observando sin arreglar, pero la
mayoría de los defectos que reveló ya se cerraron en este batch. Suite: **667
passed, 1 xfailed** — el único `xfail(strict=True)` que queda es la divergencia
deliberada del cap de SQN (A4, decisión de política), no un bug abierto.

Nota de alcance: la premisa de partida ("validator.py, incubation_validator.py
y la API pública de metrics.py tienen cero cobertura") era **falsa** —
`calculate_validator_score` ya tenía 17 llamadas en tests, `evaluate_cp3` 15,
`get_all_validator_results` 8. La única con cero cobertura real era
`calculate_portfolio_metrics`. Lo que sí faltaba por completo era la capa de
propiedades y la de oráculo diferencial.

### A. Puede cambiar un veredicto

- **A1 — CP3 muestra un score y decide con otro. ✅ CORREGIDO (fix 4A).**
  Antes: `evaluate_cp3` resolvía el veredicto con el `final_score` SIN redondear
  mientras publicaba `round(final_score, 2)`, así que un crudo 64.998 daba
  `OBSERVAR` pero la UI mostraba **65.0** (banda de `APROBAR`). Fix: se canoniza
  sobre el valor publicado — se redondea una vez (`published_score`) y el
  veredicto se decide con ese mismo valor, de modo que número mostrado y
  veredicto no pueden contradecirse. El gemelo latente en `validator.py`
  (sección E, hoy inalcanzable por la grilla exacta) recibió el mismo blindaje.
  Efecto en el veredicto: `[64.995, 65)` ahora es `APROBAR` (si no `below_mc95`),
  un corrimiento de borde de hasta 0.005 aceptado explícitamente por el usuario.
  Tests: `test_cp3_score_display_matches_verdict_at_65_boundary`,
  `test_cp3_rounded_vs_raw_score_split_at_65_is_closed`.
- **A2 — un `net_pnl` NaN desaparece de la contabilidad. ✅ RESUELTO.** Antes
  `metrics.py` partía con `p > 0` / `p <= 0`; ambas son `False` para NaN, así que
  el trade no caía en ninguna partición: `winning_trades + losing_trades <
  total_trades` y su P&L se evaporaba de `net_profit` sin error ni SIN DATOS.
  Ahora `calculate_ea_metrics` detecta los `net_pnl` no finitos de entrada,
  calcula sobre el subconjunto finito y expone un contador `non_finite_trades`,
  de modo que la reconciliación cierra: `winning + losing + breakeven +
  non_finite == total` (`metrics.py:772-776`, `:920-921`).
- **A3 — `payout_ratio` inflado ~2x por trades de P&L cero. ✅ RESUELTO** vía el
  cambio de política de exclusión del breakeven (ref. cruzada §7). Antes la
  partición `p <= 0` contaba los ceros como pérdidas y achicaba `avg_loss`,
  inflando el payout (-80/4 = -20 en vez de -80/2 = -40 → payout 5.0 en vez de
  2.5). Ahora el breakeven se excluye de ambas particiones (`metrics.py:781-783`).
  **Ojo**: esto **cambió veredictos** sobre EAs con trades breakeven —decisión de
  política aprobada— y `docs/metrics-formulas.md` §10 se actualizó para
  reflejarlo.
- **A4 — SQN sin el cap de Van Tharp.** El repo usa `sqrt(n)*mean/std`; el
  estándar es `sqrt(min(n,100))*mean/std`. Con n=150: 1.769 vs 1.444 — cruza la
  frontera de `SQN_LABELS` en 1.6, o sea "Debajo promedio" contra "Pobre". El
  código y su propia doc coinciden en no tener el cap (no es un bug nuevo), pero
  la etiqueta que ve el operador se aparta del estándar de la industria para
  muestras grandes.

### B. Crashes (fallan fuerte, no mienten)

- **B1 — ✅ RESUELTO.** `total_trades` ahora pasa por `_finite_or_none` como el
  resto de los campos `live`, así que un string no numérico ya no lanza
  `ValueError`: coacciona a float finito o `None` (`validator.py:180-184`).
- **B2 — ✅ RESUELTO.** `trades_live` se normaliza a finito-o-`None` antes de
  cualquier `int()`, de modo que `int(trades_live)` nunca ve un NaN/±inf
  (`validator.py:183`, `:223`).
- **B3 — ✅ RESUELTO.** El **`ZeroDivisionError` por underflow** (hallazgo nuevo de
  Hypothesis) ahora está guardado: se chequea que el cociente
  `bt_trades/bt_months` (y `weeks_live/4.33`) sea finito y `> 0` **después** de
  dividir, no solo los operandos por separado, y si hace underflow a `0.0` se
  devuelve `SIN DATOS` nombrando la referencia en vez de explotar
  (`validator.py:457-467`, `:562-563`). La lección —**`x > 0 and y > 0` no
  garantiza `x / y > 0`**— quedó codificada en la guarda del cociente.
- **B4 — ✅ RESUELTO.** `incubation_validator._safe_int` ahora captura
  `OverflowError` junto a `TypeError`/`ValueError`, así que
  `_safe_int(float("inf"))` cae al default en vez de propagar
  (`incubation_validator.py:49-57`).
- **B5 — ✅ RESUELTO.** `_wins_from_metrics` detecta el `win_rate` no finito antes
  de `int(round(...))`, evitando el `ValueError`/`OverflowError` cuando falta
  `winning_trades` (`incubation_validator.py:121-129`).
- **B6 — ✅ RESUELTO.** Acceso defensivo al dict de trade en `metrics.py`: un
  `net_pnl`/`close_time` faltante o un `net_pnl` no numérico ya no propaga
  `KeyError`/`TypeError`; el trade se cuenta como no finito y se descarta de la
  partición en vez de reventar (`metrics.py:105-106`, `:634-636`, `:685`).

### C. Datos silenciosamente equivocados

- **C1 — ✅ RESUELTO.** Todos los trades sin fecha daban curva de equity vacía →
  drawdown **0.0** reportado sobre pérdidas reales. Ahora una curva vacía con
  trades reales (todos untimed) da `max_dd_pct = None` (SIN DATOS) en vez del
  0.0 silencioso (`metrics.py:838-842`).
- **C2 — ✅ RESUELTO.** `capital <= 0` daba `dd_pct` **0.0** en silencio; ahora da
  `max_dd_pct = None` (SIN DATOS), siguiendo el precedente del bootstrap
  (`metrics.py:285-286`, `:324`). Mismo hallazgo que el bullet de §7.
- **C3 — ✅ RESUELTO.** Una fecha de pico malformada devolvía **0** días de
  `_calc_stagnation` —el mejor valor posible salido de un fallo de parseo—; ahora
  el fallo de parseo se declara como ausencia en vez de un 0 confiado
  (`metrics.py:411`).
- **C4 — ✅ RESUELTO.** En `_hard_gates`, un `expected_monthly == 0` (no `None`) no
  satisfacía ni la rama `is None` ni la `> 0` y la frecuencia quedaba en **"OK"**;
  ahora el `0.0` literal se trata como inusable igual que `None`
  (`incubation_validator.py:417-421`).
- **C5 — ✅ RESUELTO.** Es el mismo hallazgo que §6: `_nd_result` ahora blanquea
  **todos** los numéricos derivados de la fila, no solo `dd_limit`. Ver §6.
- **C6** — Zona ALERTA vacía cuando `mc_retest.max_dd == mc_trades.max_dd`
  (§4, quirk conocido y deliberado).
- **C7 — ✅ RESUELTO.** `live_vs_bt_profit_ratio` (antes `wfe`) redondeaba **antes**
  de bandear (120.04 → 120.0 → `OK` en vez de `ALERTA`); ahora se bandea sobre el
  valor crudo y recién después se redondea para mostrar
  (`validator.py:826-845`).
- **C8 — ✅ RESUELTO.** Contratos de forma incompatibles: las formas PRE_CP1
  ELIMINAR/PENDING **omitían** `mc_source`, que la forma SIN DATOS sí traía como
  `{}`; ahora `mc_source` está presente en todas las formas de `evaluate_cp1`
  (`incubation_validator.py:629`, `:651`).

### D. Documentación que contradice al código

- **D1** — `metrics-formulas.md:386-391` afirma que el p-valor binomial usa
  `scipy.binom.cdf` con fallback a aproximación normal. **No existe**: el código
  es `math.comb` exacto, sin scipy y sin fallback. Un oráculo construido desde
  la doc diverge 0.05–0.17 en N chico (wins=2,n=5,p=0.5 → código 0.500, doc
  0.327). El código está bien; **la doc está mal**.
- **D2** — `decision-logic.md:155-160` documenta la frecuencia como una cola;
  el código es de **dos colas** (`validator.py:444-447`). Con freq_pct=413% la
  doc dice `OK` y el código dice `FUERA`.
- **D3** — `metrics-formulas.md:297` dice que el reloj arranca en el primer
  trade; el código prioriza `date_added` (`incubation_validator.py:100-115`).
- **D4** — `decision-logic.md:331` dice "cuatro referencias" y enumera tres.
- **D5** — `decision-logic.md:128` documenta solo las bandas OK del payout;
  las de ALERTA y el corto-circuito `payout >= 1e9 → OK` no están.

### E. Hipótesis refutada por el arnés

- El split de redondeo score/veredicto **no se puede alcanzar en `validator.py`**
  (sí en CP3, ver A1). Se sospechaba que un score crudo de 69.96 podía
  publicarse como 70.0 con veredicto `MONITOREAR`. Enumerando con `Fraction`
  las 701 combinaciones alcanzables de `(s_riesgo, s_edge, s_caracter, s_desv)`
  — recordando que los tres primeros son sumas ponderadas de sub-scores, no
  valores de `{0,5,10}` — el score crudo cae en una grilla exacta de múltiplos
  de 0.125. El punto inmediatamente inferior a 70 es **69.875**, que redondea a
  69.9: mismo lado que la comparación. El intervalo `[69.95, 70)` está vacío, y
  el `[44.95, 45)` también, así que ninguna de las dos bandas puede partirse.
  Pinneado como caracterización, no como defecto
  (`test_char_validator.py:585`).

  **La estructura del defecto SÍ estaba** — hoy **✅ BLINDADA (fix 4A)**:
  `validator.py` publicaba `round(score, 1)` mientras decidía con el `score`
  crudo, el mismo patrón que en CP3. No explotaba sólo porque la grilla no tiene
  puntos en el intervalo peligroso, con un margen de **un paso de grilla
  (0.125)** — una coincidencia aritmética, no un diseño, que cualquier cambio
  futuro en los pesos de `CONFIG` podía romper. El fix canoniza sobre el valor
  publicado: `score` se redondea una sola vez y el veredicto se decide con ese
  mismo valor, así que el hueco no puede abrirse aunque la grilla se mueva.
  Ningún veredicto cambia hoy (la grilla vigente no toca las bandas de borde).
