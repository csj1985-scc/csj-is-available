"""
待办事项 (Todo) 模块
=====================
功能：CRUD 操作 + JSON 文件持久化
设计：无外部依赖，轻量级，与项目现有架构一致
"""

import json
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import WUDAO_DATA as DATA_DIR

# ── 数据文件路径 ─────────────────────────────────────────
TODOS_FILE = Path(DATA_DIR) / "todos.json"

# ── 线程锁（确保文件写入串行化） ─────────────────────────
_lock = threading.Lock()


# ── Pydantic 数据模型 ────────────────────────────────────

class TodoItem(BaseModel):
    """待办事项数据模型"""
    id: str = ""
    title: str
    description: str = ""
    completed: bool = False
    created_at: str = ""
    updated_at: str = ""

    class Config:
        json_schema_extra = {
            "example": {
                "id": "a1b2c3d4-...",
                "title": "完成项目报告",
                "description": "需要在下周五之前完成",
                "completed": False,
                "created_at": "2024-01-01T10:00:00",
                "updated_at": "2024-01-01T10:00:00",
            }
        }


class TodoCreate(BaseModel):
    """创建待办请求"""
    title: str
    description: str = ""


class TodoUpdate(BaseModel):
    """更新待办请求"""
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None


# ── 存储操作（线程安全） ────────────────────────────────

def _load_all() -> Dict[str, dict]:
    """从 JSON 文件加载所有待办事项"""
    if not TODOS_FILE.exists():
        return {}
    try:
        with open(TODOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}


def _save_all(items: Dict[str, dict]):
    """保存所有待办事项到 JSON 文件"""
    TODOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TODOS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _now() -> str:
    """返回 ISO 格式当前时间字符串"""
    return datetime.now().isoformat(timespec="seconds")


def _new_id() -> str:
    """生成唯一 ID"""
    return str(uuid.uuid4())


# ── CRUD 函数（供路由和外部调用） ───────────────────────

def list_todos() -> List[dict]:
    """获取所有待办事项，按创建时间倒序"""
    with _lock:
        items = _load_all()
    todo_list = list(items.values())
    todo_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return todo_list


def get_todo(todo_id: str) -> Optional[dict]:
    """根据 ID 获取单个待办事项"""
    with _lock:
        items = _load_all()
    return items.get(todo_id)


def create_todo(data: TodoCreate) -> dict:
    """创建新的待办事项"""
    now = _now()
    item = {
        "id": _new_id(),
        "title": data.title.strip(),
        "description": data.description.strip(),
        "completed": False,
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        items = _load_all()
        items[item["id"]] = item
        _save_all(items)
    return item


def update_todo(todo_id: str, data: TodoUpdate) -> Optional[dict]:
    """更新待办事项（部分更新）"""
    with _lock:
        items = _load_all()
        item = items.get(todo_id)
        if not item:
            return None

        # 更新字段（只更新提供的字段）
        update_data = data.model_dump(exclude_none=True)
        for key, value in update_data.items():
            if value is not None:
                if key == "title":
                    item[key] = value.strip()
                else:
                    item[key] = value

        item["updated_at"] = _now()
        items[todo_id] = item
        _save_all(items)

    return item


def delete_todo(todo_id: str) -> bool:
    """删除待办事项"""
    with _lock:
        items = _load_all()
        if todo_id not in items:
            return False
        del items[todo_id]
        _save_all(items)
    return True


# ── FastAPI 路由 ─────────────────────────────────────────

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("")
def api_list():
    """获取所有待办事项"""
    items = list_todos()
    return {"todos": items, "total": len(items)}


@router.get("/{todo_id}")
def api_get(todo_id: str):
    """获取单个待办事项"""
    item = get_todo(todo_id)
    if not item:
        raise HTTPException(status_code=404, detail="待办事项不存在")
    return item


@router.post("", status_code=201)
def api_create(data: TodoCreate):
    """创建新的待办事项"""
    if not data.title or not data.title.strip():
        raise HTTPException(status_code=400, detail="标题不能为空")
    item = create_todo(data)
    return item


@router.put("/{todo_id}")
def api_update(todo_id: str, data: TodoUpdate):
    """更新待办事项"""
    # 检查是否有更新内容
    has_updates = any(v is not None for v in [data.title, data.description, data.completed])
    if not has_updates:
        raise HTTPException(status_code=400, detail="没有提供任何更新字段")

    item = update_todo(todo_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="待办事项不存在")
    return item


@router.delete("/{todo_id}")
def api_delete(todo_id: str):
    """删除待办事项"""
    ok = delete_todo(todo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="待办事项不存在")
    return {"ok": True, "id": todo_id}


@router.delete("")
def api_clear():
    """清空所有待办事项（危险操作）"""
    with _lock:
        _save_all({})
    return {"ok": True, "message": "所有待办事项已清空"}


# ── 单元测试辅助函数 ────────────────────────────────────

def _reset_for_test():
    """测试用：重置数据"""
    with _lock:
        _save_all({})
