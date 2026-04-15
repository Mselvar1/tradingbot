/**
 * Chart.js helpers — dark theme aligned with style.css variables.
 * Chart.js 4.x UMD global: Chart
 */
(function () {
  const muted = "#8b9bb4";
  const grid = "rgba(42, 53, 72, 0.6)";
  const font = "'Inter', system-ui, sans-serif";
  let autoScrollFeed = true;
  let terminalCollapsed = false;
  let globeBooted = false;

  const VERTEX_SHADER = `
    uniform float uTime;
    uniform float uDistortion;
    uniform float uSize;
    uniform vec2 uMouse;
    varying float vNoise;

    vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
    vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
    vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
    vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

    float snoise(vec3 v) {
      const vec2 C = vec2(1.0/6.0, 1.0/3.0);
      const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
      vec3 i = floor(v + dot(v, C.yyy));
      vec3 x0 = v - i + dot(i, C.xxx);
      vec3 g = step(x0.yzx, x0.xyz);
      vec3 l = 1.0 - g;
      vec3 i1 = min(g.xyz, l.zxy);
      vec3 i2 = max(g.xyz, l.zxy);
      vec3 x1 = x0 - i1 + 1.0 * C.xxx;
      vec3 x2 = x0 - i2 + 2.0 * C.xxx;
      vec3 x3 = x0 - 1.0 + 3.0 * C.xxx;
      i = mod289(i);
      vec4 p = permute(permute(permute(
        i.z + vec4(0.0, i1.z, i2.z, 1.0))
        + i.y + vec4(0.0, i1.y, i2.y, 1.0))
        + i.x + vec4(0.0, i1.x, i2.x, 1.0));
      float n_ = 1.0/7.0;
      vec3 ns = n_ * D.wyz - D.xzx;
      vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
      vec4 x_ = floor(j * ns.z);
      vec4 y_ = floor(j - 7.0 * x_);
      vec4 x = x_ * ns.x + ns.yyyy;
      vec4 y = y_ * ns.x + ns.yyyy;
      vec4 h = 1.0 - abs(x) - abs(y);
      vec4 b0 = vec4(x.xy, y.xy);
      vec4 b1 = vec4(x.zw, y.zw);
      vec4 s0 = floor(b0) * 2.0 + 1.0;
      vec4 s1 = floor(b1) * 2.0 + 1.0;
      vec4 sh = -step(h, vec4(0.0));
      vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
      vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
      vec3 p0 = vec3(a0.xy, h.x);
      vec3 p1 = vec3(a0.zw, h.y);
      vec3 p2 = vec3(a1.xy, h.z);
      vec3 p3 = vec3(a1.zw, h.w);
      vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
      p0 *= norm.x;
      p1 *= norm.y;
      p2 *= norm.z;
      p3 *= norm.w;
      vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
      m = m * m;
      return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
    }

    void main() {
      vec3 pos = position;
      float noise = snoise(vec3(pos.x * 0.8 + uTime * 0.2, pos.y * 0.8, pos.z * 0.8));
      vNoise = noise;
      vec3 newPos = pos + (normalize(pos) * noise * uDistortion);
      float dist = distance(uMouse * 10.0, newPos.xy);
      float interaction = smoothstep(5.0, 0.0, dist);
      newPos += normalize(pos) * interaction * 0.8;
      vec4 mvPosition = modelViewMatrix * vec4(newPos, 1.0);
      gl_Position = projectionMatrix * mvPosition;
      gl_PointSize = uSize * (20.0 / -mvPosition.z) * (1.0 + noise * 0.2);
    }
  `;

  const FRAGMENT_SHADER = `
    uniform vec3 uColor;
    uniform float uOpacity;
    varying float vNoise;

    void main() {
      vec2 center = gl_PointCoord - vec2(0.5);
      float dist = length(center);
      if (dist > 0.5) discard;
      float alpha = smoothstep(0.5, 0.1, dist) * uOpacity;
      vec3 darkColor = uColor * 0.3;
      vec3 lightColor = uColor * 2.5;
      vec3 finalColor = mix(darkColor, lightColor, vNoise * 0.6 + 0.4);
      gl_FragColor = vec4(finalColor, alpha);
    }
  `;

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

  function setLastPrice(labelId, rows) {
    const el = document.getElementById(labelId);
    if (!el || !Array.isArray(rows) || !rows.length) return;
    const last = rows[rows.length - 1];
    const v = Number(last.close || last.c || 0);
    if (!Number.isFinite(v) || v <= 0) return;
    el.textContent = `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
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
    initGlobeMotion();
    const btc = readJson("dash-json-chart-btc");
    const gold = readJson("dash-json-chart-gold");
    const scores = readJson("dash-json-scores-charts");
    const perfHome = readJson("dash-json-performance-home");
    const btcOhlc = readJson("dash-json-chart-btc-ohlc");
    const goldOhlc = readJson("dash-json-chart-gold-ohlc");
    const initialFeed = readJson("dash-json-live-feed");
    lineChart("chartCloseBtc", btc, "Close (M15)", [124, 92, 255]);
    lineChart("chartCloseGold", gold, "Close (M15)", [255, 193, 7]);
    if (scores) {
      barChartH("chartScoresGold", scores.GOLD || {}, "Score");
      barChartH("chartScoresBtc", scores["BTC-USD"] || {}, "Score");
    }
    if (perfHome) {
      doughnutOutcome("chartHomeOutcomePie", perfHome.overall);
      barOutcomesByTicker("chartHomeOutcomeByTicker", perfHome.by_ticker);
    }
    candleChart("chartCandleBtc", btcOhlc || [], "btc");
    candleChart("chartCandleGold", goldOhlc || [], "gold");
    setLastPrice("btcLastPrice", btcOhlc || []);
    setLastPrice("goldLastPrice", goldOhlc || []);
    if (initialFeed && initialFeed.length) {
      renderLiveFeed(initialFeed);
    }
    initTerminalControls();
    startLiveFeedPolling();
    startCandlesPolling();
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

  function doughnutOutcome(canvasId, overall) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return;
    const ctx = canvas.getContext("2d");
    const w = overall?.wins || 0;
    const l = overall?.losses || 0;
    const o = overall?.other || 0;
    const sum = w + l + o;
    if (sum === 0) {
      new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: ["No recorded closes yet"],
          datasets: [{ data: [1], backgroundColor: ["#1f2937"], borderWidth: 0 }],
        },
        options: {
          cutout: "62%",
          plugins: { legend: { position: "bottom", labels: { color: muted, font: { family: font } } } },
        },
      });
      return;
    }
    new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["TP wins", "Stop losses", "Other"],
        datasets: [
          {
            data: [w, l, o],
            backgroundColor: [
              "rgba(52, 211, 153, 0.88)",
              "rgba(248, 113, 113, 0.88)",
              "rgba(148, 163, 184, 0.6)",
            ],
            borderColor: "#111827",
            borderWidth: 2,
            hoverOffset: 14,
          },
        ],
      },
      options: {
        cutout: "62%",
        animation: { animateRotate: true, duration: 1100, easing: "easeOutQuart" },
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: muted, padding: 14, font: { family: font, size: 11 } },
          },
          tooltip: baseOpts().plugins.tooltip,
        },
      },
    });
  }

  function barOutcomesByTicker(canvasId, rows) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return;
    const ctx = canvas.getContext("2d");
    if (!rows || !rows.length) {
      new Chart(ctx, {
        type: "bar",
        data: { labels: ["—"], datasets: [{ label: "No data", data: [0], backgroundColor: "#1f2937" }] },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: muted }, grid: { color: grid } },
            y: { beginAtZero: true, ticks: { color: muted }, grid: { color: grid } },
          },
        },
      });
      return;
    }
    const labels = rows.map((r) => r.ticker || "—");
    new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "TP wins",
            data: rows.map((r) => r.wins || 0),
            backgroundColor: "rgba(52, 211, 153, 0.85)",
            borderRadius: 6,
          },
          {
            label: "SL losses",
            data: rows.map((r) => r.losses || 0),
            backgroundColor: "rgba(248, 113, 113, 0.85)",
            borderRadius: 6,
          },
          {
            label: "Other",
            data: rows.map((r) => r.other || 0),
            backgroundColor: "rgba(148, 163, 184, 0.65)",
            borderRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 900, easing: "easeOutQuart" },
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: muted, font: { family: font, size: 11 }, boxWidth: 12 },
          },
          tooltip: baseOpts().plugins.tooltip,
        },
        scales: {
          x: {
            ticks: { color: muted, font: { family: font, size: 11 } },
            grid: { color: grid },
          },
          y: {
            beginAtZero: true,
            ticks: { color: muted, font: { family: font, size: 10 } },
            grid: { color: grid },
          },
        },
      },
    });
  }

  function initPerformance() {
    const perf = readJson("dash-json-performance");
    if (!perf) return;
    doughnutOutcome("chartOutcomePie", perf.overall);
    barOutcomesByTicker("chartOutcomeByTicker", perf.by_ticker);
  }

  const candleState = {
    btc: null,
    gold: null,
  };

  function candleChart(containerId, series, key) {
    const el = document.getElementById(containerId);
    if (!el || typeof LightweightCharts === "undefined") return;
    const rows = Array.isArray(series) ? series : [];
    if (!rows.length) return;
    const chart = LightweightCharts.createChart(el, {
      layout: {
        background: { color: "#111827" },
        textColor: "#94a3b8",
        fontFamily: "'Inter', system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(42,53,72,.45)" },
        horzLines: { color: "rgba(42,53,72,.45)" },
      },
      rightPriceScale: { borderColor: "rgba(42,53,72,.9)" },
      timeScale: {
        borderColor: "rgba(42,53,72,.9)",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    const c = chart.addCandlestickSeries({
      upColor: "rgba(52, 211, 153, 0.95)",
      downColor: "rgba(248, 113, 113, 0.95)",
      borderVisible: false,
      wickUpColor: "rgba(52, 211, 153, 0.95)",
      wickDownColor: "rgba(248, 113, 113, 0.95)",
    });
    c.setData(rows);
    chart.timeScale().fitContent();
    if (key && candleState[key] !== undefined) {
      candleState[key] = c;
    }
  }

  function renderLiveFeed(items) {
    const list = document.getElementById("terminalFeedList");
    if (!list) return;
    const rows = Array.isArray(items) ? [...items].reverse() : [];
    if (!rows.length) {
      list.innerHTML = '<li class="terminal-line mono">[boot] waiting for activity...</li>';
      return;
    }
    list.innerHTML = rows
      .slice(0, 40)
      .map(
        (it) => `
          <li class="terminal-line mono">
            [${esc(it.ts)}] ${esc(String(it.type || "evt").toUpperCase())} · ${esc(it.headline)} — ${esc(it.detail)}
          </li>`
      )
      .join("");
    if (autoScrollFeed) {
      list.scrollTop = list.scrollHeight;
    }
  }

  function startLiveFeedPolling() {
    const list = document.getElementById("terminalFeedList");
    if (!list) return;
    const tick = async () => {
      try {
        const r = await fetch("/api/live/activity?limit=32", { cache: "no-store" });
        if (!r.ok) return;
        const data = await r.json();
        renderLiveFeed(data.items || []);
      } catch {
        // silent best-effort polling
      }
    };
    window.setInterval(tick, 20000);
  }

  function initGlobeMotion() {
    if (globeBooted || typeof THREE === "undefined") return;
    const container = document.getElementById("heroGlobeCanvas");
    if (!container || !container.clientWidth || !container.clientHeight) return;
    globeBooted = true;

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x09090b, 0.035);

    const camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.1, 100);
    camera.position.set(0, 0, 18);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
    container.appendChild(renderer.domElement);

    const systemsGroup = new THREE.Group();
    systemsGroup.position.x = 4.0;
    scene.add(systemsGroup);

    const geometry = new THREE.IcosahedronGeometry(4.0, 35);
    const uniforms = {
      uTime: { value: 0 },
      uDistortion: { value: 0.6 },
      uSize: { value: 1.5 },
      uColor: { value: new THREE.Color("#10b981") },
      uOpacity: { value: 0.8 },
      uMouse: { value: new THREE.Vector2(0, 0) },
    };

    const material = new THREE.ShaderMaterial({
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      uniforms,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const particles = new THREE.Points(geometry, material);
    systemsGroup.add(particles);

    const lineGroup = new THREE.Group();
    systemsGroup.add(lineGroup);

    function createTechOrbit(radius, rotation) {
      const curve = new THREE.EllipseCurve(0, 0, radius, radius, 0, 2 * Math.PI, false, 0);
      const points = curve.getPoints(128);
      const geo = new THREE.BufferGeometry().setFromPoints(points);
      const mat = new THREE.LineBasicMaterial({
        color: 0x3f3f46,
        transparent: true,
        opacity: 0.5,
      });
      const orbit = new THREE.Line(geo, mat);
      orbit.rotation.x = rotation.x;
      orbit.rotation.y = rotation.y;
      lineGroup.add(orbit);
      return orbit;
    }

    const orbits = [
      createTechOrbit(5.5, { x: Math.PI / 2, y: 0 }),
      createTechOrbit(5.2, { x: Math.PI / 3, y: Math.PI / 6 }),
      createTechOrbit(6.0, { x: Math.PI / 1.8, y: Math.PI / 4 }),
    ];

    let time = 0;
    let mouseX = 0;
    let mouseY = 0;

    const handleMouseMove = (e) => {
      mouseX = (e.clientX / window.innerWidth) * 2 - 1;
      mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
      uniforms.uMouse.value.x += (mouseX - uniforms.uMouse.value.x) * 0.05;
      uniforms.uMouse.value.y += (mouseY - uniforms.uMouse.value.y) * 0.05;
    };

    const adjustLayout = () => {
      if (window.innerWidth < 1024) {
        systemsGroup.position.set(0, 1.5, -5);
        systemsGroup.scale.set(0.78, 0.78, 0.78);
      } else {
        systemsGroup.position.set(4.5, 0, 0);
        systemsGroup.scale.set(1, 1, 1);
      }
    };

    const handleResize = () => {
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
      adjustLayout();
    };

    adjustLayout();
    document.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("resize", handleResize);

    const animate = () => {
      if (!document.body.contains(container)) {
        renderer.dispose();
        return;
      }
      requestAnimationFrame(animate);
      time += 0.012;
      systemsGroup.rotation.y = time * 0.08;
      systemsGroup.rotation.z = Math.sin(time * 0.1) * 0.05;
      lineGroup.rotation.x = Math.sin(time * 0.05) * 0.2;
      orbits.forEach((orbit, i) => {
        orbit.rotation.z += 0.003 * (i + 1);
      });
      camera.position.x += (mouseX * 0.5 - camera.position.x) * 0.05;
      camera.position.y += (mouseY * 0.5 - camera.position.y) * 0.05;
      camera.lookAt(0, 0, 0);
      uniforms.uTime.value = time;
      renderer.render(scene, camera);
    };
    animate();
  }

  function startCandlesPolling() {
    const tick = async (instrument, key, priceId) => {
      try {
        const r = await fetch(
          `/api/chart/candles-ohlc?instrument=${encodeURIComponent(instrument)}&tf=M15&limit=120`,
          { cache: "no-store" }
        );
        if (!r.ok) return;
        const data = await r.json();
        const rows = data.series || [];
        if (candleState[key] && rows.length) {
          candleState[key].setData(rows);
        }
        setLastPrice(priceId, rows);
      } catch {
        // best effort polling
      }
    };

    const loop = () => {
      tick("BTC-USD", "btc", "btcLastPrice");
      tick("GOLD", "gold", "goldLastPrice");
    };
    window.setInterval(loop, 20000);
  }

  function initTerminalControls() {
    const shell = document.getElementById("terminalShell");
    const body = document.getElementById("terminalBody");
    const toggleBtn = document.getElementById("toggleTerminalBtn");
    const pauseBtn = document.getElementById("pauseScrollBtn");
    if (!shell || !body || !toggleBtn || !pauseBtn) return;

    toggleBtn.addEventListener("click", () => {
      terminalCollapsed = !terminalCollapsed;
      shell.classList.toggle("is-collapsed", terminalCollapsed);
      toggleBtn.textContent = terminalCollapsed ? "Expand" : "Collapse";
    });

    pauseBtn.addEventListener("click", () => {
      autoScrollFeed = !autoScrollFeed;
      pauseBtn.classList.toggle("is-paused", !autoScrollFeed);
      pauseBtn.textContent = autoScrollFeed ? "Pause scroll" : "Resume scroll";
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.dashPage;
    if (page === "index") initIndex();
    else if (page === "strategies") initStrategies();
    else if (page === "candles") initCandles();
    else if (page === "performance") initPerformance();
  });
})();
