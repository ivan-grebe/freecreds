-- Adds receiving-side AND-bundle companions to reverse_index rows.
-- When a sending course satisfies a Series of receiving courses (e.g.
-- BIOL 150 → UCR BIOL 5A AND BIOL 5LA), each row stores the DB ids of
-- the other bundle members so the UI can show "also yields BIOL 5LA".
-- NULL for ordinary (non-series) articulations.

ALTER TABLE reverse_index ADD COLUMN receiving_companion_course_ids TEXT;
