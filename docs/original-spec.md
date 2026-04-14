# ============================================================
# PROMPT PARA CLAUDE CODE - EA ANALYZER & VALIDATOR
# ============================================================
# Copiar TODO este texto como prompt inicial en Claude Code.
# Adjuntar tambien el archivo: ReportHistory-4000084439.xlsx
# ============================================================

## CONTEXTO DEL PROYECTO

Soy Ramiro. Opero Expert Advisors (EAs) en DarwinexZero usando MT5 y StrategyQuant X (SQX).
Actualmente tengo 10 EAs corriendo en vivo por 4 semanas. En el futuro tendre 20-25+.
Necesito una herramienta para:

1. Parsear el historial de trades exportado de MT5 (formato .xlsx)
2. Calcular todas las metricas de rendimiento por cada EA agrupando trades por estrategia
3. Mostrar resultados en una web local con curvas de equity, tablas y metricas
4. Exportar los datos en formato listo para copiar a mi Excel EA Validator

La herramienta reemplaza a QuantAnalyzer (que dejo de funcionar) como procesador de datos de MT5.

IMPORTANTE: Soportar numero ILIMITADO de EAs. No hardcodear limites.

---

## ARQUITECTURA REQUERIDA

Script Python local que al ejecutarse levanta un servidor web local (Flask o similar).
- python ea_analyzer.py abre localhost:5000 en el navegador
- Interfaz web donde subo el archivo .xlsx de MT5
- Todo se procesa server-side en Python
- Frontend con HTML/CSS/JS usando Plotly.js para graficos
- Dependencias minimas: Flask, openpyxl, pandas, numpy

---

## IDENTIFICACION DE EAs: MAGIC NUMBER + COMMENT

### El problema
MT5 muestra el Magic Number internamente en la pestana History/Deals, pero al exportar
a .xlsx o .html, NO incluye la columna Magic Number. Solo exporta el campo Comment
que contiene el nombre de la estrategia asignado por SQX.

### La solucion
El parser agrupa trades usando el campo Comment del export. Pero la interfaz muestra
el Magic Number como identificador principal. Para esto, la herramienta incluye una
PANTALLA DE MAPEO donde el usuario asigna el Magic Number a cada estrategia detectada.

### Flujo completo:
1. Usuario sube archivo .xlsx de MT5
2. Parser detecta todas las estrategias unicas del campo Comment
3. Se muestra PANTALLA DE MAPEO con:
   - Lista de todos los Comments detectados (ej: "Strategy_2_4_347")
   - Campo de input para asignar Magic Number a cada uno (ej: "22219")
   - El instrumento detectado automaticamente al lado
   - Si ya existe un config.json previo, los Magic Numbers se pre-llenan automaticamente
   - Boton "Guardar y Continuar" al Dashboard
4. El mapeo se guarda en config.json en la carpeta del proyecto
5. En TODA la interfaz (graficas, tablas, sidebar, exportacion) cada EA se muestra como:
   "22219 - Strategy_2_4_347" (Magic Number primero, nombre de estrategia despues)
6. Cuando el usuario suba un nuevo archivo en el futuro, el mapeo previo se carga
   automaticamente. Solo necesita mapear los EAs nuevos que no existan en el config.

### Mapeo conocido actual (para pre-llenar si se desea):
Magic 22219 = Strategy_2_4_347 (XTIUSD)
Magic 22212 = Strategy_1_23_192 (GDAXI)
Magic 22221 = Strategy_4_32_218 (GDAXI)
Magic 22207 = Strategy_4_4_189 (SP500)
Magic 11121 = Strategy_3_13_312 (USDJPY)
Magic 22209 = Strategy_3_19_105 (GBPUSD)
Magic 22203 = WF_Matrix_Strategy_2_32_150 (GBPUSD)
Magic 11120 = WF_Matrix_Strategy_3_7_88 (XAUUSD)
Magic 22218 = Strategy_2_4_518 (XTIUSD)
Magic 22201 = Strategy_2_28_179 (SP500)

