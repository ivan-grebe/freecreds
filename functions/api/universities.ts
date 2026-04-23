import { allRows } from "../_shared/d1";
import type { InstitutionRow } from "../_shared/api-types";
import { type Env, json } from "../_shared/http";

export const onRequestGet: PagesFunction<Env> = async ({ env }) => {
  const universities = await allRows<InstitutionRow>(
    env.DB.prepare(`
      SELECT code, name, category FROM institutions
      WHERE category IN ('CSU','UC','AICCU')
        AND id IN (SELECT DISTINCT university_id FROM articulations)
      ORDER BY category, name
    `),
  );

  return json({ universities });
};
