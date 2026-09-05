import { el } from "./dom.js";
import { cvcSourceHref, dedupeBundleRows, renderSourceLabel } from "./ui-logic.js";

function courseLabel(course) {
  return `${course.prefix} ${course.number} — ${course.title}`;
}

export function actionButton(label, action) {
  const button = el("button", { type: "button", class: "secondary-button" }, label);
  button.addEventListener("click", action);
  return button;
}

function courseOption(row, selectedTerms) {
  const course = row.cc_course;
  const content = [el("p", { class: "course-name" }, [
    el("strong", {}, `${course.prefix} ${course.number}`),
    el("span", {}, course.title),
  ])];
  if (!row.is_standalone) {
    content.push(el("strong", {}, "Complete all of these courses:"));
    content.push(el("ul", { class: "companion-list" }, [course, ...row.companion_courses]
      .map(c => el("li", {}, courseLabel(c)))));
  }
  if (row.receiving_companion_courses?.length) {
    content.push(el("p", { class: "source-note" }, "Also yields credit for:"));
    content.push(el("ul", { class: "receiving-companion-list" }, row.receiving_companion_courses
      .map(c => el("li", {}, courseLabel(c)))));
  }
  const source = renderSourceLabel(row.sources);
  if (source.text) content.push(el("p", { class: "source-note" }, source.text));

  if (selectedTerms.length) {
    content.push(el("p", { class: "listing-heading" }, "Online listing found"));
    content.push(el("ul", { class: "offering-list" }, selectedTerms.map(selected => {
      const term = row.offering_terms?.find(t => t.code === selected.code);
      const status = term?.status;
      const badge = status === "async_online"
        ? { className: "async-online", label: "Async online" }
        : status === "online_sync"
          ? { className: "online-sync", label: "Online sync" }
          : { className: "unknown", label: "No listing found" };
      const children = [
        el("span", { class: "offering-term" }, selected.label),
        el("span", { class: `badge ${badge.className}` }, badge.label),
      ];
      const href = cvcSourceHref(term?.source_ref);
      if (href) children.push(el("a", {
        class: "cvc-link", href, target: "_blank", rel: "noopener",
        "aria-label": `View ${course.prefix} ${course.number} on CVC for ${selected.label} (opens in a new tab)`,
      }, "View on CVC ↗"));
      return el("li", {}, children);
    })));
    if (!row.is_standalone) content.push(el("p", { class: "source-note" },
      `CVC links open ${course.prefix} ${course.number}. Check every required course before enrolling.`));
  }

  const units = course.min_units != null
    ? `${course.min_units}${course.max_units != null && course.max_units !== course.min_units ? `–${course.max_units}` : ""} units`
    : "Units not recorded";
  content.push(el("details", { class: "agreement-details" }, [
    el("summary", {}, `Agreement ${row.academic_year || "year unknown"} · Details`),
    el("p", {}, `${units} · College code: ${row.cc_code}`),
    el("p", {}, `ASSIST agreement: ${row.academic_year || "year unknown"}. The agreement year is separate from the data refresh date.`),
    el("p", {}, source.title || source.text || "Department agreement"),
  ]));
  return el("li", { class: "course-option" }, content);
}

export function renderResults(output, data, { showAll, includeCombinations, cvcRefresh, standaloneOnly }) {
  const query = data.query;
  const terms = query.terms || [];
  const rows = dedupeBundleRows(data.results.map(row => ({ ...row })));
  const colleges = Map.groupBy(rows, row => row.cc_code);
  const count = `${rows.length} ${rows.length === 1 ? "match" : "matches"} at ${colleges.size} ${colleges.size === 1 ? "college" : "colleges"}`;
  const scope = terms.length
    ? `${query.async_only ? "Async online" : "Online"} listings for ${terms.map(t => t.label).join(" or ")}`
    : "All transfer matches";
  output.replaceChildren(
    el("div", { class: "result-header" }, el("h2", {}, count)),
    el("p", { class: "result-context" }, [
      el("strong", {}, `${query.university.name} · ${courseLabel(query.course)}`),
      el("br"), scope,
      standaloneOnly ? " · One course required" : " · Includes course combinations",
    ]),
  );
  if (terms.length) {
    output.appendChild(el("p", { class: "cvc-callout" },
      `${cvcRefresh}. Listings do not confirm open seats. A missing listing does not mean a class is unavailable. Verify on CVC and with the college.`));
    output.appendChild(el("div", { class: "result-actions" }, actionButton("Show all transfer matches", showAll)));
  }
  if (!rows.length) {
    output.appendChild(el("p", {}, terms.length
      ? "No matching online listings were found for these terms. Try all transfer matches or another term."
      : "No transfer matches were found with these options. Try another course or include course combinations."));
    if (standaloneOnly) output.appendChild(actionButton("Include course combinations", includeCombinations));
  }
  output.appendChild(el("div", { class: "college-list" }, [...colleges.values()].map(options =>
    el("article", { class: "college-card" }, [
      el("div", { class: "college-heading" }, [
        el("h3", {}, options[0].cc_name),
        el("span", {}, `${options.length} ${options.length === 1 ? "option" : "alternatives"}`),
      ]),
      el("ul", { class: "course-options" }, options.map(row => courseOption(row, terms))),
    ]))));
  if (data.no_articulation.length) {
    output.appendChild(el("details", {}, [
      el("summary", {}, `${data.no_articulation.length} colleges with no articulation on record`),
      el("div", { class: "no-art-list" }, data.no_articulation.map(row =>
        el("div", { class: "no-art-item" }, `${row.cc_name}: ${row.reason}`))),
    ]));
  }
  return count;
}
