"""
看护模块 REST API — Flutter App 数据接口
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc

from .database import get_session
from .models import (
    CareUser, CareBind, HealthRecord,
    Alert, Reminder, ReminderLog, LocationRecord,
)
from .schemas import (
    RegisterReq, LoginReq, UserResp,
    BindReq,
    HealthRecordReq, HealthRecordResp,
    AlertReq, AlertResp,
    ReminderReq, ReminderResp, CheckinReq,
    LocationPoint, LocationBatchReq, LocationResp,
    ElderDashboard,
)

router = APIRouter(prefix="/api/care", tags=["看护"])


def _user_to_resp(u: CareUser) -> UserResp:
    return UserResp(id=u.id, phone=u.phone, name=u.name,
                    role=u.role.value if hasattr(u.role, 'value') else u.role,
                    avatar=u.avatar, created_at=u.created_at)


# ── 用户 ────────────────────────────────────────────────

@router.post("/user/register")
def register(body: RegisterReq, db: Session = Depends(get_session)):
    existing = db.query(CareUser).filter(CareUser.phone == body.phone).first()
    if existing:
        return {"ok": True, "user": _user_to_resp(existing).model_dump()}
    user = CareUser(phone=body.phone, name=body.name,
                    role=body.role, avatar=body.avatar,
                    device_info=body.device_info)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"ok": True, "user": _user_to_resp(user).model_dump()}


@router.post("/user/login")
def login(body: LoginReq, db: Session = Depends(get_session)):
    user = db.query(CareUser).filter(CareUser.phone == body.phone).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    return {"ok": True, "user": _user_to_resp(user).model_dump()}


@router.get("/user/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_session)):
    user = db.query(CareUser).filter(CareUser.id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    return {"ok": True, "user": _user_to_resp(user).model_dump()}


# ── 绑定 ────────────────────────────────────────────────

@router.post("/bind")
def bind(body: BindReq, db: Session = Depends(get_session)):
    exists = db.query(CareBind).filter(
        CareBind.elder_id == body.elder_id,
        CareBind.child_id == body.child_id
    ).first()
    if exists:
        return {"ok": True, "bind_id": exists.id}
    bind = CareBind(elder_id=body.elder_id, child_id=body.child_id,
                    relationship=body.relationship)
    db.add(bind)
    db.commit()
    db.refresh(bind)
    return {"ok": True, "bind_id": bind.id}


@router.get("/bind/elders/{child_id}")
def get_elders(child_id: int, db: Session = Depends(get_session)):
    """子女查绑定的老人列表 — 用 JOIN 消除 N+1"""
    results = (
        db.query(CareUser)
        .join(CareBind, CareBind.elder_id == CareUser.id)
        .filter(CareBind.child_id == child_id)
        .all()
    )
    return {"ok": True, "elders": [_user_to_resp(u).model_dump() for u in results]}


@router.get("/bind/children/{elder_id}")
def get_children(elder_id: int, db: Session = Depends(get_session)):
    """老人查绑定的子女列表 — 用 JOIN 消除 N+1"""
    results = (
        db.query(CareUser)
        .join(CareBind, CareBind.child_id == CareUser.id)
        .filter(CareBind.elder_id == elder_id)
        .all()
    )
    return {"ok": True, "children": [_user_to_resp(u).model_dump() for u in results]}


# ── 健康 ────────────────────────────────────────────────

@router.post("/health")
def record_health(body: HealthRecordReq, db: Session = Depends(get_session)):
    rec = HealthRecord(
        user_id=body.user_id, record_type=body.record_type,
        value=body.value, unit=body.unit, note=body.note,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {"ok": True, "id": rec.id}


@router.get("/health/{user_id}")
def get_health(
    user_id: int,
    record_type: str = Query("bp"),
    days: int = Query(7),
    db: Session = Depends(get_session),
):
    since = datetime.now() - timedelta(days=days)
    records = (
        db.query(HealthRecord)
        .filter(
            HealthRecord.user_id == user_id,
            HealthRecord.record_type == record_type,
            HealthRecord.recorded_at >= since,
        )
        .order_by(HealthRecord.recorded_at.asc())
        .all()
    )
    return {
        "ok": True,
        "records": [
            HealthRecordResp(
                id=r.id, record_type=r.record_type.value if hasattr(r.record_type, 'value') else r.record_type,
                value=r.value, unit=r.unit, note=r.note, recorded_at=r.recorded_at
            ).model_dump()
            for r in records
        ]
    }


# ── 警报 ────────────────────────────────────────────────

@router.post("/alert")
def trigger_alert(body: AlertReq, db: Session = Depends(get_session)):
    alert = Alert(
        user_id=body.user_id, alert_type=body.alert_type,
        location_lat=body.location_lat, location_lng=body.location_lng,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    # TODO: 推送子女端 + 短信兜底
    return {"ok": True, "alert_id": alert.id}


@router.get("/alert/{user_id}")
def get_alerts(
    user_id: int,
    limit: int = Query(20),
    db: Session = Depends(get_session),
):
    alerts = (
        db.query(Alert)
        .filter(Alert.user_id == user_id)
        .order_by(desc(Alert.created_at))
        .limit(limit)
        .all()
    )
    return {
        "ok": True,
        "alerts": [
            AlertResp(
                id=a.id, alert_type=a.alert_type.value if hasattr(a.alert_type, 'value') else a.alert_type,
                status=a.status.value if hasattr(a.status, 'value') else a.status,
                location_lat=a.location_lat, location_lng=a.location_lng,
                created_at=a.created_at, resolved_at=a.resolved_at
            ).model_dump()
            for a in alerts
        ]
    }


@router.post("/alert/{alert_id}/resolve")
def resolve_alert(alert_id: int, handled_by: int = Query(...), db: Session = Depends(get_session)):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(404, "警报不存在")
    alert.status = "resolved"
    alert.handled_by = handled_by
    alert.resolved_at = datetime.now()
    db.commit()
    return {"ok": True}


# ── 提醒 ────────────────────────────────────────────────

@router.post("/reminder")
def create_reminder(body: ReminderReq, db: Session = Depends(get_session)):
    r = Reminder(
        user_id=body.user_id, creator_id=body.creator_id,
        remind_type=body.remind_type, title=body.title,
        cron_expr=body.cron_expr,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"ok": True, "reminder_id": r.id}


@router.get("/reminder/{user_id}")
def get_reminders(user_id: int, db: Session = Depends(get_session)):
    reminders = db.query(Reminder).filter(
        Reminder.user_id == user_id, Reminder.enabled == True
    ).all()
    return {
        "ok": True,
        "reminders": [
            ReminderResp(
                id=r.id, remind_type=r.remind_type.value if hasattr(r.remind_type, 'value') else r.remind_type,
                title=r.title, cron_expr=r.cron_expr,
                enabled=r.enabled, next_run_at=r.next_run_at
            ).model_dump()
            for r in reminders
        ]
    }


@router.delete("/reminder/{reminder_id}")
def delete_reminder(reminder_id: int, db: Session = Depends(get_session)):
    r = db.query(Reminder).filter(Reminder.id == reminder_id).first()
    if not r:
        raise HTTPException(404, "提醒不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/reminder/checkin")
def checkin_reminder(body: CheckinReq, db: Session = Depends(get_session)):
    log = ReminderLog(reminder_id=body.reminder_id, status=body.status)
    db.add(log)
    db.commit()
    return {"ok": True}


# ── 位置 ────────────────────────────────────────────────

@router.post("/location/batch")
def upload_location(body: LocationBatchReq, db: Session = Depends(get_session)):
    now = datetime.now()
    for p in body.points:
        rec = LocationRecord(
            user_id=body.user_id, lat=p.lat, lng=p.lng,
            accuracy=p.accuracy,
            recorded_at=datetime.fromisoformat(p.recorded_at) if p.recorded_at else now,
        )
        db.add(rec)
    db.commit()
    return {"ok": True, "count": len(body.points)}


@router.get("/location/{user_id}/today")
def get_today_trail(user_id: int, db: Session = Depends(get_session)):
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    records = (
        db.query(LocationRecord)
        .filter(
            LocationRecord.user_id == user_id,
            LocationRecord.recorded_at >= today_start,
        )
        .order_by(LocationRecord.recorded_at.asc())
        .all()
    )
    return {
        "ok": True,
        "trail": [
            LocationResp(lat=r.lat, lng=r.lng,
                         accuracy=r.accuracy, recorded_at=r.recorded_at)
            .model_dump()
            for r in records
        ]
    }


# ── Dashboard ───────────────────────────────────────────

@router.get("/elder/{elder_id}/dashboard")
def elder_dashboard(elder_id: int, db: Session = Depends(get_session)):
    elder = db.query(CareUser).filter(CareUser.id == elder_id).first()
    if not elder:
        raise HTTPException(404, "老人不存在")

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 合并查询：一次查出今日健康 + 待处理警报 + 今日轨迹 + 提醒
    # 使用 4 次独立查询（SQLite 不支持并行，但每个查询都走了复合索引，性能可接受）
    health = db.query(HealthRecord).filter(
        HealthRecord.user_id == elder_id,
        HealthRecord.recorded_at >= today,
    ).order_by(desc(HealthRecord.recorded_at)).all()

    alerts = db.query(Alert).filter(
        Alert.user_id == elder_id,
        Alert.status.in_(["pending", "escalated"]),
    ).order_by(desc(Alert.created_at)).limit(10).all()

    trail = db.query(LocationRecord).filter(
        LocationRecord.user_id == elder_id,
        LocationRecord.recorded_at >= today,
    ).order_by(LocationRecord.recorded_at.asc()).all()

    reminders = db.query(Reminder).filter(
        Reminder.user_id == elder_id,
        Reminder.enabled == True,
    ).all()

    return {
        "ok": True,
        "dashboard": ElderDashboard(
            elder=_user_to_resp(elder),
            today_health=[
                HealthRecordResp(
                    id=h.id,
                    record_type=h.record_type.value if hasattr(h.record_type, 'value') else h.record_type,
                    value=h.value, unit=h.unit, note=h.note, recorded_at=h.recorded_at
                ) for h in health
            ],
            pending_alerts=[
                AlertResp(
                    id=a.id,
                    alert_type=a.alert_type.value if hasattr(a.alert_type, 'value') else a.alert_type,
                    status=a.status.value if hasattr(a.status, 'value') else a.status,
                    location_lat=a.location_lat, location_lng=a.location_lng,
                    created_at=a.created_at, resolved_at=a.resolved_at
                ) for a in alerts
            ],
            today_trail=[
                LocationResp(lat=r.lat, lng=r.lng, accuracy=r.accuracy, recorded_at=r.recorded_at)
                for r in trail
            ],
            reminders_today=[
                ReminderResp(
                    id=r.id,
                    remind_type=r.remind_type.value if hasattr(r.remind_type, 'value') else r.remind_type,
                    title=r.title, cron_expr=r.cron_expr,
                    enabled=r.enabled, next_run_at=r.next_run_at
                ) for r in reminders
            ],
        ).model_dump()
    }
