#!/usr/bin/env node
// Generates frontend/universities.json from the local SQLite DB.
// Served as a static asset — avoids a D1 query + ~4.8M row reads on every
// page load (the old /api/universities endpoint did a DISTINCT scan of
// articulations). Regenerate whenever the ingester runs; the list only
// changes when a university is added or removed, which is roughly never.

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const [inputArg = "data/assist.db", outputArg = "frontend/universities.json"] =
  process.argv.slice(2);
const inputPath = resolve(inputArg);
const outputPath = resolve(outputArg);

if (!existsSync(inputPath)) {
  console.error(`SQLite database not found: ${inputPath}`);
  process.exit(1);
}

const query = `
SELECT code, name, category FROM institutions
WHERE category IN ('CSU','UC','AICCU')
  AND id IN (SELECT DISTINCT university_id FROM articulations)
ORDER BY category, name;
`;

function runSqlite() {
  return new Promise((resolveProc, rejectProc) => {
    const proc = spawn("sqlite3", ["-json", inputPath, query], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => { stdout += d.toString("utf8"); });
    proc.stderr.on("data", (d) => { stderr += d.toString("utf8"); });
    proc.on("error", rejectProc);
    proc.on("close", (code) => {
      if (code !== 0) {
        rejectProc(new Error(`sqlite3 exited ${code}: ${stderr}`));
        return;
      }
      resolveProc(stdout);
    });
  });
}

const stdout = await runSqlite();
const rows = stdout.trim() ? JSON.parse(stdout) : [];
const universities = rows.map((row) => ({
  code: (row.code ?? "").trim(),
  name: row.name,
  category: row.category,
}));

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, JSON.stringify({ universities }, null, 2) + "\n");
console.log(`Wrote ${universities.length} universities to ${outputPath}`);
