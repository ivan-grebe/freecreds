CREATE TABLE IF NOT EXISTS institutions (
  id INTEGER PRIMARY KEY,
  assist_id INTEGER UNIQUE NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL CHECK(category IN ('CCC','CSU','UC','AICCU')),
  term_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inst_code ON institutions(code);
CREATE INDEX IF NOT EXISTS idx_inst_code_nocase
  ON institutions(code COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_inst_category ON institutions(category);

CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  course_identifier_parent_id INTEGER NOT NULL,
  prefix TEXT NOT NULL,
  number TEXT NOT NULL,
  title TEXT NOT NULL,
  min_units REAL,
  max_units REAL,
  is_terminated BOOLEAN NOT NULL DEFAULT 0,
  UNIQUE(institution_id, course_identifier_parent_id)
);
CREATE INDEX IF NOT EXISTS idx_courses_lookup ON courses(institution_id, prefix, number);
CREATE INDEX IF NOT EXISTS idx_courses_lookup_nocase
  ON courses(institution_id, prefix COLLATE NOCASE, number COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS articulations (
  id INTEGER PRIMARY KEY,
  receiving_course_id INTEGER NOT NULL REFERENCES courses(id),
  sending_cc_id INTEGER NOT NULL REFERENCES institutions(id),
  university_id INTEGER NOT NULL REFERENCES institutions(id),
  academic_year_id INTEGER NOT NULL,
  source_context TEXT NOT NULL DEFAULT 'AllDepartments',
  no_articulation_reason TEXT,
  UNIQUE(receiving_course_id, sending_cc_id, academic_year_id, source_context)
);
CREATE INDEX IF NOT EXISTS idx_art_lookup ON articulations(
  university_id, academic_year_id, receiving_course_id
);
CREATE INDEX IF NOT EXISTS idx_art_receiving_year_cc ON articulations(
  receiving_course_id, academic_year_id, sending_cc_id
);

CREATE TABLE IF NOT EXISTS reverse_index (
  id INTEGER PRIMARY KEY,
  receiving_course_id INTEGER NOT NULL REFERENCES courses(id),
  sending_cc_id INTEGER NOT NULL REFERENCES institutions(id),
  sending_course_id INTEGER NOT NULL REFERENCES courses(id),
  is_standalone_equivalent BOOLEAN NOT NULL,
  companion_course_ids TEXT,
  academic_year_id INTEGER NOT NULL,
  source_context TEXT NOT NULL DEFAULT 'AllDepartments',
  receiving_companion_course_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_reverse_receiving_year_cc ON reverse_index(
  receiving_course_id, academic_year_id, sending_cc_id
);
CREATE INDEX IF NOT EXISTS idx_reverse_sending ON reverse_index(
  sending_cc_id, academic_year_id
);

CREATE TABLE IF NOT EXISTS terms (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  season TEXT NOT NULL,
  year INTEGER NOT NULL,
  start_date TEXT,
  end_date TEXT
);

CREATE TABLE IF NOT EXISTS class_offerings (
  id INTEGER PRIMARY KEY,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  course_id INTEGER REFERENCES courses(id),
  prefix TEXT NOT NULL,
  number TEXT NOT NULL,
  term_id INTEGER NOT NULL REFERENCES terms(id),
  modality TEXT NOT NULL CHECK(modality IN ('online_async','online_sync','online_mixed')),
  source TEXT NOT NULL,
  source_ref TEXT,
  fetched_at TEXT NOT NULL,
  UNIQUE(institution_id, prefix, number, term_id, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_offerings_course
  ON class_offerings(course_id, term_id);
CREATE INDEX IF NOT EXISTS idx_offerings_source_term
  ON class_offerings(source, term_id);

CREATE TABLE IF NOT EXISTS ingest_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('cvc','assist')),
  status TEXT NOT NULL CHECK(status IN ('queued','dispatched','completed','failed')),
  requested_by TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT,
  metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_kind_started
  ON ingest_jobs(kind, started_at);
