# Employee Attendance & Analytics API

A FastAPI + MongoDB service for employee attendance: punch-in / punch-out with business rules, manual corrections with an audit trail, and analytics built on MongoDB aggregation pipelines. All application code is in `app/main.py`.

Also in this repo: `REVIEW.md` (defects found in the starter code) and `DECISIONS.md` (design decisions).

## Requirements

- Python 3.11+
- MongoDB 6.0+ (Atlas free tier or a local instance). The analytics use `$setWindowFields` and `$dateAdd`, which need 5.0 or newer.

## Run

```bash
pip install -r requirements.txt

# Linux / macOS
export MONGO_URI="mongodb+srv://<user>:<password>@<cluster-host>/"
export MONGO_DB="attendance_db"

# Windows (cmd)
set MONGO_URI=mongodb+srv://<user>:<password>@<cluster-host>/
set MONGO_DB=attendance_db

uvicorn app.main:app --port 8000
```

- `MONGO_URI` and `MONGO_DB` are read from the environment. A local `.env` file is also loaded for convenience, but real environment variables take priority. No connection details are hard-coded.
- All indexes are created at startup and are idempotent; no manual steps.
- Interactive API docs: <http://localhost:8000/docs>. `GET /health` returns 200 when MongoDB answers, otherwise 503.
- No seed data is included in this repository.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Pings MongoDB (200 / 503) |
| POST | `/employees` | Create an employee (409 on duplicate `emp_code`) |
| GET | `/employees` | List, filter by department, paginated, sorted by `emp_code` |
| POST | `/attendance/punch-in` | Create the day's record; late minutes computed |
| POST | `/attendance/punch-out` | Close the latest open record; work hours, overtime, half day |
| PATCH | `/attendance/{emp_code}/{date}` | Manual correction; recomputes derived fields, appends to `history` |
| GET | `/attendance` | Filter by employee / dates / status, paginated |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly summary for one employee |
| GET | `/analytics/departments/summary` | Department summary for a month |
| GET | `/analytics/leaderboard/late` | Late-comers ranking (ties share a rank) |
| GET | `/analytics/departments/{department}/trend` | Daily series with 7-day moving average |
| GET | `/admin/explain/{endpoint}` | MongoDB `executionStats` explain for an endpoint's main query |

## How it works

- **Time.** The API takes and returns instants as epoch milliseconds, validated strictly (integers only; seconds, floats and strings give 422). They are stored as UTC datetimes. Shift times and attendance dates are IST. A record's date is the IST date of its punch-in, with the overnight-shift rule applied, and times are truncated to whole seconds.
- **Rounding.** Work hours and reported numbers use `Decimal` with half-up rounding, not Python's `round()`.
- **Concurrency.**
  - Punch-in and employee creation rely on unique indexes; a `DuplicateKeyError` becomes a 409.
  - Punch-out closes a record with one atomic `find_one_and_update` that only matches while the record is still open.
  - PATCH appends to `history` with a guard on the history length, so two concurrent corrections cannot silently overwrite each other (the loser gets 409).
- **Analytics.** Aggregation happens inside MongoDB. The department summary starts from `employees`, so people with no logs are still counted in the headcount. The leaderboard uses `$setWindowFields` with `$rank`. The trend builds one row per calendar day with `$range` and computes the moving average with a window. Python only does the final half-up rounding and the working-day calendar count.
- **Indexes.** `(emp_code, date)` unique, `(date desc, emp_code)`, `(emp_code, punch_in desc)` on attendance; `emp_code` unique, `joined_on`, `department` on employees.
- **Explain.** The explain endpoint builds each query with the same builder functions as the real endpoint, and uses index hints so the plans are stable.

## Known limitations

- Tested manually through the Swagger UI against the small sample dataset. There is no automated test suite.
- Explain plans were checked on the small sample only (index scans, no collection scans), not on a 100,000-record dataset.
- The trend's moving average is computed from unrounded daily rates and rounded only at output.
- The trend endpoint first reads the department's employee list (small) in Python and passes it into the pipeline.