### Formato del config.json:
{
  "mappings": {
    "Strategy_2_4_347": { "magic": 22219, "instrument": "XTIUSD" },
    "Strategy_1_23_192": { "magic": 22212, "instrument": "GDAXI" },
    ...
  },
  "last_file": "ReportHistory-4000084439.xlsx",
  "last_updated": "2026-03-03"
}

---

## ESTRUCTURA EXACTA DEL ARCHIVO MT5 (.xlsx)

El archivo ReportHistory-XXXXXXXX.xlsx tiene esta estructura (verificada con datos reales):

### Seccion 1: HEADER (Filas 1-5)
- Row 1: Trade History Report
- Row 2: Name: ramtraderC_MT5
- Row 3: Account: 4000084439 (USD, Darwinex-Live, real, Hedge)
- Row 4: Company: Tradeslide Trading Tech Limited
- Row 5: Date: 2026.03.02 19:09

### Seccion 2: POSITIONS (headers detectados dinamicamente)
Seccion PRINCIPAL con trades cerrados. Empieza con fila que dice "Positions", siguiente fila headers.
Columnas:
- Col 1 (A): Time (apertura) = string formato "2026.02.02 04:00:00"
- Col 2 (B): Position (ID del trade) = int
- Col 3 (C): Symbol = string (SP500, GDAXI, XTIUSD, GBPUSD, USDJPY, XAUUSD)
- Col 4 (D): Type = string ("buy" o "sell")
- Col 5 (E): Volume = string "0.01" (OJO: es string, no float!)
- Col 6 (F): Price (apertura) = float
- Col 7 (G): S/L = float (Stop Loss)
- Col 8 (H): T/P = float o None (Take Profit, muchos no tienen)
- Col 9 (I): Time (cierre) = string
- Col 10 (J): Price (cierre) = float
- Col 11 (K): Commission = float (negativo)
- Col 12 (L): Swap = float
- Col 13 (M): Profit = float (P&L sin comision ni swap)
NOTA: Esta seccion NO tiene Magic Number NI Comment. Cruzar Position ID con Orders.

### Seccion 3: ORDERS (headers detectados dinamicamente)
Empieza con fila "Orders", siguiente fila headers.
Columnas clave:
- Col 2 (B): Order (ID = mismo que Position ID de seccion Positions)
- Col 12 (L): Comment = nombre del EA (ej: "Strategy_2_28_179")
IMPORTANTE: Algunas orders tienen comments de cierre como "[sl 6939.3]" o "[sl 155.359]".
Solo usar orders cuyo Comment empiece con "Strategy_" o "WF_Matrix_Strategy_".

### Seccion 4: DEALS (headers detectados dinamicamente)
Empieza con fila "Deals".
Columnas: Time, Deal, Symbol, Type, Direction (in/out), Volume, Price, Order,
          Commission (o Cost), Fee, Swap, Profit, Balance, Comment
Contiene items que NO son trades y deben IGNORARSE:
- Dividendos: "SP500 div. 0.54 USD / lot"
- Tax refunds: "GDAXI tax ref. 7.89 EUR / lot"
- Depositos: "First deposit"
- Tipo "balance" o "dividend" en columna Type

### Seccion 5: OPEN POSITIONS
Trades abiertos. Tienen Comment directamente en Col 13.
Mostrar como info pero NO incluir en metricas de trades cerrados.

### Seccion 6: RESULTS
Resumen general de MT5. Usar para validar calculos.

CRITICO: El parser NO debe hardcodear numeros de fila. Debe detectar las secciones
buscando los textos "Positions", "Orders", "Deals", "Open Positions", "Results" en col A.

---

## ESTRATEGIAS ACTUALMENTE IDENTIFICADAS

60 trades cerrados totales + 7 posiciones abiertas, 10 estrategias unicas.
En el futuro habra 20-25+. El codigo debe manejar cualquier cantidad.

---

## METRICAS A CALCULAR POR CADA EA Y POR EL PORTAFOLIO

