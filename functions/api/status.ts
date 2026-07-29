import { json } from "../_shared/http";

interface Env {
  DB: D1Database;
}

interface RefreshRow {
  kind: "assist" | "cvc";
  last_updated: string;
}

export function buildStatusPayload(rows: RefreshRow[]) {
  const updatedAt: Record<RefreshRow["kind"], string | null> = {
    assist: null,
    cvc: null,
  };
  for (const row of rows) updatedAt[row.kind] = row.last_updated;
  return { updated_at: updatedAt };
}

export const onRequestGet: PagesFunction<Env> = async ({ env }) => {
  const result = await env.DB.prepare(`
    SELECT kind, MAX(finished_at) AS last_updated
    FROM ingest_jobs
    WHERE status = 'completed' AND finished_at IS NOT NULL
    GROUP BY kind
  `).all<RefreshRow>();

  return json(buildStatusPayload(result.results), {
    headers: { "cache-control": "public, max-age=300" },
  });
};
