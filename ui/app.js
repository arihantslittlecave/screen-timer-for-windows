const REFRESH_MS = 5000;
const COLLAPSED_APP_COUNT = 4;
const LIMIT_MAX_HOURS = 23; // pairs with 5-minute steps up to :55, so max is 23h 55m
const GOAL_HOUR_OPTIONS = Array.from({ length: 16 }, (_, i) => i + 1); // 1h..16h

const ICONS = {
  limit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 1.5M9 2h6"/></svg>',
};

const state = {
  month: { year: null, month: null }, // resolved from the first response
  selectedDay: null, // a date string, or null meaning "today"
  scope: "day", // "day" (selectedDay, or today if none picked) | "month" (the displayed month's aggregate)
  appsExpanded: false,
  limitEditorFor: null, // processName of the row with its editor open — one Apps panel now, one slot
};

// The 5s poll would otherwise rebuild these on every tick, dropping hover
// state and flickering. Re-render only when the data actually changed.
const lastRender = { calendar: null, apps: null };

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

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---- calendar grid: real weeks, weekday-aligned, always the full month ----

function renderCalendar(days, selectedDay) {
  const key = JSON.stringify([days.map((d) => [d.date, d.seconds, d.isFuture]), selectedDay]);
  if (lastRender.calendar === key) return;
  lastRender.calendar = key;

  const container = document.getElementById("cal-grid");
  container.innerHTML = "";
  if (!days.length) return;

  const leading = parseIsoDate(days[0].date).getDay(); // 0=Sun..6=Sat
  for (let i = 0; i < leading; i++) {
    container.appendChild(el(`<div class="cal-cell blank"></div>`));
  }

  days.forEach((d) => {
    if (d.isFuture) {
      container.appendChild(el(`<div class="cal-cell future">${d.dayNum}</div>`));
      return;
    }
    const classes = [
      "cal-cell",
      d.seconds > 0 ? "has-data" : "",
      d.isToday ? "today" : "",
      d.date === selectedDay ? "selected" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const cell = el(`<div class="${classes}" title="${escapeHtml(shortDate(d.date))} — ${escapeHtml(d.label)}">${d.dayNum}</div>`);
    cell.addEventListener("click", () => {
      state.selectedDay = state.selectedDay === d.date ? null : d.date;
      state.scope = "day";
      refresh();
    });
    container.appendChild(cell);
  });

  const trailing = (7 - (container.children.length % 7)) % 7;
  for (let i = 0; i < trailing; i++) {
    container.appendChild(el(`<div class="cal-cell blank"></div>`));
  }
}

// ---- app rows: one renderer, used by the single Apps panel ----

function renderAppRows(container, apps, { expanded, editorFor, onToggleEditor, moreBtn }) {
  container.innerHTML = "";

  if (!apps.length) {
    container.appendChild(el(`<div class="empty-note">Nothing recorded.</div>`));
    if (moreBtn) moreBtn.classList.add("hidden");
    return;
  }

  const canCollapse = apps.length - COLLAPSED_APP_COUNT >= 2;
  const hidden = canCollapse ? apps.length - COLLAPSED_APP_COUNT : 0;
  const visible = expanded || !canCollapse ? apps : apps.slice(0, COLLAPSED_APP_COUNT);

  if (moreBtn) {
    moreBtn.classList.toggle("hidden", hidden <= 0);
    moreBtn.textContent = expanded ? "Show less" : `Show ${hidden} more`;
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
    const op = i === 0 ? 1 : 0.6;

    const wrap = el(`
      <div class="app-row-wrap">
        <div class="app-row" title="${name} — ${escapeHtml(a.label)}">
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
        ${app.limitMinutes ? '<button class="text-link" type="button" data-action="reset">Reset</button>' : "<span></span>"}
        <div class="limit-editor-actions-right">
          <button class="text-link" type="button" data-action="cancel">Cancel</button>
          <button class="text-link accent" type="button" data-action="ok">OK</button>
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

function setTextOptionActive(container, value) {
  container.dataset.value = value;
  [...container.children].forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.value === String(value));
  });
}

function populateGoalSelect() {
  const select = document.getElementById("goal-select");
  select.innerHTML = GOAL_HOUR_OPTIONS.map((h) => `<option value="${h}">${h}h</option>`).join("");
}

// ---- pulling state and rendering ----

async function refreshToday() {
  let data;
  try {
    data = await window.pywebview.api.get_state();
  } catch (err) {
    console.error("get_state() failed, will retry on next poll:", err);
    return null;
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
  if (data.delta) {
    const dir = data.delta.direction === "same" ? "about the same as" : `${data.delta.label} ${data.delta.direction === "up" ? "more" : "less"} than`;
    sub.textContent += (sub.textContent ? " · " : "") + `${dir} yesterday`;
  }

  document.getElementById("break-time").textContent = `in ${data.breakInLabel}`;

  setTextOptionActive(document.getElementById("break-segment"), data.breakMinutes);
  const goalSelect = document.getElementById("goal-select");
  if (document.activeElement !== goalSelect) {
    goalSelect.value = String(Math.min(16, Math.max(1, Math.round(data.goalHours))));
  }

  return data;
}

async function refreshMonth() {
  let data;
  try {
    data = await window.pywebview.api.get_month_state(
      state.month.year || undefined,
      state.month.month || undefined
    );
  } catch (err) {
    console.error("get_month_state() failed, will retry on next poll:", err);
    return null;
  }

  state.month = { year: data.year, month: data.month };

  document.getElementById("month-label").textContent = data.monthLabel;
  document.getElementById("month-prev").disabled = !data.canGoPrev;
  document.getElementById("month-next").disabled = !data.canGoNext;

  const today = todayStr();
  const isRealCurrentMonth = data.monthLabel === parseIsoDate(today).toLocaleDateString("en-US", { month: "long", year: "numeric" });
  document.getElementById("month-context-label").textContent = isRealCurrentMonth ? "This month" : data.monthLabel;
  document.getElementById("avg-value").textContent = data.activeDays ? data.avgLabel : "0m";
  document.getElementById("month-sub").textContent = data.activeDays ? `per day · ${data.totalLabel} total` : "no data yet";

  renderCalendar(data.days, state.selectedDay);

  return data;
}

async function refreshApps(todayData, monthData) {
  const dayLabel = document.getElementById("scope-day");
  const monthLabel = document.getElementById("scope-month");

  dayLabel.classList.toggle("active", state.scope === "day");
  monthLabel.classList.toggle("active", state.scope === "month");

  const today = todayStr();
  const viewingDay = state.selectedDay || today;
  dayLabel.textContent = viewingDay === today ? "Today" : shortDate(viewingDay);

  let apps, total, label, showTotal;

  if (state.scope === "month") {
    apps = monthData ? monthData.topApps : [];
    total = monthData ? monthData.totalLabel : "";
    showTotal = true;
  } else {
    // Only fetch a day's breakdown when needed: today's is already in
    // get_state()'s topApps, so browsing "today" costs no extra call.
    if (viewingDay === today) {
      // Reuses refreshToday()'s already-fetched data rather than calling
      // get_state() a second time in the same cycle.
      apps = todayData ? todayData.topApps : [];
      showTotal = false; // already shown in the Today bento tile — no repeat
      total = "";
    } else {
      try {
        const day = await window.pywebview.api.get_month_state(
          state.month.year || undefined,
          state.month.month || undefined,
          viewingDay
        );
        apps = day.selected ? day.selected.topApps : [];
        total = day.selected ? day.selected.totalLabel : "";
      } catch (err) {
        console.error("get_month_state(selected_day) failed:", err);
        apps = [];
        total = "";
      }
      showTotal = true;
    }
  }

  document.getElementById("apps-total").textContent = showTotal ? total : "";

  const key = JSON.stringify([
    state.scope,
    viewingDay,
    apps.map((a) => [a.name, a.seconds, !!a.icon, a.limitMinutes, a.limitExceeded]),
    state.appsExpanded,
    state.limitEditorFor,
  ]);
  if (lastRender.apps !== key) {
    lastRender.apps = key;
    renderAppRows(document.getElementById("app-list"), apps, {
      expanded: state.appsExpanded,
      editorFor: state.limitEditorFor,
      onToggleEditor: (name) => {
        state.limitEditorFor = state.limitEditorFor === name ? null : name;
        refresh();
      },
      moreBtn: document.getElementById("show-more-apps"),
    });
  }
}

async function refresh() {
  const todayData = await refreshToday();
  const monthData = await refreshMonth();
  await refreshApps(todayData, monthData);
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
    setTextOptionActive(e.currentTarget, btn.dataset.value);
    saveSettings();
  });

  document.getElementById("goal-select").addEventListener("change", saveSettings);

  document.getElementById("snooze-btn").addEventListener("click", async () => {
    await window.pywebview.api.snooze_break();
    refresh();
  });

  document.getElementById("show-more-apps").addEventListener("click", () => {
    state.appsExpanded = !state.appsExpanded;
    lastRender.apps = null;
    refresh();
  });

  document.getElementById("scope-day").addEventListener("click", () => {
    state.scope = "day";
    lastRender.apps = null;
    refresh();
  });

  document.getElementById("scope-month").addEventListener("click", () => {
    state.scope = "month";
    lastRender.apps = null;
    refresh();
  });

  document.getElementById("month-prev").addEventListener("click", () => {
    const m = state.month.month === 1 ? 12 : state.month.month - 1;
    const y = state.month.month === 1 ? state.month.year - 1 : state.month.year;
    state.month = { year: y, month: m };
    state.selectedDay = null;
    lastRender.calendar = null;
    refresh();
  });

  document.getElementById("month-next").addEventListener("click", () => {
    const m = state.month.month === 12 ? 1 : state.month.month + 1;
    const y = state.month.month === 12 ? state.month.year + 1 : state.month.year;
    state.month = { year: y, month: m };
    state.selectedDay = null;
    lastRender.calendar = null;
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
    "font:11px/1.4 inherit;color:#5aa3ff;border-bottom:1px solid #2a2a2e;";
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
