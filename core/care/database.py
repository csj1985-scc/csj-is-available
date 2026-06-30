"""
看护模块数据库 — SQLite + 同步 SQLAlchemy
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from core.config import PROJECT_ROOT

DB_DIR = Path(os.getenv("WUDAO_DATA", str(PROJECT_ROOT / "data")))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(DB_DIR / "care.db")

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def init_db():
    """建表（幂等）"""
    Base.metadata.create_all(bind=engine)


def get_session():
    """获取新 session"""
    return SessionLocal()
