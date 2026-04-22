const uniInput = document.getElementById("uni-input");
const uniList = document.getElementById("uni-list");
const courseInput = document.getElementById("course-input");
const courseList = document.getElementById("course-list");
const standaloneCheck = document.getElementById("standalone-only");
const termSel = document.getElementById("term-filter");
const asyncCheck = document.getElementById("async-only");
const form = document.getElementById("search-form");
const output = document.getElementById("output");

function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

function showOutput(className, message) {
  output.replaceChildren(el("div", { class: className }, message));
}

// --- University dropdown ---

async function loadTerms() {
  try {
    const res = await fetch("/api/terms");
    if (!res.ok) return;
    const { terms } = await res.json();
    for (const t of terms) {
      termSel.appendChild(el("option", { value: t.code }, t.label));
    }
  } catch (e) {
    // Non-fatal: the skip option stays as the only choice.
  }
}

// Async-only is only meaningful when the user has opted into offering checks.
// Start disabled; enable once a term is picked. Clearing the term unchecks + disables.
function syncAsyncToggle() {
  const hasTerm = !!termSel.value;
  asyncCheck.disabled = !hasTerm;
  if (!hasTerm) asyncCheck.checked = false;
}
termSel.addEventListener("change", syncAsyncToggle);

// --- Generic searchable combobox factory ---
// Used for both the university input and the course input. Caller supplies
// the matcher, display text, per-item render content, and an optional
// onSelect callback (for the university combo that chain-loads courses).

function createCombo({ input, list, matches, renderItem, displayText, exactShortcuts, onSelect }) {
  const state = { items: [], filtered: [], activeIndex: -1, selected: null };

  function setOpen(v) {
    list.classList.toggle("open", v);
    input.setAttribute("aria-expanded", v ? "true" : "false");
  }
  function render() {
    list.replaceChildren();
    if (!state.filtered.length) {
      list.appendChild(el("li", { class: "combo-empty" }, "No matches"));
      return;
    }
    if (state.filtered.length > 5) {
      list.appendChild(el("li", { class: "combo-count" },
        `${state.filtered.length} matches — scroll for more`));
    }
    state.filtered.forEach((item, i) => {
      const li = el("li", {
        class: "combo-item" + (i === state.activeIndex ? " active" : ""),
        role: "option",
        "data-index": String(i),
      });
      const parts = renderItem(item);
      for (const p of [].concat(parts)) {
        if (p instanceof Node) li.appendChild(p);
        else if (p != null) li.appendChild(document.createTextNode(String(p)));
      }
      li.addEventListener("mousedown", (e) => {
        // mousedown (not click) so it fires before input blur hides the list
        e.preventDefault();
        select(i);
      });
      list.appendChild(li);
    });
    if (state.activeIndex >= 0) {
      const node = list.querySelector(`[data-index="${state.activeIndex}"]`);
      if (node) node.scrollIntoView({ block: "nearest" });
    }
  }
  function updateFiltered() {
    const q = input.value.trim().toLowerCase();
    const tokens = q ? q.split(/\s+/) : [];
    state.filtered = state.items.filter(it => matches(it, tokens));
    if (!state.filtered.length) state.activeIndex = -1;
    else if (state.activeIndex >= state.filtered.length) state.activeIndex = 0;
    else if (state.activeIndex < 0) state.activeIndex = 0;
    render();
  }
  function select(i) {
    const item = state.filtered[i];
    if (!item) return;
    state.selected = item;
    input.value = displayText(item);
    setOpen(false);
    if (onSelect) onSelect(item);
  }
  function openOnFocus() {
    if (input.disabled) return;
    updateFiltered();
    setOpen(true);
  }

  input.addEventListener("input", () => {
    state.selected = null;
    updateFiltered();
    setOpen(true);
  });
  input.addEventListener("focus", openOnFocus);
  input.addEventListener("click", openOnFocus);
  document.addEventListener("mousedown", (e) => {
    if (!input.contains(e.target) && !list.contains(e.target)) setOpen(false);
  });
  input.addEventListener("keydown", (e) => {
    if (!list.classList.contains("open")) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        openOnFocus();
        e.preventDefault();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      if (state.filtered.length) {
        state.activeIndex = (state.activeIndex + 1) % state.filtered.length;
        render();
      }
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      if (state.filtered.length) {
        state.activeIndex = (state.activeIndex - 1 + state.filtered.length) % state.filtered.length;
        render();
      }
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (state.activeIndex >= 0) {
        select(state.activeIndex);
        e.preventDefault();
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      e.preventDefault();
    }
  });

  return {
    setItems(items) {
      state.items = items;
      state.filtered = items.slice();
      state.activeIndex = -1;
      state.selected = null;
      input.value = "";
    },
    getSelected() { return state.selected; },
    setEnabled(enabled, placeholder) {
      input.disabled = !enabled;
      if (placeholder !== undefined) input.placeholder = placeholder;
    },
    reset() {
      state.items = [];
      state.filtered = [];
      state.activeIndex = -1;
      state.selected = null;
      input.value = "";
      setOpen(false);
    },
    // If the user typed an exact display value (or one of the shortcuts)
    // and hit Enter without clicking a dropdown item, promote it to the
    // current selection so the form handler can proceed.
    tryPromoteTypedSelection() {
      if (state.selected) return state.selected;
      const typed = input.value.trim().toLowerCase();
      if (!typed) return null;
      const shortcuts = exactShortcuts || (() => []);
      const hit = state.items.find(it => {
        if (displayText(it).toLowerCase() === typed) return true;
        return (shortcuts(it) || []).some(s => s.toLowerCase() === typed);
      });
      if (hit) {
        state.selected = hit;
        return hit;
      }
      return null;
    },
  };
}

