export type QueryResolver = (
  sql: string,
  params: readonly unknown[],
) => Array<Record<string, unknown>>;

class FakeStatement {
  constructor(
    private readonly sql: string,
    private readonly resolver: QueryResolver,
    private readonly params: readonly unknown[] = [],
  ) {}

  bind(...params: unknown[]) {
    return new FakeStatement(this.sql, this.resolver, params);
  }

  async first<T>() {
    return (this.resolver(this.sql, this.params)[0] as T | undefined) ?? null;
  }

  async all<T>() {
    return { results: this.resolver(this.sql, this.params) as T[] };
  }
}

export function fakeD1(resolver: QueryResolver): D1Database {
  return {
    prepare(sql: string) {
      return new FakeStatement(sql, resolver);
    },
  } as unknown as D1Database;
}
