# Reverse ASSIST Search

Reverse-lookup tool for California community college articulation agreements.
Pick a university + course → see every CCC with an articulating course.

Architecture and the full spec are in [PLAN.md](PLAN.md).

## Quick start

```bash
# 1. Install dependencies (user install, no venv needed)
python -m pip install --user httpx fastapi uvicorn pytest respx

# 2. Verify the ASSIST API is reachable
python -m src.assist_api

# 3. Ingest CSUF articulations into data/assist.db
#    (one-time, ~1 minute for all CCCs; add --limit 3 for a quick test)
python -m src.ingester --university CSUF

# 4. Serve the API + frontend
python -m uvicorn src.api:app --reload --port 8000
# Open http://127.0.0.1:8000/

# 5. Tests
python -m pytest tests/ -v
```

## Implementation notes

- **Python 3.8+** (no dependency on 3.10 syntax).
- **AcademicYears endpoint is gated.** The documented `/AcademicYears/api`
  endpoint requires an API key we don't have (public access opens late 2026).
  The client infers the current academic year ID from `sendingYearIds` in
  `/Agreements/Published/from/{id}` instead.
- **Alias map** (`CSUF` → `CSUFULL`, etc.) in `src/assist_api.py::CODE_ALIASES`.
- **AND/OR tree is preserved** in `articulation_course_groups` + `articulation_group_members`.
  The `reverse_index` table is the denormalized lookup the query endpoint reads.
- **Series-type** receiving articulations are skipped in MVP (see PLAN.md § Gotchas).

## API

- `GET /api/universities` — list ingested universities.
- `GET /api/courses?university=CSUFULL` — list courses at that university.
- `GET /api/reverse?university=CSUFULL&prefix=MATH&number=150A[&standalone_only=true]`
  — return every CCC with an articulating course, with AND-bundle companions inlined.
