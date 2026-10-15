"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
import calendar
from datetime import date,datetime, timedelta, timezone
from typing import Annotated,Literal, Optional



from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,Field, StrictInt,field_validator, model_validator
from pymongo import MongoClient,ASCENDING,DESCENDING,ReturnDocument
from contextlib import asynccontextmanager
from pymongo.errors import DuplicateKeyError

from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                     serverSelectionTimeoutMS=5000,
                     tz_aware=True,)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
def create_indexes():
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True, name="uniq_emp_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)], name="date_desc_emp",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="emp_punch_in",
    )
    db.employees.create_index("emp_code", unique=True, name="uniq_emp_code")
    db.employees.create_index([("joined_on", ASCENDING)], name="joined_on_idx")
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_indexes()
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0",lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def shift_start_dt( date_str:str,hhmm:str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)


def compute_late_minutes(punch_in: datetime, shift_start: datetime) -> int:
    punch_in=punch_in.replace(microsecond=0)
    seconds=int((punch_in - shift_start).total_seconds())
    return seconds // 60 if seconds>600 else 0 
    

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds=int((punch_out-punch_in).total_seconds())
    hours = Decimal(seconds)/Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def shift_end_dt(date_str: str, shift_start: str, shift_end: str) -> datetime:
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=eh, minute=em, tzinfo=IST)
    if (eh, em) <= (sh, sm):
        end += timedelta(days=1)
    return end
def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    ist_time = punch_in.replace(microsecond=0).astimezone(IST)
    day = ist_time.date()
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    is_overnight = (eh, em) <= (sh, sm)
    if is_overnight and (ist_time.hour, ist_time.minute) < (eh, em):
        day -= timedelta(days=1)
    return day.isoformat()

def compute_overtime(punch_out: datetime, shift_end: datetime) -> int:
    punch_out = punch_out.replace(microsecond=0)
    seconds = int((punch_out - shift_end).total_seconds())
    return seconds // 60 if seconds >= 1800 else 0



EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)



def ms_to_dt(ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=ms)


def dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH) // timedelta(milliseconds=1)

def _ms_if_datetime(value):
    return dt_to_ms(value) if isinstance(value, datetime) else value

def serialize_record(doc:dict)-> dict:
    history = [
        {
            "at":dt_to_ms(h["at"]),
            "by":h["by"],
            "reason" : h["reason"],
            "changes" :{
                field : {"from" : _ms_if_datetime(c["from"]), "to" : _ms_if_datetime(c["to"])}
                 for field, c in h["changes"].items()
            },
        }
        for h in (doc.get("history") or [])
    ]
    return{
    "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": None if doc.get("punch_in") is None else dt_to_ms(doc["punch_in"]),
        "punch_out": None if doc.get("punch_out") is None else dt_to_ms(doc["punch_out"]),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history,
    }


    




# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length = 1, max_length = 100)
    email: str = Field(max_length = 120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length = 1, max_length = 50)
    shift_start: str = Field(default="09:30", pattern = TIME_PATTERN)
    shift_end: str = Field(default="18:30", pattern = TIME_PATTERN)
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("joined_on")
    @classmethod
    def joined_on_is_real_date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("joined_on must be a real calendar date")
        return v
    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self

EpoMs = Annotated[StrictInt, Field(ge=100_000_000_000, le=4_102_444_800_000)]

class PunchInIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: Optional[EpoMs] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"
class PunchOutIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: Optional[EpoMs] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}




def serialize_employee(doc: dict) -> dict:
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": dt_to_ms(doc["created_at"]),
    }

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),):
    q = {}
    if department:
        q["department"] = department
    total = db.employees.count_documents(q)
    cursor = (
        db.employees.find(q)
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [serialize_employee(d) for d in cursor],
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    if body.punched_at is None:
        ts = datetime.now(timezone.utc)
    else:
        ts = ms_to_dt(body.punched_at)
    ts = ts.replace(microsecond=0)

    d=attendance_date(ts, emp["shift_start"], emp["shift_end"])
    late = compute_late_minutes(ts, shift_start_dt(d, emp["shift_start"]))

    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_record(doc)
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Status = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]

def check_date_param(name:str, value:Optional[str])-> None:
    if value is None:
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", name),
              "msg": "must be a real YYYY-MM-DD date", "input": value}]
        )
def attendance_filter(emp_code, date_from, date_to, status) -> dict:
    q={}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"]={}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    return q
