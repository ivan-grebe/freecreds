#!/usr/bin/env node
import { spawn } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { createGunzip } from "node:zlib";

const [inputArg = "seed/cvc-base.sql.gz", outputArg = "data/assist.db"] = process.argv.slice(2);
const inputPath = resolve(inputArg);
const outputPath = resolve(outputArg);

if (!existsSync(inputPath)) {
  console.error(`CVC seed snapshot not found: ${inputPath}`);
  process.exit(1);
}

mkdirSync(dirname(outputPath), { recursive: true });
for (const suffix of ["", "-wal", "-shm", "-journal"]) {
  rmSync(`${outputPath}${suffix}`, { force: true });
}

async function main() {
  const sqlite = spawn("sqlite3", [outputPath], {
    stdio: ["pipe", "inherit", "pipe"],
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

  const source = createReadStream(inputPath);
  if (inputPath.toLowerCase().endsWith(".gz")) {
    source.pipe(createGunzip()).pipe(sqlite.stdin);
  } else {
    source.pipe(sqlite.stdin);
  }

  const exitCode = await new Promise((resolveClose) => sqlite.once("close", resolveClose));
  if (exitCode !== 0) {
    console.error(stderr || "sqlite3 restore failed");
    process.exit(typeof exitCode === "number" ? exitCode : 1);
  }

  console.log(`Restored CVC seed snapshot to ${outputPath}`);
}

await main();
