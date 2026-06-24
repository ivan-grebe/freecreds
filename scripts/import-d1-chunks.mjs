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

for (const [index, part] of manifest.parts.entries()) {
  const file = join(inputDir, part.filename);
  console.log(
    `Importing ${part.filename} (${index + 1}/${manifest.parts.length}, ${part.inserts} inserts)...`,
  );
  const result = spawnSync(
    "npx",
    ["wrangler", "d1", "execute", database, "--remote", "--file", file, "--yes"],
    { stdio: "inherit", shell: process.platform === "win32" },
  );
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}