ATTENDANCE_SORT = [("date", DESCENDING), ("emp_code", ASCENDING)]


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern = DATE_PATTERN),
    date_to: Optional[str] = Query(None, pattern = DATE_PATTERN),
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    check_date_param("date_from", date_from)
    check_date_param("date_to", date_to)
    if date_from and date_to and date_from > date_to:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", "date_from"),
              "msg": "date_from must not be after date_to", "input": date_from}]
        )
    q = attendance_filter(emp_code, date_from, date_to, status)
    total = db.attendance_logs.count_documents(q)
    cursor = (
        db.attendance_logs.find(q)
        .sort(ATTENDANCE_SORT)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [serialize_record(d) for d in cursor],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
class RegularizeIn(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[EpoMs] = None
    punch_out: Optional[EpoMs] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


@app.patch("/attendance/{emp_code}/{record_date}")
def regularize(emp_code: str, record_date: str, body: RegularizeIn):
    check_date_param("date", record_date)
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": record_date})
    if rec is None:
        raise HTTPException(404, "no record for that date")

    new_status = body.status or rec["status"]
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT/LEAVE cannot have punch times")
        new_in = new_out = None
    else:
        new_in = (ms_to_dt(body.punch_in).replace(microsecond=0)
                  if body.punch_in is not None else rec.get("punch_in"))
        new_out = (ms_to_dt(body.punch_out).replace(microsecond=0)
                   if body.punch_out is not None else rec.get("punch_out"))
        if new_in is None:
            raise HTTPException(422, "a presence status requires punch_in")
        if (body.punch_in is not None
                and attendance_date(new_in, emp["shift_start"], emp["shift_end"]) != record_date):
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
        if new_out is not None and not (new_in < new_out <= new_in + timedelta(hours=24)):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    if new_in is None:
        late, hours, overtime, half = 0, None, 0, False
    else:
        late = compute_late_minutes(new_in, shift_start_dt(record_date, emp["shift_start"]))
        if new_out is None:
            hours, overtime, half = None, 0, False
        else:
            hours = compute_work_hours(new_in, new_out)
            end = shift_end_dt(record_date, emp["shift_start"], emp["shift_end"])
            overtime = compute_overtime(new_out, end)
            half = hours < 4.5

    new_vals = {
        "status": new_status, "punch_in": new_in, "punch_out": new_out,
        "work_hours": hours, "late_minutes": late,
        "overtime_minutes": overtime, "half_day": half,
    }
    old_vals = {
        "status": rec["status"], "punch_in": rec.get("punch_in"), "punch_out": rec.get("punch_out"),
        "work_hours": rec.get("work_hours"), "late_minutes": rec.get("late_minutes", 0),
        "overtime_minutes": rec.get("overtime_minutes", 0), "half_day": rec.get("half_day", False),
    }
    if all(new_vals[f] == old_vals[f] for f in ("status", "punch_in", "punch_out")):
        raise HTTPException(422, "request changes nothing")
    changes = {f: {"from": old_vals[f], "to": new_vals[f]}
               for f in new_vals if new_vals[f] != old_vals[f]}

    entry = {"at": datetime.now(timezone.utc), "by": body.regularized_by,
             "reason": body.reason, "changes": changes}
    n = len(rec.get("history") or [])
    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": emp_code, "date": record_date,
         "punch_in": old_vals["punch_in"], "punch_out": old_vals["punch_out"],
         f"history.{n}": {"$exists": False}},
        {"$set": new_vals, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "record was modified concurrently; retry")
    return serialize_record(updated)

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
PRESENT_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]


def round_half_up(value, places: int) -> float:
    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def month_bounds(month: str):
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def count_working_days(start: date, end: date) -> int:
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5:          # Monday=0 ... Friday=4
            n += 1
        d += timedelta(days=1)
    return n


