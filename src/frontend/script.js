import { createCombo } from "./combobox.js";
import { el } from "./dom.js";
import { initThemeToggle } from "./theme.js";
import { cvcSourceHref, dedupeBundleRows, renderSourceLabel } from "./ui-logic.js";

const uniInput = document.getElementById("uni-input");
const uniList = document.getElementById("uni-list");
const courseInput = document.getElementById("course-input");
const courseList = document.getElementById("course-list");
const standaloneCheck = document.getElementById("standalone-only");
const combineBundlesCheck = document.getElementById("combine-bundles");
const termFilter = document.getElementById("term-filter");
const asyncCheck = document.getElementById("async-only");
const form = document.getElementById("search-form");
const output = document.getElementById("output");
const assistUpdated = document.getElementById("assist-updated");
const cvcUpdated = document.getElementById("cvc-updated");
let lastResultsData = null;
let courseRequestController = null;
let searchRequestController = null;

function showOutput(className, message) {
  const children = [];
  if (className.includes("loading-state")) {
    children.push(el("span", { class: "spinner", "aria-hidden": "true" }));
  }
  children.push(message);
  output.replaceChildren(el("div", { class: className }, children));
}

async function loadTerms() {
  try {
    const res = await fetch("/api/terms");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { terms } = await res.json();
    termFilter.replaceChildren(...terms.map((t) => el("label", { class: "term-option" }, [
      el("input", { type: "checkbox", name: "term", value: t.code }),
      el("span", {}, t.label),
    ])));
  } catch {
    termFilter.replaceChildren(el("span", { class: "term-loading" }, "Terms unavailable"));
  }
}

function formatRefreshTime(value) {
  if (!value) return "not recorded yet";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "unavailable";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(timestamp);
}

async function loadDataStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error("Status unavailable");
    const { updated_at: updatedAt } = await res.json();
    assistUpdated.textContent = `ASSIST: ${formatRefreshTime(updatedAt?.assist)}`;
    cvcUpdated.textContent = `CVC: ${formatRefreshTime(updatedAt?.cvc)}`;
  } catch {
    assistUpdated.textContent = "ASSIST: unavailable";
    cvcUpdated.textContent = "CVC: unavailable";
  }
}

// Async-only is only meaningful when the user has opted into offering checks.
// Start disabled; enable once a term is picked. Clearing the term unchecks + disables.
function syncAsyncToggle() {
  const hasTerm = !!termFilter.querySelector('input[name="term"]:checked');
  asyncCheck.disabled = !hasTerm;
  if (!hasTerm) asyncCheck.checked = false;
}
termFilter.addEventListener("change", syncAsyncToggle);

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
  displayText: (c) => `${c.prefix} ${c.number}: ${c.title}`,
  exactShortcuts: (c) => [`${c.prefix} ${c.number}`, `${c.prefix}${c.number}`],
});

