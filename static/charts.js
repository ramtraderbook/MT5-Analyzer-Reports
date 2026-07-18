/* ============================================================
   charts.js - Plotly helpers for EA Analyzer
   All charts use the dark theme layout defined below.
   ============================================================ */

// ─── Shared Plotly config ───────────────────────────────────────────────────

const PLOTLY_LAYOUT = {
  paper_bgcolor: "#0d1117",
  plot_bgcolor: "#161b22",
  font: {
    color: "#e6edf3",
    family: "'Inter', -apple-system, sans-serif",
    size: 12,
  },
  xaxis: {
    gridcolor: "#30363d",
    linecolor: "#30363d",
    zerolinecolor: "#30363d",
    tickfont: { color: "#8b949e", size: 11 },
  },
  yaxis: {
    gridcolor: "#30363d",
    linecolor: "#30363d",
    zerolinecolor: "#30363d",
    tickfont: { color: "#8b949e", size: 11 },
  },
  legend: {
    bgcolor: "rgba(0,0,0,0)",
    font: { color: "#8b949e", size: 11 },
    bordercolor: "#30363d",
    borderwidth: 1,
  },
  margin: { t: 40, r: 24, b: 48, l: 72 },
  hovermode: "x unified",
  hoverlabel: {
    bgcolor: "#161b22",
    bordercolor: "#30363d",
    font: { color: "#e6edf3", size: 12 },
  },
};

const PLOTLY_CONFIG = {
  responsive: true,
  displayModeBar: true,
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
  displaylogo: false,
  toImageButtonOptions: { format: "png", scale: 2, filename: "ea_chart" },
};

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Per-div request-generation token to discard stale async responses when
// range-selector clicks fire concurrent requests (last to ARRIVE would
// otherwise win regardless of click order).
const _chartReqSeq = {};

function _nextChartReq(divId) {
  _chartReqSeq[divId] = (_chartReqSeq[divId] || 0) + 1;
  return _chartReqSeq[divId];
}

function _isStaleChartReq(divId, token) {
  return _chartReqSeq[divId] !== token;
}

// Plotly inserts its .plot-container as :first-child and leaves foreign
// siblings untouched, so an empty-state message injected earlier would stay
// visible next to a chart that now has data. Drop it before plotting.
function _clearChartEmptyMsg(divId) {
  const el = document.getElementById(divId);
  if (!el) return;
  el.querySelectorAll(".chart-empty-msg").forEach((n) => n.remove());
}

function mergeLayout(overrides) {
  return Object.assign(
    {},
    PLOTLY_LAYOUT,
    overrides,
    overrides.xaxis
      ? { xaxis: Object.assign({}, PLOTLY_LAYOUT.xaxis, overrides.xaxis) }
      : {},
    overrides.yaxis
      ? { yaxis: Object.assign({}, PLOTLY_LAYOUT.yaxis, overrides.yaxis) }
      : {},
  );
}

// ─── Dashboard Charts ───────────────────────────────────────────────────────

async function renderEquityCurves(divId, days = null) {
  const _req = _nextChartReq(divId);
  try {
    const url = days ? "/api/equity_curves?days=" + days : "/api/equity_curves";
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    if (_isStaleChartReq(divId, _req)) return;
    if (!data.traces || data.traces.length === 0) {
      Plotly.purge(divId);
      const el = document.getElementById(divId);
      if (el)
        el.innerHTML =
          '<div class="chart-empty-msg">Sin datos en este rango.</div>';
      return;
    }

    const traces = data.traces.map((t) => ({
      x: t.x,
      y: t.y,
      name: t.name,
      type: "scatter",
      mode: "lines",
      line: {
        color: t.color,
        width: t.width,
        shape: "linear",
      },
      visible: t.visible,
      hovertemplate: t.is_portfolio
        ? "<b>%{fullData.name}</b><br>%{x}<br>$%{y:,.2f}<extra></extra>"
        : "%{fullData.name}: $%{y:,.2f}<extra></extra>",
    }));

    const layout = mergeLayout({
      height: document.getElementById(divId).clientHeight || 500,
      yaxis: {
        tickprefix: "$",
        tickformat: "+,.0f",
        zeroline: true,
        zerolinecolor: "#8b949e",
        zerolinewidth: 1,
        title: { text: "P&L Neto (USD)", font: { color: "#8b949e", size: 11 } },
      },
      xaxis: {
        type: "date",
        title: { text: "", font: { color: "#8b949e", size: 11 } },
      },
    });

    _clearChartEmptyMsg(divId);
    Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
  } catch (e) {
    console.error("Error rendering equity curves:", e);
  }
}