const uniCombo = createCombo({
  input: uniInput,
  list: uniList,
  matches: (u, tokens) => {
    if (!tokens.length) return true;
    const hay = `${u.name} ${u.code.trim()} ${u.category || ""}`.toLowerCase();
    return tokens.every(t => hay.includes(t));
  },
  renderItem: (u) => [
    el("span", { class: "code" }, u.code.trim()),
    el("span", { class: "title" }, u.name),
  ],
  displayText: (u) => `${u.name} (${u.code.trim()})`,
  exactShortcuts: (u) => [u.code.trim(), u.name],
  onSelect: (u) => loadCoursesForUniversity(u.code.trim()),
});

const courseCombo = createCombo({
  input: courseInput,
  list: courseList,
  matches: (c, tokens) => {
    if (!tokens.length) return true;
    const hay = `${c.prefix} ${c.number} ${c.title}`.toLowerCase();
    return tokens.every(t => hay.includes(t));
  },
  renderItem: (c) => [
    el("span", { class: "code" }, `${c.prefix} ${c.number}`),
    el("span", { class: "title" }, c.title),
  ],
  displayText: (c) => `${c.prefix} ${c.number} — ${c.title}`,
  exactShortcuts: (c) => [`${c.prefix} ${c.number}`, `${c.prefix}${c.number}`],
});

async function loadUniversities() {
  uniCombo.setEnabled(false, "Loading universities…");
  let data;
  try {
    const res = await fetch("/api/universities");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    showOutput("error", "Failed to load universities. Has the ingester been run?");
    return;
  }
  const { universities } = data;
  if (!universities.length) {
    uniCombo.setEnabled(false, "(no data — run ingester)");
    return;
  }
  uniCombo.setItems(universities);
  uniCombo.setEnabled(true, `Search ${universities.length} universities`);
}

