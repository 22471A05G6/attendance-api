"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
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
