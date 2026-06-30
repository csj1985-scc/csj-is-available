"""
看护模块 — Pydantic 请求/响应模型
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── 用户 ────────────────────────────────────────────────

class RegisterReq(BaseModel):
    phone: str = Field(..., pattern=r"^\d{11}$")
    name: str = Field(..., min_length=1, max_length=50)
    role: str = "elder"          # elder | child
    avatar: str = ""
    device_info: str = ""

class LoginReq(BaseModel):
    phone: str = Field(..., pattern=r"^\d{11}$")
    name: str = ""


class UserResp(BaseModel):
    id: int
    phone: str
    name: str
    role: str
    avatar: str
    created_at: datetime


# ── 绑定 ────────────────────────────────────────────────

class BindReq(BaseModel):
    elder_id: int
    child_id: int
    relationship: str = ""


# ── 健康 ────────────────────────────────────────────────

class HealthRecordReq(BaseModel):
    user_id: int
    record_type: str              # bp | glucose | heart_rate | weight
    value: str
    unit: str = ""
    note: str = ""

class HealthRecordResp(BaseModel):
    id: int
    record_type: str
    value: str
    unit: str
    note: str
    recorded_at: datetime

class TrendQuery(BaseModel):
    record_type: str
    days: int = 7


# ── 警报 ────────────────────────────────────────────────

class AlertReq(BaseModel):
    user_id: int
    alert_type: str = "sos"       # sos | fall | geofence | abnormal
    location_lat: float = 0
    location_lng: float = 0

class AlertResp(BaseModel):
    id: int
    alert_type: str
    status: str
    location_lat: float
    location_lng: float
    created_at: datetime
    resolved_at: Optional[datetime] = None


# ── 提醒 ────────────────────────────────────────────────

class ReminderReq(BaseModel):
    user_id: int
    creator_id: int
    remind_type: str              # med | meal | sleep | activity
    title: str = Field(..., min_length=1, max_length=100)
    cron_expr: str = ""

class ReminderResp(BaseModel):
    id: int
    remind_type: str
    title: str
    cron_expr: str
    enabled: bool
    next_run_at: Optional[datetime] = None

class CheckinReq(BaseModel):
    reminder_id: int
    status: str = "done"          # done | skipped


# ── 位置 ────────────────────────────────────────────────

class LocationPoint(BaseModel):
    lat: float
    lng: float
    accuracy: float = 0
    recorded_at: Optional[str] = None

class LocationReq(BaseModel):
    user_id: int
    lat: float
    lng: float
    accuracy: float = 0
    recorded_at: Optional[str] = None

class LocationBatchReq(BaseModel):
    user_id: int
    points: list[LocationPoint]


class LocationResp(BaseModel):
    lat: float
    lng: float
    accuracy: float
    recorded_at: datetime


# ── Dashboard ───────────────────────────────────────────

class ElderDashboard(BaseModel):
    elder: UserResp
    today_health: list[HealthRecordResp]
    pending_alerts: list[AlertResp]
    today_trail: list[LocationResp]
    reminders_today: list[ReminderResp]
