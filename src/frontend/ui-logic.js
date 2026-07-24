export function renderSourceLabel(sources) {
  if (!sources || !sources.length) return { text: "", title: "" };
  if (sources.includes("AllDepartments")) return { text: "", title: "" };

  const specificMajors = sources
    .filter(source => source && source.startsWith("Major: "))
    .map(source => source.slice("Major: ".length));

  if (specificMajors.length) {
    const shown = specificMajors.slice(0, 3).join(", ");
    const extra = specificMajors.length > 3 ? `, +${specificMajors.length - 3} more` : "";
    return { text: `Major-specific: ${shown}${extra}`, title: specificMajors.join("\n") };
  }
  if (sources.includes("AllMajors")) return { text: "Major-specific", title: "" };
  return { text: "", title: "" };
}

function offeringRank(status) {
  if (status === "async_online") return 3;
  if (status === "online_sync") return 2;
  return 1;
}

function courseIdentity(course) {
  return [
    course.prefix || "",
    course.number || "",
    course.title || "",
    course.min_units == null ? "" : String(course.min_units),
    course.max_units == null ? "" : String(course.max_units),
  ].map(value => String(value).trim().toUpperCase()).join("|");
}

export function dedupeBundleRows(rows) {
  const byBundle = new Map();
  const output = [];
  let hidden = 0;

  for (const row of rows) {
    if (row.is_standalone || !row.companion_courses || !row.companion_courses.length) {
      output.push(row);
      continue;
    }

    const requiredCourses = [row.cc_course, ...row.companion_courses]
      .map(courseIdentity)
      .sort()
      .join(";");
    const receivingCourses = (row.receiving_companion_courses || [])
      .map(courseIdentity)
      .sort()
      .join(";");
    const key = [
      row.cc_code,
      row.academic_year_id || "",
      requiredCourses,
      receivingCourses,
    ].join("||");

    const existing = byBundle.get(key);
    if (!existing) {
      byBundle.set(key, row);
      output.push(row);
      continue;
    }

    hidden += 1;
    existing.sources = Array.from(new Set([...(existing.sources || []), ...(row.sources || [])]));
    if (offeringRank(row.offering_status) > offeringRank(existing.offering_status)) {
      existing.offering_status = row.offering_status;
    }
  }

  return { rows: output, hidden };
}