def monthly_pipeline(emp_code: str, first_iso: str, last_iso: str) -> list:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first_iso, "$lte": last_iso}}},
        {"$addFields": {
            "dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}}
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$status", PRESENT_STATUSES]},
                    {"$gte": ["$dow", 2]}, {"$lte": ["$dow", 6]},
                ]},
                {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=MONTH_PATTERN)):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    first, last = month_bounds(month)
    joined = date.fromisoformat(emp["joined_on"])
    working_days = count_working_days(max(first, joined), last)

    rows = list(db.attendance_logs.aggregate(
        monthly_pipeline(emp_code, first.isoformat(), last.isoformat())
    ))
    agg = rows[0] if rows else {}
    present = agg.get("present_days", 0)
    pct = None
    if working_days > 0:
        pct = round_half_up(Decimal(str(present)) / Decimal(working_days) * 100, 2)
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": round_half_up(present, 2),
        "leave_days": agg.get("leave_days", 0),
        "late_count": agg.get("late_count", 0),
        "total_late_minutes": agg.get("total_late_minutes", 0),
        "total_overtime_minutes": agg.get("total_overtime_minutes", 0),
        "attendance_pct": pct,
    }
def dept_summary_pipeline(first_iso: str, last_iso: str, department: Optional[str]) -> list:
    match = {"joined_on": {"$lte": last_iso}}            # R9: joined on/before month end
    if department:
        match["department"] = department
    pres = {"$in": ["$logs.status", PRESENT_STATUSES]}
    has_wh = {"$gte": [{"$ifNull": ["$logs.work_hours", -1]}, 0]}
    return [
        {"$match": match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", first_iso]},
                    {"$lte": ["$date", last_iso]},
                ]}}},
                {"$addFields": {"dow": {"$dayOfWeek": {
                    "$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}}}},
            ],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "emps": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [pres, {"$gte": ["$logs.dow", 2]}, {"$lte": ["$logs.dow", 6]}]},
                {"$cond": [{"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}, 0.5, 1]},
                0]}},
            "wh_sum": {"$sum": {"$cond": [{"$and": [pres, has_wh]}, {"$toDecimal": "$logs.work_hours"}, 0]}},
            "wh_count": {"$sum": {"$cond": [{"$and": [pres, has_wh]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$emps"},
                      "present_days": 1, "wh_sum": 1, "wh_count": 1, "late_count": 1,
                      "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},
        {"$sort": {"department": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=MONTH_PATTERN),
                       department: Optional[str] = None):
    first, last = month_bounds(month)
    pipeline = dept_summary_pipeline(first.isoformat(), last.isoformat(), department)
    items = []
    for r in db.employees.aggregate(pipeline, hint="joined_on_idx"):
        avg = None
        if r["wh_count"]:
            total = r["wh_sum"].to_decimal() if hasattr(r["wh_sum"], "to_decimal") else Decimal(str(r["wh_sum"]))
            avg = float((total / r["wh_count"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        items.append({
            "department": r["department"],
            "headcount": r["headcount"],
            "present_days": round_half_up(r["present_days"], 2),
            "avg_work_hours": avg,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"],
        })
    return {"month": month, "items": items}
def leaderboard_pipeline(first_iso: str, last_iso: str, limit: int, department: Optional[str]) -> list:
    pipeline = [
        {"$match": {"date": {"$gte": first_iso, "$lte": last_iso}}},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id",
                     "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1},
                              "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$emp.name",
                      "department": "$emp.department", "total_late_minutes": 1, "late_count": 1}},
    ]
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=MONTH_PATTERN),
                     limit: int = Query(10, ge=1, le=50),
                     department: Optional[str] = None):
    first, last = month_bounds(month)
    pipeline = leaderboard_pipeline(first.isoformat(), last.isoformat(), limit, department)
    items = list(db.attendance_logs.aggregate(pipeline, hint="date_desc_emp"))
    return {"month": month, "items": items}
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code" : body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    if body.punched_at is None:
        ts = datetime.now(timezone.utc)
    else:
        ts=ms_to_dt(body.punched_at)
    ts = ts.replace(microsecond=0)

    rec = db.attendance_logs.find_one(
        {"emp_code" :body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", DESCENDING)],
    )
    if rec is None:
        raise HTTPException(404, "no punch-in found")
    if rec.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
    punch_in_dt = rec["punch_in"]
    if ts <= punch_in_dt:
        raise HTTPException(422, "punched_at must be after punch_in")
    if ts - punch_in_dt > timedelta(hours=24):
        raise HTTPException(422, "punched_at is more than 24 hours after punch_in")
    work_hours = compute_work_hours(punch_in_dt, ts)
    end = shift_end_dt(rec["date"], emp["shift_start"], emp["shift_end"])
    overtime = compute_overtime(ts, end)

    updated = db.attendance_logs.find_one_and_update(
        {"_id": rec["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": work_hours < 4.5,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return serialize_record(updated)


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
