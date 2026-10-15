# DECISIONS.md

1. **Indexes.** Unique (emp_code, date) on attendance_logs: guarantees one record per day and serves per-employee queries. (date desc, emp_code) serves the list sort and the monthly leaderboard. (emp_code, punch_in desc) serves punch-out lookup. On employees: unique emp_code, joined_on for the department summary, department for the trend. I rejected a status index: it has few values, so the date index plus a filter is enough.

2. **Punch-in race.** Both requests try insert_one. The unique index lets exactly one succeed (201). The other raises DuplicateKeyError, which I return as 409. There is no check-then-insert, so no gap between check and write.

3. **Ties.** I rank with $rank, so tied employees share a rank and the next is skipped. limit filters rank <= limit after ranking, so a tie at the cutoff returns all tied employees, possibly more than limit rows.

4. **Headcount.** The pipeline starts from employees (joined on or before month end), looks up logs, and unwinds with preserveNullAndEmptyArrays, so people with no logs keep a row. Headcount is the number of distinct emp_codes.

5. **100x data.** I would keep pre-aggregated daily stats per department, updated on punch-in/out, instead of recomputing from raw logs with $lookup for every request.