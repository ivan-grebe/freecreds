-- Tighten common API lookup paths to avoid broad row reads.

CREATE INDEX IF NOT EXISTS idx_inst_code_nocase
  ON institutions(code COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_courses_lookup_nocase
  ON courses(institution_id, prefix COLLATE NOCASE, number COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_art_receiving_year_cc
  ON articulations(receiving_course_id, academic_year_id, sending_cc_id);

CREATE INDEX IF NOT EXISTS idx_reverse_receiving_year_cc
  ON reverse_index(receiving_course_id, academic_year_id, sending_cc_id);

CREATE INDEX IF NOT EXISTS idx_offerings_source_term
  ON class_offerings(source, term_id);
