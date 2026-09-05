export async function allRows<T extends Record<string, unknown>>(
  statement: D1PreparedStatement,
): Promise<T[]> {
  const { results } = await statement.all<T>();
  return results || [];
}

export function placeholders(count: number): string {
  if (!Number.isInteger(count) || count <= 0) {
    throw new Error(`Invalid placeholder count: ${count}`);
  }
  return Array.from({ length: count }, () => "?").join(",");
}

export function chunks<T>(items: T[], size: number): T[][] {
  if (size <= 0) {
    throw new Error("chunk size must be positive");
  }
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

export function uniqueNumbers(values: Iterable<unknown>): number[] {
  const out = new Set<number>();
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      out.add(value);
    }
  }
  return [...out];
}