Todas las metricas se calculan para cada EA individual Y para el portafolio global.

### Metricas principales:
1. Trades: Total de trades cerrados
2. Semanas operando: Fecha primer trade a fecha ultimo trade
3. Win Rate (%): (Trades ganadores / Total trades) x 100
   Trade ganador = P&L neto (Profit + Commission + Swap) > 0
   Trade perdedor = P&L neto <= 0
4. Profit Factor: Sum(ganancias netas) / |Sum(perdidas netas)|
5. Payout Ratio: Average Win / |Average Loss| (neto)
6. Expectancy ($/trade): Total Net Profit / Total Trades
7. Max DD (%): Mayor caida pico-a-valle como % del equity peak
8. Max DD ($): Mayor caida pico-a-valle en dolares
9. Ret/DD Max: Total Net Profit / Max DD ($). Si DD=0 mostrar "N/A"
10. SQN Score (System Quality Number - Van Tharp):
    SQN = sqrt(N) x mean(R) / stdev(R)
    N = numero trades, R = P&L neto de cada trade
    Interpretacion: <1.6 Pobre, 1.6-1.9 Debajo promedio, 2.0-2.4 Promedio,
    2.5-2.9 Bueno, 3.0-5.0 Excelente, 5.0-6.9 Sobresaliente, 7.0+ Santo Grial
    Con <20 trades: mostrar nota "(orientativo)"
11. Duracion promedio: En horas (close_time - open_time)
12. Max Consec Losses: Mayor racha de trades perdedores consecutivos
13. Max Consec Wins: Mayor racha de trades ganadores consecutivos
14. Stagnation (dias): Dias desde ultimo maximo de equity hasta hoy

### Metricas adicionales:
- Total Net Profit, Gross Profit, Gross Loss
- Trade mayor ganador y perdedor
- Average Win y Average Loss
- Avg Consec Wins y Avg Consec Losses
- Recovery Factor (Net Profit / Max DD $)
- Trades Long y Short con Win Rate de cada uno

---

## P&L NETO POR TRADE (CRITICO)

P&L real = Profit + Commission + Swap
La columna "Profit" de MT5 es SOLO movimiento de precio, SIN comisiones ni swap.
SIEMPRE usar P&L neto para clasificar wins/losses y todas las metricas.

---

## DISENO Y UI/UX - DARK THEME PROFESIONAL

### ESTETICA: PLATAFORMA DE TRADING (TradingView, Bloomberg, cTrader)
La app debe verse profesional y dar gusto analizar los resultados en ella.

### Paleta de colores OBLIGATORIA
- Fondo principal: #0d1117 (negro azulado profundo)
- Fondo cards/paneles: #161b22
- Fondo hover/activo: #1f2937
- Bordes: #30363d (sutiles)
- Texto principal: #e6edf3 (blanco suave, NO blanco puro)
- Texto secundario: #8b949e (gris)
- Texto numeros: #c9d1d9
- Acento principal: #4FC3F7 (azul claro)
- Acento secundario: #7C4DFF (violeta)
- Profit/positivo: #4CAF50 (verde)
- Loss/negativo: #FF5252 (rojo)
- Alerta/neutro: #FFC107 (ambar)
- Tipografia: Inter, Roboto, o sans-serif limpia
- SIN fondos blancos, SIN bordes claros, SIN Bootstrap generico blanco

### PRINCIPIOS DE LAYOUT Y ESPACIADO (MUY IMPORTANTE)

La informacion debe RESPIRAR. Ni comprimida/amontonada ni dispersa/vacia.
Debe dar gusto leer y analizar los resultados.

- Padding generoso en cards: minimo 24px
- Separacion entre secciones: 32-48px de margin
- Gap entre cards de KPIs: 16-24px
- Graficos GRANDES: altura minima 450px para los principales
- Tablas: filas con 44-48px de altura, padding 12-16px
- Texto: line-height 1.5-1.6, font-size base 14-15px
- Titulos de seccion: claros, 24px de margin-bottom
- Scroll natural, cada seccion con espacio propio
- Max-width contenido: 1400px centrado
- Numeros en tablas: font monospace para mejor alineacion
- Alternar color de filas ligeramente para legibilidad