async function loadUniversities() {
  uniCombo.setEnabled(false, "Loading universities…");
  let data;
  try {
    const res = await fetch("/universities.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch {
    uniCombo.setEnabled(false, "Universities unavailable");
    showOutput("error", "Failed to load universities. Please try again later.");
    return;
  }
  const { universities } = data;
  if (!universities.length) {
    uniCombo.setEnabled(false, "(no data; run ingester)");
    return;
  }
  uniCombo.setItems(universities);
  uniCombo.setEnabled(true, `Search ${universities.length} universities`);
}

async function loadCoursesForUniversity(code) {
  courseRequestController?.abort();
  courseRequestController = null;
  if (!code) {
    courseCombo.reset();
    courseCombo.setEnabled(false, "Pick a university first");
    return;
  }
  courseCombo.reset();
  courseCombo.setEnabled(false, "Loading courses…");
  const controller = new AbortController();
  courseRequestController = controller;
  let data;
  try {
    const res = await fetch(
      `/api/courses?university=${encodeURIComponent(code)}`,
      { signal: controller.signal },
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (courseRequestController === controller) courseRequestController = null;
    courseCombo.setEnabled(false, "Failed to load courses");
    return;
  }
  if (courseRequestController !== controller) return;
  courseRequestController = null;
  const { courses } = data;
  courseCombo.setItems(courses);
  courseCombo.setEnabled(true, `Search ${courses.length} courses (e.g. "math 150")`);
}

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

function renderYearNote(row) {
  if (!row.academic_year) return null;
  return el("div", { class: "source-note" }, `Agreement Year: ${row.academic_year}`);
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

function renderOfferingBadge(status, sourceRef = null, termLabel = "") {
  const badge = status === "async_online"
    ? { className: "async-online", label: "async online" }
    : status === "online_sync"
      ? { className: "online-sync", label: "online sync" }
      : { className: "unknown", label: "unknown" };
  const href = cvcSourceHref(sourceRef);
  if (!href) return el("span", { class: `badge ${badge.className}` }, badge.label);

  const context = termLabel ? ` for ${termLabel}` : "";
  return el("a", {
    class: `badge offering-link ${badge.className}`,
    href,
    target: "_blank",
    rel: "noopener",
    title: `View this class on CVC${context}`,
    "aria-label": `${badge.label}${context}: view class on CVC (opens in a new tab)`,
  }, `${badge.label} \u2197`);
}

function formatTermScope(terms) {
  const labels = terms.map((term) => term.label);
  if (labels.length === 0) return "any upcoming term";
  if (labels.length === 1) return labels[0];
  if (labels.length === 2) return `${labels[0]} or ${labels[1]}`;
  return `${labels.slice(0, -1).join(", ")}, or ${labels.at(-1)}`;
}

function renderOfferingTerms(terms, fallbackStatus) {
  if (!terms || terms.length === 0) return renderOfferingBadge(fallbackStatus);
  return el("ul", { class: "offering-list" }, terms.map((term) => el("li", {}, [
    el("span", { class: "offering-term" }, term.label),
    renderOfferingBadge(term.status, term.source_ref, term.label),
  ])));
}

function renderResults(data, { animateSummary = true } = {}) {
  output.replaceChildren();
  const q = data.query;
  const selectedTerms = Array.isArray(q.terms) ? q.terms : (q.term ? [q.term] : []);
  const termScope = formatTermScope(selectedTerms);
  const headerMeta = selectedTerms.length
    ? `${q.university.name}: ${renderCourse(q.course)} - ${termScope}`
    : `${q.university.name}: ${renderCourse(q.course)}`;
  output.appendChild(el("div", { class: "meta" }, headerMeta));

  const bundleFilter = combineBundlesCheck ? combineBundlesCheck.checked : true;
  const { rows: visibleResults, hidden: hiddenBundleRows } = bundleFilter
    ? dedupeBundleRows(data.results.map(row => ({ ...row })))
    : { rows: data.results, hidden: 0 };
  const n = visibleResults.length;
  let headline;
  if (q.async_only) {
    headline = `Articulating colleges offering this course async online in ${termScope} (${n})`;
  } else if (selectedTerms.length) {
    headline = `Articulating colleges offering this course online in ${termScope} (${n})`;
  } else {
    headline = `Articulating community colleges (${n})`;
  }
  output.appendChild(el(
    "div",
    { class: "result-header" + (animateSummary ? " enter-result-summary" : "") },
    [el("h2", {}, headline)],
  ));
  output.appendChild(el("div", { class: "cvc-callout" }, [
    "Course availability changes frequently. ",
    el("a", {
      href: "https://www.cvc.edu/",
      target: "_blank",
      rel: "noopener",
    }, "Check CVC for current availability"),
    ".",
  ]));
  if (hiddenBundleRows) {
    output.appendChild(el(
      "div",
      { class: "meta" },
      `Combined ${hiddenBundleRows} duplicate AND-bundle ${hiddenBundleRows === 1 ? "row" : "rows"}.`,
    ));
  }

  const showOfferingCols = selectedTerms.length > 0;

  if (!visibleResults.length) {
    let msg;
    if (q.async_only) {
      msg = `No colleges confirmed to offer this course as async online in ${termScope}.`;
    } else if (selectedTerms.length) {
      msg = `No colleges confirmed to offer this course online in ${termScope}. `
        + `Clear the selected terms to see all articulations, or check CVC for current availability.`;
    } else {
      msg = "No articulations found for this course.";
    }
    output.appendChild(el("div", { class: "meta" }, msg));
  } else {
    const table = el("table");
    const headerCells = [
      el("th", {}, "Community College"),
      el("th", {}, "Articulating Course"),
      el("th", {}, "Type"),
    ];
    if (showOfferingCols) {
      headerCells.push(el("th", {}, "Online availability"));
    }
    table.appendChild(el("thead", {}, el("tr", {}, headerCells)));
    const tbody = el("tbody");
    for (const r of visibleResults) {
      const ccChildren = [
        r.cc_name,
        el("div", { class: "meta" }, r.cc_code),
      ];
      const yearNote = renderYearNote(r);
      if (yearNote) ccChildren.push(yearNote);
      const cc = el("td", { "data-label": "Community College" }, ccChildren);

      const courseCell = el("td", { "data-label": "Articulating Course" });
      courseCell.appendChild(document.createTextNode(renderCourse(r.cc_course)));
      if (!r.is_standalone && r.companion_courses.length) {
        const list = el("ul", { class: "companion-list" });
        list.appendChild(el("li", {}, "Must be taken with:"));
        for (const comp of r.companion_courses) {
          list.appendChild(el("li", {}, renderCourse(comp)));
        }
        courseCell.appendChild(list);
      }
      if (r.receiving_companion_courses && r.receiving_companion_courses.length) {
        const list = el("ul", { class: "receiving-companion-list" });
        list.appendChild(el("li", {}, "Also yields credit for:"));
        for (const comp of r.receiving_companion_courses) {
          list.appendChild(el("li", {}, renderCourse(comp)));
        }
        courseCell.appendChild(list);
      }
      const sourceNote = renderSourceNote(r.sources || []);
      if (sourceNote) courseCell.appendChild(sourceNote);

      const badge = r.is_standalone
        ? el("span", { class: "badge standalone" }, "standalone")
        : el("span", { class: "badge bundle" }, "AND bundle");
      const type = el("td", { "data-label": "Type" }, badge);

      const cells = [cc, courseCell, type];
      if (showOfferingCols) {
        cells.push(el(
          "td",
          { "data-label": "Online availability" },
          renderOfferingTerms(r.offering_terms, r.offering_status),
        ));
      }
      const row = el("tr", {}, cells);
      tbody.appendChild(row);
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
  for (const input of termFilter.querySelectorAll('input[name="term"]:checked')) {
    params.append("term", input.value);
  }
  if (asyncCheck.checked) params.set("async_only", "true");
  lastResultsData = null;
  showOutput("meta loading-state", "Searching...");
  searchRequestController?.abort();
  const controller = new AbortController();
  searchRequestController = controller;

  let res;
  let data;
  try {
    res = await fetch(`/api/reverse?${params.toString()}`, { signal: controller.signal });
    data = await res.json();
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (searchRequestController === controller) searchRequestController = null;
    showOutput("error", "Search failed. Please try again.");
    return;
  }
  if (searchRequestController !== controller) return;
  searchRequestController = null;
  if (!res.ok) {
    const err = el("div", { class: "error" }, data.error || data.detail || "Error");
    output.replaceChildren();
    output.appendChild(err);
    if (data.did_you_mean && data.did_you_mean.length) {
      const list = el("div", { class: "suggestion-list" }, [
        el("strong", {}, "Did you mean:"),
      ]);
      for (const s of data.did_you_mean) {
        list.appendChild(el("div", {}, `${s.prefix} ${s.number}: ${s.title}`));
      }
      output.appendChild(list);
    }
    return;
  }
  lastResultsData = data;
  renderResults(data);
}

form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
if (combineBundlesCheck) {
  combineBundlesCheck.addEventListener("change", () => {
    if (lastResultsData) renderResults(lastResultsData, { animateSummary: false });
  });
}

loadUniversities();
loadTerms();
loadDataStatus();
initThemeToggle();