async function renderDrawdownCurves(divId, days = null) {
  const _req = _nextChartReq(divId);
  try {
    const url = days
      ? "/api/drawdown_curves?days=" + days
      : "/api/drawdown_curves";
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    if (_isStaleChartReq(divId, _req)) return;
    if (!data.traces || data.traces.length === 0) {
      Plotly.purge(divId);
      const el = document.getElementById(divId);
      if (el)
        el.innerHTML =
          '<div class="chart-empty-msg">Sin datos en este rango.</div>';
      return;
    }

    const traces = data.traces.map((t) => ({
      x: t.x,
      y: t.y,
      name: t.name,
      type: "scatter",
      mode: "lines",
      fill: t.is_portfolio ? "tozeroy" : "none",
      fillcolor: t.is_portfolio ? "rgba(255,82,82,0.08)" : "transparent",
      line: {
        color: t.color,
        width: t.width,
        shape: "linear",
      },
      visible: t.visible,
      hovertemplate: "%{fullData.name}: %{y:.2f}%<extra></extra>",
    }));

    const layout = mergeLayout({
      height: document.getElementById(divId).clientHeight || 320,
      yaxis: {
        ticksuffix: "%",
        zeroline: true,
        zerolinecolor: "#8b949e",
        zerolinewidth: 1,
        title: { text: "Drawdown (%)", font: { color: "#8b949e", size: 11 } },
      },
      xaxis: { type: "date" },
    });

    _clearChartEmptyMsg(divId);
    Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
  } catch (e) {
    console.error("Error rendering drawdown curves:", e);
  }
}

async function renderContribution(divId) {
  try {
    const res = await fetch("/api/contribution");
    if (!res.ok) return;
    const data = await res.json();
    if (!data.labels || data.labels.length === 0) return;

    const traces = [
      {
        x: data.values,
        y: data.labels,
        type: "bar",
        orientation: "h",
        marker: {
          color: data.colors,
          opacity: 0.85,
        },
        text: data.values.map((v) => (v >= 0 ? "+" : "") + "$" + v.toFixed(2)),
        textposition: "outside",
        textfont: {
          color: "#c9d1d9",
          size: 11,
          family: "'Roboto Mono', monospace",
        },
        hovertemplate: "<b>%{y}</b><br>%{x:+.2f} USD<extra></extra>",
      },
    ];

    const layout = mergeLayout({
      height: document.getElementById(divId).clientHeight || 360,
      xaxis: {
        tickprefix: "$",
        tickformat: ",.0f",
        zeroline: true,
        zerolinecolor: "#8b949e",
        zerolinewidth: 1,
      },
      yaxis: { automargin: true },
      margin: { t: 20, r: 80, b: 40, l: 24 },
    });

    Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
  } catch (e) {
    console.error("Error rendering contribution chart:", e);
  }
}

// ─── Strategy Detail Charts ─────────────────────────────────────────────────

async function _renderEAEquityFromApi(equityDivId, ddDivId, eaName, days, apiBase) {
  const _reqKey = equityDivId + "|" + ddDivId;
  const _req = _nextChartReq(_reqKey);
  try {
    const encoded = encodeURIComponent(eaName);
    const url = days ? apiBase + encoded + "?days=" + days : apiBase + encoded;
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    if (_isStaleChartReq(_reqKey, _req)) return;

    if (!data.equity || data.equity.length === 0) {
      [equityDivId, ddDivId].forEach((id) => {
        Plotly.purge(id);
        const el = document.getElementById(id);
        if (el)
          el.innerHTML =
            '<div class="chart-empty-msg">Sin datos en este rango.</div>';
      });
      return;
    }

    const equityX = data.equity.map((p) => p.date);
    const equityY = data.equity.map((p) => p.equity);
    const ddX = data.drawdown.map((p) => p.date);
    const ddY = data.drawdown.map((p) => p.dd_pct);

    // Equity chart with drawdown fill
    const equityTraces = [
      {
        x: equityX,
        y: equityY,
        name: data.label,
        type: "scatter",
        mode: "lines",
        line: { color: "#4FC3F7", width: 2 },
        fill: "tozeroy",
        fillcolor: "rgba(79,195,247,0.06)",
        hovertemplate: "%{x}<br>$%{y:,.2f}<extra></extra>",
      },
    ];

    const equityLayout = mergeLayout({
      height: document.getElementById(equityDivId).clientHeight || 450,
      yaxis: {
        tickprefix: "$",
        tickformat: "+,.2f",
        zeroline: true,
        zerolinecolor: "#8b949e",
        zerolinewidth: 1,
        title: { text: "P&L Neto (USD)", font: { color: "#8b949e", size: 11 } },
      },
      xaxis: { type: "date" },
      showlegend: false,
    });

    _clearChartEmptyMsg(equityDivId);
    Plotly.newPlot(equityDivId, equityTraces, equityLayout, PLOTLY_CONFIG);

    // Drawdown chart
    const ddTraces = [
      {
        x: ddX,
        y: ddY,
        name: "Drawdown",
        type: "scatter",
        mode: "lines",
        line: { color: "#FF5252", width: 1.5 },
        fill: "tozeroy",
        fillcolor: "rgba(255,82,82,0.12)",
        hovertemplate: "%{x}<br>%{y:.2f}%<extra></extra>",
      },
    ];

    const ddLayout = mergeLayout({
      height: document.getElementById(ddDivId).clientHeight || 280,
      yaxis: {
        ticksuffix: "%",
        zeroline: true,
        zerolinecolor: "#8b949e",
        zerolinewidth: 1,
        title: { text: "Drawdown (%)", font: { color: "#8b949e", size: 11 } },
      },
      xaxis: { type: "date" },
      showlegend: false,
    });

    _clearChartEmptyMsg(ddDivId);
    Plotly.newPlot(ddDivId, ddTraces, ddLayout, PLOTLY_CONFIG);
  } catch (e) {
    console.error("Error rendering EA equity chart:", e);
  }
}

