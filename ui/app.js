"use strict";

// Data on disk only changes every 10s (the tracker's save interval), so
// asking more often than that just repeats work.
const VISIBLE_POLL_MS = 10000;
// While the window is hidden in the tray, the only call is a cheap
// "am I visible yet?" check. Opening the window wakes the UI immediately
// (Python nudges it, and so does any focus or mouse movement).
const HIDDEN_POLL_MS = 30000;
const COLLAPSED_APPS = 5;
const LIMIT_MAX_HOURS = 23;
const BREAK_OPTIONS = [15, 30, 45, 60, 90];
const GOAL_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];

const state = {
  kind: "day", // day | week | month | all
  anchor: null, // ISO date inside the shown period; null = the current one
  settingsOpen: false,
  appsExpanded: false,
  limitEditorFor: null, // processName whose limit editor is open
  hidden: false,
  live: null, // get_state()
  period: null, // get_period()
  periodKey: null,
};

const iconCache = new Map(); // processName -> data URI or null
const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;
const periodKey = () => `${state.kind}|${state.anchor || ""}`;

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

// Rewrites an element only when its content actually changed, so the
// 10-second refresh doesn't knock hover or focus off whatever you're on.
function setHTML(el, html) {
  if (el.__html === html) return;
  el.__html = html;
  el.innerHTML = html;
}

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

