export interface Env {
  DB: D1Database;
}

export interface JsonError {
  error: string;
}

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
};

export function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      ...JSON_HEADERS,
      ...(init.headers || {}),
    },
  });
}

export function error(status: number, message: string): Response {
  return json({ error: message } satisfies JsonError, { status });
}

interface StringParamOptions {
  maxLength: number;
  pattern?: RegExp;
  description: string;
}

export function requireStringParam(
  url: URL,
  name: string,
  options: StringParamOptions,
): string | Response {
  const value = url.searchParams.get(name);
  if (value == null || !value.trim()) {
    return error(400, `Missing required query parameter: ${name}`);
  }
  return normalizeStringParam(name, value, options);
}

export function optionalStringParam(
  url: URL,
  name: string,
  options: StringParamOptions,
): string | Response | null {
  const value = url.searchParams.get(name);
  if (value == null || !value.trim()) {
    return null;
  }
  return normalizeStringParam(name, value, options);
}

function normalizeStringParam(
  name: string,
  value: string,
  options: StringParamOptions,
): string | Response {
  const normalized = value.trim().toUpperCase();
  if (normalized.length > options.maxLength) {
    return error(400, `${name} is too long; expected ${options.description}`);
  }
  if (options.pattern && !options.pattern.test(normalized)) {
    return error(400, `Invalid ${name}; expected ${options.description}`);
  }
  return normalized;
}

export function parseBooleanParam(url: URL, name: string): boolean {
  const value = url.searchParams.get(name);
  if (value == null) {
    return false;
  }
  return value === "1" || value.toLowerCase() === "true";
}

export function safeScheduleUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  try {
    const parsed = new URL(value);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return parsed.toString();
    }
  } catch {
    return null;
  }
  return null;
}
