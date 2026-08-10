const BAR_AREA_PX = 56;
const REFRESH_MS = 5000;
const DIAL_TICKS = 32;
const COLLAPSED_APP_COUNT = 4;
const LIMIT_MAX_HOURS = 23; // pairs with 5-minute steps up to :55, so max is 23h 55m
const GOAL_HOUR_OPTIONS = Array.from({ length: 16 }, (_, i) => i + 1); // 1h..16h

const ICONS = {
  limit: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 1.5M9 2h6"/></svg>',
};

function applyTheme(theme) {
  const root = document.documentElement;
  root.classList.add("theme-switching");
  root.setAttribute("data-theme", theme);
  void root.offsetHeight; // flush the swap while transitions are muted
  requestAnimationFrame(() => root.classList.remove("theme-switching"));

  document.getElementById("theme-toggle").textContent = theme;
  localStorage.setItem("theme", theme);
}

function initTheme() {
  applyTheme(localStorage.getItem("theme") || "dark");
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    applyTheme(current === "dark" ? "light" : "dark");
  });
}

const state = {
  selectedDay: null,
  historyDays: 7,
  appsExpanded: false,
  limitEditorFor: null, // processName of the row with its preset editor open
};

// The 5s poll would otherwise rebuild these lists every tick, dropping hover
// state and flickering. Re-render only when the data actually changed.
const lastRender = { history: null, apps: null };

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function minutesLabel(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

// Stepped dial (not a smooth arc — old hardware couldn't render one), lit
// proportionally for today's total against the daily limit.
function renderDial(totalSeconds, goalSeconds) {
  const g = document.getElementById("ticks");
  g.innerHTML = "";
  const fraction = goalSeconds > 0 ? Math.min(totalSeconds / goalSeconds, 1) : 0;
  const litCount = Math.round(DIAL_TICKS * fraction);

  for (let i = 0; i < DIAL_TICKS; i++) {
    const angle = (i / DIAL_TICKS) * Math.PI * 2 - Math.PI / 2;
    const inner = 36, outer = 44;
    const x1 = 50 + Math.cos(angle) * inner, y1 = 50 + Math.sin(angle) * inner;
    const x2 = 50 + Math.cos(angle) * outer, y2 = 50 + Math.sin(angle) * outer;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", x1);
    line.setAttribute("y1", y1);
    line.setAttribute("x2", x2);
    line.setAttribute("y2", y2);
    line.setAttribute("class", "dial-tick" + (i < litCount ? " lit" : ""));
    line.setAttribute("stroke-linecap", "round");
    g.appendChild(line);
  }
}

function renderHistory(history, dense) {
  const key = JSON.stringify([history, dense]);
  if (lastRender.history === key) return;
  lastRender.history = key;

  const container = document.getElementById("week-chart");
  container.innerHTML = "";
  container.classList.toggle("dense", dense);

  const max = Math.max(...history.map((d) => d.seconds), 1);

  history.forEach((d) => {
    const heightPx = d.seconds ? Math.max((d.seconds / max) * BAR_AREA_PX, 2) : 0;
    const col = el(`
      <div class="week-col ${d.isSelected ? "selected" : ""}" title="${escapeHtml(d.weekday)} — ${escapeHtml(d.label)}">
        <div class="week-bar-track">
          <div class="week-bar ${d.isToday ? "today" : ""} ${d.seconds ? "" : "empty"}" style="height:${heightPx}px"></div>
        </div>
        <div class="week-day ${d.isToday ? "today" : ""}">${escapeHtml(d.weekday)}</div>
      </div>
    `);
    col.addEventListener("click", () => {
      state.selectedDay = d.isToday ? null : d.date;
      refresh();
    });
    container.appendChild(col);
  });
}

function renderApps(apps, isToday) {
  // Icons are large data URIs, so key on presence rather than content.
  const key = JSON.stringify([
    apps.map((a) => [a.name, a.seconds, !!a.icon, a.limitMinutes, a.limitExceeded]),
    isToday,
    state.appsExpanded,
    state.limitEditorFor,
  ]);
  if (lastRender.apps === key) return;
  lastRender.apps = key;

  const container = document.getElementById("app-list");
  const moreBtn = document.getElementById("show-more-apps");
  container.innerHTML = "";

  if (!apps.length) {
    const note = isToday
      ? "No activity tracked yet today."
      : "No activity recorded for this day.";
    container.appendChild(el(`<div class="empty-note">${note}</div>`));
    moreBtn.classList.add("hidden");
    return;
  }

  // Collapsing to hide one row costs a full-width button to save a 26px row —
  // so the fold only earns its place from two hidden rows up.
  const collapsible = apps.length - COLLAPSED_APP_COUNT >= 2;
  const hidden = collapsible ? apps.length - COLLAPSED_APP_COUNT : 0;
  const visible =
    state.appsExpanded || !collapsible ? apps : apps.slice(0, COLLAPSED_APP_COUNT);

  moreBtn.classList.toggle("hidden", hidden <= 0);
  moreBtn.textContent = state.appsExpanded ? "see less" : `see ${hidden} more`;

  const maxSeconds = Math.max(...apps.map((a) => a.seconds), 1);

  visible.forEach((a, i) => {
    const name = escapeHtml(a.name);
    const glyph = a.icon
      ? `<img class="app-icon" src="${a.icon}" alt="" />`
      : `<span class="app-fallback">${escapeHtml(a.name.charAt(0).toUpperCase())}</span>`;

    const limitBadge = a.limitExceeded
      ? '<span class="over-tag">[over]</span>'
      : a.limitMinutes
      ? `<span class="limit-note">of ${escapeHtml(minutesLabel(a.limitMinutes))}</span>`
      : "";

    const pct = Math.round((a.seconds / maxSeconds) * 100);
    const op = i === 0 ? 1 : 0.55;

    const wrap = el(`
      <div class="app-row-wrap">
        <div class="app-row ${a.limitExceeded ? "over-limit" : ""}" title="${name} — ${escapeHtml(a.label)}">
          ${glyph}
          <span class="app-name">${name}</span>
          <span class="app-bar"><span class="app-bar-fill" style="--pct:${pct}%; --op:${op}"></span></span>
          ${limitBadge}
          <span class="app-time">${escapeHtml(a.label)}</span>
          <button class="limit-btn" type="button" title="Set a daily limit for ${name}">${ICONS.limit}</button>
        </div>
      </div>
    `);

    wrap.querySelector(".limit-btn").addEventListener("click", () => {
      state.limitEditorFor = state.limitEditorFor === a.processName ? null : a.processName;
      refresh();
    });

    if (state.limitEditorFor === a.processName) {
      wrap.appendChild(buildLimitEditor(a));
    }

    container.appendChild(wrap);
  });
}

// Hours/minutes picker + Cancel/OK, in place of fixed presets — 0h 0m via OK
// clears the limit, same as set_app_limit already treats 0 as "no limit".
function buildLimitEditor(app) {
  const editor = el(`
    <div class="limit-editor">
      <span class="limit-editor-label">daily limit for ${escapeHtml(app.name)}</span>
      <div class="time-picker-row">
        <select class="select-input time-picker-hours"></select>
        <span class="time-picker-sep">h</span>
        <select class="select-input time-picker-minutes"></select>
        <span class="time-picker-sep">m</span>
      </div>
      <div class="limit-editor-actions">
        ${app.limitMinutes ? '<button class="ghost-btn" type="button" data-action="reset">reset</button>' : "<span></span>"}
        <div class="limit-editor-actions-right">
          <button class="ghost-btn" type="button" data-action="cancel">cancel</button>
          <button class="ghost-btn primary" type="button" data-action="ok">ok</button>
        </div>
      </div>
    </div>
  `);

  const hoursSelect = editor.querySelector(".time-picker-hours");
  const minutesSelect = editor.querySelector(".time-picker-minutes");
  hoursSelect.innerHTML = Array.from({ length: LIMIT_MAX_HOURS + 1 }, (_, h) => `<option value="${h}">${h}</option>`).join("");
  minutesSelect.innerHTML = Array.from({ length: 12 }, (_, i) => i * 5)
    .map((m) => `<option value="${m}">${m}</option>`)
    .join("");

  const current = app.limitMinutes || 0;
  hoursSelect.value = Math.floor(current / 60);
  // Round to the nearest 5-minute option so a pre-existing value doesn't
  // leave the dropdown showing nothing selected.
  minutesSelect.value = Math.round((current % 60) / 5) * 5;

  const close = () => {
    state.limitEditorFor = null;
    refresh();
  };

  editor.querySelector('[data-action="cancel"]').addEventListener("click", close);
  editor.querySelector('[data-action="ok"]').addEventListener("click", async () => {
    const total = Number(hoursSelect.value) * 60 + Number(minutesSelect.value);
    await window.pywebview.api.set_app_limit(app.processName, total);
    close();
  });
  editor.querySelector('[data-action="reset"]')?.addEventListener("click", async () => {
    await window.pywebview.api.set_app_limit(app.processName, 0);
    close();
  });

  return editor;
}

function setSegmentActive(segment, value) {
  segment.dataset.value = value;
  [...segment.children].forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.value === String(value));
  });
}