### Libreria de graficos: Plotly.js
Config dark theme para TODOS los graficos:
layout = {
  paper_bgcolor: '#0d1117',
  plot_bgcolor: '#161b22',
  font: { color: '#e6edf3', family: 'Inter, sans-serif' },
  xaxis: { gridcolor: '#30363d', linecolor: '#30363d' },
  yaxis: { gridcolor: '#30363d', linecolor: '#30363d' },
  legend: { bgcolor: 'rgba(0,0,0,0)', font: { color: '#8b949e' } },
  margin: { t: 40, r: 30, b: 50, l: 60 }
}

### Paleta para curvas de EAs (distinguibles en fondo oscuro):
#4FC3F7 (azul), #FF7043 (naranja), #66BB6A (verde), #AB47BC (purpura),
#FFA726 (ambar), #26C6DA (cyan), #EC407A (rosa), #8D6E63 (marron),
#78909C (gris azul), #D4E157 (lima), #5C6BC0 (indigo), #FF8A65 (salmon)
Curva PORTAFOLIO GLOBAL: #FFFFFF (blanco) con grosor 3px.
Si hay mas de 12 EAs: generar colores con HSL distribuido uniformemente.

### Navegacion
- Sidebar fijo izquierdo, ancho 240px, fondo #0d1117, borde derecho #30363d
- Logo/titulo "EA Analyzer" arriba
- Info: cuenta, fecha reporte, total EAs
- Links: Dashboard, separador, cada EA como "22219 - Strategy_2_4_347", separador, Exportar
- EA activo: fondo #1f2937, borde izquierdo #4FC3F7
- Sidebar con scroll si muchos EAs

---

### PAGINA 1: UPLOAD (inicio al abrir la app)

Diseno minimalista centrado:
- Titulo grande "EA Analyzer" en #4FC3F7
- Subtitulo: "Sube tu reporte de MT5 para analizar tus Expert Advisors"
- Zona drag-and-drop (300x200px min) con borde punteado #30363d
- Texto: "Arrastra tu archivo .xlsx aqui" + icono upload
- Boton alternativo "O selecciona un archivo"
- Spinner al subir: "Procesando X trades de Y estrategias..."
- Al terminar: redirect a Pantalla de Mapeo

---

### PAGINA 2: PANTALLA DE MAPEO DE MAGIC NUMBERS (despues del upload)

Se muestra SIEMPRE despues de subir un archivo (incluso si ya hay config previo).
Permite verificar y ajustar el mapeo.

Diseno:
- Titulo: "Configurar Magic Numbers"
- Subtitulo: "Asigna el Magic Number de MT5 a cada estrategia detectada"
- Tabla editable con columnas:
  | # | Comment detectado | Instrumento | Trades | Magic Number [input] |
  Ejemplo de fila:
  | 1 | Strategy_2_4_347 | XTIUSD | 6 | [22219] |
- Si config.json existe, los Magic Numbers se pre-llenan automaticamente
- Los EAs nuevos (sin mapeo previo) resaltados con borde amarillo
- Input numerico para el Magic Number
- Boton grande "Guardar y Ver Dashboard"
- Al guardar: actualizar config.json y redirigir al Dashboard
- Opcion: "Saltar (usar nombres de estrategia)" por si no tiene los Magic Numbers a mano

---

### PAGINA 3: DASHBOARD DEL PORTAFOLIO (pagina principal)

#### Seccion 1: Header
- Titulo: "Dashboard del Portafolio"
- Info: cuenta, periodo, total EAs, total trades

