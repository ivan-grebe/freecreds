import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

export function createTestDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  const migrations = new URL("../../migrations/", import.meta.url);
  for (const file of readdirSync(migrations).filter((name) => name.endsWith(".sql")).sort()) {
    sqlite.exec(readFileSync(new URL(file, migrations), "utf8"));
  }

  const db = {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let params: SQLInputValue[] = [];
      return {
        bind(...values: SQLInputValue[]) {
          params = values;
          return this;
        },
        async first() {
          return statement.get(...params) ?? null;
        },
        async all() {
          return { results: statement.all(...params) };
        },
      };
    },
  } as unknown as D1Database;

  return { sqlite, db };
}
