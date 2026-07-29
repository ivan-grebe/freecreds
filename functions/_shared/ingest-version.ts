import { allRows } from "./d1";

interface RefreshVersionRow extends Record<string, unknown> {
  kind: string;
  finished_at: string;
}

export async function latestIngestVersion(
  db: D1Database,
  kinds: readonly string[],
): Promise<string> {
  if (!kinds.length) return "unversioned";
  const placeholders = kinds.map(() => "?").join(",");
  const rows = await allRows<RefreshVersionRow>(
    db.prepare(`
      SELECT kind, MAX(finished_at) AS finished_at
      FROM ingest_jobs
      WHERE status = 'completed'
        AND finished_at IS NOT NULL
        AND kind IN (${placeholders})
      GROUP BY kind
    `).bind(...kinds),
  );
  const byKind = new Map(rows.map((row) => [row.kind, row.finished_at]));
  return kinds.map((kind) => `${kind}:${byKind.get(kind) || "never"}`).join("|");
}
