export interface InstitutionRow extends Record<string, unknown> {
  id: number;
  code: string;
  name: string;
  category?: string;
}

export interface CourseRow extends Record<string, unknown> {
  id?: number;
  prefix: string;
  number: string;
  title: string;
  min_units: number | null;
  max_units: number | null;
}

export interface ReverseIndexRow extends Record<string, unknown> {
  cc_code: string;
  cc_name: string;
  cc_schedule_url: string | null;
  cc_course_id: number;
  cc_prefix: string;
  cc_number: string;
  cc_title: string;
  min_units: number | null;
  max_units: number | null;
  is_standalone_equivalent: number;
  companion_course_ids: string | null;
  cc_institution_id: number;
  sources_csv: string | null;
  academic_year_id: number;
}

export interface OfferingRow extends Record<string, unknown> {
  institution_id: number;
  prefix: string;
  number: string;
  modality: string;
}

export interface NoArticulationRow extends Record<string, unknown> {
  cc_code: string;
  cc_name: string;
  no_articulation_reason: string;
}

export interface TermRow extends Record<string, unknown> {
  id: number;
  label: string;
}