function populateGoalSelect() {
  const select = document.getElementById("goal-select");
  select.innerHTML = GOAL_HOUR_OPTIONS.map((h) => `<option value="${h}">${h}h</option>`).join("");
}

async function refresh() {
  let data;
  try {
    data = await window.pywebview.api.get_state(state.selectedDay, state.historyDays);
  } catch (err) {
    // Swallow so one bad poll doesn't become an unhandled rejection — the
    // next 5s tick just tries again instead of the UI silently freezing.
    console.error("refresh() failed, will retry on next poll:", err);
    return;
  }

  document.getElementById("total-value").textContent = data.totalLabel;
  document.getElementById("day-label").textContent = "/ " + data.dayLabel.toLowerCase();

  const limitLine = document.getElementById("limit-line");
  const overText = data.goalExceeded ? ' <span class="over">[over]</span>' : "";
  // "limit 5h" alone says nothing about where the day stands against it; the
  // percentage is the part that's actually actionable at a glance.
  const pct =
    data.goalSeconds > 0
      ? Math.round((data.totalSeconds / data.goalSeconds) * 100)
      : null;
  const pctText =
    pct === null
      ? ""
      : ` · <span class="${data.goalExceeded ? "over" : "val"}">${pct}%</span>`;
  limitLine.innerHTML =
    `limit <span class="val">${data.goalHours}h</span>${pctText}${overText}`;

  renderDial(data.totalSeconds, data.goalSeconds);
  renderHistory(data.history, data.historyDays > 7);
  renderApps(data.topApps, data.isToday);

  const breakSub = document.getElementById("break-sub");
  const snooze = document.getElementById("snooze-btn");
  if (data.isToday) {
    breakSub.classList.remove("hidden");
    document.getElementById("break-time").textContent = `in ${data.breakInLabel}`;
    snooze.textContent = `snooze ${data.snoozeMinutes}m`;
    snooze.classList.remove("hidden");
  } else {
    breakSub.classList.add("hidden");
  }

  document.getElementById("back-to-today").classList.toggle("hidden", data.isToday);
  document.getElementById("chart-hint").classList.toggle("hidden", !data.isToday);

  setSegmentActive(document.getElementById("break-segment"), data.breakMinutes);
  setSegmentActive(document.getElementById("range-segment"), data.historyDays);

  const goalSelect = document.getElementById("goal-select");
  if (document.activeElement !== goalSelect) {
    goalSelect.value = String(Math.min(16, Math.max(1, Math.round(data.goalHours))));
  }
}

