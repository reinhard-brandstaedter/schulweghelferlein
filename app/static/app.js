"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  config: null,
  location: null,
  date: null,
  gran: "day",
  statsLocation: "",
  charts: {},
};

// --- Hilfsfunktionen --------------------------------------------------------

function isoDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function daysFromToday(offset) {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return isoDate(d);
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch {}
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

let toastTimer;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Nicht angemeldet");
  }
  if (!res.ok) {
    let detail = `Fehler ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

// --- Anmeldung --------------------------------------------------------------

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-key").focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error");
  err.hidden = true;
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ key: $("#login-key").value }) });
    $("#login-key").value = "";
    await start();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

// --- Tabs -------------------------------------------------------------------

function showTab(name) {
  $$(".tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
  window.scrollTo(0, 0);
  if (name === "stats") loadStats();
  if (name === "history") loadHistory();
}
$$(".tabbar button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// --- Erfassen ---------------------------------------------------------------

function renderLocations() {
  const box = $("#locations");
  box.innerHTML = "";
  for (const loc of state.config.locations) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "choice";
    btn.setAttribute("role", "radio");
    btn.dataset.id = loc.id;
    btn.textContent = loc.name;
    btn.addEventListener("click", () => selectLocation(loc.id));
    box.append(btn);
  }
  const remembered = recall("swh.location");
  if (remembered && state.config.locations.some((l) => l.id === remembered)) selectLocation(remembered);
}

function selectLocation(id) {
  state.location = id;
  store("swh.location", id);
  $$("#locations .choice").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.id === id)));
}

function selectDate(iso) {
  state.date = iso;
  const today = daysFromToday(0);
  const yesterday = daysFromToday(-1);
  $$("[data-date-offset]").forEach((b) => {
    b.setAttribute("aria-checked", String(daysFromToday(Number(b.dataset.dateOffset)) === iso));
  });
  const other = iso !== today && iso !== yesterday;
  $(".chip-date").setAttribute("aria-checked", String(other));
  $("#date-other-label").textContent = other ? formatDate(iso) : "Anderes…";
}

$$("[data-date-offset]").forEach((b) =>
  b.addEventListener("click", () => selectDate(daysFromToday(Number(b.dataset.dateOffset))))
);
$("#date-other").max = daysFromToday(0);
$("#date-other").addEventListener("change", (e) => e.target.value && selectDate(e.target.value));

function counterInput(cat) {
  return $(`.counter[data-cat="${cat}"] input`);
}

function clampCounter(input) {
  const v = Math.max(0, Math.min(999, parseInt(input.value, 10) || 0));
  input.value = v;
  return v;
}

$$(".counter").forEach((row) => {
  const input = $("input", row);
  $$(".step", row).forEach((btn) =>
    btn.addEventListener("click", () => {
      input.value = (parseInt(input.value, 10) || 0) + Number(btn.dataset.step);
      clampCounter(input);
      if (navigator.vibrate) navigator.vibrate(10);
    })
  );
  input.addEventListener("focus", () => input.select());
  input.addEventListener("blur", () => clampCounter(input));
});

function resetEntryForm() {
  ["red", "yellow", "other"].forEach((c) => (counterInput(c).value = 0));
  $("#note").value = "";
  $(".note").open = false;
  selectDate(daysFromToday(0));
}

$("#entry-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.location) {
    toast("Bitte einen Standort wählen", true);
    return;
  }
  const payload = {
    date: state.date,
    location_id: state.location,
    red: clampCounter(counterInput("red")),
    yellow: clampCounter(counterInput("yellow")),
    other: clampCounter(counterInput("other")),
    note: $("#note").value.trim() || null,
  };
  const btn = $("#submit");
  btn.disabled = true;
  try {
    await api("/api/entries", { method: "POST", body: JSON.stringify(payload) });
    const total = payload.red + payload.yellow + payload.other;
    toast(`Gespeichert – ${total} Verstöße. Danke!`);
    resetEntryForm();
  } catch (ex) {
    toast(`Nicht gespeichert: ${ex.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

// --- Verlauf ----------------------------------------------------------------

async function loadHistory() {
  const list = $("#history");
  const entries = await api("/api/entries?limit=50");
  list.innerHTML = "";
  if (!entries.length) {
    list.innerHTML = '<li class="muted">Noch keine Einträge.</li>';
    return;
  }
  for (const e of entries) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div>
        <div class="h-title"></div>
        <div class="h-meta"></div>
      </div>
      <button type="button" class="h-delete">Löschen</button>
      <div class="h-counts">
        <span><span class="swatch sw-red"></span>Rot ${e.red}</span>
        <span><span class="swatch sw-yellow"></span>Gelb ${e.yellow}</span>
        <span><span class="swatch sw-other"></span>Sonstige ${e.other}</span>
      </div>`;
    $(".h-title", li).textContent = e.location_name;
    $(".h-meta", li).textContent = formatDate(e.date);
    if (e.note) {
      const note = document.createElement("div");
      note.className = "h-note";
      note.textContent = e.note;
      li.append(note);
    }
    $(".h-delete", li).addEventListener("click", async () => {
      if (!confirm(`Eintrag „${e.location_name}“ vom ${formatDate(e.date)} löschen?`)) return;
      try {
        await api(`/api/entries/${e.id}`, { method: "DELETE" });
        li.remove();
        toast("Eintrag gelöscht");
      } catch (ex) {
        toast(ex.message, true);
      }
    });
    list.append(li);
  }
}

// --- Statistik --------------------------------------------------------------

const CATEGORY_ORDER = ["red", "other", "yellow"]; // Stapelreihenfolge = validierte Farbreihenfolge
const CATEGORY_NAMES = { red: "Rot", yellow: "Gelb", other: "Sonstige" };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function locationColor(id) {
  const idx = state.config.locations.findIndex((l) => l.id === id);
  return idx >= 0 && idx < 8 ? cssVar(`--loc-${idx + 1}`) : cssVar("--muted");
}

function baseOptions({ horizontal = false } = {}) {
  const text2 = cssVar("--text-2");
  const grid = cssVar("--grid");
  const valueAxis = {
    stacked: true,
    beginAtZero: true,
    grid: { color: grid, drawTicks: false },
    border: { display: false },
    ticks: { color: text2, precision: 0, padding: 6 },
  };
  const catAxis = {
    stacked: true,
    grid: { display: false },
    border: { color: grid },
    ticks: { color: text2, maxRotation: 0, autoSkipPadding: 12 },
  };
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    indexAxis: horizontal ? "y" : "x",
    interaction: { mode: "index", intersect: false, axis: horizontal ? "y" : "x" },
    scales: horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis },
    plugins: {
      legend: {
        position: "bottom",
        labels: { color: text2, usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, boxHeight: 10, padding: 14 },
      },
      tooltip: {
        backgroundColor: cssVar("--surface"),
        titleColor: cssVar("--text"),
        bodyColor: cssVar("--text-2"),
        footerColor: cssVar("--text"),
        borderColor: cssVar("--border"),
        borderWidth: 1,
        padding: 10,
        usePointStyle: true,
        boxPadding: 4,
        filter: (item) => item.raw > 0,
      },
    },
  };
}

function barDataset(label, data, color) {
  return {
    label,
    data,
    backgroundColor: color,
    borderColor: cssVar("--surface"),
    borderWidth: 1,
    borderSkipped: false,
    maxBarThickness: 36,
  };
}

function drawChart(key, canvas, config) {
  state.charts[key]?.destroy();
  state.charts[key] = new Chart(canvas, config);
}

function totalFooter(items) {
  const sum = items.reduce((s, i) => s + i.raw, 0);
  return `Gesamt: ${Number.isInteger(sum) ? sum : sum.toFixed(1)}`;
}

const fmt1 = (n) => (Math.round(n * 10) / 10).toLocaleString("de-DE");

function renderStats(s) {
  const t = s.totals;
  $("#kpi-total").textContent = t.total.toLocaleString("de-DE");
  $("#kpi-red").textContent = t.red.toLocaleString("de-DE");
  $("#kpi-shifts").textContent = t.shifts.toLocaleString("de-DE");
  $("#kpi-avg").textContent = t.shifts ? fmt1(t.total / t.shifts) : "–";
  $("#stats-range").textContent = `Zeitraum: ${formatDate(s.start)} – ${formatDate(s.end)}`;

  // 1) Zeitverlauf nach Art
  const catOpts = baseOptions();
  catOpts.plugins.tooltip.callbacks = { footer: totalFooter };
  drawChart("category", $("#chart-category"), {
    type: "bar",
    data: {
      labels: s.labels,
      datasets: CATEGORY_ORDER.map((c) => barDataset(CATEGORY_NAMES[c], s.by_category[c], cssVar(`--cat-${c}`))),
    },
    options: catOpts,
  });

  // 2) Zeitverlauf nach Standort (nur ohne Standortfilter sinnvoll)
  const showLocTime = !state.statsLocation;
  $("#card-location-time").hidden = !showLocTime;
  if (showLocTime) {
    const opts = baseOptions();
    opts.plugins.tooltip.callbacks = { footer: totalFooter };
    drawChart("locationTime", $("#chart-location-time"), {
      type: "bar",
      data: {
        labels: s.labels,
        datasets: s.by_location.map((l) => barDataset(l.name, l.values, locationColor(l.id))),
      },
      options: opts,
    });
  }

  // 3) Standortvergleich: Ø pro Einsatz, damit unterschiedlich oft besetzte Standorte fair verglichen werden
  const locs = s.location_totals;
  const hOpts = baseOptions({ horizontal: true });
  hOpts.plugins.tooltip.callbacks = {
    label: (item) => ` ${item.dataset.label}: ${fmt1(item.raw)}`,
    footer: (items) => {
      const l = locs[items[0].dataIndex];
      return `Ø gesamt: ${l.shifts ? fmt1(l.total / l.shifts) : "–"} (${l.shifts} Einsätze)`;
    },
  };
  hOpts.scales.y.ticks.autoSkip = false;
  $("#chart-location-box").style.height = `${Math.max(160, locs.length * 44 + 70)}px`;
  drawChart("location", $("#chart-location"), {
    type: "bar",
    data: {
      labels: locs.map((l) => l.name),
      datasets: CATEGORY_ORDER.map((c) =>
        barDataset(CATEGORY_NAMES[c], locs.map((l) => (l.shifts ? l[c] / l.shifts : 0)), cssVar(`--cat-${c}`))
      ),
    },
    options: hOpts,
  });

  renderTables(s);
}

function renderTables(s) {
  const esc = (v) => String(v).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
  const head = "<th>Rot</th><th>Gelb</th><th>Sonstige</th><th>Gesamt</th>";

  $("#table-locations").innerHTML =
    `<thead><tr><th>Standort</th>${head}<th>Einsätze</th><th>Ø</th></tr></thead><tbody>` +
    s.location_totals
      .map((l) => `<tr><td>${esc(l.name)}</td><td>${l.red}</td><td>${l.yellow}</td><td>${l.other}</td>` +
        `<td>${l.total}</td><td>${l.shifts}</td><td>${l.shifts ? fmt1(l.total / l.shifts) : "–"}</td></tr>`)
      .join("") +
    "</tbody>";

  $("#table-time").innerHTML =
    `<thead><tr><th>Zeitraum</th>${head}<th>Einsätze</th></tr></thead><tbody>` +
    s.labels
      .map((label, i) => {
        const r = s.by_category.red[i], y = s.by_category.yellow[i], o = s.by_category.other[i];
        return `<tr><td>${esc(label)}</td><td>${r}</td><td>${y}</td><td>${o}</td><td>${r + y + o}</td><td>${s.shifts[i]}</td></tr>`;
      })
      .join("") +
    "</tbody>";
}

let lastStats = null;
async function loadStats() {
  const params = new URLSearchParams({ granularity: state.gran, end: daysFromToday(0) });
  if (state.statsLocation) params.set("location", state.statsLocation);
  try {
    lastStats = await api(`/api/stats?${params}`);
    renderStats(lastStats);
  } catch (ex) {
    toast(ex.message, true);
  }
}

$$(".segmented button").forEach((b) =>
  b.addEventListener("click", () => {
    state.gran = b.dataset.gran;
    $$(".segmented button").forEach((x) => x.classList.toggle("active", x === b));
    loadStats();
  })
);
$("#stats-location").addEventListener("change", (e) => {
  state.statsLocation = e.target.value;
  loadStats();
});

// Diagramme bei Wechsel Hell/Dunkel neu zeichnen, damit sie die passenden Farben bekommen
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (lastStats && !$("#tab-stats").hidden) renderStats(lastStats);
});

// --- Start ------------------------------------------------------------------

async function start() {
  state.config = await api("/api/config");
  document.title = state.config.title;
  $("#app-title").textContent = state.config.title;
  renderLocations();
  const sel = $("#stats-location");
  sel.length = 1;
  for (const loc of state.config.locations) sel.add(new Option(loc.name, loc.id));
  resetEntryForm();
  $("#login").hidden = true;
  $("#app").hidden = false;
}

(async () => {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  const { authorized } = await api("/api/session");
  if (authorized) await start();
  else showLogin();
})();