async function loadCoursesForUniversity(code) {
  if (!code) {
    courseCombo.reset();
    courseCombo.setEnabled(false, "Pick a university first");
    return;
  }
  courseCombo.reset();
  courseCombo.setEnabled(false, "Loading courses…");
  let data;
  try {
    const res = await fetch(`/api/courses?university=${encodeURIComponent(code)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    courseCombo.setEnabled(false, "Failed to load courses");
    return;
  }
  const { courses } = data;
  courseCombo.setItems(courses);
  courseCombo.setEnabled(true, `Search ${courses.length} courses (e.g. "math 150")`);
}

// --- Results rendering (unchanged) ---

function renderCourse(c) {
  const hasMin = c.min_units != null;
  const hasMax = c.max_units != null;
  let units = "";
  if (hasMin && hasMax) {
    units = c.min_units === c.max_units ? `${c.min_units}` : `${c.min_units}-${c.max_units}`;
  } else if (hasMin || hasMax) {
    units = `${hasMin ? c.min_units : c.max_units}`;
  }
  const unitsText = units ? ` (${units} units)` : "";
  return `${c.prefix} ${c.number} - ${c.title}${unitsText}`;
}

// Returns { text, title } — `text` is shown under the row, `title` is the
// tooltip (full list of major names when truncated).
//
// AllDepartments is the university's default articulation — it applies
// to any major that hasn't overridden it, so attaching major names on
// top of it is redundant. We only call out major names when the path is
// *only* major-specific (no dept. summary backing it).
function renderSourceLabel(sources) {
  if (!sources || !sources.length) return { text: "", title: "" };
  if (sources.includes("AllDepartments")) return { text: "", title: "" };

  const specificMajors = sources
    .filter(s => s && s.startsWith("Major: "))
    .map(s => s.slice("Major: ".length));

  if (specificMajors.length) {
    const shown = specificMajors.slice(0, 3).join(", ");
    const extra = specificMajors.length > 3 ? `, +${specificMajors.length - 3} more` : "";
    return { text: "Major-specific: " + shown + extra, title: specificMajors.join("\n") };
  }
  if (sources.includes("AllMajors")) return { text: "Major-specific", title: "" };
  return { text: "", title: "" };
}

// Agreement-year caption shown under the community-college code.
// Rendered in a warmer color when this row's year is older than the
// newest year in the result set (subtle stale-data flag).
function renderYearNote(row, maxYearId) {
  if (!row.academic_year) return null;
  const stale = row.academic_year_id && maxYearId && row.academic_year_id < maxYearId;
  return el("div", { class: "source-note" + (stale ? " stale" : "") },
           `Agreement Year: ${row.academic_year}`);
}

// Source caption shown under the articulating course. Only surfaces when
// the path is major-specific (no AllDepartments backing); default/generic
// paths get no caption at all.
function renderSourceNote(sources) {
  const { text, title } = renderSourceLabel(sources);
  if (!text) return null;
  const note = el("div", { class: "source-note" }, text);
  if (title) note.setAttribute("title", title);
  return note;
}

function renderOfferingBadge(status) {
  if (status === "async_online") return el("span", { class: "badge async-online" }, "async online");
  if (status === "online_sync") return el("span", { class: "badge online-sync" }, "online sync");
  return el("span", { class: "badge unknown" }, "unknown");
}

function renderScheduleCell(r, termLabel) {
  if (!r.schedule_url) {
    return el("span", { class: "schedule-muted" }, "—");
  }
  const label = termLabel ? `Check ${termLabel}` : "Check schedule";
  return el("a", {
    class: "schedule-link",
    href: r.schedule_url,
    target: "_blank",
    rel: "noopener",
  }, label);
}

function renderResults(data) {
  output.replaceChildren();
  const q = data.query;
  const termLabel = q.term ? q.term.label : null;
  const termScope = termLabel || "any ingested term";
  const headerMeta = termLabel ? `${q.university.name}: ${renderCourse(q.course)} - ${termLabel}`
                                : `${q.university.name}: ${renderCourse(q.course)}`;
  output.appendChild(el("div", { class: "meta" }, headerMeta));

  const n = data.results.length;
  let headline;
  if (q.async_only) {
    headline = `Articulating colleges offering this course async online in ${termScope} (${n})`;
  } else if (termLabel) {
    headline = `Articulating colleges offering this course in ${termLabel} (${n})`;
  } else {
    headline = `Articulating community colleges (${n})`;
  }
  output.appendChild(el("div", { class: "result-header" }, [el("h2", {}, headline)]));

  const showOfferingCols = !!q.term;

  if (!data.results.length) {
    let msg;
    if (q.async_only) {
      msg = `No colleges confirmed to offer this course as async online in ${termScope}.`;
    } else if (termLabel) {
      msg = `No colleges confirmed to offer this course in ${termLabel}. `
        + `Pick "skip" above to see the full articulation list, `
        + "or click a schedule link to check a specific college.";
    } else {
      msg = "No articulations found for this course.";
    }
    output.appendChild(el("div", { class: "meta" }, msg));
  } else {
    const maxYearId = data.results.reduce(
      (m, r) => Math.max(m, r.academic_year_id || 0), 0
    );
    const table = el("table");
    const headerCells = [
      el("th", {}, "Community College"),
      el("th", {}, "Articulating Course"),
      el("th", {}, "Type"),
    ];
    if (showOfferingCols) {
      headerCells.push(el("th", {}, "Offered"));
      headerCells.push(el("th", {}, "Schedule"));
    }
    table.appendChild(el("thead", {}, el("tr", {}, headerCells)));
    const tbody = el("tbody");
    for (const r of data.results) {
      const ccChildren = [
        r.cc_name,
        el("div", { class: "meta" }, r.cc_code),
      ];
      const yearNote = renderYearNote(r, maxYearId);
      if (yearNote) ccChildren.push(yearNote);
      const cc = el("td", {}, ccChildren);

      const courseCell = el("td");
      courseCell.appendChild(document.createTextNode(renderCourse(r.cc_course)));
      if (!r.is_standalone && r.companion_courses.length) {
        const list = el("ul", { class: "companion-list" });
        list.appendChild(el("li", {}, "Must be taken with:"));
        for (const comp of r.companion_courses) {
          list.appendChild(el("li", {}, renderCourse(comp)));
        }
        courseCell.appendChild(list);
      }
      const sourceNote = renderSourceNote(r.sources || []);
      if (sourceNote) courseCell.appendChild(sourceNote);

      const badge = r.is_standalone
        ? el("span", { class: "badge standalone" }, "standalone")
        : el("span", { class: "badge bundle" }, "AND bundle");
      const type = el("td", {}, badge);

      const cells = [cc, courseCell, type];
      if (showOfferingCols) {
        cells.push(el("td", {}, renderOfferingBadge(r.offering_status)));
        cells.push(el("td", {}, renderScheduleCell(r, termLabel)));
      }
      tbody.appendChild(el("tr", {}, cells));
    }
    table.appendChild(tbody);
    output.appendChild(table);
  }

  if (data.no_articulation.length) {
    const details = el("details");
    details.appendChild(el(
      "summary",
      {},
      `${data.no_articulation.length} colleges with no articulation on record`,
    ));
    const list = el("div", { class: "no-art-list" });
    for (const n of data.no_articulation) {
      list.appendChild(el(
        "div",
        { class: "no-art-item" },
        `${n.cc_name} (${n.cc_code}): ${n.reason}`,
      ));
    }
    details.appendChild(list);
    output.appendChild(details);
  }
}

async function runSearch() {
  const uni = uniCombo.tryPromoteTypedSelection();
  if (!uni) {
    showOutput("error", "Pick a university from the list first.");
    return;
  }
  const code = uni.code.trim();

  const course = courseCombo.tryPromoteTypedSelection();
  if (!course) {
    showOutput("error", "Pick a course from the list first.");
    return;
  }

  const params = new URLSearchParams({
    university: code,
    prefix: course.prefix,
    number: course.number,
  });
  if (standaloneCheck.checked) params.set("standalone_only", "true");
  if (termSel.value) params.set("term", termSel.value);
  if (asyncCheck.checked) params.set("async_only", "true");
  showOutput("meta", "Searching...");

  let res;
  let data;
  try {
    res = await fetch(`/api/reverse?${params.toString()}`);
    data = await res.json();
  } catch (e) {
    showOutput("error", "Search failed. Check that the API server is running.");
    return;
  }
  if (!res.ok) {
    const err = el("div", { class: "error" }, data.error || data.detail || "Error");
    output.replaceChildren();
    output.appendChild(err);
    if (data.did_you_mean && data.did_you_mean.length) {
      const list = el("div", { class: "suggestion-list" }, [
        el("strong", {}, "Did you mean:"),
      ]);
      for (const s of data.did_you_mean) {
        list.appendChild(el("div", {}, `${s.prefix} ${s.number} — ${s.title}`));
      }
      output.appendChild(list);
    }
    return;
  }
  renderResults(data);
}

form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

loadUniversities();
loadTerms();

// --- Theme toggle ---
// Two-state toggle. Initial state follows the OS via prefers-color-scheme
// (no data-theme set). First click flips and locks to the opposite of the
// current effective theme; subsequent clicks swap between light/dark.
(function () {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function effectiveTheme() {
    const attr = root.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
    return media && media.matches ? "dark" : "light";
  }
  function refreshLabel() {
    btn.textContent = effectiveTheme() === "dark" ? "Light mode" : "Dark mode";
  }
  btn.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
    refreshLabel();
  });
  if (media && media.addEventListener) {
    media.addEventListener("change", () => {
      if (!root.hasAttribute("data-theme")) refreshLabel();
    });
  }
  refreshLabel();
})();
