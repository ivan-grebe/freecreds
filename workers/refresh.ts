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
  warning?: string;
  metadata?: Record<string, unknown>;
}

interface GitHubTarget {
  owner: string;
  repo: string;
}

const CRON_TO_JOB: Record<string, JobKind> = {
  "0 9 1,15 * *": "cvc",
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
  return Boolean(env.GITHUB_TOKEN && githubTarget(env));
}

function githubTarget(env: Env): GitHubTarget | null {
  const owner = normalizeGitHubPart(env.GITHUB_OWNER);
  const repoValue = normalizeGitHubPart(env.GITHUB_REPO);
  if (!repoValue) {
    return null;
  }

  const repoPath = repoValue
    .replace(/^https:\/\/github\.com\//i, "")
    .replace(/\.git$/i, "");
  const parts = repoPath.split("/").filter(Boolean);

  if (parts.length >= 2) {
    return { owner: parts[0], repo: parts[1] };
  }
  if (owner) {
    return { owner, repo: parts[0] };
  }
  return null;
}

function normalizeGitHubPart(value: string | undefined): string | null {
  const normalized = value?.trim().replace(/^["']|["']$/g, "");
  return normalized || null;
}

function normalizeGitHubToken(value: string): string {
  return value
    .trim()
    .replace(/^Bearer\s+/i, "")
    .replace(/^token\s+/i, "")
    .replace(/[^A-Za-z0-9_]/g, "");
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

function isDbSizeError(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return message.includes("Exceeded maximum DB size");
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
  const target = githubTarget(env);
  if (!env.GITHUB_TOKEN || !target) {
    return;
  }

  const repoPath = `${target.owner}/${target.repo}`;
  const url = `https://api.github.com/repos/${encodeURIComponent(target.owner)}/${encodeURIComponent(target.repo)}/dispatches`;
  const payload = {
    event_type: `freecreds-${job.kind}-refresh`,
    client_payload: {
      kind: job.kind,
      job_id: job.id,
    },
  };
  const payloadJson = JSON.stringify(payload);
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${normalizeGitHubToken(env.GITHUB_TOKEN)}`,
      "Content-Type": "application/json",
      "User-Agent": "freecreds-cloudflare-refresh",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: new TextEncoder().encode(payloadJson),
  });

  if (response.status !== 204) {
    const body = await response.text();
    const requestId = response.headers.get("x-github-request-id");
    throw new Error(
      `GitHub dispatch failed for ${repoPath}: HTTP ${response.status}`
      + `${requestId ? ` request_id=${requestId}` : ""}`
      + `${body ? ` ${body}` : ""}`,
    );
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

  let jobStored = true;
  try {
    await insertJob(env, job);
  } catch (err) {
    if (!isDbSizeError(err)) {
      throw err;
    }
    jobStored = false;
    job.warning = "D1 job logging skipped because the database is over the write-size limit";
  }

  try {
    await dispatchGitHubJob(env, job);
    if (githubConfigured(env)) {
      job.status = "dispatched";
      if (jobStored) {
        await updateJob(env, job.id, "dispatched");
      }
    }
  } catch (err) {
    job.status = "failed";
    job.error = err instanceof Error ? err.message : String(err);
    if (jobStored) {
      await updateJob(env, job.id, "failed", job.error);
    }
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
    try {
      const url = new URL(request.url);
      if (request.method === "POST" && (url.pathname === "/" || url.pathname === "/refresh")) {
        return await handleManual(request, env);
      }
      return json({ ok: true, schedules: CRON_TO_JOB });
    } catch (err) {
      return json({
        error: err instanceof Error ? err.message : String(err),
      }, { status: 500 });
    }
  },
} satisfies ExportedHandler<Env>;
