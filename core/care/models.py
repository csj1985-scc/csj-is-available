"""
看护模块数据模型
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Enum, ForeignKey, Boolean, Text, Index
from .database import Base


class CareUser(Base):
    __tablename__ = "care_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(50), nullable=False)
    role = Column(Enum("elder", "child", name="user_role"), nullable=False, default="elder")
    avatar = Column(String(256), default="")
    device_info = Column(String(256), default="")      # 手机型号/系统
    push_token = Column(String(256), default="")        # 推送 token
    created_at = Column(DateTime, default=datetime.now)


class CareBind(Base):
    """子女-老人绑定关系"""
    __tablename__ = "care_binds"
    id = Column(Integer, primary_key=True, autoincrement=True)
    elder_id = Column(Integer, ForeignKey("care_users.id"), nullable=False, index=True)
    child_id = Column(Integer, ForeignKey("care_users.id"), nullable=False, index=True)
    relationship = Column(String(20), default="")       # 父子/母女
    created_at = Column(DateTime, default=datetime.now)

    # 复合索引：按elder查子女 / 按child查老人，高频场景
    __table_args__ = (
        Index("idx_bind_elder_child", "elder_id", "child_id"),
        Index("idx_bind_child_elder", "child_id", "elder_id"),
    )


class HealthRecord(Base):
    """健康记录（血压/血糖/心率/体重）"""
    __tablename__ = "care_health_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("care_users.id"), nullable=False, index=True)
    record_type = Column(Enum("bp", "glucose", "heart_rate", "weight", name="health_type"), nullable=False)
    value = Column(String(50), nullable=False)           # e.g. "120/80", "5.6", "72", "65"
    unit = Column(String(20), default="")
    note = Column(String(200), default="")
    recorded_at = Column(DateTime, default=datetime.now)

    # 复合索引：按用户+类型+时间范围查询，覆盖Dashboard和健康趋势
    __table_args__ = (
        Index("idx_health_user_type_time", "user_id", "record_type", "recorded_at"),
    )


class Alert(Base):
    """警报（SOS/跌倒/围栏/异常）"""
    __tablename__ = "care_alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("care_users.id"), nullable=False, index=True)
    alert_type = Column(Enum("sos", "fall", "geofence", "abnormal", name="alert_type"), nullable=False)
    status = Column(Enum("pending", "escalated", "resolved", name="alert_status"), default="pending")
    location_lat = Column(Float, default=0)
    location_lng = Column(Float, default=0)
    audio_clip = Column(String(256), default="")        # 最后30秒录音路径
    handled_by = Column(Integer, ForeignKey("care_users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime, nullable=True)

    # 复合索引：Dashboard查待处理警报
    __table_args__ = (
        Index("idx_alert_user_status", "user_id", "status"),
    )


class Reminder(Base):
    """提醒配置"""
    __tablename__ = "care_reminders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("care_users.id"), nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey("care_users.id"), nullable=False)
    remind_type = Column(Enum("med", "meal", "sleep", "activity", name="remind_type"), nullable=False)
    title = Column(String(100), nullable=False)
    cron_expr = Column(String(100), default="")          # cron 表达式
    enabled = Column(Boolean, default=True)
    next_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    # 复合索引：按用户查启用的提醒
    __table_args__ = (
        Index("idx_reminder_user_enabled", "user_id", "enabled"),
    )


class ReminderLog(Base):
    """提醒打卡记录"""
    __tablename__ = "care_reminder_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    reminder_id = Column(Integer, ForeignKey("care_reminders.id"), nullable=False, index=True)
    status = Column(Enum("done", "skipped", name="remind_status"), nullable=False)
    checked_at = Column(DateTime, default=datetime.now)


class LocationRecord(Base):
    """位置记录（GPS 轨迹）"""
    __tablename__ = "care_location_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("care_users.id"), nullable=False, index=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    accuracy = Column(Float, default=0)
    recorded_at = Column(DateTime, default=datetime.now)

    # 复合索引：按用户+时间查轨迹
    __table_args__ = (
        Index("idx_location_user_time", "user_id", "recorded_at"),
    )