function flashSaved() {
  const status = document.getElementById("save-status");
  status.textContent = "saved";
  setTimeout(() => (status.textContent = ""), 1200);
}

async function saveSettings() {
  const breakMinutes = document.getElementById("break-segment").dataset.value;
  const goalHours = document.getElementById("goal-select").value;
  await window.pywebview.api.save_settings(breakMinutes, goalHours);
  flashSaved();
  refresh(); // reflect the new goal/interval now rather than on the next poll
}

function initControls() {
  document.getElementById("break-segment").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    setSegmentActive(e.currentTarget, btn.dataset.value);
    saveSettings();
  });

  document.getElementById("range-segment").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.historyDays = Number(btn.dataset.value);
    refresh();
  });

  document.getElementById("back-to-today").addEventListener("click", () => {
    state.selectedDay = null;
    refresh();
  });

  document.getElementById("show-more-apps").addEventListener("click", () => {
    state.appsExpanded = !state.appsExpanded;
    refresh();
  });

  document.getElementById("snooze-btn").addEventListener("click", async () => {
    await window.pywebview.api.snooze_break();
    refresh();
  });

  document.getElementById("goal-select").addEventListener("change", saveSettings);
}

let initialized = false;

function boot() {
  // Defensive: if this ever runs more than once, re-running
  // populateGoalSelect() would wipe the dropdown's selected value (rebuilding
  // a <select>'s options resets selection) and duplicate every listener.
  if (initialized) return;
  initialized = true;

  try {
    initTheme();
    populateGoalSelect();
    initControls();
    refresh();
    setInterval(refresh, REFRESH_MS);
  } catch (err) {
    reportFatal(err);
  }
}

// Booting on the pywebviewready event alone is a race: if the API is injected
// and the event dispatched before this file finishes parsing, the listener is
// attached too late and the app sits there fully unpopulated with no error.
// So: take the event if it comes, but also poll for the API, and boot on
// whichever wins.
window.addEventListener("pywebviewready", boot);

const bootPoll = setInterval(() => {
  if (initialized) {
    clearInterval(bootPoll);
  } else if (window.pywebview && window.pywebview.api) {
    clearInterval(bootPoll);
    boot();
  }
}, 100);

function reportFatal(err) {
  const message = (err && (err.stack || err.message)) || String(err);
  console.error("Screen Timer failed to start:", message);
  // An empty window gives the user nothing to report. Surfacing the error in
  // the UI beats a silent blank panel, especially in a packaged build where
  // there is no console to look at.
  const banner = document.createElement("pre");
  banner.style.cssText =
    "white-space:pre-wrap;word-break:break-word;padding:12px;margin:0;" +
    "font:11px/1.4 inherit;color:#f28c28;border-bottom:1px solid #2b2620;";
  banner.textContent = "Screen Timer failed to start:\n" + message;
  document.body.prepend(banner);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.log_error) {
    window.pywebview.api.log_error(message);
  }
}

window.addEventListener("error", (e) =>
  reportFatal(e.error || e.message || "unknown error")
);
window.addEventListener("unhandledrejection", (e) => reportFatal(e.reason));

// If the bridge never turns up, the window would otherwise sit blank forever
// with nothing to report. Say so, and say what was missing.
setTimeout(() => {
  if (initialized) return;
  clearInterval(bootPoll);
  reportFatal(
    "The UI never received the pywebview bridge.\n" +
      "window.pywebview      : " + !!window.pywebview + "\n" +
      "window.pywebview.api  : " + !!(window.pywebview && window.pywebview.api) + "\n" +
      "document.readyState   : " + document.readyState
  );
}, 6000);