#### Seccion 2: KPIs en cards (flex horizontal, gap 20px)
Cards en una o dos filas, con espacio entre ellas:
- Net Profit ($): verde/rojo segun signo
- Total Trades
- Win Rate (%): con barra de progreso visual
- Profit Factor
- Max DD (%)
- Ret/DD Max
- SQN Score: con etiqueta de interpretacion
- Recovery Factor
Cada card: valor grande (24-28px), etiqueta arriba (11-12px gris),
fondo #161b22, borde #30363d, border-radius 8px, padding 24px

#### Seccion 3: GRAFICO EQUITY CURVES [PRIORIDAD VISUAL #1]
El grafico MAS IMPORTANTE de toda la aplicacion. GRANDE y prominente.
- Titulo: "Curvas de Equity"
- UN solo grafico con TODAS las curvas superpuestas:
  - Linea GRUESA (3px) BLANCA: PORTAFOLIO GLOBAL, visible por defecto
  - Lineas DELGADAS (1.5px) semi-transparentes: cada EA, OCULTAS por defecto
  - Leyenda interactiva Plotly: click en nombre = show/hide curva
  - Nombres en leyenda: "22219 - Strategy_2_4_347" (Magic + nombre)
  - Tooltip hover: fecha, equity portafolio, equity EAs visibles
  - Eje X: fechas. Eje Y: equity $ (desde $100,000)
  - Zoom interactivo (drag zoom, doble click reset)
- Tamano: 100% ancho, 500px altura minimo
- Nota debajo: "Click en la leyenda para mostrar/ocultar curvas individuales"

#### Seccion 4: GRAFICO DRAWDOWN [PRIORIDAD VISUAL #2]
- Titulo: "Drawdown"
- Mismo concepto interactivo:
  - Areas/lineas invertidas (DD como valores negativos)
  - Portafolio BLANCO grueso visible por defecto
  - EAs ocultos, activables via leyenda
  - Eje X: fechas. Eje Y: DD en % (negativos)
  - Zoom interactivo
- Tamano: 100% ancho, 350px min

#### Seccion 5: Tabla resumen de EAs
- Titulo: "Resumen por Estrategia"
- Fila por EA: Magic | Nombre | Instrumento | Trades | WinRate% | PF | Payout |
  Expect$ | MaxDD% | Ret/DD | SQN | ConsecLoss | NetProfit$
- Ordenable por columna (click header)
- Colores condicionales: verde/rojo segun metricas
- Click en nombre = navega a detalle del EA
- Fila final bold: TOTAL PORTAFOLIO
- Font monospace para numeros, filas alternas para legibilidad

#### Seccion 6: Contribucion por EA
- Titulo: "Contribucion al Resultado"
- Barras horizontales: contribucion por EA al Net Profit ($)
- Verde profit, rojo loss, ordenado mayor a menor
- Labels: "22219 - Strategy_2_4_347: +$XX.XX"

---

### PAGINA 4: DETALLE POR EA (click en tabla o sidebar)

#### Header
- "22219 - Strategy_2_4_347" grande, instrumento, trades, periodo
- Boton "< Volver al Dashboard"

#### KPIs cards (misma estetica, para este EA)
Net Profit, Trades, WinRate%, PF, MaxDD%, Ret/DD, SQN, Payout, Expectancy

#### Equity curve individual
- Ancho 100%, altura 450px
- Linea equity + zona sombreada de drawdown

#### Drawdown chart individual
- Areas: DD en %, altura 300px

#### Tabla de trades
- Todos los trades:
  # | Apertura | Cierre | Tipo | Volume | Precio In | Precio Out | SL | TP |
  Comision | Swap | P&L Neto | Duracion (hrs)
- Ordenable, verde/rojo sutil por fila
- Fila final con totales/promedios

#### Histograma P&L
- Eje X: rangos $. Eje Y: frecuencia
- Barras verdes/rojas, 300px altura

#### Rachas wins/losses
- Barras verticales en secuencia temporal
- Verde arriba = win (altura proporcional), rojo abajo = loss
- Visualiza rachas de forma intuitiva

---

### PAGINA 5: EXPORTACION PARA EA VALIDATOR

