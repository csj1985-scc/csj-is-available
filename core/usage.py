"""
调用统计 - 3D 神经躯体的"心脏"

5 触手分类：
  - thinking    思维  ← coding/project 类 prompt + 每次 LLM 响应
  - memory      记忆  ← memory.json 读写
  - perception  感知  ← /chat 用户输入
  - learning    学习  ← learned.json 沉淀 + creative/meta 类 prompt + 反馈
  - self        自我  ← USER 加载 + 安全门触发

每个 item 记录：
  - calls_total:   累计调用
  - calls_7d:      7 天滑动计数（用于触手节点亮度）
  - success_count: 成功次数
  - last_used:     最后调用时间
  - heat:          min(calls_7d/10, 1.0) 归一化，给 3D 节点当亮度

启动时一次性加载，record() 时增量写回。简单 JSON 文件，不上数据库。
"""
import json
import time
from pathlib import Path
from threading import Lock

DATA_DIR = Path(__file__).parent.parent / "data"
USAGE_FILE = DATA_DIR / "usage_stats.json"
_lock = Lock()

# 5 触手定义（key 决定 3D 渲染顺序、颜色权重、节点密度）
# 注意：每条都把 key 自己加进 categories，避免反向映射漏掉
TENTACLES = [
    {"key": "thinking",   "name": "思维", "categories": ["thinking", "coding", "project"]},
    {"key": "memory",     "name": "记忆", "categories": ["memory"]},
    {"key": "perception", "name": "感知", "categories": ["perception"]},
    {"key": "learning",   "name": "学习", "categories": ["learning", "creative", "meta"]},
    {"key": "self",       "name": "自我", "categories": ["self"]},
]

# 反向映射：category -> tentacle key（单查快）
CATEGORY_TO_TENTACLE = {}
for _t in TENTACLES:
    for _c in _t["categories"]:
        CATEGORY_TO_TENTACLE[_c] = _t["key"]


# ---------- 文件 I/O ----------

def _ensure_file():
    """首次启动建空数据文件"""
    if not USAGE_FILE.exists():
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps({
            "version": "0.3.1",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "totals": {"all": 0},
            "items": {},
        }, ensure_ascii=False, indent=2), encoding="utf-8")


def _load() -> dict:
    _ensure_file()
    with open(USAGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data.setdefault("version", "0.3.1")
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 7d 衰减 ----------

def _maybe_decay_7d(item: dict):
    """超过 7 天未用，把 7d 计数清零（防止永久累加）"""
    last = item.get("last_used", "")
    if not last:
        return
    try:
        last_ts = time.mktime(time.strptime(last[:19], "%Y-%m-%dT%H:%M:%S"))
        if time.time() - last_ts > 7 * 86400:
            item["calls_7d"] = 0
    except Exception:
        pass


# ---------- 核心 API ----------

def record(item_id: str, category: str, title: str = "", success: bool = True) -> dict:
    """
    记录一次调用
    - item_id:  唯一标识（prompt id / "chat_input" / "memory.default" 等）
    - category: 分类（自动映射到触手）
    - title:    显示名（给前端 hover 用）
    - success:  是否成功
    """
    if not item_id:
        return {}
    with _lock:
        data = _load()
        items = data.setdefault("items", {})
        item = items.setdefault(item_id, {
            "category": category,
            "title": title or item_id,
            "calls_total": 0,
            "calls_7d": 0,
            "success_count": 0,
            "last_used": None,
            "first_used": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _maybe_decay_7d(item)
        item["calls_total"] = item.get("calls_total", 0) + 1
        item["calls_7d"] = item.get("calls_7d", 0) + 1
        if success:
            item["success_count"] = item.get("success_count", 0) + 1
        item["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        item["title"] = title or item.get("title", item_id)
        item["category"] = category or item.get("category", "memory")
        data["totals"]["all"] = data["totals"].get("all", 0) + 1
        _save(data)
        return item


def get_brain_state() -> dict:
    """
    返回 5 触手状态，给前端 3D 渲染用
    每触手含 items 列表（含 heat 0-1，给节点亮度）
    """
    with _lock:
        data = _load()
    items = data.get("items", {})

    tentacles = []
    for t in TENTACLES:
        items_in = []
        for item_id, item in items.items():
            cat = item.get("category", "")
            tentacle_key = CATEGORY_TO_TENTACLE.get(cat)
            if tentacle_key != t["key"]:
                continue
            total = item.get("calls_total", 0)
            success = item.get("success_count", 0)
            calls_7d = item.get("calls_7d", 0)
            # heat: 7d 归一化（10 次=满）
            heat = min(calls_7d / 10.0, 1.0)
            # 有过调用就至少 0.1 亮度，避免全是黑点
            if total > 0 and heat < 0.1:
                heat = 0.1
            items_in.append({
                "id": item_id,
                "title": item.get("title", ""),
                "calls_7d": calls_7d,
                "calls_total": total,
                "heat": round(heat, 3),
                "success_rate": round(success / total, 3) if total > 0 else 0.0,
                "last_used": item.get("last_used"),
            })
        # 按热度降序
        items_in.sort(key=lambda x: -x["heat"])
        tentacles.append({
            "key": t["key"],
            "name": t["name"],
            "total_calls": sum(i["calls_total"] for i in items_in),
            "hot_count": sum(1 for i in items_in if i["heat"] > 0.5),
            "items": items_in,
        })

    return {
        "version": "0.3.1",
        "tentacles": tentacles,
        "totals": data.get("totals", {}),
        "updated_at": data.get("updated_at"),
    }


def reset_for_test():
    """测试用：清空数据"""
    with _lock:
        _save({
            "version": "0.3.1",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "totals": {"all": 0},
            "items": {},
        })


# 启动时确保文件存在
_ensure_file()
