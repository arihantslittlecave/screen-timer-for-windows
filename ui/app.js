const REFRESH_MS = 5000;
const COLLAPSED_APP_COUNT = 4;
const LIMIT_MAX_HOURS = 23; // pairs with 5-minute steps up to :55, so max is 23h 55m
const GOAL_HOUR_OPTIONS = Array.from({ length: 16 }, (_, i) => i + 1); // 1h..16h
// Less than this and the bar chart's baseline is thicker than the bar itself.
// More than this and the tallest realistic day (a handful of hours) barely
// leaves the ground, which is exactly the "flat, lifeless chart" a premium
// history section shouldn't have.
const MONTH_BAR_AREA_PX = 62;

const ICONS = {
  limit: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 1.5M9 2h6"/></svg>',
};

const state = {
  month: { year: null, month: null }, // resolved from the first response
  selectedDay: null,
  appsExpanded: false,
  monthAppsExpanded: false,
  topLimitEditorFor: null, // processName of the row with its preset editor open, in the "today" list
  monthLimitEditorFor: null, // same, in the month-aggregate list
  dayLimitEditorFor: null, // same, in the day-detail list — three separate keys so opening one editor never closes another
};

// The 5s poll would otherwise rebuild these on every tick, dropping hover
// state and flickering. Re-render only when the data actually changed.
const lastRender = { chart: null, topApps: null, monthApps: null, dayApps: null };

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

