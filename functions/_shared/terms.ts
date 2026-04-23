export interface Term {
  code: string;
  label: string;
  season: string;
  year: number;
}

const SEASON_LABELS: Record<string, string> = {
  SP: "Spring",
  SU: "Summer",
  FA: "Fall",
  WI: "Winter",
};

export function makeTerm(seasonAbbr: string, year: number): Term {
  const season = SEASON_LABELS[seasonAbbr];
  if (!season) {
    throw new Error(`Invalid season: ${seasonAbbr}`);
  }
  return {
    code: `${seasonAbbr}${String(year % 100).padStart(2, "0")}`,
    label: `${season} ${year}`,
    season,
    year,
  };
}

export function currentTerm(today = new Date()): Term {
  const month = today.getUTCMonth() + 1;
  const year = today.getUTCFullYear();
  if (month <= 5) {
    return makeTerm("SP", year);
  }
  if (month <= 7) {
    return makeTerm("SU", year);
  }
  return makeTerm("FA", year);
}

export function nextTerm(term: Term): Term {
  if (term.season === "Spring") {
    return makeTerm("SU", term.year);
  }
  if (term.season === "Summer") {
    return makeTerm("FA", term.year);
  }
  if (term.season === "Fall") {
    return makeTerm("WI", term.year + 1);
  }
  return makeTerm("SP", term.year);
}

export function upcomingTerms(today = new Date(), count = 4): Term[] {
  const terms: Term[] = [];
  let term = currentTerm(today);
  for (let i = 0; i < count; i += 1) {
    terms.push(term);
    term = nextTerm(term);
  }
  return terms;
}

export function parseTermCode(code: string): Term | null {
  const normalized = code.trim().toUpperCase();
  if (!/^(SP|SU|FA|WI)\d{2}$/.test(normalized)) {
    return null;
  }
  return makeTerm(normalized.slice(0, 2), 2000 + Number(normalized.slice(2)));
}

export function academicYearLabel(yearId: number): string {
  const start = 1949 + Math.trunc(yearId);
  return `${start}-${start + 1}`;
}
