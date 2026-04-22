const uniSel = document.getElementById("uni");
const courseSel = document.getElementById("course");
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

async function loadCourses() {
  const code = uniSel.value;
  courseSel.innerHTML = "";
  courseSel.disabled = true;
  if (!code) return;
  courseSel.appendChild(el("option", { value: "" }, "loading…"));
  const res = await fetch(`/api/courses?university=${encodeURIComponent(code)}`);
  if (!res.ok) {
    courseSel.innerHTML = "";
    courseSel.appendChild(el("option", { value: "" }, "(error)"));
    return;
  }
  const { courses } = await res.json();
  courseSel.innerHTML = "";
  courseSel.appendChild(el("option", { value: "" }, `— choose (${courses.length} courses) —`));
  for (const c of courses) {
    const label = `${c.prefix} ${c.number} — ${c.title}`;
    courseSel.appendChild(el("option", { value: `${c.prefix}|${c.number}` }, label));
  }
  courseSel.disabled = false;
}

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
  const courseVal = courseSel.value;
  if (!code || !courseVal) return;
  const [prefix, number] = courseVal.split("|");
  const params = new URLSearchParams({ university: code, prefix, number });
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

uniSel.addEventListener("change", loadCourses);
form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

loadUniversities();