// Date-only ISO strings ("2026-09-16") are UTC midnight by the JS Date spec,
// so parsing one with `new Date(str)` and then formatting in a negative-UTC
// timezone can print the day before. These strings represent a LOCAL
// calendar day chosen by Python's date.today(), so they must be built as
// local midnight instead — the two-argument-free Date(y, m, d) constructor
// does that.
function parseIsoDate(str) {
  const [y, m, d] = str.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shortDate(str) {
  return parseIsoDate(str).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function fullDate(str) {
  return parseIsoDate(str).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

// ---- month bar chart ----

function renderMonthChart(days, selectedDay) {
  const key = JSON.stringify([days.map((d) => [d.date, d.seconds]), selectedDay]);
  if (lastRender.chart === key) return;
  lastRender.chart = key;

  const container = document.getElementById("month-chart");
  container.innerHTML = "";

  const max = Math.max(...days.map((d) => d.seconds), 1);

  days.forEach((d) => {
    const heightPx = d.seconds ? Math.max((d.seconds / max) * MONTH_BAR_AREA_PX, 2) : 0;
    const isTick = d.dayNum === 1 || d.dayNum % 5 === 1;
    const classes = [
      "month-col",
      d.date === selectedDay ? "selected" : "",
      isTick ? "month-col--tick" : "",
      d.isToday ? "month-col--today" : "",
    ]
      .filter(Boolean)
      .join(" ");

    const col = el(`
      <div class="${classes}" title="${escapeHtml(shortDate(d.date))} — ${escapeHtml(d.label)}">
        <div class="month-bar-track">
          <div class="month-bar ${d.isToday ? "today" : ""} ${d.seconds ? "" : "empty"}" style="height:${heightPx}px"></div>
        </div>
        <div class="month-day-num">${d.dayNum}</div>
      </div>
    `);
    col.addEventListener("click", () => {
      state.selectedDay = state.selectedDay === d.date ? null : d.date;
      refresh();
    });
    container.appendChild(col);
  });
}

// ---- shared app-row rendering, used by "today" and the day-detail list ----

function renderAppRows(container, apps, { collapsible, expanded, editorFor, onToggleEditor, moreBtn }) {
  container.innerHTML = "";

  if (!apps.length) {
    container.appendChild(el(`<div class="empty-note">Nothing recorded.</div>`));
    if (moreBtn) moreBtn.classList.add("hidden");
    return;
  }

  const canCollapse = collapsible && apps.length - COLLAPSED_APP_COUNT >= 2;
  const hidden = canCollapse ? apps.length - COLLAPSED_APP_COUNT : 0;
  const visible = expanded || !canCollapse ? apps : apps.slice(0, COLLAPSED_APP_COUNT);

  if (moreBtn) {
    moreBtn.classList.toggle("hidden", hidden <= 0);
    moreBtn.textContent = expanded ? "See less" : `See ${hidden} more`;
  }

  const maxSeconds = Math.max(...apps.map((a) => a.seconds), 1);

  visible.forEach((a, i) => {
    const name = escapeHtml(a.name);
    const glyph = a.icon
      ? `<img class="app-icon" src="${a.icon}" alt="" />`
      : `<span class="app-fallback">${escapeHtml(a.name.charAt(0).toUpperCase())}</span>`;

    const limitBadge = a.limitExceeded
      ? '<span class="over-tag">Over</span>'
      : a.limitMinutes
      ? `<span class="limit-note">of ${escapeHtml(minutesLabel(a.limitMinutes))}</span>`
      : "";

    const pct = Math.round((a.seconds / maxSeconds) * 100);
    const op = i === 0 ? 1 : 0.55;

    const wrap = el(`
      <div class="app-row-wrap">
        <div class="app-row ${a.limitExceeded ? "over-limit" : ""}" title="${name} — ${escapeHtml(a.label)}">
          <span class="app-icon-wrap">${glyph}</span>
          <div class="app-main">
            <div class="app-name">${name}</div>
            <div class="app-bar"><span class="app-bar-fill" style="--pct:${pct}%; --op:${op}"></span></div>
          </div>
          <div class="app-meta">
            ${limitBadge}
            <span class="app-time">${escapeHtml(a.label)}</span>
            <button class="limit-btn" type="button" title="Set a daily limit for ${name}">${ICONS.limit}</button>
          </div>
        </div>
      </div>
    `);

    wrap.querySelector(".limit-btn").addEventListener("click", () => onToggleEditor(a.processName));

    if (editorFor === a.processName) {
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
      <span class="limit-editor-label">Daily limit for ${escapeHtml(app.name)}</span>
      <div class="time-picker-row">
        <select class="select-input time-picker-hours"></select>
        <span class="time-picker-sep">h</span>
        <select class="select-input time-picker-minutes"></select>
        <span class="time-picker-sep">m</span>
      </div>
      <div class="limit-editor-actions">
        ${app.limitMinutes ? '<button class="ghost-btn" type="button" data-action="reset">Reset</button>' : "<span></span>"}
        <div class="limit-editor-actions-right">
          <button class="ghost-btn" type="button" data-action="cancel">Cancel</button>
          <button class="ghost-btn primary" type="button" data-action="ok">OK</button>
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
    state.topLimitEditorFor = null;
    state.monthLimitEditorFor = null;
    state.dayLimitEditorFor = null;
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

// ---- pulling both halves of state and rendering them ----

async function refreshToday() {
  let data;
  try {
    data = await window.pywebview.api.get_state();
  } catch (err) {
    console.error("get_state() failed, will retry on next poll:", err);
    return;
  }

  document.getElementById("today-value").textContent = data.totalLabel;

  const sub = document.getElementById("today-sub");
  if (data.goalSeconds > 0) {
    const pct = Math.round((data.totalSeconds / data.goalSeconds) * 100);
    sub.innerHTML = data.goalExceeded
      ? `<span class="accent">${pct}% of ${data.goalHours}h limit — over</span>`
      : `${pct}% of ${data.goalHours}h limit`;
  } else {
    sub.textContent = "No daily limit set";
  }

  const deltaEl = document.getElementById("today-delta");
  if (data.delta) {
    deltaEl.textContent =
      data.delta.direction === "same"
        ? "About the same as yesterday"
        : `${data.delta.label} ${data.delta.direction === "up" ? "more" : "less"} than yesterday`;
  } else {
    deltaEl.textContent = "";
  }

  document.getElementById("avg-value").textContent = data.avgLabel;

  document.getElementById("break-time").textContent = `in ${data.breakInLabel}`;
  const snooze = document.getElementById("snooze-btn");
  snooze.textContent = `Snooze ${data.snoozeMinutes}m`;
  snooze.classList.remove("hidden");

  const key = JSON.stringify([
    data.topApps.map((a) => [a.name, a.seconds, !!a.icon, a.limitMinutes, a.limitExceeded]),
    state.appsExpanded,
    state.topLimitEditorFor,
  ]);
  if (lastRender.topApps !== key) {
    lastRender.topApps = key;
    renderAppRows(document.getElementById("app-list"), data.topApps, {
      collapsible: true,
      expanded: state.appsExpanded,
      editorFor: state.topLimitEditorFor,
      onToggleEditor: (name) => {
        state.topLimitEditorFor = state.topLimitEditorFor === name ? null : name;
        refresh();
      },
      moreBtn: document.getElementById("show-more-apps"),
    });
  }

  setSegmentActive(document.getElementById("break-segment"), data.breakMinutes);
  const goalSelect = document.getElementById("goal-select");
  if (document.activeElement !== goalSelect) {
    goalSelect.value = String(Math.min(16, Math.max(1, Math.round(data.goalHours))));
  }
}

async function refreshMonth() {
  let data;
  try {
    data = await window.pywebview.api.get_month_state(
      state.month.year || undefined,
      state.month.month || undefined,
      state.selectedDay || undefined
    );
  } catch (err) {
    console.error("get_month_state() failed, will retry on next poll:", err);
    return;
  }

  state.month = { year: data.year, month: data.month };

  document.getElementById("month-label").textContent = data.monthLabel;
  document.getElementById("month-prev").disabled = !data.canGoPrev;
  document.getElementById("month-next").disabled = !data.canGoNext;

  document.getElementById("month-total").textContent = data.totalLabel;
  document.getElementById("month-avg").textContent = data.activeDays ? data.avgLabel : "—";
  document.getElementById("month-busiest").textContent = data.busiestDay
    ? `${shortDate(data.busiestDay.date)} · ${data.busiestDay.label}`
    : "—";

  renderMonthChart(data.days, state.selectedDay);

  const monthKey = JSON.stringify([
    data.topApps.map((a) => [a.name, a.seconds, !!a.icon]),
    state.monthAppsExpanded,
    state.monthLimitEditorFor,
  ]);
  if (lastRender.monthApps !== monthKey) {
    lastRender.monthApps = monthKey;
    renderAppRows(document.getElementById("month-app-list"), data.topApps, {
      collapsible: true,
      expanded: state.monthAppsExpanded,
      editorFor: state.monthLimitEditorFor,
      onToggleEditor: (name) => {
        state.monthLimitEditorFor = state.monthLimitEditorFor === name ? null : name;
        refresh();
      },
      moreBtn: document.getElementById("show-more-month-apps"),
    });
  }

  const detail = document.getElementById("day-detail");
  if (data.selected) {
    detail.classList.remove("hidden");
    document.getElementById("day-detail-date").textContent = fullDate(data.selected.date);
    document.getElementById("day-detail-total").textContent = data.selected.totalLabel;

    const key = JSON.stringify([
      data.selected.date,
      data.selected.topApps.map((a) => [a.name, a.seconds, !!a.icon]),
      state.dayLimitEditorFor,
    ]);
    if (lastRender.dayApps !== key) {
      lastRender.dayApps = key;
      renderAppRows(document.getElementById("day-detail-apps"), data.selected.topApps, {
        collapsible: false,
        expanded: true,
        editorFor: state.dayLimitEditorFor,
        onToggleEditor: (name) => {
          state.dayLimitEditorFor = state.dayLimitEditorFor === name ? null : name;
          refresh();
        },
        moreBtn: null,
      });
    }
  } else {
    detail.classList.add("hidden");
    lastRender.dayApps = null;
  }
}

async function refresh() {
  await Promise.all([refreshToday(), refreshMonth()]);
}

function flashSaved() {
  const status = document.getElementById("save-status");
  status.textContent = "Saved";
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

  document.getElementById("goal-select").addEventListener("change", saveSettings);

  document.getElementById("snooze-btn").addEventListener("click", async () => {
    await window.pywebview.api.snooze_break();
    refresh();
  });

  document.getElementById("show-more-apps").addEventListener("click", () => {
    state.appsExpanded = !state.appsExpanded;
    lastRender.topApps = null;
    refresh();
  });

  document.getElementById("show-more-month-apps").addEventListener("click", () => {
    state.monthAppsExpanded = !state.monthAppsExpanded;
    lastRender.monthApps = null;
    refresh();
  });

  document.getElementById("month-prev").addEventListener("click", () => {
    const m = state.month.month === 1 ? 12 : state.month.month - 1;
    const y = state.month.month === 1 ? state.month.year - 1 : state.month.year;
    state.month = { year: y, month: m };
    state.selectedDay = null;
    lastRender.chart = null;
    refresh();
  });

  document.getElementById("month-next").addEventListener("click", () => {
    const m = state.month.month === 12 ? 1 : state.month.month + 1;
    const y = state.month.month === 12 ? state.month.year + 1 : state.month.year;
    state.month = { year: y, month: m };
    state.selectedDay = null;
    lastRender.chart = null;
    refresh();
  });

  document.getElementById("day-detail-close").addEventListener("click", () => {
    state.selectedDay = null;
    refresh();
  });
}

let initialized = false;

function boot() {
  // Defensive: if this ever runs more than once, re-running
  // populateGoalSelect() would wipe the dropdown's selected value (rebuilding
  // a <select>'s options resets selection) and duplicate every listener.
  if (initialized) return;
  initialized = true;

  try {
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
