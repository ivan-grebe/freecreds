const uniSel = document.getElementById("uni");
const courseInput = document.getElementById("course-input");
const courseList = document.getElementById("course-list");
const standaloneCheck = document.getElementById("standalone-only");
const form = document.getElementById("search-form");
const output = document.getElementById("output");

function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

// --- University dropdown ---

async function loadUniversities() {
  const res = await fetch("/api/universities");
  if (!res.ok) {
    output.innerHTML = `<div class="error">Failed to load universities. Has the ingester been run?</div>`;
    return;
  }
  const { universities } = await res.json();
  uniSel.innerHTML = "";
  if (!universities.length) {
    uniSel.appendChild(el("option", { value: "" }, "(no data — run ingester)"));
    return;
  }
  uniSel.appendChild(el("option", { value: "" }, "— choose —"));
  for (const u of universities) {
    uniSel.appendChild(el("option", { value: u.code.trim() }, `${u.name} (${u.code.trim()})`));
  }
}

// --- Course combobox (searchable dropdown) ---

const combo = {
  courses: [],       // all courses for the selected university
  filtered: [],      // current filtered list
  activeIndex: -1,   // highlighted item in filtered
  selected: null,    // {prefix, number, title, ...}
};

function displayForCourse(c) {
  return `${c.prefix} ${c.number} — ${c.title}`;
}

// Case-insensitive substring match against "PREFIX NUMBER TITLE".
// Each whitespace-separated query token must appear somewhere in the haystack.
function matchesQuery(course, tokens) {
  if (!tokens.length) return true;
  const hay = `${course.prefix} ${course.number} ${course.title}`.toLowerCase();
  return tokens.every(t => hay.includes(t));
}

function setComboOpen(open) {
  courseList.classList.toggle("open", open);
  courseInput.setAttribute("aria-expanded", open ? "true" : "false");
}

function renderCombo() {
  courseList.innerHTML = "";
  if (!combo.filtered.length) {
    courseList.appendChild(el("li", { class: "combo-empty" }, "No matches"));
    return;
  }
  // Small count header when filtered set is larger than what's visually in the first 5
  if (combo.filtered.length > 5) {
    courseList.appendChild(el("li", { class: "combo-count" },
      `${combo.filtered.length} matches — scroll for more`));
  }
  combo.filtered.forEach((c, i) => {
    const item = el("li", {
      class: "combo-item" + (i === combo.activeIndex ? " active" : ""),
      role: "option",
      "data-index": String(i),
    }, [
      el("span", { class: "code" }, `${c.prefix} ${c.number}`),
      el("span", { class: "title" }, c.title),
    ]);
    item.addEventListener("mousedown", (e) => {
      // mousedown (not click) so it fires before input blur hides the list
      e.preventDefault();
      selectCourse(i);
    });
    courseList.appendChild(item);
  });
  // Scroll active into view
  if (combo.activeIndex >= 0) {
    const node = courseList.querySelector(`[data-index="${combo.activeIndex}"]`);
    if (node) node.scrollIntoView({ block: "nearest" });
  }
}

function updateFiltered() {
  const q = courseInput.value.trim().toLowerCase();
  const tokens = q ? q.split(/\s+/) : [];
  combo.filtered = combo.courses.filter(c => matchesQuery(c, tokens));
  // Keep active highlight valid
  if (combo.filtered.length === 0) combo.activeIndex = -1;
  else if (combo.activeIndex >= combo.filtered.length) combo.activeIndex = 0;
  else if (combo.activeIndex < 0) combo.activeIndex = 0;
  renderCombo();
}

function selectCourse(index) {
  const c = combo.filtered[index];
  if (!c) return;
  combo.selected = c;
  courseInput.value = displayForCourse(c);
  setComboOpen(false);
}

function resetCourseCombo(enabled, placeholder) {
  combo.courses = [];
  combo.filtered = [];
  combo.activeIndex = -1;
  combo.selected = null;
  courseInput.value = "";
  courseInput.disabled = !enabled;
  courseInput.placeholder = placeholder;
  setComboOpen(false);
}

async function loadCoursesForUniversity() {
  const code = uniSel.value;
  if (!code) {
    resetCourseCombo(false, "Pick a university first");
    return;
  }
  resetCourseCombo(false, "Loading courses…");
  const res = await fetch(`/api/courses?university=${encodeURIComponent(code)}`);
  if (!res.ok) {
    resetCourseCombo(false, "Failed to load courses");
    return;
  }
  const { courses } = await res.json();
  combo.courses = courses;
  courseInput.disabled = false;
  courseInput.placeholder = `Search ${courses.length} courses (e.g. "math 150")`;
}

