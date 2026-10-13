"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
from dataclasses import field
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated,Literal, Optional



from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel,Field, StrictInt
from pymongo import MongoClient,ASCENDING
from contextlib import asynccontextmanager
from pymongo.errors import DuplicateKeyError

from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
def create_indexes():
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique = True,
        name = "uniq_emp_date",
    )
    db.employees.create_index("emp_code", unique = True, name = "uniq_emp_code")

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
    return float(hours.quantize(Decimal(0.01), rounding=ROUND_HALF_UP))

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
    return dt_to_ms(value) if instance(value, datetime) else value

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
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str
EpoMs = Annotated[StrictInt, Field(ge=100_000_000_000, le=4_102_444_800_000)]

class PunchInIn(BaseModel):
    emp_code:str =  Field(min_length=1)
    punched_at: Optional[EpoMs] = None
    status: Literal["PRESENT", "WFH","ON_DUTY"] = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = page * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


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


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


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
