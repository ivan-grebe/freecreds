// @vitest-environment happy-dom
import { expect, it, vi } from "vitest";
import { renderResults } from "../../src/frontend/results.js";

it("groups alternatives by college while preserving the complete required course combination", () => {
  const a = { prefix: "MATH", number: "1A", title: "Calculus I" };
  const b = { prefix: "MATH", number: "1B", title: "Calculus II" };
  const shared = {
    cc_code: "TEST", cc_name: "Test College", academic_year_id: 76,
    academic_year: "2025-2026", is_standalone: false,
    receiving_companion_courses: [], sources: ["AllDepartments"],
  };
  const output = document.createElement("section");
  const count = renderResults(output, {
    query: { university: { name: "University" }, course: a, terms: [] },
    results: [
      { ...shared, cc_course: a, companion_courses: [b] },
      { ...shared, cc_course: b, companion_courses: [a] },
      { ...shared, cc_course: { ...a, number: "20" }, companion_courses: [], is_standalone: true,
        academic_year: "2024-2025", academic_year_id: 75, sources: ["Major: Physics"] },
    ],
    no_articulation: [],
  }, { showAll: vi.fn(), includeCombinations: vi.fn(), cvcRefresh: "", standaloneOnly: false });

  expect(count).toContain("2 matches at 1 college");
  expect(output.querySelectorAll("article")).toHaveLength(1);
  expect(output.querySelectorAll(".course-option")).toHaveLength(2);
  expect(output.querySelector(".course-name strong")?.textContent).toBe("MATH 1A");
  expect([...output.querySelectorAll(".companion-list li")].map(item => item.textContent))
    .toEqual(["MATH 1B — Calculus II"]);
  const details = output.querySelector("details")!;
  expect(details.open).toBe(false);
  expect(details.textContent).toContain("2025-2026");
  expect(details.textContent).toContain("2024-2025");
  expect(output.querySelector(".course-option .source-note")?.textContent).toContain("Physics");
});
