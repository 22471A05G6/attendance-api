# REVIEW.md

| # | Where | What is wrong | How you'd notice it | How I fixed it |
|---|---|---|---|---|
| 1 | compute_work_hours | Python round() on a float: banker's rounding and float error, not half-up (R4) | 54 seconds worked gives 0.01, should be 0.02 | Decimal arithmetic with ROUND_HALF_UP |
| 2 | compute_late_minutes | Floored to whole minutes before the grace check, so 09:40:01 gave 0 instead of 10 (R2) | Shift 09:30, punch-in 09:40:01 | Compare in seconds (> 600), then floor to minutes |
| 3 | compute_late_minutes | Built shift start from punch_in's own date and timezone; ignored IST | Punch-in 10:05 IST (04:35 UTC) returned 0 instead of 35 | Aware IST datetimes via shift_start_dt, passed into the function |
| 4 | compute_overtime | No 30-minute minimum; shift end built on the wrong day for overnight shifts (R3) | Leaving 5 min after shift end counted as overtime; 22:00-06:00 shift gave nonsense | shift_end_dt adds a day for overnight shifts; counts only if >= 30 min |
| 5 | create_employee | Check-then-insert race; no input validation; naive created_at | Two parallel creates both succeed; bad email or shift accepted | Unique index plus DuplicateKeyError -> 409; Pydantic validation; UTC created_at |
| 6 | list_employees | Skip used page * page_size; total ignored the filter; no sort; no limits on paging | page=1 skipped the first page; wrong total with department filter | (page-1)*page_size, same filter for count, sort by emp_code, Query limits |
| 7 | punch_in | Missing employee not checked | Unknown emp_code gave a 500 | 404 when employee not found |
| 8 | punch_in | status was any string | status "banana" stored | Literal of PRESENT/WFH/ON_DUTY |
| 9 | punch_in | Time handled in server-local timezone, date taken from local time, truthiness check on punched_at, no strict int validation | Seconds, floats or strings accepted; wrong date near midnight | Strict epoch-ms model, UTC datetimes, attendance_date helper with the overnight rule (R1) |
| 10 | punch_in | Check-then-insert race | Two simultaneous requests both get 201 | Unique (emp_code, date) index; DuplicateKeyError -> 409 |
| 11 | punch_in / list_attendance | Response exposed id and returned datetimes as text, not epoch ms | Response had "id" and ISO strings | serialize_record helper |
| 12 | list_attendance | Loaded everything into memory and sorted/paged in Python; wrong sort (date only); no page_size cap | Slow at 100k; ties in wrong order | Filter, sort, skip, limit in MongoDB; sort date desc then emp_code; limits; supporting index |
| 13 | list_attendance | Legacy records without half_day/history | Missing fields in response | Defaults in serialize_record |
| 14 | /health | Always returned OK | Still 200 with MongoDB down | Ping MongoDB, return 503 on failure |
| 15 | startup | No indexes created | Collection scans at scale | Idempotent create_index calls at startup |

**Looked at and judged fine:** reading MONGO_URI from the environment with load_dotenv, which does not override real environment variables; Pydantic ignoring client-sent derived fields.