#### Tabla de exportacion
Fila por EA:
Magic | Nombre | Trades(AC) | Semanas(AD) | WinRate%(AE) | PF(AF) | Payout(AG) |
Expect$(AH) | MaxDD%(AI) | DurProm(AJ) | ConsecLoss(AK) | StagnDias(AL)
(Letras entre parentesis = columna destino en Excel EA Validator)
Numeros con 2 decimales

#### Botones
- "Copiar tabla" = clipboard tab-separated para pegar en Excel
- "Descargar CSV"
- Feedback visual: "Copiado!" temporal

#### Nota
"Estos valores corresponden a las columnas AC-AL de la hoja Datos del
archivo EA Validator. Pegalos en las filas correspondientes a cada EA."

---

## EDGE CASES Y CONSIDERACIONES TECNICAS

1. Volume es string en xlsx: convertir a float
2. Fechas son strings "2026.02.02 04:00:00": parsear a datetime
3. Dividendos/tax refunds en Deals: filtrar (sin Symbol, o "div."/"tax ref." en comment, o type="dividend"/"balance")
4. Orders de cierre: comments "[sl 6939.3]": ignorar, solo usar "Strategy_*" o "WF_Matrix_*"
5. Posiciones abiertas: mostrar pero NO incluir en metricas
6. Equity curve: empezar con $100,000 (balance DarwinexZero) + acumular P&L neto cronologicamente
7. Max DD%: max(peak - valley) / peak x 100
8. SQN con <20 trades: calcular pero mostrar "(orientativo)"
9. Ret/DD Max: si DD$=0, mostrar "N/A"
10. Colores curvas: generar dinamicamente con HSL si hay mas de 12 EAs
11. Config.json: crear en carpeta del proyecto, persistente entre sesiones
12. Encoding HTML: utf-16 (por si se implementa parser HTML en futuro)

---

## ESTRUCTURA DEL PROYECTO

ea_analyzer/
  ea_analyzer.py          # Entry point + Flask server
  parser.py               # Parser del xlsx de MT5
  metrics.py              # Calculo de metricas (SQN, Ret/DD, etc)
  config.json             # Mapeo Magic Number <-> Comment (auto-generado)
  templates/
    base.html             # Template base: sidebar + dark theme + Plotly CDN
    upload.html            # Pagina de upload
    mapping.html           # Pantalla de mapeo Magic Numbers
    dashboard.html         # Dashboard portafolio
    strategy.html          # Detalle por EA
    export.html            # Exportacion EA Validator
  static/
    style.css              # Dark theme completo
    charts.js              # Config y helpers Plotly
  requirements.txt         # Flask, openpyxl, pandas, numpy
  test_data/
    ReportHistory-4000084439.xlsx

---

## VERIFICACION

Archivo de prueba adjunto: ReportHistory-4000084439.xlsx
Resultados esperados del portafolio:
- Total Trades: 60
- Net Profit: aprox -$431.45 (validar contra seccion Results)
- Estrategias unicas: 10
- Balance inicial: $100,000
- Win Rate global: 35% (21 wins / 60 trades segun Results)
- Max Consec Losses global: 12 (segun Results del xlsx)

---

## PRIORIDADES DE IMPLEMENTACION

1. Parser correcto del xlsx (detectar secciones, identificar estrategias, cruzar Position-Order)
2. Calculo correcto de TODAS las metricas (WinRate, PF, DD, Ret/DD, SQN, ConsecLoss, etc)
   Validar contra totales de seccion Results del xlsx
3. Pantalla de mapeo Magic Numbers funcional con persistencia en config.json
4. Graficos equity curves y drawdown con toggles interactivos (PRIORIDAD VISUAL)
5. Dashboard con KPIs espaciados, tabla ordenable, contribucion por EA
6. Paginas de detalle por EA con graficos y tabla de trades
7. Exportacion para EA Validator Excel
8. Pulir estetica: espaciado generoso, colores, tipografia, que de gusto usarla
