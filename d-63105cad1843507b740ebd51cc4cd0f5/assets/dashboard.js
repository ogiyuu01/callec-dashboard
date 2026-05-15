/* Dashboard JS — tab切替 + JSON読込 + Chart.js描画 */
const DATA_DIR = "data";

// === Chart.js global theme (Editorial Luxury · Deep Espresso) ===
if (typeof Chart !== "undefined") {
  Chart.defaults.font.family = "'Plus Jakarta Sans', 'Noto Sans JP', sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = "#c4b8a9";
  Chart.defaults.borderColor = "rgba(244,237,226,0.06)";
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.padding = 14;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(20,16,13,0.96)";
  Chart.defaults.plugins.tooltip.titleColor = "#f4ede2";
  Chart.defaults.plugins.tooltip.bodyColor = "#c4b8a9";
  Chart.defaults.plugins.tooltip.borderColor = "rgba(244,237,226,0.12)";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 14;
  Chart.defaults.plugins.tooltip.cornerRadius = 12;
  Chart.defaults.plugins.tooltip.displayColors = true;
  Chart.defaults.plugins.tooltip.usePointStyle = true;
  Chart.defaults.elements.line.borderWidth = 1.5;
  Chart.defaults.elements.line.tension = 0.4;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 6;
  Chart.defaults.elements.point.hoverBorderWidth = 2;
  Chart.defaults.scale.grid.color = "rgba(244,237,226,0.05)";
  Chart.defaults.scale.grid.drawTicks = false;
  Chart.defaults.scale.ticks.padding = 10;
}

// Editorial luxury palette
const CHART_PALETTE = ["#d4b87a", "#95c891", "#a99dd6", "#e6b855", "#d68d8d", "#7fb8c9", "#b8a99a", "#8a7a6c"];

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

