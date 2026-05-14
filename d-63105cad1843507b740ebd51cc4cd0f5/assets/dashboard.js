/* Dashboard JS — tab切替 + JSON読込 + Chart.js描画 */
const DATA_DIR = "data";

// === Chart.js global dark theme ===
if (typeof Chart !== "undefined") {
  Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = "#a8b0bd";
  Chart.defaults.borderColor = "#232936";
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.padding = 14;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(17,20,26,0.95)";
  Chart.defaults.plugins.tooltip.titleColor = "#e8ecf1";
  Chart.defaults.plugins.tooltip.bodyColor = "#a8b0bd";
  Chart.defaults.plugins.tooltip.borderColor = "#2e3645";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 12;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.displayColors = true;
  Chart.defaults.plugins.tooltip.usePointStyle = true;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.line.tension = 0.3;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 6;
  Chart.defaults.elements.point.hoverBorderWidth = 2;
  Chart.defaults.scale.grid.color = "rgba(35,41,54,0.6)";
  Chart.defaults.scale.grid.drawTicks = false;
  Chart.defaults.scale.ticks.padding = 8;
}

// 高級感のあるチャート配色
const CHART_PALETTE = ["#c8a96a", "#4ade80", "#60a5fa", "#f87171", "#a78bfa", "#fbbf24", "#34d399", "#fb7185"];

const yen = v => "¥" + Math.round(v).toLocaleString("ja-JP");
const num = v => Math.round(v).toLocaleString("ja-JP");
const pct = (v, d=2) => (v == null) ? "—" : (v * 100).toFixed(d) + "%";

function delta(cur, base) {
  if (base == null || base === 0) return { txt: "—", cls: "flat" };
  const diff = (cur - base) / base * 100;
  const sign = diff >= 0 ? "+" : "";
  const cls = diff > 1 ? "up" : diff < -1 ? "down" : "flat";
  return { txt: sign + diff.toFixed(1) + "%", cls };
}

async function load(file) {
  try {
    const r = await fetch(DATA_DIR + "/" + file + "?_=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("page-" + btn.dataset.tab).classList.add("active");
  });
});

function renderKpis(data) {
  if (!data || !data.last_7d) return;
  const last7 = data.last_7d, prev7 = data.prev_7d || {}, yoy = data.yoy || {};
  const kpis = [
    { label: "売上 (直近7日)", value: yen(last7.sales), delta: delta(last7.sales, prev7.sales), sub: "YoY " + delta(last7.sales, yoy.sales).txt },
    { label: "注文数", value: num(last7.orders), delta: delta(last7.orders, prev7.orders), sub: "YoY " + delta(last7.orders, yoy.orders).txt },
    { label: "セッション", value: num(last7.sessions), delta: delta(last7.sessions, prev7.sessions), sub: "YoY " + delta(last7.sessions, yoy.sessions).txt },
    { label: "CVR", value: pct(last7.cvr, 2), delta: delta(last7.cvr, prev7.cvr), sub: "YoY " + delta(last7.cvr, yoy.cvr).txt },
    { label: "AOV", value: yen(last7.aov), delta: delta(last7.aov, prev7.aov), sub: "YoY " + delta(last7.aov, yoy.aov).txt },
    { label: "items/session", value: last7.items_per_session != null ? last7.items_per_session.toFixed(2) : "—", delta: delta(last7.items_per_session, prev7.items_per_session), sub: "YoY " + delta(last7.items_per_session, yoy.items_per_session).txt },
  ];
  document.getElementById("exec-kpis").innerHTML = kpis.map(k =>
    '<div class="kpi"><div class="label">' + k.label + '</div><div class="value">' + k.value + '</div><div class="delta ' + k.delta.cls + '">WoW ' + k.delta.txt + '</div><div class="sub">' + k.sub + '</div></div>'
  ).join("");
}

function renderWeeklyTrend(data) {
  if (!data || !data.weeks) return;
  new Chart(document.getElementById("chart-weekly-trend"), {
    type: "line",
    data: {
      labels: data.weeks.map(w => w.week),
      datasets: [
        { label: "売上 (¥)", data: data.weeks.map(w => w.sales), borderColor: CHART_PALETTE[0], backgroundColor: "rgba(200,169,106,0.08)", yAxisID: "y", fill: true },
        { label: "注文数", data: data.weeks.map(w => w.orders), borderColor: CHART_PALETTE[1], yAxisID: "y2" },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { position: "left", title: { display: true, text: "売上" } },
        y2: { position: "right", title: { display: true, text: "注文数" }, grid: { drawOnChartArea: false } },
      },
    },
  });
}

function renderSignals(data) {
  if (!data || !data.signals) return;
  document.getElementById("exec-signals").innerHTML = data.signals.map(s =>
    '<div style="margin-bottom:10px;"><span class="signal ' + s.level + '">' + s.level.toUpperCase() + '</span> <strong>' + s.metric + '</strong>: ' + s.value + ' (基準 ' + s.target + ') — ' + (s.note || '') + '</div>'
  ).join("");
}

function renderFunnel(data) {
  if (!data || !data.steps) return;
  const max = Math.max.apply(null, data.steps.map(s => s.count));
  document.getElementById("funnel-bars").innerHTML = data.steps.map((s, i) => {
    const w = max ? (s.count / max * 100).toFixed(1) : 0;
    const prev = i > 0 ? data.steps[i - 1].count : null;
    const rate = (prev && prev > 0) ? (s.count / prev * 100).toFixed(1) + "%" : "—";
    return '<div class="step"><div class="name">' + s.name + '</div><div class="bar"><div class="fill" style="width:' + w + '%"></div></div><div class="count">' + num(s.count) + '</div><div class="rate">' + rate + '</div></div>';
  }).join("");
}