function renderEAEquityFromData(equityDivId, ddDivId, data) {
  if (!data || !data.equity || data.equity.length === 0) return false;

  const equityX = data.equity.map((p) => p.date);
  const equityY = data.equity.map((p) => p.equity);
  const ddX = data.drawdown.map((p) => p.date);
  const ddY = data.drawdown.map((p) => p.dd_pct);

  const equityTraces = [
    {
      x: equityX,
      y: equityY,
      name: data.label || "Incubation",
      type: "scatter",
      mode: "lines",
      line: { color: data.color || "#4FC3F7", width: 2 },
      fill: "tozeroy",
      fillcolor: "rgba(79,195,247,0.06)",
      hovertemplate: "%{x}<br>$%{y:,.2f}<extra></extra>",
    },
  ];

  const equityLayout = mergeLayout({
    height: document.getElementById(equityDivId)?.clientHeight || 450,
    yaxis: {
      tickprefix: "$",
      tickformat: "+,.2f",
      zeroline: true,
      zerolinecolor: "#8b949e",
      zerolinewidth: 1,
      title: { text: "P&L Neto (USD)", font: { color: "#8b949e", size: 11 } },
    },
    xaxis: { type: "date" },
    showlegend: false,
  });

  Plotly.newPlot(equityDivId, equityTraces, equityLayout, PLOTLY_CONFIG);

  const ddTraces = [
    {
      x: ddX,
      y: ddY,
      name: "Drawdown",
      type: "scatter",
      mode: "lines",
      line: { color: "#FF5252", width: 1.5 },
      fill: "tozeroy",
      fillcolor: "rgba(255,82,82,0.12)",
      hovertemplate: "%{x}<br>%{y:.2f}%<extra></extra>",
    },
  ];

  const ddLayout = mergeLayout({
    height: document.getElementById(ddDivId)?.clientHeight || 280,
    yaxis: {
      ticksuffix: "%",
      zeroline: true,
      zerolinecolor: "#8b949e",
      zerolinewidth: 1,
      title: { text: "Drawdown (%)", font: { color: "#8b949e", size: 11 } },
    },
    xaxis: { type: "date" },
    showlegend: false,
  });

  Plotly.newPlot(ddDivId, ddTraces, ddLayout, PLOTLY_CONFIG);
  return true;
}

async function renderEAEquity(equityDivId, ddDivId, eaName, days = null) {
  return _renderEAEquityFromApi(equityDivId, ddDivId, eaName, days, "/api/ea_equity/");
}

async function renderIncubationEAEquity(equityDivId, ddDivId, eaName, days = null) {
  return _renderEAEquityFromApi(
    equityDivId,
    ddDivId,
    eaName,
    days,
    "/api/incubation/ea_equity/",
  );
}

async function _loadEAPnLDataFromApi(eaName, apiBase) {
  try {
    const encoded = encodeURIComponent(eaName);
    const res = await fetch(apiBase + encoded);
    if (!res.ok) return;
    const data = await res.json();

    renderPnLHistogram("pnl-histogram", data.pnl_list);
    renderStreakChart("streak-chart", data.streak_data);
    renderWeekdayChart("weekday-chart", data.weekday_pnl);
    renderHourChart("hour-chart", data.hour_pnl);
    renderLongShortPie("longshort-chart", data.long_short);
    renderDurationScatter("duration-scatter-chart", data.duration_scatter);
  } catch (e) {
    console.error("Error loading EA P&L data:", e);
  }
}

