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

## 11. Backend Flask — hallazgos auditados y no corregidos (JD-4)

Los cuatro puntos siguientes los reportó **un solo** juez en la auditoría JD-4 del
backend Flask. El protocolo solo corrige lo que dos jueces confirman, así que
quedaron registrados en vez de aplicados. No son especulativos: el código hace lo
que se describe. Lo que falta es la corroboración independiente que justifique
tocarlos.

- **`GET /incubation/strategy/<ea_name>` escribe en disco**. Cuando la referencia
  está completa, la ruta persiste el resultado de la evaluación
  (`store[ea_name] = bundle["entry"]; save_incubation_store(store)`) durante un
  simple GET. Un GET que muta estado es re-disparable por prefetchers del
  navegador y por cualquier recarga. Además es un read-modify-write del store
  entero sin lock: bajo WSGI, dos vistas concurrentes se pisan las escrituras
  —incluidas las de **otros** EAs que viven en el mismo archivo—. Verificado a
  nivel de código; retenido por falta de segundo juez.
- **Carrera de nombres en `uploads/` (solo WSGI)**. Todas las subidas caen en una
  carpeta compartida con el nombre que devuelve `secure_filename`. Los exports de
  MT5 suelen traer el mismo nombre por default, así que dos usuarios concurrentes
  compiten entre `file.save()` y `parse_mt5_report()`: uno puede parsear el
  archivo del otro. En el uso local monousuario no aplica. Los archivos subidos,
  además, no se limpian nunca.
- **`cache_key` se interpola en rutas de archivo sin validar**. `_cache_file_path`
  y sus pares construyen `f"{prefix}{cache_key}.json"` sin filtrar separadores.
  Hoy la clave siempre es un UUID generado por el servidor y explotarlo exige
  falsificar la cookie de sesión, o sea que es defensa en profundidad, no un
  agujero abierto. Vale como cinturón si el `.secret_key` alguna vez se filtra.
- **Carrera del `.secret_key` al importar (solo WSGI multi-worker)**. El bootstrap
  es un check-then-write sin `O_EXCL`: dos workers pueden generar y escribir
  claves distintas, o uno puede leer el archivo a medio escribir. Resultado:
  firmas de sesión inconsistentes y usuarios perdiendo la sesión de forma
  intermitente. El entrypoint que se envía es monoproceso, así que no aplica al
  uso documentado.

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
  `incubation_strategy.html:263-268` vs `metrics.py:37-45`); los umbrales de
  trades de checkpoint 5/20/40 y los cortes de CP3 65/45
  (`incubation_strategy.html:44-49,75-76` e
  `incubation_dashboard.html:88-153` vs `incubation_domain.py:353-360`,
  `incubation_validator.py:830-835`); los hard gates "DD > MC95 × 1.5" y
  "p < 0.03" (`incubation_validator.py:342-346`). Todos coinciden HOY con el
  motor — JD-6 verificó cada uno — pero nada los mantiene sincronizados. La
  leyenda de CP2 que corrigió JD-6 es exactamente esta clase de bug ya
  habiendo derivado una vez, que es la razón por la que esto queda en la
  lista.

  **Para cerrarlo**: pasar los umbrales desde el backend al contexto de la
  plantilla.

- **La banda "50%" del WFE no existe en el código**
  (`templates/validator.html:480-485`). La tarjeta de info de WFE inventa un
  límite "Aceptable/Degradación" en 50% y omite el caso `>120 → ALERTA`,
  mientras que las bandas reales son `>=70 OK`, `30–70 ALERTA`, `<30 FUERA`,
  `>120 ALERTA` (`validator.py:673-681`). La leyenda no se corrigió porque la
  redacción de la tarjeta necesita una decisión de producto, no solo un
  cambio de números.

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