function renderItemsTrend(data) {
  if (!data || !data.weeks) return;
  new Chart(document.getElementById("chart-items-trend"), {
    type: "line",
    data: {
      labels: data.weeks.map(w => w.week),
      datasets: [
        { label: "items/session", data: data.weeks.map(w => w.items_per_session), borderColor: CHART_PALETTE[2], backgroundColor: "rgba(96,165,250,0.08)", fill: true },
        { label: "CVR (%)", data: data.weeks.map(w => w.cvr * 100), borderColor: CHART_PALETTE[3], yAxisID: "y2" },
      ],
    },
    options: {
      scales: {
        y: { position: "left", title: { display: true, text: "items/session" } },
        y2: { position: "right", title: { display: true, text: "CVR (%)" }, grid: { drawOnChartArea: false } },
      },
    },
  });
}

function renderCollections(data) {
  if (!data || !data.collections) return;
  const rows = data.collections.map(c => {
    const cls = c.pdp_reach < 25 ? "bad" : c.pdp_reach < 35 ? "warn" : "";
    return '<tr class="' + cls + '"><td>' + c.path + '</td><td class="num">' + num(c.sessions) + '</td><td class="num">' + num(c.pdp_reached) + '</td><td class="num">' + c.pdp_reach.toFixed(1) + '%</td></tr>';
  }).join("");
  document.getElementById("table-collections").innerHTML =
    '<thead><tr><th>コレクション</th><th class="num">着地</th><th class="num">PDP到達</th><th class="num">到達率</th></tr></thead><tbody>' + rows + '</tbody>';
}

function renderChannels(data) {
  if (!data || !data.channels) return;
  document.getElementById("table-channels").innerHTML =
    '<thead><tr><th>チャネル</th><th class="num">セッション</th><th class="num">注文</th><th class="num">CVR</th><th class="num">売上</th></tr></thead><tbody>' +
    data.channels.map(c => '<tr><td>' + c.channel + '</td><td class="num">' + num(c.sessions) + '</td><td class="num">' + num(c.orders) + '</td><td class="num">' + pct(c.cvr) + '</td><td class="num">' + yen(c.sales) + '</td></tr>').join("") +
    '</tbody>';
}

function renderChannelTrend(data) {
  if (!data || !data.trend) return;
  new Chart(document.getElementById("chart-channel-trend"), {
    type: "line",
    data: {
      labels: data.trend.weeks,
      datasets: data.trend.series.map((s, i) => ({
        label: s.channel, data: s.sessions, borderColor: CHART_PALETTE[i % CHART_PALETTE.length],
      })),
    },
    options: { scales: { y: { beginAtZero: true } } },
  });
}

function renderUtmHealth(data) {
  if (!data) return;
  const broken = data.broken || [];
  const status = broken.length === 0 ? "ok" : "ng";
  const sym = broken.length === 0 ? "✅" : "🔴";
  const total = broken.reduce((a, b) => a + (b.sessions || 0), 0);
  const head = '<p class="' + status + '">' + sym + ' 破損 utm セッション数: <strong>' + total + '</strong> (' + broken.length + ' パターン)</p>';
  const list = broken.slice(0, 10).map(b => '<li><code>' + b.source + '</code> — ' + b.sessions + ' sess</li>').join("");
  const seCount = (data.shopify_email_campaigns || []).length;
  document.getElementById("utm-health").innerHTML =
    head + (broken.length ? '<ul>' + list + '</ul>' : '') +
    '<p>Shopify Email 自動UTM 着信キャンペーン数: <strong>' + seCount + '</strong></p>';
}

function renderReleases(data) {
  if (!data || !data.releases) return;
  document.getElementById("table-releases").innerHTML =
    '<thead><tr><th>ID</th><th>反映日</th><th>内容</th><th>仮説指標</th><th>期待</th><th>判定</th></tr></thead><tbody>' +
    data.releases.map(r => {
      const cls = r.decision === "RED" ? "bad" : r.decision === "YELLOW" ? "warn" : "";
      return '<tr class="' + cls + '"><td>' + r.release_id + '</td><td>' + (r.deployed_at || '—') + '</td><td>' + (r.summary || '') + '</td><td>' + (r.hypothesis_metric || '') + '</td><td>' + (r.expected_lift_pct ? '+' + r.expected_lift_pct + '%' : '—') + '</td><td>' + (r.decision || 'DRAFT') + '</td></tr>';
    }).join("") + '</tbody>';
}

(async () => {
  const [summary, funnel, channels, releases, utm] = await Promise.all([
    load("summary.json"),
    load("funnel.json"),
    load("channels.json"),
    load("releases.json"),
    load("utm_health.json"),
  ]);
  if (summary && summary.last_updated) {
    document.getElementById("last-updated").textContent = summary.last_updated;
  }
  renderKpis(summary);
  renderWeeklyTrend(summary);
  renderSignals(summary);
  renderFunnel(funnel);
  renderItemsTrend(funnel);
  renderCollections(funnel);
  renderChannels(channels);
  renderChannelTrend(channels);
  renderUtmHealth(utm);
  renderReleases(releases);
})();
