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
    assist_url: "https://assist.org/transfer/results?year=76&institution=200&agreement=100",
  };
  const output = document.createElement("section");
  const count = renderResults(output, {
    query: { university: { name: "University" }, course: a, terms: [] },
    results: [
      { ...shared, cc_course: a, companion_courses: [b] },
      { ...shared, cc_course: b, companion_courses: [a] },
      { ...shared, cc_course: { ...a, number: "20" }, companion_courses: [], is_standalone: true,
        academic_year: "2024-2025", academic_year_id: 75, sources: ["Major: Physics"],
        assist_url: "https://assist.org/transfer/results?year=75&institution=200&agreement=100" },
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
  const links = [...output.querySelectorAll<HTMLAnchorElement>(".agreement-links a")];
  expect(links).toHaveLength(2);
  expect(links.map(link => new URL(link.href).searchParams.get("year"))).toEqual(["76", "75"]);
  expect(links[0].textContent).toBe("View 2025-2026 agreements on ASSIST ↗");
  expect(links[0].getAttribute("aria-label")).toContain("Test College");
  expect(links.every(link => link.target === "_blank" && link.rel === "noopener")).toBe(true);
});

it("filters colleges without losing course combinations or expanded details, and clears an empty result", () => {
  const course = { prefix: "MATH", number: "1A", title: "Calculus I" };
  const shared = {
    cc_course: course, academic_year: "2025-2026", academic_year_id: 76,
    is_standalone: false, sources: ["AllDepartments"], receiving_companion_courses: [],
    companion_courses: [{ ...course, number: "1B", title: "Calculus II" }],
    assist_url: "https://assist.org/transfer/results?year=76&institution=103&agreement=129",
  };
  const data = {
    query: { university: { name: "University" }, course, terms: [] },
    results: [
      { ...shared, cc_code: "CAMINO", cc_name: "El Camino College" },
      { ...shared, cc_code: "AHC", cc_name: "Allan Hancock College" },
    ],
    no_articulation: [],
  };
  const options = { showAll: vi.fn(), includeCombinations: vi.fn(), cvcRefresh: "", standaloneOnly: false };
  const output = document.createElement("section");
  document.body.appendChild(output);
  renderResults(output, data, options);
  const input = output.querySelector<HTMLInputElement>("#college-filter")!;
  const cards = [...output.querySelectorAll<HTMLElement>(".college-card")];
  const details = cards[0].querySelector("details")!;
  details.open = true;
  function type(value: string) {
    input.value = value;
    input.dispatchEvent(new Event("input"));
  }

  type("  cAMino  ");
  expect(cards.map(card => card.hidden)).toEqual([false, true]);
  expect(output.querySelector('[role="status"]')?.textContent).toBe("Showing 1 of 2 colleges");
  expect(cards[0].textContent).toContain("MATH 1B");
  expect(details.open).toBe(true);

  type("no such college");
  expect(cards.every(card => card.hidden)).toBe(true);
  expect([...output.querySelectorAll("p")].find(p => p.textContent?.startsWith("No matching colleges"))?.hidden).toBe(false);

  [...output.querySelectorAll("button")].find(button => button.textContent === "Clear filter")!.click();
  expect(cards.every(card => !card.hidden)).toBe(true);
  expect(input.value).toBe("");
  expect(document.activeElement).toBe(input);
  expect(details.open).toBe(true);

  type("Camino");
  renderResults(output, data, options);
  expect(output.querySelector<HTMLInputElement>("#college-filter")?.value).toBe("");
  expect([...output.querySelectorAll<HTMLElement>(".college-card")].every(card => !card.hidden)).toBe(true);
  output.remove();
});
