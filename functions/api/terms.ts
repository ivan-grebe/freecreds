import { json } from "../_shared/http";
import { upcomingTerms } from "../_shared/terms";

export const onRequestGet: PagesFunction = async () => {
  return json({ terms: upcomingTerms(new Date(), 4) });
};
