import { createCombo } from "./combobox.js";
import { el } from "./dom.js";
import { initThemeToggle } from "./theme.js";
import { actionButton, renderResults } from "./results.js";

const uniInput = document.getElementById("uni-input");
const uniList = document.getElementById("uni-list");
const courseInput = document.getElementById("course-input");
const courseList = document.getElementById("course-list");
const standaloneCheck = document.getElementById("standalone-only");
const onlineMode = document.getElementById("online-matches");
const allMode = document.getElementById("all-matches");
const onlineOptions = document.getElementById("online-options");
const searchStatus = document.getElementById("search-status");
const termFilter = document.getElementById("term-filter");
const asyncCheck = document.getElementById("async-only");
const form = document.getElementById("search-form");
const output = document.getElementById("output");
const assistUpdated = document.getElementById("assist-updated");
const cvcUpdated = document.getElementById("cvc-updated");
let cvcRefresh = "CVC refresh date unavailable";
let courseRequestController = null;
let searchRequestController = null;

function showOutput(className, message) {
  searchStatus.textContent = message;
  output.setAttribute("aria-busy", String(className.includes("loading-state")));
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
  if (!value) return "refresh date unavailable";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "unavailable";
  const formatted = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(timestamp);
  const days = Math.max(0, Math.floor((Date.now() - timestamp.getTime()) / 86400000));
  return `${formatted} (${days === 0 ? "today" : `${days} ${days === 1 ? "day" : "days"} ago`})`;
}

async function loadDataStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error("Status unavailable");
    const { updated_at: updatedAt } = await res.json();
    assistUpdated.textContent = `ASSIST: ${formatRefreshTime(updatedAt?.assist)}`;
    cvcUpdated.textContent = `CVC: ${formatRefreshTime(updatedAt?.cvc)}`;
    cvcRefresh = updatedAt?.cvc
      ? `CVC listings last checked ${formatRefreshTime(updatedAt.cvc)}`
      : "CVC refresh date unavailable";
  } catch {
    assistUpdated.textContent = "ASSIST: unavailable";
    cvcUpdated.textContent = "CVC: unavailable";
  }
}

function syncOnlineOptions() {
  onlineOptions.hidden = !onlineMode.checked;
  const hasTerm = onlineMode.checked && !!termFilter.querySelector('input[name="term"]:checked');
  asyncCheck.disabled = !hasTerm;
  if (!hasTerm) asyncCheck.checked = false;
}
termFilter.addEventListener("change", syncOnlineOptions);
onlineMode.addEventListener("change", syncOnlineOptions);
allMode.addEventListener("change", syncOnlineOptions);

function invalidateResults() {
  searchRequestController?.abort();
  searchRequestController = null;
  output.removeAttribute("aria-busy");
  if (output.hasChildNodes()) {
    output.replaceChildren();
    searchStatus.textContent = "Search options changed. Search again to update results.";
  }
}
form.addEventListener("change", invalidateResults);

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
  onInput: () => loadCoursesForUniversity(null),
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
  onInput: invalidateResults,
  onSelect: invalidateResults,
});

async function loadUniversities() {
  invalidateResults();
  uniCombo.setEnabled(false, "Loading universities…");
  let data;
  try {
    const res = await fetch("/universities.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch {
    uniCombo.setEnabled(false, "Universities unavailable");
    showOutput("error", "Universities could not be loaded. Check your connection and retry.");
    output.appendChild(actionButton("Retry loading universities", loadUniversities));
    return;
  }
  const { universities } = data;
  if (!universities.length) {
    uniCombo.setEnabled(false, "No universities available");
    return;
  }
  uniCombo.setItems(universities);
  uniCombo.setEnabled(true, `Search ${universities.length} universities`);
}

async function loadCoursesForUniversity(code) {
  invalidateResults();
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
    if (courseRequestController !== controller) return;
    courseRequestController = null;
    courseCombo.setEnabled(false, "Failed to load courses");
    showOutput("error", "Courses could not be loaded. Check your connection and retry.");
    output.appendChild(actionButton("Retry loading courses", () => loadCoursesForUniversity(code)));
    return;
  }
  if (courseRequestController !== controller) return;
  courseRequestController = null;
  const { courses } = data;
  courseCombo.setItems(courses);
  courseCombo.setEnabled(true, `Search ${courses.length} courses (e.g. "math 150")`);
}

function showAllMatches() {
  allMode.checked = true;
  syncOnlineOptions();
  runSearch();
}

function revealResults() {
  output.focus({ preventScroll: true });
  output.scrollIntoView({ block: "start" });
}

async function runSearch() {
  const uni = uniCombo.tryPromoteTypedSelection();
  if (!uni) {
    showOutput("error", "Pick a university from the list first.");
    uniInput.focus();
    return;
  }
  const code = uni.code.trim();

  const course = courseCombo.tryPromoteTypedSelection();
  if (!course) {
    showOutput("error", "Pick a course from the list first.");
    courseInput.focus();
    return;
  }

  const params = new URLSearchParams({
    university: code,
    prefix: course.prefix,
    number: course.number,
  });
  if (standaloneCheck.checked) params.set("standalone_only", "true");
  if (onlineMode.checked) {
    const terms = [...termFilter.querySelectorAll('input[name="term"]:checked')];
    if (!terms.length) {
      showOutput("error", "Choose at least one term, or switch to All transfer matches.");
      output.appendChild(actionButton("Show all transfer matches", showAllMatches));
      revealResults();
      return;
    }
    for (const input of terms) params.append("term", input.value);
    if (asyncCheck.checked) params.set("async_only", "true");
  }
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
    if (searchRequestController !== controller) return;
    searchRequestController = null;
    showOutput("error", "Search failed. Check your connection and retry.");
    output.appendChild(actionButton("Retry search", runSearch));
    revealResults();
    return;
  }
  if (searchRequestController !== controller) return;
  searchRequestController = null;
  output.removeAttribute("aria-busy");
  if (!res.ok) {
    searchStatus.textContent = "Search failed. Review the error below.";
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
    revealResults();
    return;
  }
  searchStatus.textContent = renderResults(output, data, {
    cvcRefresh,
    standaloneOnly: standaloneCheck.checked,
    showAll: showAllMatches,
    includeCombinations: () => { standaloneCheck.checked = false; runSearch(); },
  });
  revealResults();
}

form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

loadUniversities();
loadTerms();
loadDataStatus();
initThemeToggle();
