#!/usr/bin/env node
import { spawn } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { createGzip } from "node:zlib";

const [inputArg = "data/assist.db", outputArg = "seed/cvc-base.sql.gz"] = process.argv.slice(2);
const inputPath = resolve(inputArg);
const outputPath = resolve(outputArg);

if (!existsSync(inputPath)) {
  console.error(`SQLite database not found: ${inputPath}`);
  process.exit(1);
}

mkdirSync(dirname(outputPath), { recursive: true });

function clean(column) {
  return `replace(replace(replace(${column}, char(9), ' '), char(10), ' '), char(13), ' ')`;
}

function exportScript() {
  return `
.headers off
.nullvalue NULL
.mode insert institutions
SELECT id, assist_id, ${clean("code")}, ${clean("name")},
       ${clean("category")}, ${clean("term_type")}, ${clean("schedule_url")}
FROM institutions
ORDER BY id;
.mode insert courses
SELECT id, institution_id, course_identifier_parent_id,
       ${clean("prefix")}, ${clean("number")}, ${clean("title")},
       min_units, max_units, is_terminated
FROM courses
ORDER BY id;
.mode insert terms
SELECT id, ${clean("code")}, ${clean("label")}, ${clean("season")},
       year, ${clean("start_date")}, ${clean("end_date")}
FROM terms
ORDER BY id;
`;
}

const SNAPSHOT_HEADER = [
  "PRAGMA foreign_keys=OFF;",
  "BEGIN TRANSACTION;",
  "CREATE TABLE institutions (",
  "  id INTEGER PRIMARY KEY,",
  "  assist_id INTEGER UNIQUE NOT NULL,",
  "  code TEXT NOT NULL,",
  "  name TEXT NOT NULL,",
  "  category TEXT NOT NULL CHECK(category IN ('CCC','CSU','UC','AICCU')),",
  "  term_type TEXT NOT NULL,",
  "  schedule_url TEXT",
  ");",
  "CREATE INDEX idx_inst_code ON institutions(code);",
  "CREATE INDEX idx_inst_category ON institutions(category);",
  "CREATE TABLE courses (",
  "  id INTEGER PRIMARY KEY,",
  "  institution_id INTEGER NOT NULL REFERENCES institutions(id),",
  "  course_identifier_parent_id INTEGER NOT NULL,",
  "  prefix TEXT NOT NULL,",
  "  number TEXT NOT NULL,",
  "  title TEXT NOT NULL,",
  "  min_units REAL,",
  "  max_units REAL,",
  "  is_terminated BOOLEAN NOT NULL DEFAULT 0,",
  "  UNIQUE(institution_id, course_identifier_parent_id)",
  ");",
  "CREATE INDEX idx_courses_lookup ON courses(institution_id, prefix, number);",
  "CREATE TABLE terms (",
  "  id INTEGER PRIMARY KEY,",
  "  code TEXT NOT NULL UNIQUE,",
  "  label TEXT NOT NULL,",
  "  season TEXT NOT NULL,",
  "  year INTEGER NOT NULL,",
  "  start_date TEXT,",
  "  end_date TEXT",
  ");",
];

function writeLine(stream, line) {
  return new Promise((resolveWrite) => {
    if (stream.write(`${line}\n`)) {
      resolveWrite();
    } else {
      stream.once("drain", resolveWrite);
    }
  });
}

async function main() {
  const sqlite = spawn("sqlite3", [inputPath], {
    stdio: ["pipe", "pipe", "pipe"],
    shell: process.platform === "win32",
  });

  sqlite.on("error", (err) => {
    console.error("Failed to run sqlite3. Install the SQLite CLI, then rerun this script.");
    console.error(err.message);
    process.exit(1);
  });

  let stderr = "";
  sqlite.stderr.setEncoding("utf8");
  sqlite.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  sqlite.stdin.end(exportScript());

  const output = createWriteStream(outputPath);
  const gzip = outputPath.toLowerCase().endsWith(".gz") ? createGzip({ level: 9 }) : null;
  const sink = gzip || output;
  if (gzip) {
    gzip.pipe(output);
  }

  for (const line of SNAPSHOT_HEADER) {
    await writeLine(sink, line);
  }

  const lines = createInterface({
    input: sqlite.stdout,
    crlfDelay: Infinity,
  });
  for await (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    await writeLine(sink, line);
  }

  await writeLine(sink, "COMMIT;");

  if (gzip) {
    gzip.end();
  } else {
    output.end();
  }

  const [exitCode] = await Promise.all([
    new Promise((resolveClose) => sqlite.once("close", resolveClose)),
    new Promise((resolveFinish) => output.once("finish", resolveFinish)),
  ]);

  if (exitCode !== 0) {
    console.error(stderr || "sqlite3 export failed");
    process.exit(typeof exitCode === "number" ? exitCode : 1);
  }

  console.log(`Wrote CVC seed snapshot to ${outputPath}`);
}

await main();
