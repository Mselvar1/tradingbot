/**
 * Chart.js helpers — dark theme aligned with style.css variables.
 * Chart.js 4.x UMD global: Chart
 */
(function () {
  const muted = "#8b9bb4";
  const grid = "rgba(42, 53, 72, 0.6)";
  const font = "'DM Sans', system-ui, sans-serif";

  function baseOpts() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#121a2b",
          titleColor: "#e8ecf4",
          bodyColor: "#8b9bb4",
          borderColor: "#2a3548",
          borderWidth: 1,
          padding: 10,
          cornerRadius: 8,
        },
      },
      scales: {
        x: {
          ticks: { color: muted, maxRotation: 45, minRotation: 0, font: { family: font, size: 10 } },
          grid: { color: grid },
        },
        y: {
          ticks: { color: muted, font: { family: font, size: 10 } },
          grid: { color: grid },
        },
      },
    };
  }

  function readJson(id) {
    const el = document.getElementById(id);
    if (!el || !el.textContent) return null;
    try {
      return JSON.parse(el.textContent);
    } catch {
      return null;
    }
  }

  function barColors(directions) {
    return (directions || []).map((d) => {
      const x = String(d || "").toLowerCase();
      if (x.includes("long") || x.includes("buy")) return "rgba(46, 229, 157, 0.75)";
      if (x.includes("short") || x.includes("sell")) return "rgba(255, 92, 92, 0.75)";
      return "rgba(61, 139, 253, 0.55)";
    });
  }

  function lineChart(canvasId, series, label, rgb) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;
    const ctx = canvas.getContext("2d");
    const [r, g, b] = rgb;
    const grad = ctx.createLinearGradient(0, 0, 0, canvas.height || 200);
    grad.addColorStop(0, `rgba(${r},${g},${b},0.35)`);
    grad.addColorStop(1, `rgba(${r},${g},${b},0.02)`);
    const data = series && series.closes ? series.closes : [];
    const labels = series && series.labels ? series.labels : [];
    if (!data.length) return null;
    return new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label,
            data,
            borderColor: `rgb(${r},${g},${b})`,
            backgroundColor: grad,
            fill: true,
            tension: 0.25,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
          },
        ],
      },
      options: {
        ...baseOpts(),
        interaction: { intersect: false, mode: "index" },
        plugins: { ...baseOpts().plugins, legend: { display: false } },
      },
    });
  }

  function barChartH(canvasId, payload, title) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;
    const labels = payload.labels || [];
    const scores = payload.scores || [];
    const directions = payload.directions || [];
    if (!labels.length) return null;
    return new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: title,
            data: scores,
            backgroundColor: barColors(directions),
            borderRadius: 6,
            borderSkipped: false,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: baseOpts().plugins.tooltip,
        },
        scales: {
          x: {
            beginAtZero: true,
            max: 100,
            ticks: { color: muted, font: { family: font, size: 10 } },
            grid: { color: grid },
          },
          y: {
            ticks: { color: muted, font: { family: font, size: 10 } },
            grid: { display: false },
          },
        },
      },
    });
  }

  function initIndex() {
    const btc = readJson("dash-json-chart-btc");
    const gold = readJson("dash-json-chart-gold");
    const scores = readJson("dash-json-scores-charts");
    lineChart("chartCloseBtc", btc, "Close (M15)", [124, 92, 255]);
    lineChart("chartCloseGold", gold, "Close (M15)", [255, 193, 7]);
    if (scores) {
      barChartH("chartScoresGold", scores.GOLD || {}, "Score");
      barChartH("chartScoresBtc", scores["BTC-USD"] || {}, "Score");
    }
  }

  function initStrategies() {
    const scores = readJson("dash-json-scores-charts");
    if (!scores) return;
    barChartH("chartStratGold", scores.GOLD || {}, "Score");
    barChartH("chartStratBtc", scores["BTC-USD"] || {}, "Score");
  }

  function initCandles() {
    const series = readJson("dash-json-candles-chart");
    const inst = (document.body.dataset.instrument || "").toLowerCase();
    const rgb = inst.includes("gold") ? [255, 193, 7] : [61, 139, 253];
    lineChart("chartCandlesClose", series, "Close", rgb);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.dashPage;
    if (page === "index") initIndex();
    else if (page === "strategies") initStrategies();
    else if (page === "candles") initCandles();
  });
})();