function hoursLabel(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function heatLevel(seconds) {
  if (seconds <= 0) return 0;
  const hours = seconds / 3600;
  if (hours < 2) return 1;
  if (hours < 4) return 2;
  if (hours < 6) return 3;
  if (hours < 8) return 4;
  return 5;
}

// ---- refresh scheduling: one refresh at a time, never overlapping ----

let timer = null;
let running = false;
let rerun = false;

async function refresh() {
  if (running) {
    rerun = true;
    return;
  }
  running = true;
  clearTimeout(timer);
  let delay = VISIBLE_POLL_MS;
  try {
    do {
      rerun = false;
      delay = await refreshOnce();
    } while (rerun);
  } catch (err) {
    console.error("refresh failed, will retry:", err);
  } finally {
    running = false;
    timer = setTimeout(refresh, delay);
  }
}

async function refreshOnce() {
  if (state.hidden) {
    if (!(await api().is_visible())) return HIDDEN_POLL_MS;
    state.hidden = false;
  }

  const live = await api().get_state();
  if (!live.visible) {
    state.hidden = true;
    return HIDDEN_POLL_MS;
  }
  state.live = live;

  // A past week or month can't change, so it's fetched once; anything that
  // includes today is refreshed so the numbers keep counting.
  const key = periodKey();
  if (state.periodKey !== key || !state.period || state.period.isCurrent) {
    const period = await api().get_period(state.kind, state.anchor);
    if (key !== periodKey()) {
      rerun = true; // navigated while this was in flight; fetch the new one
      return VISIBLE_POLL_MS;
    }
    await loadIcons(period.apps);
    state.period = period;
    state.periodKey = key;
  }

  render();
  return VISIBLE_POLL_MS;
}

function wake() {
  if (!state.hidden && running) return;
  state.hidden = false;
  refresh();
}

window.__stWake = wake;

async function loadIcons(apps) {
  const missing = apps.map((a) => a.processName).filter((p) => !iconCache.has(p));
  if (!missing.length) return;
  try {
    const found = await api().get_icons(missing);
    missing.forEach((p) => iconCache.set(p, found[p] || null));
  } catch (err) {
    missing.forEach((p) => iconCache.set(p, null));
  }
}

// ---- navigation ----

function go(kind, anchor = null) {
  state.kind = kind;
  state.anchor = anchor;
  state.appsExpanded = false;
  state.limitEditorFor = null;
  renderTabs();
  // Until the new period arrives, what's on screen belongs to the old one;
  // dim it rather than let it pass for the answer.
  $("main-view").classList.toggle("stale", state.periodKey !== periodKey());
  refresh();
}

function step(direction) {
  const p = state.period;
  // A period still loading has no arrows yet; the old one's would jump
  // somewhere unrelated.
  if (!p || state.settingsOpen || state.periodKey !== periodKey()) return;
  const target = direction < 0 ? p.prevAnchor : p.nextAnchor;
  if (target) go(state.kind, target);
}

// ---- rendering ----

function render() {
  renderTabs();
  renderSettings();
  const p = state.period;
  if (!p) return;
  $("main-view").classList.toggle("stale", state.periodKey !== periodKey());

  setText($("period-title"), p.title);
  $("period-nav").classList.toggle("hidden", p.kind === "all");
  $("prev-btn").disabled = !p.prevAnchor;
  $("next-btn").disabled = !p.nextAnchor;
  setText($("big"), p.totalLabel);

  renderLines(p);
  renderTodayExtras(p);
  renderChart(p);
  renderStats(p);
  renderApps(p);
}

function renderTabs() {
  document.querySelectorAll('[role="tab"]').forEach((tab) => {
    const selected = tab.dataset.kind === state.kind;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  $("main-view").classList.toggle("hidden", state.settingsOpen);
  $("settings-view").classList.toggle("hidden", !state.settingsOpen);
}

function compareHTML(c, perDay) {
  if (!c) return "";
  if (c.direction === "same") return `About the same as ${esc(c.against)}`;
  const up = c.direction === "up";
  const amount = perDay ? `${esc(c.label)} a day` : esc(c.label);
  return (
    `<span class="${up ? "up" : "down"}" aria-hidden="true">${up ? "▲" : "▼"}</span> ` +
    `${amount} ${up ? "more" : "less"} than ${esc(c.against)}`
  );
}

function renderLines(p) {
  let one = "";
  let two = "";
  if (p.kind === "day") {
    if (!p.totalSeconds) one = "Nothing tracked yet.";
    else if (p.previousLabel) one = `Yesterday you spent ${esc(p.previousLabel)}`;
    else one = compareHTML(p.compare, false);
  } else if (p.kind === "all") {
    one = p.activeDays ? `${p.activeDays} ${p.activeDays === 1 ? "day" : "days"} tracked` : "Nothing tracked yet.";
  } else {
    one = p.activeDays ? `${esc(p.avgLabel)} a day on average` : "Nothing tracked yet.";
    two = compareHTML(p.compare, true);
  }
  setHTML($("line1"), one);
  setHTML($("line2"), two);
}

function renderTodayExtras(p) {
  const live = state.live;
  if (p.kind !== "day" || !live) {
    setHTML($("today-extras"), "");
    return;
  }

  let html = "";
  if (live.goalSeconds > 0) {
    const pct = Math.round((p.totalSeconds / live.goalSeconds) * 100);
    const over = p.totalSeconds > live.goalSeconds;
    const goal = hoursLabel(Math.round(live.goalSeconds / 60));
    const text = over
      ? `<span class="warn">Over your ${goal} daily limit</span>`
      : `<strong>${pct}%</strong> of your ${goal} daily limit`;
    html +=
      `<div class="limit">` +
      `<div class="track${over ? " over" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" ` +
      `aria-valuenow="${Math.min(pct, 100)}" aria-label="Daily limit used"><span style="width:${Math.min(pct, 100)}%"></span></div>` +
      `<div class="limit-text">${text}</div></div>`;
  }
  if (p.isCurrent) {
    html +=
      `<div class="break-row"><span>Next break in <strong>${esc(live.breakInLabel)}</strong></span>` +
      `<button class="link" type="button" data-action="snooze">Snooze ${live.snoozeMinutes}m</button></div>`;
  }
  setHTML($("today-extras"), html);
}

function renderChart(p) {
  const chart = $("chart");
  let html = "";
  if (p.kind === "week") html = weekChart(p);
  else if (p.kind === "month") html = monthChart(p);
  else if (p.kind === "all" && p.months.length) html = monthsList(p);
  chart.classList.toggle("hidden", !html);
  setHTML(chart, html);
}

function weekChart(p) {
  const goal = state.live ? state.live.goalSeconds : 0;
  const most = Math.max(...p.days.map((d) => d.seconds), 1);
  // Show the limit line only when it fits without squashing the bars flat.
  const showGoal = goal > 0 && goal <= most * 1.5;
  const scale = showGoal ? Math.max(most, goal) : most;
  const SLOT = 120;

  const bars = p.days
    .map((d) => {
      const height = d.seconds ? Math.max(4, Math.round((d.seconds / scale) * SLOT)) : 0;
      const label = d.isFuture ? "" : d.seconds ? d.label : "–";
      const aria = `${d.weekday} ${d.dayNum}: ${d.isFuture ? "not yet" : d.label}`;
      return (
        `<button class="bar${d.isToday ? " today" : ""}" type="button" data-action="goto" data-kind="day" ` +
        `data-anchor="${d.date}" aria-label="${esc(aria)}" ${d.isFuture ? "disabled" : ""}>` +
        `<span class="bar-value">${esc(label)}</span>` +
        `<span class="bar-slot"><span class="bar-fill" style="height:${height}px"></span></span>` +
        `<span class="bar-day">${esc(d.weekday)}</span></button>`
      );
    })
    .join("");

  // 16px value row + 6px gap sits above each slot; 6px gap + day row below.
  const line = showGoal
    ? `<div class="limit-line" aria-hidden="true" style="bottom:${Math.round((goal / scale) * SLOT) + 6 + 18}px">` +
      `<span>${hoursLabel(Math.round(goal / 60))} limit</span></div>`
    : "";
  return `<div class="bars">${bars}${line}</div>`;
}

function monthChart(p) {
  const head = ["M", "T", "W", "T", "F", "S", "S"].map((d) => `<span>${d}</span>`).join("");
  const blanks = '<span class="cell blank"></span>'.repeat(p.days[0].weekdayIndex);
  const cells = p.days
    .map((d) => {
      if (d.isFuture) return `<span class="cell future" aria-hidden="true">${d.dayNum}</span>`;
      const level = heatLevel(d.seconds);
      const cls = ["cell", level ? `h${level}` : "", d.isToday ? "today" : ""].filter(Boolean).join(" ");
      const aria = `${d.weekday} ${d.dayNum}: ${d.seconds ? d.label : "nothing tracked"}`;
      return (
        `<button class="${cls}" type="button" data-action="goto" data-kind="day" data-anchor="${d.date}" ` +
        `aria-label="${esc(aria)}" title="${esc(aria)}">${d.dayNum}</button>`
      );
    })
    .join("");
  const legend =
    `<div class="legend" aria-hidden="true">Less` +
    [1, 2, 3, 4, 5].map((n) => `<i style="background:var(--heat-${n})"></i>`).join("") +
    `More</div>`;
  return `<div class="cal-head" aria-hidden="true">${head}</div><div class="cal">${blanks}${cells}</div>${legend}`;
}

function monthsList(p) {
  const most = Math.max(...p.months.map((m) => m.seconds), 1);
  const rows = p.months
    .map(
      (m) =>
        `<button class="month-row" type="button" data-action="goto" data-kind="month" data-anchor="${m.anchor}" ` +
        `aria-label="${esc(m.title)}: ${esc(m.label)}">` +
        `<span class="month-name">${esc(m.title)}</span>` +
        `<span class="app-bar"><span style="width:${Math.round((m.seconds / most) * 100)}%"></span></span>` +
        `<span class="month-time">${esc(m.label)}</span></button>`
    )
    .join("");
  return `<h2 class="section-title">By month</h2><div class="months">${rows}</div>`;
}

function renderStats(p) {
  const stats = $("stats");
  let html = "";
  if (p.kind === "all" && p.activeDays) {
    const busiest = p.busiest
      ? `<button class="stat" type="button" data-action="goto" data-kind="day" data-anchor="${p.busiest.date}">` +
        `<div class="stat-label">Busiest day</div><div class="stat-value">${esc(p.busiest.label)}</div>` +
        `<div class="stat-sub">${esc(p.busiest.title)}</div></button>`
      : "";
    html =
      `<div class="stat"><div class="stat-label">Daily average</div><div class="stat-value">${esc(p.avgLabel)}</div>` +
      `<div class="stat-sub">on a typical day</div></div>` +
      busiest;
  }
  stats.classList.toggle("hidden", !html);
  setHTML(stats, html);
}

function renderApps(p) {
  const list = $("app-list");
  // An open limit editor holds the user's half-made choice in its dropdowns;
  // re-rendering would reset them mid-edit.
  if (state.limitEditorFor && list.querySelector(".editor")) return;

  if (!p.apps.length) {
    const msg = p.kind === "day" && p.isCurrent ? "Nothing yet today. Apps show up here as you use them." : "Nothing tracked.";
    setHTML(list, `<p class="empty">${msg}</p>`);
    return;
  }

  const most = p.apps[0].seconds;
  const collapsible = p.apps.length > COLLAPSED_APPS + 1;
  const shown = state.appsExpanded || !collapsible ? p.apps : p.apps.slice(0, COLLAPSED_APPS);
  const canLimit = p.kind === "day";

  let html = shown.map((a) => appRow(a, most, canLimit)).join("");
  if (collapsible) {
    const label = p.appCount > p.apps.length ? `See top ${p.apps.length} apps` : `See all ${p.apps.length} apps`;
    html += `<button class="link more" type="button" data-action="more-apps">${
      state.appsExpanded ? "Show fewer" : label
    }</button>`;
  }
  setHTML(list, html);
}

function appRow(a, most, canLimit) {
  const icon = iconCache.get(a.processName);
  const glyph = icon
    ? `<img src="${icon}" alt="" />`
    : `<span class="letter">${esc(a.name.charAt(0).toUpperCase())}</span>`;
  let note = "";
  if (canLimit && a.limitMinutes) {
    note = a.limitExceeded
      ? `<div class="app-note warn">Over its ${hoursLabel(a.limitMinutes)} limit</div>`
      : `<div class="app-note">Limit ${hoursLabel(a.limitMinutes)} a day</div>`;
  }
  const limitBtn = canLimit
    ? `<button class="icon-btn small" type="button" data-action="limit-open" data-proc="${esc(a.processName)}" ` +
      `aria-label="Set a daily limit for ${esc(a.name)}" title="Set a daily limit">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
      `<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 1.5M9 2h6"/></svg></button>`
    : "";
  const row =
    `<div class="app-row"><span class="app-icon">${glyph}</span><div class="app-main">` +
    `<div class="app-top"><span class="app-name">${esc(a.name)}</span><span class="app-time">${esc(a.label)}</span></div>` +
    `<div class="app-bar"><span style="width:${Math.max(2, Math.round((a.seconds / most) * 100))}%"></span></div>` +
    `${note}</div>${limitBtn}</div>`;
  return state.limitEditorFor === a.processName ? row + limitEditor(a) : row;
}

function limitEditor(a) {
  const current = a.limitMinutes || 0;
  const hours = Array.from({ length: LIMIT_MAX_HOURS + 1 }, (_, h) =>
    `<option value="${h}" ${h === Math.floor(current / 60) ? "selected" : ""}>${h}</option>`
  ).join("");
  const nearest = Math.round((current % 60) / 5) * 5;
  const minutes = Array.from({ length: 12 }, (_, i) => i * 5)
    .map((m) => `<option value="${m}" ${m === nearest ? "selected" : ""}>${m}</option>`)
    .join("");
  return (
    `<div class="editor"><div class="editor-label">Daily limit for ${esc(a.name)}</div>` +
    `<div class="editor-row"><select class="select" id="limit-hours" aria-label="Hours">${hours}</select>h ` +
    `<select class="select" id="limit-minutes" aria-label="Minutes">${minutes}</select>m</div>` +
    `<div class="editor-actions"><button class="link" type="button" data-action="limit-ok">Save</button>` +
    `<button class="link quiet" type="button" data-action="limit-cancel">Cancel</button>` +
    (a.limitMinutes ? `<button class="link quiet" type="button" data-action="limit-reset">Remove limit</button>` : "") +
    `</div></div>`
  );
}

function renderSettings() {
  const live = state.live;
  if (!live) return;
  setHTML(
    $("break-options"),
    BREAK_OPTIONS.map(
      (m) =>
        `<button class="option" type="button" data-action="break" data-value="${m}" ` +
        `aria-pressed="${m === live.breakMinutes}">${hoursLabel(m)}</button>`
    ).join("")
  );
  setHTML(
    $("login-options"),
    [true, false]
      .map(
        (on) =>
          `<button class="option" type="button" data-action="login" data-value="${on}" ` +
          `aria-pressed="${on === live.startOnLogin}">${on ? "On" : "Off"}</button>`
      )
      .join("")
  );
  const select = $("goal-select");
  if (document.activeElement !== select) {
    select.value = String(Math.round(live.goalHours));
  }
}

function populateGoalSelect() {
  $("goal-select").innerHTML = GOAL_OPTIONS.map(
    (h) => `<option value="${h}">${h ? `${h} hours` : "No limit"}</option>`
  ).join("");
}

// ---- actions ----

let savedTimer = null;
function flashSaved() {
  const status = $("save-status");
  status.textContent = "Saved";
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (status.textContent = ""), 1500);
}

async function saveSettings(breakMinutes, goalHours) {
  await api().save_settings(breakMinutes, goalHours);
  flashSaved();
  state.periodKey = null; // the limit shows in the Day view; refetch
  refresh();
}

async function closeLimitEditor(save) {
  if (save !== undefined) await api().set_app_limit(state.limitEditorFor, save);
  state.limitEditorFor = null;
  state.periodKey = null;
  $("app-list").__html = null;
  refresh();
}

const actions = {
  tab: (el) => go(el.dataset.kind),
  goto: (el) => go(el.dataset.kind, el.dataset.anchor),
  prev: () => step(-1),
  next: () => step(1),
  snooze: async () => {
    await api().snooze_break();
    refresh();
  },
  "more-apps": () => {
    state.appsExpanded = !state.appsExpanded;
    render();
  },
  "limit-open": (el) => {
    const proc = el.dataset.proc;
    state.limitEditorFor = state.limitEditorFor === proc ? null : proc;
    $("app-list").__html = null;
    render();
    $("limit-hours")?.focus();
  },
  "limit-ok": () => closeLimitEditor(Number($("limit-hours").value) * 60 + Number($("limit-minutes").value)),
  "limit-cancel": () => closeLimitEditor(),
  "limit-reset": () => closeLimitEditor(0),
  "open-settings": () => {
    state.settingsOpen = true;
    renderTabs();
    document.querySelector('#settings-view [data-action="close-settings"]').focus();
  },
  "close-settings": () => {
    state.settingsOpen = false;
    renderTabs();
    document.querySelector('[data-action="open-settings"]').focus();
  },
  break: (el) => saveSettings(Number(el.dataset.value), $("goal-select").value),
  login: async (el) => {
    await api().set_start_on_login(el.dataset.value === "true");
    flashSaved();
    refresh();
  },
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el || el.disabled) return;
  const action = actions[el.dataset.action];
  if (action) action(el);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (state.limitEditorFor) closeLimitEditor();
    else if (state.settingsOpen) actions["close-settings"]();
    return;
  }

  const inTabs = e.target.closest && e.target.closest('[role="tablist"]');
  if (inTabs && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const i = tabs.indexOf(e.target);
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    next.focus();
    go(next.dataset.kind);
    e.preventDefault();
    return;
  }

  // Left/right steps through days, weeks or months, unless a control that
  // uses those keys itself has focus.
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "select" || tag === "input") return;
  if (e.key === "ArrowLeft") step(-1);
  if (e.key === "ArrowRight") step(1);
});

// Anything that means someone is looking wakes a hidden-mode UI at once.
["focus", "pointermove", "keydown"].forEach((type) =>
  window.addEventListener(type, () => state.hidden && wake(), { passive: true })
);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") wake();
});

// ---- boot ----

let initialized = false;

function boot() {
  // Defensive: running twice would duplicate the refresh loop.
  if (initialized) return;
  initialized = true;
  try {
    populateGoalSelect();
    $("goal-select").addEventListener("change", (e) =>
      saveSettings(state.live ? state.live.breakMinutes : 30, e.target.value)
    );
    refresh();
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
    "font:12px/1.4 inherit;color:#5aa3ff;border-bottom:1px solid #2c2c32;";
  banner.textContent = "Screen Timer failed to start:\n" + message;
  document.body.prepend(banner);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.log_error) {
    window.pywebview.api.log_error(message);
  }
}

window.addEventListener("error", (e) => reportFatal(e.error || e.message || "unknown error"));
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