function activateTab(tabName) {
  document.querySelectorAll(".tab, .nav-item").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll('[data-tab="' + tabName + '"]').forEach(t => t.classList.add("active"));
  const page = document.getElementById("page-" + tabName);
  if (page) page.classList.add("active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}
document.querySelectorAll(".tab, .nav-item").forEach(btn => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

function sparklineSVG(values) {
  if (!values || values.length < 2) return "";
  const w = 100, h = 28, pad = 1;
  const min = Math.min.apply(null, values);
  const max = Math.max.apply(null, values);
  const range = (max - min) || 1;
  const step = (w - pad * 2) / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = pad + i * step;
    const y = h - pad - ((v - min) / range) * (h - pad * 2);
    return x.toFixed(1) + "," + y.toFixed(1);
  });
  const linePath = "M" + pts.join(" L");
  const areaPath = linePath + " L" + (pad + (values.length - 1) * step).toFixed(1) + "," + (h - pad) + " L" + pad + "," + (h - pad) + " Z";
  return '<svg class="sparkline" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
    '<defs><linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#d4b87a" stop-opacity="0.35"/><stop offset="100%" stop-color="#d4b87a" stop-opacity="0"/></linearGradient></defs>' +
    '<path class="area" d="' + areaPath + '"/>' +
    '<path class="line" d="' + linePath + '"/>' +
    '</svg>';
}

function renderKpis(data) {
  if (!data || !data.last_7d) return;
  const last7 = data.last_7d, prev7 = data.prev_7d || {}, yoy = data.yoy || {};
  const days = data.daily || [];
  const series = (key) => days.map(d => +d[key] || 0);

  const kpis = [
    { label: "売上 (直近7日)", value: yen(last7.sales), delta: delta(last7.sales, prev7.sales), sub: "YoY " + delta(last7.sales, yoy.sales).txt, spark: series("sales") },
    { label: "注文数", value: num(last7.orders), delta: delta(last7.orders, prev7.orders), sub: "YoY " + delta(last7.orders, yoy.orders).txt, spark: series("orders") },
    { label: "セッション", value: num(last7.sessions), delta: delta(last7.sessions, prev7.sessions), sub: "YoY " + delta(last7.sessions, yoy.sessions).txt, spark: series("sessions") },
    { label: "CVR", value: pct(last7.cvr, 2), delta: delta(last7.cvr, prev7.cvr), sub: "YoY " + delta(last7.cvr, yoy.cvr).txt, spark: series("cvr") },
    { label: "AOV", value: yen(last7.aov), delta: delta(last7.aov, prev7.aov), sub: "YoY " + delta(last7.aov, yoy.aov).txt, spark: series("aov") },
    { label: "items/session", value: last7.items_per_session != null ? last7.items_per_session.toFixed(2) : "—", delta: delta(last7.items_per_session, prev7.items_per_session), sub: "YoY " + delta(last7.items_per_session, yoy.items_per_session).txt, spark: series("items_per_session") },
  ];
  document.getElementById("exec-kpis").innerHTML = kpis.map(k =>
    '<div class="kpi"><div class="label">' + k.label + '</div><div class="value">' + k.value + '</div><div class="delta ' + k.delta.cls + '">WoW ' + k.delta.txt + '</div><div class="sub">' + k.sub + '</div>' + sparklineSVG(k.spark) + '</div>'
  ).join("");
}

function renderAnomalies(data) {
  const el = document.getElementById("anomaly-list");
  if (!el) return;
  const events = (data && data.anomalies) || [];
  if (events.length === 0) {
    el.innerHTML = '<div class="anomaly-empty">直近14日に大きな変動はありません。</div>';
    return;
  }
  el.innerHTML = events.map(e =>
    '<div class="anomaly-row">' +
      '<div class="date">' + e.date + '</div>' +
      '<div class="desc">' + e.desc + '</div>' +
      '<div class="delta ' + e.direction + '">' + e.delta_text + '</div>' +
    '</div>'
  ).join("");
}

function renderProductsTop5(data) {
  const el = document.getElementById("products-top5");
  if (!el) return;
  const items = (data && data.products_top5) || [];
  if (items.length === 0) {
    el.innerHTML = '<div class="anomaly-empty">商品データがまだ蓄積されていません。</div>';
    return;
  }
  el.innerHTML = items.map((p, i) =>
    '<div class="product-row">' +
      '<div class="rank">' + (i + 1) + '</div>' +
      '<div class="name">' + (p.name || '—') + '</div>' +
      '<div class="qty">' + (p.qty || 0) + '点</div>' +
      '<div class="rev">' + yen(p.revenue || 0) + '</div>' +
    '</div>'
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

function renderNarrative(summary) {
  if (!summary || !summary.narrative) return;
  const n = summary.narrative;
  const sumEl = document.getElementById("narrative-summary");
  if (sumEl) {
    sumEl.innerHTML = '<h3>今週の状況</h3>' +
      '<div class="narrative-lines">' +
      (n.lines || []).map(l => '<div class="narrative-line">' + l + '</div>').join("") +
      '</div>';
  }
  const actEl = document.getElementById("narrative-actions");
  if (actEl && n.actions && n.actions.length) {
    actEl.innerHTML = '<h3>📌 今やるべきこと</h3>' +
      '<ol class="action-list">' +
      n.actions.map(a => '<li>' + a + '</li>').join("") +
      '</ol>';
  }
}

function renderArchive(data) {
  if (!data || !data.weeks) {
    document.getElementById("archive-list").innerHTML = '<p style="color:var(--text-muted)">まだ履歴がありません。来週月曜から自動保存されます。</p>';
    return;
  }
  const html = data.weeks.map(w => {
    const k = w.kpis || {};
    const prev = w.prev || {};
    const yoy = w.yoy || {};
    const sales = k.sales || 0;
    const orders = k.orders || 0;
    const cvr = k.cvr || 0;
    const items = k.items_per_session || 0;
    const wow = prev.sales ? ((sales - prev.sales) / prev.sales * 100).toFixed(0) : null;
    const yoyDelta = yoy.sales ? ((sales - yoy.sales) / yoy.sales * 100).toFixed(0) : null;
    const wowBadge = wow !== null ? '<span class="badge ' + (wow >= 0 ? 'up' : 'down') + '">先週比 ' + (wow >= 0 ? '+' : '') + wow + '%</span>' : '';
    const yoyBadge = yoyDelta !== null ? '<span class="badge ' + (yoyDelta >= 0 ? 'up' : 'down') + '">前年比 ' + (yoyDelta >= 0 ? '+' : '') + yoyDelta + '%</span>' : '';
    const narrativeHtml = (w.narrative && w.narrative.lines)
      ? '<div class="narrative-lines compact">' + w.narrative.lines.map(l => '<div class="narrative-line">' + l + '</div>').join('') + '</div>'
      : '';
    return '<div class="archive-card">' +
      '<div class="archive-head">' +
        '<div><strong>' + w.week + '</strong> <span class="archive-period">(' + w.start_date + ' 〜 ' + w.end_date + ')</span></div>' +
        '<div class="archive-badges">' + wowBadge + ' ' + yoyBadge + '</div>' +
      '</div>' +
      '<div class="archive-kpis">' +
        '<div><span class="lab">売上</span><span class="val">' + yen(sales) + '</span></div>' +
        '<div><span class="lab">注文</span><span class="val">' + num(orders) + '</span></div>' +
        '<div><span class="lab">CVR</span><span class="val">' + pct(cvr) + '</span></div>' +
        '<div><span class="lab">items/session</span><span class="val">' + items.toFixed(2) + '</span></div>' +
      '</div>' +
      narrativeHtml +
      '<div class="archive-meta">保存日時: ' + (w.captured_at || '—') + '</div>' +
    '</div>';
  }).join("");
  document.getElementById("archive-list").innerHTML = html;
}

function monthLabelJa(m) {
  if (!m || m.length < 7) return m;
  const y = m.substring(0, 4);
  const mm = parseInt(m.substring(5, 7), 10);
  return y + "年" + mm + "月度";
}

function renderArchiveMonthly(data) {
  const el = document.getElementById("archive-monthly-list");
  if (!el) return;
  if (!data || !data.months || data.months.length === 0) {
    el.innerHTML = '<p style="color:var(--text-muted)">月次データがまだありません。来月以降に蓄積されます。</p>';
    return;
  }
  el.innerHTML = data.months.map(m => {
    const k = m.kpis || {};
    const prev = m.prev || {};
    const yoy = m.yoy || {};
    const mom = (prev.sales) ? ((k.sales - prev.sales) / prev.sales * 100) : null;
    const yoyP = (yoy.sales) ? ((k.sales - yoy.sales) / yoy.sales * 100) : null;
    const momBadge = mom !== null
      ? '<span class="badge ' + (mom >= 0 ? 'up' : 'down') + '">前月比 ' + (mom >= 0 ? '+' : '') + mom.toFixed(0) + '%</span>'
      : '';
    const yoyBadge = yoyP !== null
      ? '<span class="badge ' + (yoyP >= 0 ? 'up' : 'down') + '">前年同月比 ' + (yoyP >= 0 ? '+' : '') + yoyP.toFixed(0) + '%</span>'
      : '';
    const narrativeHtml = (m.narrative && m.narrative.lines)
      ? '<div class="month-narrative">' + m.narrative.lines.map(l => '<div>' + l + '</div>').join('') + '</div>'
      : '';
    return '<div class="archive-month-card">' +
      '<div class="archive-month-head">' +
        '<div class="month-title">' + monthLabelJa(m.month) + '</div>' +
        '<div class="month-sub">' + m.month + '</div>' +
      '</div>' +
      '<div class="compare">' + momBadge + ' ' + yoyBadge + '</div>' +
      '<div class="month-kpis">' +
        '<div><span class="lab">売上</span><span class="val">' + yen(k.sales || 0) + '</span></div>' +
        '<div><span class="lab">注文</span><span class="val">' + num(k.orders || 0) + '</span></div>' +
        '<div><span class="lab">セッション</span><span class="val">' + num(k.sessions || 0) + '</span></div>' +
        '<div><span class="lab">CVR</span><span class="val">' + ((k.cvr || 0) * 100).toFixed(2) + '%</span></div>' +
      '</div>' +
      narrativeHtml +
    '</div>';
  }).join("");
}

function renderTheme(data) {
  if (!data) return;
  const cur = data.current || {};
  const tasks = cur.tasks || {};

  const titleEl = document.getElementById("today-theme-title");
  if (titleEl && cur.week) titleEl.textContent = "今週のテーマ — " + cur.week;

  const card = document.getElementById("theme-card");
  if (card) {
    card.innerHTML =
      '<div class="theme-meta">' +
        '<span class="pill">' + (cur.week || "—") + '</span>' +
        (cur.topic_slug ? '<span class="pill">' + cur.topic_slug + '</span>' : '') +
      '</div>' +
      '<div class="theme-headline">' + (cur.theme || '(まだテーマが設定されていません)') + '</div>' +
      '<div class="theme-set-at">設定日: ' + (cur.set_at || '—') + '</div>';
  }

  const ownerEl = document.getElementById("theme-tasks-owner");
  const nakaEl = document.getElementById("theme-tasks-nakamura");
  const ichiEl = document.getElementById("theme-tasks-ichikawa");
  const renderTasks = (el, arr) => {
    if (!el) return;
    if (!arr || !arr.length) {
      el.innerHTML = '<li style="color:var(--text-muted);">タスクが設定されていません</li>';
    } else {
      el.innerHTML = arr.map(t => '<li>' + t + '</li>').join("");
    }
  };
  renderTasks(ownerEl, tasks.owner);
  renderTasks(nakaEl, tasks.nakamura);
  renderTasks(ichiEl, tasks.ichikawa);

  const hist = document.getElementById("theme-history");
  if (hist) {
    const items = (data.history || []).slice(0, 12);
    if (!items.length) {
      hist.innerHTML = '<p style="color:var(--text-muted);">過去のテーマがまだありません。</p>';
    } else {
      hist.innerHTML = items.map(h =>
        '<div class="history-row">' +
          '<div class="wk">' + (h.week || '—') + '</div>' +
          '<div><strong>' + (h.theme || '—') + '</strong>' +
            (h.result_summary ? '<div class="summary">' + h.result_summary + '</div>' : '') +
          '</div>' +
          '<div class="summary">' + (h.set_at || '') + '</div>' +
        '</div>'
      ).join("");
    }
  }
}

let _allProducts = [];
let _currentStateFilter = "all";

function renderProducts(data) {
  if (!data) return;
  _allProducts = data.products || [];
  const counts = data.state_counts || {};
  const stateOrder = ["順調", "PV高・カート低", "カート高・購入低", "経過観察", "流入弱"];
  const stateColor = {
    "順調": "good",
    "PV高・カート低": "warn",
    "カート高・購入低": "warn",
    "経過観察": "neutral",
    "流入弱": "muted",
  };

  const sumEl = document.getElementById("product-state-summary");
  if (sumEl) {
    sumEl.innerHTML = stateOrder.map(s =>
      '<div class="state-card ' + (stateColor[s] || 'neutral') + '">' +
        '<div class="count">' + (counts[s] || 0) + '</div>' +
        '<span class="label">' + s + '</span>' +
      '</div>'
    ).join("");
  }

  const filterEl = document.getElementById("product-state-filter");
  if (filterEl) {
    const chips = ["all"].concat(stateOrder);
    filterEl.innerHTML = chips.map(s =>
      '<button class="state-chip ' + (s === _currentStateFilter ? 'active' : '') + '" data-state="' + s + '">' +
        (s === "all" ? "すべて (" + _allProducts.length + ")" : s + " (" + (counts[s] || 0) + ")") +
      '</button>'
    ).join("");
    filterEl.querySelectorAll(".state-chip").forEach(btn => {
      btn.addEventListener("click", () => {
        _currentStateFilter = btn.dataset.state;
        renderProductTable();
      });
    });
  }

  renderProductTable();
}

function renderProductTable() {
  const tbl = document.getElementById("table-products");
  if (!tbl) return;
  const stateColor = {
    "順調": "good",
    "PV高・カート低": "warn",
    "カート高・購入低": "warn",
    "経過観察": "neutral",
    "流入弱": "muted",
  };
  const filtered = _currentStateFilter === "all"
    ? _allProducts
    : _allProducts.filter(p => p.state === _currentStateFilter);
  const rows = filtered.map(p =>
    '<tr>' +
      '<td><span class="state-badge ' + (stateColor[p.state] || 'neutral') + '">' + p.state + '</span></td>' +
      '<td>' + (p.name || '—') + '</td>' +
      '<td class="num">' + num(p.views) + '</td>' +
      '<td class="num">' + num(p.atcs) + '</td>' +
      '<td class="num">' + num(p.buys) + '</td>' +
      '<td class="num">' + (p.atc_per_view || 0).toFixed(1) + '%</td>' +
      '<td class="num">' + (p.buy_per_view || 0).toFixed(1) + '%</td>' +
      '<td class="num">' + yen(p.revenue || 0) + '</td>' +
      '<td class="advice">' + (p.advice || '') + '</td>' +
    '</tr>'
  ).join("");
  tbl.innerHTML =
    '<thead><tr>' +
      '<th>状態</th><th>商品名</th>' +
      '<th class="num">PV</th><th class="num">ATC</th><th class="num">購入</th>' +
      '<th class="num">ATC/V</th><th class="num">買/V</th>' +
      '<th class="num">売上</th><th>推奨アクション</th>' +
    '</tr></thead><tbody>' + rows + '</tbody>';
}

(async () => {
  const [summary, funnel, channels, releases, utm, archive, monthly, themes, products] = await Promise.all([
    load("summary.json"),
    load("funnel.json"),
    load("channels.json"),
    load("releases.json"),
    load("utm_health.json"),
    load("archive.json"),
    load("archive_monthly.json"),
    load("themes.json"),
    load("products.json"),
  ]);
  if (summary && summary.last_updated) {
    document.getElementById("last-updated").textContent = summary.last_updated;
    const side = document.getElementById("last-updated-side");
    if (side) side.textContent = summary.last_updated;
  }
  renderTheme(themes);
  renderProducts(products);
  renderNarrative(summary);
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
  renderArchive(archive);
  renderArchiveMonthly(monthly);
  renderAnomalies(summary);
  renderProductsTop5(funnel);

  // Scroll reveal
  const candidates = document.querySelectorAll(".card, .kpi, .archive-card, .archive-month-card, .state-card");
  candidates.forEach(el => el.classList.add("reveal"));
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });
  candidates.forEach(el => io.observe(el));

  document.querySelectorAll(".tab, .nav-item").forEach(btn => {
    btn.addEventListener("click", () => {
      requestAnimationFrame(() => {
        const targetPage = document.querySelector(".page.active");
        if (!targetPage) return;
        targetPage.querySelectorAll(".reveal").forEach((el, i) => {
          el.classList.remove("in");
          setTimeout(() => el.classList.add("in"), 40 + i * 30);
        });
      });
    });
  });
})();
