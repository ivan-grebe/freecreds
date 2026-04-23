type JobKind = "cvc" | "assist";
type JobStatus = "queued" | "dispatched" | "completed" | "failed";

interface Env {
  DB: D1Database;
  GITHUB_TOKEN?: string;
  GITHUB_OWNER?: string;
  GITHUB_REPO?: string;
  MANUAL_TRIGGER_TOKEN?: string;
}

interface JobRecord {
  id: string;
  kind: JobKind;
  status: JobStatus;
  requested_by: string;
  started_at: string;
  error?: string;
  metadata?: Record<string, unknown>;
}

const CRON_TO_JOB: Record<string, JobKind> = {
  "0 9 1,15 * *": "cvc",
  "0 10 1 1,4,7,10 *": "assist",
};

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "x-content-type-options": "nosniff",
      ...(init.headers || {}),
    },
  });
}

function parseJobKind(value: string | null): JobKind | null {
  if (value === "cvc" || value === "assist") {
    return value;
  }
  return null;
}

function githubConfigured(env: Env): boolean {
  return Boolean(env.GITHUB_TOKEN && env.GITHUB_OWNER && env.GITHUB_REPO);
}

async function insertJob(env: Env, job: JobRecord): Promise<void> {
  await env.DB.prepare(`
    INSERT INTO ingest_jobs
      (id, kind, status, requested_by, started_at, error, metadata)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).bind(
    job.id,
    job.kind,
    job.status,
    job.requested_by,
    job.started_at,
    job.error ?? null,
    JSON.stringify(job.metadata ?? {}),
  ).run();
}

async function updateJob(
  env: Env,
  id: string,
  status: JobStatus,
  error: string | null = null,
): Promise<void> {
  await env.DB.prepare(`
    UPDATE ingest_jobs
    SET status = ?, error = ?, finished_at = ?
    WHERE id = ?
  `).bind(status, error, new Date().toISOString(), id).run();
}

async function dispatchGitHubJob(env: Env, job: JobRecord): Promise<void> {
  if (!githubConfigured(env)) {
    return;
  }

  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "content-type": "application/json",
      "user-agent": "freecreds-cloudflare-refresh",
      "x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify({
      event_type: `freecreds-${job.kind}-refresh`,
      client_payload: {
        job_id: job.id,
        kind: job.kind,
        requested_by: job.requested_by,
        source: "cloudflare-cron",
      },
    }),
  });

  if (response.status !== 204) {
    const body = await response.text();
    throw new Error(`GitHub dispatch failed: HTTP ${response.status} ${body}`);
  }
}

async function enqueueRefresh(
  env: Env,
  kind: JobKind,
  requestedBy: string,
): Promise<JobRecord> {
  const job: JobRecord = {
    id: `${kind}-${crypto.randomUUID()}`,
    kind,
    status: "queued",
    requested_by: requestedBy,
    started_at: new Date().toISOString(),
    metadata: {
      dispatch_target: githubConfigured(env) ? "github_repository_dispatch" : "not_configured",
    },
  };

  await insertJob(env, job);

  try {
    await dispatchGitHubJob(env, job);
    if (githubConfigured(env)) {
      job.status = "dispatched";
      await updateJob(env, job.id, "dispatched");
    }
  } catch (err) {
    job.status = "failed";
    job.error = err instanceof Error ? err.message : String(err);
    await updateJob(env, job.id, "failed", job.error);
    throw err;
  }

  return job;
}

async function handleScheduled(cron: string, env: Env): Promise<void> {
  const kind = CRON_TO_JOB[cron];
  if (!kind) {
    throw new Error(`Unexpected cron schedule: ${cron}`);
  }
  await enqueueRefresh(env, kind, `cron:${cron}`);
}

async function handleManual(request: Request, env: Env): Promise<Response> {
  if (!env.MANUAL_TRIGGER_TOKEN) {
    return json({ error: "Manual refresh is disabled" }, { status: 403 });
  }

  const auth = request.headers.get("authorization");
  if (auth !== `Bearer ${env.MANUAL_TRIGGER_TOKEN}`) {
    return json({ error: "Unauthorized" }, { status: 401 });
  }

  const url = new URL(request.url);
  const kind = parseJobKind(url.searchParams.get("job"));
  if (!kind) {
    return json({ error: "Expected ?job=cvc or ?job=assist" }, { status: 400 });
  }

  const job = await enqueueRefresh(env, kind, "manual");
  return json({ job });
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(handleScheduled(controller.cron, env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && (url.pathname === "/" || url.pathname === "/refresh")) {
      return await handleManual(request, env);
    }
    return json({ ok: true, schedules: CRON_TO_JOB });
  },
} satisfies ExportedHandler<Env>;