// Input events: typing updates the filter and opens the dropdown.
courseInput.addEventListener("input", () => {
  // Typing invalidates any prior selection
  combo.selected = null;
  updateFiltered();
  setComboOpen(true);
});

// Focus/click opens the dropdown (even when input is empty) so users can browse.
function openOnFocus() {
  if (courseInput.disabled) return;
  updateFiltered();
  setComboOpen(true);
}
courseInput.addEventListener("focus", openOnFocus);
courseInput.addEventListener("click", openOnFocus);

// Close on outside click.
document.addEventListener("mousedown", (e) => {
  if (!courseInput.contains(e.target) && !courseList.contains(e.target)) {
    setComboOpen(false);
  }
});

// Keyboard nav: Up/Down/Enter/Escape.
courseInput.addEventListener("keydown", (e) => {
  if (!courseList.classList.contains("open")) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      openOnFocus();
      e.preventDefault();
      return;
    }
    return;
  }
  if (e.key === "ArrowDown") {
    if (combo.filtered.length) {
      combo.activeIndex = (combo.activeIndex + 1) % combo.filtered.length;
      renderCombo();
    }
    e.preventDefault();
  } else if (e.key === "ArrowUp") {
    if (combo.filtered.length) {
      combo.activeIndex = (combo.activeIndex - 1 + combo.filtered.length) % combo.filtered.length;
      renderCombo();
    }
    e.preventDefault();
  } else if (e.key === "Enter") {
    if (combo.activeIndex >= 0) {
      selectCourse(combo.activeIndex);
      e.preventDefault();
    }
  } else if (e.key === "Escape") {
    setComboOpen(false);
    e.preventDefault();
  }
});

// --- Results rendering (unchanged) ---

function renderCourse(c) {
  const units = c.min_units === c.max_units ? `${c.min_units}` : `${c.min_units}–${c.max_units}`;
  return `${c.prefix} ${c.number} — ${c.title} (${units} units)`;
}

function renderResults(data) {
  output.innerHTML = "";
  const q = data.query;
  output.appendChild(el("div", { class: "meta" },
    `${q.university.name}: ${renderCourse(q.course)}`));

  const header = el("div", { class: "result-header" }, [
    el("h2", {}, `Articulating community colleges (${data.results.length})`),
  ]);
  output.appendChild(header);

  if (!data.results.length) {
    output.appendChild(el("div", { class: "meta" }, "No articulations found for this course."));
  } else {
    const table = el("table");
    table.appendChild(el("thead", {}, el("tr", {}, [
      el("th", {}, "Community College"),
      el("th", {}, "Articulating Course"),
      el("th", {}, "Type"),
    ])));
    const tbody = el("tbody");
    for (const r of data.results) {
      const cc = el("td", {}, [
        r.cc_name,
        el("div", { class: "meta" }, r.cc_code),
      ]);
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
      const badge = r.is_standalone
        ? el("span", { class: "badge standalone" }, "standalone")
        : el("span", { class: "badge bundle" }, "AND bundle");
      const type = el("td", {}, badge);
      tbody.appendChild(el("tr", {}, [cc, courseCell, type]));
    }
    table.appendChild(tbody);
    output.appendChild(table);
  }

  if (data.no_articulation.length) {
    const details = el("details");
    details.appendChild(el("summary", {}, `${data.no_articulation.length} colleges with no articulation on record`));
    const list = el("div", { class: "no-art-list" });
    for (const n of data.no_articulation) {
      list.appendChild(el("div", { class: "no-art-item" }, `${n.cc_name} (${n.cc_code}): ${n.reason}`));
    }
    details.appendChild(list);
    output.appendChild(details);
  }
}

async function runSearch() {
  const code = uniSel.value;
  if (!code) return;

  // If the typed text exactly matches one course, auto-select it so users
  // can type + Enter without a mouse click.
  if (!combo.selected && courseInput.value.trim()) {
    const typed = courseInput.value.trim().toLowerCase();
    const exact = combo.courses.find(c =>
      displayForCourse(c).toLowerCase() === typed
      || `${c.prefix} ${c.number}`.toLowerCase() === typed
    );
    if (exact) combo.selected = exact;
  }

  if (!combo.selected) {
    output.innerHTML = `<div class="error">Pick a course from the list first.</div>`;
    return;
  }
  const c = combo.selected;
  const params = new URLSearchParams({ university: code, prefix: c.prefix, number: c.number });
  if (standaloneCheck.checked) params.set("standalone_only", "true");
  output.innerHTML = `<div class="meta">Searching…</div>`;

  const res = await fetch(`/api/reverse?${params.toString()}`);
  const data = await res.json();
  if (!res.ok) {
    const err = el("div", { class: "error" }, data.error || "Error");
    output.innerHTML = "";
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

uniSel.addEventListener("change", loadCoursesForUniversity);
form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

loadUniversities();