async function loadEAPnLData(eaName) {
  return _loadEAPnLDataFromApi(eaName, "/api/ea_pnl_data/");
}

async function loadIncubationEAPnLData(eaName) {
  try {
    const encoded = encodeURIComponent(eaName);
    const res = await fetch("/api/incubation/ea_pnl_data/" + encoded);
    if (!res.ok) return;
    const data = await res.json();

    renderPnLHistogram("inc-pnl-histogram", data.pnl_list);
    renderWeekdayHourHeatmap("inc-weekday-hour-heatmap", data.weekday_hour_heatmap);
    renderLongShortPie("inc-longshort-chart", data.long_short);
    renderDurationScatter("inc-duration-scatter-chart", data.duration_scatter);
  } catch (e) {
    console.error("Error loading incubation P&L data:", e);
  }
}

function renderIncubationDistributionFromData(data) {
  if (!data) return false;
  renderPnLHistogram("inc-pnl-histogram", data.pnl_list);
  renderWeekdayHourHeatmap("inc-weekday-hour-heatmap", data.weekday_hour_heatmap);
  renderLongShortPie("inc-longshort-chart", data.long_short);
  renderDurationScatter("inc-duration-scatter-chart", data.duration_scatter);
  return true;
}

function renderPnLHistogram(divId, pnlList) {
  if (!pnlList || pnlList.length === 0) return;

  const wins = pnlList.filter((v) => v > 0);
  const losses = pnlList.filter((v) => v <= 0);

  const traces = [];
  if (wins.length > 0) {
    traces.push({
      x: wins,
      name: "Wins",
      type: "histogram",
      marker: {
        color: "rgba(76,175,80,0.75)",
        line: { color: "#4CAF50", width: 1 },
      },
      hovertemplate: "$%{x:.2f}<br>%{y} trades<extra></extra>",
    });
  }
  if (losses.length > 0) {
    traces.push({
      x: losses,
      name: "Losses",
      type: "histogram",
      marker: {
        color: "rgba(255,82,82,0.75)",
        line: { color: "#FF5252", width: 1 },
      },
      hovertemplate: "$%{x:.2f}<br>%{y} trades<extra></extra>",
    });
  }

  const layout = mergeLayout({
    title: { text: "Distribución P&L", font: { color: "#c9d1d9", size: 13 } },
    height: document.getElementById(divId).clientHeight || 300,
    barmode: "overlay",
    xaxis: {
      type: "linear",
      tickprefix: "$",
      tickformat: ".2f",
      title: { text: "P&L Neto ($)", font: { color: "#8b949e", size: 11 } },
    },
    yaxis: {
      type: "linear",
      title: { text: "Frecuencia", font: { color: "#8b949e", size: 11 } },
    },
    margin: { t: 40, r: 16, b: 48, l: 52 },
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

function renderStreakChart(divId, streakData) {
  if (!streakData || streakData.length === 0) return;

  const x = streakData.map((d) => d.index);
  const y = streakData.map((d) => d.pnl);
  const colors = streakData.map((d) =>
    d.pnl > 0 ? "rgba(76,175,80,0.8)" : "rgba(255,82,82,0.8)",
  );

  const traces = [
    {
      x: x,
      y: y,
      type: "bar",
      marker: { color: colors },
      hovertemplate: "Trade #%{x}<br>$%{y:+.2f}<extra></extra>",
    },
  ];

  const layout = mergeLayout({
    title: { text: "Rachas Win/Loss", font: { color: "#c9d1d9", size: 13 } },
    height: document.getElementById(divId).clientHeight || 300,
    xaxis: {
      type: "linear",
      title: { text: "Trade #", font: { color: "#8b949e", size: 11 } },
      tickformat: "d",
      dtick: Math.max(1, Math.floor(streakData.length / 10)),
    },
    yaxis: {
      type: "linear",
      tickprefix: "$",
      tickformat: "+.2f",
      zeroline: true,
      zerolinecolor: "#8b949e",
      zerolinewidth: 1,
      title: { text: "P&L Neto ($)", font: { color: "#8b949e", size: 11 } },
    },
    margin: { t: 40, r: 16, b: 48, l: 64 },
    showlegend: false,
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

function renderWeekdayChart(divId, weekdayPnl) {
  if (!weekdayPnl || weekdayPnl.length === 0) return;
  const labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
  const colors = weekdayPnl.map((v) =>
    v >= 0 ? "rgba(76,175,80,0.82)" : "rgba(255,82,82,0.82)",
  );

  const traces = [
    {
      x: labels,
      y: weekdayPnl,
      type: "bar",
      marker: {
        color: colors,
        line: { color: colors.map((c) => c.replace("0.82", "1")), width: 1 },
      },
      hovertemplate: "<b>%{x}</b><br>$%{y:+.2f}<extra></extra>",
    },
  ];

  const layout = mergeLayout({
    title: {
      text: "P/L por Día de Semana",
      font: { color: "#c9d1d9", size: 13 },
    },
    height: document.getElementById(divId)?.clientHeight || 300,
    xaxis: { type: "category" },
    yaxis: {
      tickprefix: "$",
      tickformat: "+,.0f",
      zeroline: true,
      zerolinecolor: "#8b949e",
      zerolinewidth: 1,
      title: { text: "P&L Neto ($)", font: { color: "#8b949e", size: 11 } },
    },
    margin: { t: 40, r: 16, b: 40, l: 64 },
    showlegend: false,
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

function renderHourChart(divId, hourPnl) {
  if (!hourPnl || hourPnl.length === 0) return;
  const labels = Array.from({ length: 24 }, (_, i) => i);
  const colors = hourPnl.map((v) =>
    v >= 0 ? "rgba(76,175,80,0.82)" : "rgba(255,82,82,0.82)",
  );

  const traces = [
    {
      x: labels,
      y: hourPnl,
      type: "bar",
      marker: {
        color: colors,
        line: { color: colors.map((c) => c.replace("0.82", "1")), width: 1 },
      },
      hovertemplate: "<b>%{x}:00h</b><br>$%{y:+.2f}<extra></extra>",
    },
  ];

  const layout = mergeLayout({
    title: {
      text: "P/L por Hora de Cierre",
      font: { color: "#c9d1d9", size: 13 },
    },
    height: document.getElementById(divId)?.clientHeight || 300,
    xaxis: {
      type: "linear",
      tickformat: "d",
      dtick: 2,
      title: { text: "Hora (UTC)", font: { color: "#8b949e", size: 11 } },
    },
    yaxis: {
      tickprefix: "$",
      tickformat: "+,.0f",
      zeroline: true,
      zerolinecolor: "#8b949e",
      zerolinewidth: 1,
      title: { text: "P&L Neto ($)", font: { color: "#8b949e", size: 11 } },
    },
    margin: { t: 40, r: 16, b: 48, l: 64 },
    showlegend: false,
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

function renderLongShortPie(divId, data) {
  if (!data || data.long_count + data.short_count === 0) return;

  const total = data.long_count + data.short_count;
  const longPct = ((data.long_count / total) * 100).toFixed(1);
  const shortPct = ((data.short_count / total) * 100).toFixed(1);

  const traces = [
    {
      labels: [`Long (${data.long_count})`, `Short (${data.short_count})`],
      values: [data.long_count, data.short_count],
      type: "pie",
      hole: 0.0,
      marker: {
        colors: ["#3D6EBF", "#C0392B"],
        line: { color: "#0d1117", width: 2 },
      },
      textinfo: "label+percent",
      textfont: { color: "#e6edf3", size: 12 },
      hovertemplate:
        "<b>%{label}</b><br>%{value} trades (%{percent})<br>P&L: $%{customdata:+.2f}<extra></extra>",
      customdata: [data.long_pnl, data.short_pnl],
      pull: [0.03, 0.03],
    },
  ];

  const layout = mergeLayout({
    title: {
      text: "Long vs Short Trades",
      font: { color: "#c9d1d9", size: 13 },
    },
    height: document.getElementById(divId)?.clientHeight || 300,
    showlegend: false,
    margin: { t: 40, r: 16, b: 16, l: 16 },
    hovermode: "closest",
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

function renderDurationScatter(divId, scatterData) {
  if (!scatterData || scatterData.length === 0) return;

  const wins = scatterData.filter((d) => d.win);
  const losses = scatterData.filter((d) => !d.win);

  const traces = [];
  if (wins.length > 0) {
    traces.push({
      x: wins.map((d) => d.x),
      y: wins.map((d) => d.y),
      name: "Ganadora",
      type: "scatter",
      mode: "markers",
      marker: {
        color: "rgba(76,175,80,0.75)",
        size: 8,
        line: { color: "#4CAF50", width: 1 },
      },
      hovertemplate:
        "<b>Win</b><br>Duración: %{x:.1f}h<br>P&L: $%{y:+.2f}<extra></extra>",
    });
  }
  if (losses.length > 0) {
    traces.push({
      x: losses.map((d) => d.x),
      y: losses.map((d) => d.y),
      name: "Perdedora",
      type: "scatter",
      mode: "markers",
      marker: {
        color: "rgba(255,82,82,0.75)",
        size: 8,
        line: { color: "#FF5252", width: 1 },
      },
      hovertemplate:
        "<b>Loss</b><br>Duración: %{x:.1f}h<br>P&L: $%{y:+.2f}<extra></extra>",
    });
  }

  const layout = mergeLayout({
    title: {
      text: "Duración vs P&L por Trade",
      font: { color: "#c9d1d9", size: 13 },
    },
    height: document.getElementById(divId)?.clientHeight || 300,
    xaxis: {
      type: "linear",
      title: { text: "Duración (horas)", font: { color: "#8b949e", size: 11 } },
    },
    yaxis: {
      tickprefix: "$",
      tickformat: "+,.2f",
      zeroline: true,
      zerolinecolor: "#8b949e",
      zerolinewidth: 1,
      title: { text: "P&L Neto ($)", font: { color: "#8b949e", size: 11 } },
    },
    margin: { t: 40, r: 16, b: 48, l: 72 },
    hovermode: "closest",
    legend: { x: 0.01, y: 0.99, xanchor: "left", yanchor: "top" },
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

function renderWeekdayHourHeatmap(divId, heatmap) {
  if (!heatmap || !heatmap.z || heatmap.z.length === 0) return;

  const traces = [
    {
      z: heatmap.z,
      x: heatmap.x,
      y: heatmap.y,
      type: "heatmap",
      colorscale: [
        [0, "#FF5252"],
        [0.5, "#2d333b"],
        [1, "#4CAF50"],
      ],
      zmid: 0,
      hovertemplate: "<b>%{y}</b> · %{x}:00<br>$%{z:+.2f}<extra></extra>",
      colorbar: {
        tickprefix: "$",
        title: "P&L",
      },
    },
  ];

  const layout = mergeLayout({
    title: {
      text: "P&L por Día / Hora",
      font: { color: "#c9d1d9", size: 13 },
    },
    height: document.getElementById(divId)?.clientHeight || 320,
    xaxis: {
      type: "category",
      title: { text: "Hora de cierre", font: { color: "#8b949e", size: 11 } },
    },
    yaxis: {
      type: "category",
      autorange: "reversed",
      title: { text: "Día", font: { color: "#8b949e", size: 11 } },
    },
    margin: { t: 40, r: 24, b: 48, l: 56 },
  });

  Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
}

async function loadPortfolioAnalytics() {
  try {
    const res = await fetch("/api/portfolio_analytics");
    if (!res.ok) return;
    const data = await res.json();

    renderPnLHistogram("port-pnl-histogram", data.pnl_list);
    renderStreakChart("port-streak-chart", data.streak_data);
    renderWeekdayChart("port-weekday-chart", data.weekday_pnl);
    renderHourChart("port-hour-chart", data.hour_pnl);
    renderLongShortPie("port-longshort-chart", data.long_short);
    renderDurationScatter("port-duration-scatter-chart", data.duration_scatter);
  } catch (e) {
    console.error("Error loading portfolio analytics:", e);
  }
}

// ─── Table Sorting ──────────────────────────────────────────────────────────

function makeSortable(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;

  const headers = table.querySelectorAll("th[data-sort]");
  headers.forEach((th) => {
    th.addEventListener("click", () => {
      const dir = th.dataset.dir === "asc" ? "desc" : "asc";
      headers.forEach((h) => {
        h.dataset.dir = "";
        h.classList.remove("sort-asc", "sort-desc");
      });
      th.dataset.dir = dir;
      th.classList.add(dir === "asc" ? "sort-asc" : "sort-desc");

      const colIdx = Array.from(th.parentNode.children).indexOf(th);
      sortTableByColumn(table, colIdx, dir);
    });
  });
}

function sortTableByColumn(table, colIdx, dir) {
  const tbody = table.querySelector("tbody");
  if (!tbody) return;

  const rows = Array.from(tbody.querySelectorAll("tr"));
  rows.sort((a, b) => {
    const aCell = a.cells[colIdx];
    const bCell = b.cells[colIdx];
    const aText = (aCell?.textContent || "").trim();
    const bText = (bCell?.textContent || "").trim();

    // Prefer an explicit sort key (e.g. ISO dates on dd/mm/yyyy cells) so the
    // raw display text does not get misparsed by the numeric fallback below.
    const aSortVal = aCell?.dataset?.sortValue;
    const bSortVal = bCell?.dataset?.sortValue;
    if (aSortVal !== undefined && bSortVal !== undefined) {
      // Only treat the sort key as numeric when the WHOLE trimmed value is a
      // finite number. parseFloat() would happily read the leading digits of an
      // ISO-8601 timestamp ("2024-03-01T..." -> 2024) and never return NaN, so
      // same-year dates would tie and never sort chronologically. ISO strings
      // must fall through to a string compare, whose lexicographic order is
      // already chronological order.
      const isNumericKey = (v) => {
        const s = String(v).trim();
        return s !== "" && Number.isFinite(Number(s));
      };
      let cmpSort;
      if (isNumericKey(aSortVal) && isNumericKey(bSortVal)) {
        cmpSort = Number(aSortVal) - Number(bSortVal);
      } else {
        cmpSort = String(aSortVal).localeCompare(String(bSortVal));
      }
      return dir === "asc" ? cmpSort : -cmpSort;
    }

    // Try numeric
    const aNum = parseFloat(aText.replace(/[$%,+∞]/g, "").trim());
    const bNum = parseFloat(bText.replace(/[$%,+∞]/g, "").trim());

    let cmp;
    if (!isNaN(aNum) && !isNaN(bNum)) {
      cmp = aNum - bNum;
    } else {
      cmp = aText.localeCompare(bText);
    }

    return dir === "asc" ? cmp : -cmp;
  });

  rows.forEach((r) => tbody.appendChild(r));
}

// ─── Time Range Selector ────────────────────────────────────────────────────

function renderTimeRangeSelector(selectorId, onChangeFn) {
  const container = document.getElementById(selectorId);
  if (!container) return;

  const ranges = [
    { label: "7D", days: 7 },
    { label: "14D", days: 14 },
    { label: "1M", days: 30 },
    { label: "3M", days: 90 },
    { label: "6M", days: 180 },
    { label: "1Y", days: 365 },
    { label: "ALL", days: null },
  ];

  container.className = "time-range-selector";

  ranges.forEach(function (r) {
    const btn = document.createElement("button");
    btn.className = "trs-btn" + (r.days === null ? " active" : "");
    btn.textContent = r.label;
    btn.dataset.days = r.days !== null ? r.days : "";
    btn.addEventListener("click", function () {
      container.querySelectorAll(".trs-btn").forEach(function (b) {
        b.classList.remove("active");
      });
      btn.classList.add("active");
      onChangeFn(r.days);
    });
    container.appendChild(btn);
  });
}

// ─── Correlation Matrix ─────────────────────────────────────────────────────

async function renderCorrelationMatrix(divId) {
  try {
    const res = await fetch("/api/correlation");
    if (!res.ok) return;
    const data = await res.json();

    if (data.error) {
      const el = document.getElementById(divId);
      if (el)
        el.innerHTML =
          '<div class="chart-empty-msg">' +
          (data.error === "need_2_eas"
            ? "Se necesitan al menos 2 EAs activos para calcular correlación."
            : data.error === "insufficient_data"
              ? "Datos insuficientes para calcular correlación."
              : "No hay datos.") +
          "</div>";
      return;
    }

    const labels = data.labels;
    const matrix = data.matrix;
    const n = labels.length;

    // Plotly heatmap — invertir Y para que diagonal vaya top-left a bottom-right
    const z = matrix.slice().reverse();
    const yLabels = labels.slice().reverse();

    // Texto en cada celda
    const textMatrix = z.map((row) => row.map((v) => v.toFixed(2)));

    const trace = {
      type: "heatmap",
      x: labels,
      y: yLabels,
      z: z,
      text: textMatrix,
      texttemplate: "%{text}",
      textfont: { size: n > 8 ? 9 : 11, color: "#ffffff" },
      colorscale: [
        [0.0, "#B71C1C"], // -1  rojo intenso
        [0.35, "#FF5252"], // -0.3
        [0.5, "#1a1f2e"], // 0   neutro oscuro
        [0.65, "#66BB6A"], // +0.3
        [0.85, "#43A047"], // +0.7
        [1.0, "#1B5E20"], // +1  verde intenso
      ],
      zmin: -1,
      zmax: 1,
      showscale: true,
      colorbar: {
        title: { text: "ρ", font: { color: "#8b949e", size: 11 } },
        tickfont: { color: "#8b949e", size: 10 },
        thickness: 12,
        len: 0.8,
      },
      hovertemplate: "<b>%{x}</b> × <b>%{y}</b><br>ρ = %{z:.3f}<extra></extra>",
    };

    const height = Math.max(280, Math.min(520, n * 48 + 60));

    const layout = mergeLayout({
      height,
      margin: { t: 20, r: 80, b: 120, l: 120 },
      xaxis: {
        tickangle: -35,
        tickfont: { size: n > 8 ? 9 : 10, color: "#c9d1d9" },
        side: "bottom",
      },
      yaxis: {
        tickfont: { size: n > 8 ? 9 : 10, color: "#c9d1d9" },
        automargin: true,
      },
    });

    _clearChartEmptyMsg(divId);
    Plotly.newPlot(divId, [trace], layout, PLOTLY_CONFIG);

    // Renderizar alertas de alta correlación
    if (data.high_corr_pairs && data.high_corr_pairs.length > 0) {
      const alertDiv = document.getElementById(divId + "-alerts");
      if (alertDiv) {
        alertDiv.innerHTML = data.high_corr_pairs
          .map(
            (p) =>
              '<span class="corr-alert-chip">' +
              "⚠ " +
              escapeHtml(p.ea1) +
              " × " +
              escapeHtml(p.ea2) +
              " <strong>ρ=" +
              p.corr.toFixed(2) +
              "</strong></span>",
          )
          .join("");
      }
    } else {
      const alertDiv = document.getElementById(divId + "-alerts");
      if (alertDiv) {
        alertDiv.innerHTML =
          '<span class="corr-ok-chip">✓ Sin pares con correlación alta (&gt; 0.7)</span>';
      }
    }
  } catch (e) {
    console.error("Error rendering correlation matrix:", e);
  }
}

// ─── Rolling Metrics Chart ──────────────────────────────────────────────────

async function renderRollingMetrics(divId, eaName, window) {
  const _req = _nextChartReq(divId);
  try {
    const encoded = encodeURIComponent(eaName);
    const url =
      "/api/rolling_metrics/" + encoded + (window ? "?window=" + window : "");
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    if (_isStaleChartReq(divId, _req)) return;

    const el = document.getElementById(divId);
    if (!el) return;

    if (data.insufficient || !data.rolling || data.rolling.length === 0) {
      el.innerHTML =
        '<div class="chart-empty-msg">Historial insuficiente para métricas rodantes (mínimo 10 trades). Actualmente: ' +
        (data.total_trades || 0) +
        " trades.</div>";
      return;
    }

    const x = data.rolling.map((p) => p.date);
    const expectancy = data.rolling.map((p) => p.expectancy);
    const winRate = data.rolling.map((p) => p.win_rate);
    const pf = data.rolling.map((p) => p.pf);

    const traces = [
      {
        x,
        y: expectancy,
        name: "Expectancy",
        type: "scatter",
        mode: "lines",
        line: { color: "#4FC3F7", width: 2, shape: "spline" },
        yaxis: "y",
        hovertemplate: "Trade #%{text}<br>Expectancy: $%{y:.2f}<extra></extra>",
        text: data.rolling.map((p) => p.index),
      },
      {
        x,
        y: winRate,
        name: "Win Rate %",
        type: "scatter",
        mode: "lines",
        line: { color: "#66BB6A", width: 1.5, shape: "spline", dash: "dot" },
        yaxis: "y2",
        hovertemplate: "Trade #%{text}<br>WR: %{y:.1f}%<extra></extra>",
        text: data.rolling.map((p) => p.index),
      },
      {
        x,
        y: pf.map((v) => v ?? null),
        name: "Profit Factor",
        type: "scatter",
        mode: "lines",
        line: { color: "#FFA726", width: 1.5, shape: "spline", dash: "dash" },
        yaxis: "y2",
        hovertemplate: "Trade #%{text}<br>PF: %{y:.2f}<extra></extra>",
        text: data.rolling.map((p) => p.index),
      },
    ];

    const layout = mergeLayout({
      height: el.clientHeight || 300,
      margin: { t: 20, r: 60, b: 48, l: 64 },
      yaxis: {
        title: { text: "Expectancy ($)", font: { color: "#4FC3F7", size: 10 } },
        tickprefix: "$",
        tickformat: "+,.2f",
        zeroline: true,
        zerolinecolor: "#8b949e",
        zerolinewidth: 1,
        tickfont: { color: "#4FC3F7", size: 9 },
      },
      yaxis2: {
        title: { text: "WR% / PF", font: { color: "#66BB6A", size: 10 } },
        overlaying: "y",
        side: "right",
        tickfont: { color: "#66BB6A", size: 9 },
        showgrid: false,
        zeroline: false,
        range: [0, Math.max(...winRate) * 1.3],
      },
      xaxis: { type: "date" },
      showlegend: true,
      legend: {
        x: 0.01,
        y: 0.99,
        xanchor: "left",
        yanchor: "top",
        font: { size: 10 },
      },
      annotations: [
        {
          text: "Ventana: " + data.window + " trades",
          xref: "paper",
          yref: "paper",
          x: 0.5,
          y: 1.0,
          showarrow: false,
          font: { size: 10, color: "#8b949e" },
          xanchor: "center",
        },
      ],
    });

    _clearChartEmptyMsg(divId);
    Plotly.newPlot(divId, traces, layout, PLOTLY_CONFIG);
  } catch (e) {
    console.error("Error rendering rolling metrics:", e);
  }
}

// ─── Toast notification ─────────────────────────────────────────────────────

function showToast(msg, duration = 2500) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.remove("hidden");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add("hidden"), duration);
}
