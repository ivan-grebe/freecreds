#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const [inputArg = "d1_import", database = "freecreds-db"] = process.argv.slice(2);
const inputDir = resolve(inputArg);
const manifestPath = join(inputDir, "manifest.json");

if (!existsSync(manifestPath)) {
  console.error(`Import manifest not found: ${manifestPath}`);
  console.error("Run `npm run cf:db:dump` first.");
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
if (!Array.isArray(manifest.parts) || !manifest.parts.length) {
  console.error(`No import parts listed in ${manifestPath}`);
  process.exit(1);
}

function runWrangler(args, stdio = "inherit") {
  return spawnSync("npx", ["wrangler", ...args], {
    stdio,
    encoding: stdio === "pipe" ? "utf8" : undefined,
    shell: process.platform === "win32",
  });
}

const bookmarkResult = runWrangler(
  ["d1", "time-travel", "info", database, "--json"],
  "pipe",
);
const bookmarkOutput = `${bookmarkResult.stdout || ""}\n${bookmarkResult.stderr || ""}`;
let bookmark;
try {
  bookmark = JSON.parse(bookmarkResult.stdout || "{}").bookmark;
} catch {
  bookmark = undefined;
}
if (bookmarkResult.status !== 0 || !bookmark) {
  console.error(bookmarkOutput.trim() || "Could not capture a D1 rollback bookmark.");
  console.error("Refusing to mutate the database without a recovery point.");
  process.exit(bookmarkResult.status || 1);
}
console.log(`Captured D1 rollback bookmark ${bookmark}.`);

for (const [index, part] of manifest.parts.entries()) {
  const file = join(inputDir, part.filename);
  console.log(
    `Importing ${part.filename} (${index + 1}/${manifest.parts.length}, ${part.inserts} inserts)...`,
  );
  const result = runWrangler(
    ["d1", "execute", database, "--remote", "--file", file, "--yes"],
  );
  if (result.status !== 0) {
    console.error(`Import failed; restoring D1 to bookmark ${bookmark}...`);
    const restore = runWrangler(
      ["d1", "time-travel", "restore", database, `--bookmark=${bookmark}`, "--json"],
    );
    if (restore.status !== 0) {
      console.error("Automatic D1 rollback also failed; manual recovery is required.");
      process.exit(2);
    }
    console.error("D1 was restored to its pre-import state.");
    process.exit(result.status || 1);
  }
}
