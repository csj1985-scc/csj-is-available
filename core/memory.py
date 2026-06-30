"""
记忆系统 - 三层架构

短期记忆 (Short-term):
  memory.json 存每轮对话，最近 10 轮传入 LLM 上下文

中期记忆 (Medium-term):
  memory_medium.json 存重要事实
  - 从对话中提取，悟道通过 [MEMORIZE] 标记触发
  - 30 天无访问自动遗忘
  - 检索时同时使用：访问次数排序 + 关键词匹配 + 临期刷新
  - 访问 5 次以上晋升长期记忆（同时从中期移除）

长期记忆 (Long-term):
  memory_long.json 存晋升后的永久事实
  - 除非手动删除，否则永久保留
  - 每次对话加入 LLM 上下文
"""
import json
import os
import time
import time
import random
import re
import threading
import tempfile
import shutil
from datetime import datetime, timedelta
from typing import List, Dict, Optional


# ====================================================================
# 工具函数：原子写入 + 文件锁
# ====================================================================

class FileLock:
    """每进程文件锁，防止多 worker 并发写同一文件"""
    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._global = threading.Lock()

    def acquire(self, filepath: str):
        with self._global:
            if filepath not in self._locks:
                self._locks[filepath] = threading.Lock()
            lock = self._locks[filepath]
        lock.acquire()

    def release(self, filepath: str):
        with self._global:
            lock = self._locks.get(filepath)
        if lock:
            lock.release()

_lock = FileLock()

# 文件写入去抖：同一文件 60 秒内不重复写磁盘
_last_save_time: Dict[str, float] = {}
_SAVE_DEBOUNCE = 60.0


def _atomic_save(path: str, data: dict):
    """原子写入：先写临时文件再 rename，防止崩溃导致文件损坏"""
    dirname = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=dirname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _safe_load(path: str, default: dict) -> dict:
    """安全加载 JSON，损坏时保留备份返回默认值"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, EOFError):
        # 文件损坏，尝试保留备份
        backup = path + ".corrupted." + datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(path, backup)
            print(f"[memory] 文件损坏已备份: {backup}")
        except Exception:
            pass
        return default


# ====================================================================
# 短期记忆 (原 Memory，增强 session_id 管理)
# ====================================================================

class Memory:
    def __init__(self, data_dir: str, max_sessions: int = 100, max_messages_per_session: int = 500):
        self.data_dir = data_dir
        self.max_sessions = max_sessions
        self.max_messages_per_session = max_messages_per_session
        os.makedirs(data_dir, exist_ok=True)
        self.file = os.path.join(data_dir, "memory.json")
        if not os.path.exists(self.file):
            self._save({"sessions": {}, "meta": {"created": datetime.now().isoformat()}})

    def _load(self) -> dict:
        data = _safe_load(self.file, {"sessions": {}, "meta": {}})
        # 回收箱自动清理：超过 30 天的永久删除
        trash = data.get("trash")
        if trash:
            now = datetime.now()
            expired = [sid for sid, msgs in trash.items()
                       if msgs and (now - datetime.fromisoformat(msgs[-1]["ts"])).days >= 30]
            for sid in expired:
                del trash[sid]
            if expired:
                data["meta"]["last_update"] = now.isoformat()
        return data

    def _save(self, data: dict):
        _lock.acquire(self.file)
        try:
            _atomic_save(self.file, data)
        finally:
            _lock.release(self.file)

    def get_history(self, session_id: str) -> List[Dict]:
        data = self._load()
        return data["sessions"].get(session_id, [])

    def append(self, session_id: str, user: str, assistant: str):
        data = self._load()
        if session_id not in data["sessions"]:
            data["sessions"][session_id] = []
        data["sessions"][session_id].append({
            "ts": datetime.now().isoformat(),
            "user": user,
            "assistant": assistant,
        })
        # pruning：限制每 session 最大消息数
        if len(data["sessions"][session_id]) > self.max_messages_per_session:
            data["sessions"][session_id] = data["sessions"][session_id][-self.max_messages_per_session:]
        data["meta"]["last_update"] = datetime.now().isoformat()
        self._save(data)

        # 懒惰 pruning：~10% 概率检查 session 总量
        if random.randint(1, 10) == 1:
            self._prune_sessions()

    def _prune_sessions(self):
        """删除最旧的 session 直到不超过 max_sessions"""
        data = self._load()
        if len(data["sessions"]) <= self.max_sessions:
            return
        ordered = sorted(
            data["sessions"].items(),
            key=lambda kv: (
                kv[1][-1]["ts"] if kv[1] else ""
            ),
            reverse=True,
        )
        data["sessions"] = dict(ordered[:self.max_sessions])
        data["meta"]["pruned_at"] = datetime.now().isoformat()
        self._save(data)

    def list_sessions(self) -> List[str]:
        data = self._load()
        return list(data.get("sessions", {}).keys())

    def trash_session(self, session_id: str) -> bool:
        """移到回收箱（软删除）"""
        data = self._load()
        if session_id not in data["sessions"]:
            return False
        if "trash" not in data:
            data["trash"] = {}
        data["trash"][session_id] = data["sessions"].pop(session_id)
        data["meta"]["last_update"] = datetime.now().isoformat()
        self._save(data)
        return True

    def restore_session(self, session_id: str) -> bool:
        """从回收箱恢复"""
        data = self._load()
        if "trash" not in data or session_id not in data["trash"]:
            return False
        data["sessions"][session_id] = data["trash"].pop(session_id)
        data["meta"]["last_update"] = datetime.now().isoformat()
        self._save(data)
        return True

    def list_trash(self) -> List[Dict]:
        """列出回收箱中的会话摘要"""
        data = self._load()
        trash = data.get("trash", {})
        result = []
        for sid, msgs in trash.items():
            title = ""
            if msgs:
                first = msgs[0].get("user", "") or ""
                title = first[:60]
            result.append({
                "id": sid,
                "title": title,
                "count": len(msgs),
                "last_ts": msgs[-1]["ts"] if msgs else "",
            })
        result.sort(key=lambda s: s["last_ts"], reverse=True)
        return result


# ====================================================================
# 中 / 长期记忆
# ====================================================================

MEDIUM_FILE = "memory_medium.json"
LONG_FILE = "memory_long.json"
IDEAS_FILE = "memory_ideas.json"
PROMOTE_THRESHOLD = 5       # 访问 5 次晋升长期
EVICT_DAYS = 30             # 30 天无访问遗忘
MAX_MEMORIES_IN_CONTEXT = 8  # 每次最多带 8 条进上下文
REFRESH_BEFORE_DAYS = 7     # 距过期 7 天时自动刷新（即 23 天未访问就刷新）
REFRESH_COUNT = 3           # 每次检索顺带刷新 N 条临期条目
NEW_BONUS_DAYS = 7          # 7 天内新条目加分
KEYWORD_BONUS = 5           # 关键词匹配加分
MAX_IDEAS_IN_CONTEXT = 3    # 每次最多带 3 条待跟进创意进上下文
DECAY_DAYS = 7              # 每 7 天访问计数衰减一次
DECAY_PER_PERIOD = 1         # 每次衰减几分
VECTOR_BONUS = 5             # 向量匹配加分（与关键词同权重）
VECTOR_TOP_K = 10            # 向量检索取前 N 条


def _now_ts() -> str:
    return datetime.now().isoformat()


def _days_since(ts_str: str) -> float:
    if not ts_str:
        return 999
    try:
        dt = datetime.fromisoformat(ts_str)
        return (datetime.now() - dt).total_seconds() / 86400
    except Exception:
        return 999


def _extract_keywords(text: str) -> List[str]:
    """
    从查询文本中提取关键词，用于记忆匹配。
    对中文使用 2-15 字滑动窗口 n-gram，对英文按空格分词。
    返回去重列表。
    """
    if not text:
        return []
    q = text.lower()
    ngrams = set()
    # 全文作为一个关键词（匹配精确包含）
    ngrams.add(q)
    # 滑动窗口 n-gram（2-15 字）
    for i in range(len(q)):
        for j in range(i + 2, min(i + 15, len(q) + 1)):
            ngrams.add(q[i:j])
    return list(ngrams)


class MediumLongMemory:
    """中期+长期记忆管理器"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._medium_file = os.path.join(data_dir, MEDIUM_FILE)
        self._long_file = os.path.join(data_dir, LONG_FILE)
        self._init_file(self._medium_file, {"entries": [], "meta": {"created": _now_ts()}})
        self._init_file(self._long_file, {"entries": [], "meta": {"created": _now_ts()}})
        self._vector = VectorMemory(data_dir)

    def _init_file(self, path: str, default: dict):
        if not os.path.exists(path):
            _atomic_save(path, default)

    def _load(self, path: str) -> dict:
        return _safe_load(path, {"entries": [], "meta": {}})

    def _save(self, path: str, data: dict, force: bool = False):
        # 去抖：非强制写入且 60 秒内刚存过则跳过
        if not force:
            _last = _last_save_time.get(path, 0)
            if time.time() - _last < _SAVE_DEBOUNCE:
                return
        _lock.acquire(path)
        try:
            _atomic_save(path, data)
            _last_save_time[path] = time.time()
        finally:
            _lock.release(path)

    # ---- 晋升 ----

    def _promote_if_ready(self, entry: dict, medium_data: dict, long_data: dict) -> bool:
        """如果访问次数达标，从中期晋升到长期（从 medium 中移除）"""
        if entry.get("access_count", 0) < PROMOTE_THRESHOLD:
            return False

        # 查重：已在长期就不重复加
        for le in long_data["entries"]:
            if le["content"] == entry["content"]:
                le["access_count"] = entry["access_count"]
                le["last_accessed_at"] = entry.get("last_accessed_at", _now_ts())
                # 从 medium 移除
                medium_data["entries"] = [e for e in medium_data["entries"] if e["id"] != entry["id"]]
                return True

        new_id = f"ml_{int(time.time())}_{len(long_data['entries'])}"
        long_data["entries"].append({
            "id": new_id,
            "content": entry["content"],
            "category": entry.get("category", "general"),
            "created_at": entry.get("created_at", _now_ts()),
            "promoted_at": _now_ts(),
            "access_count": entry["access_count"],
        })
        # 从 medium 移除
        medium_data["entries"] = [e for e in medium_data["entries"] if e["id"] != entry["id"]]
        long_data["meta"]["last_update"] = _now_ts()
        # 同步向量索引：移除旧 ID，添加新 ID
        self._vector.remove(entry.get("id", ""))
        self._vector.add(new_id, entry["content"], {
            "category": entry.get("category", "general"),
            "created_at": entry.get("created_at", ""),
        })
        return True

    # ---- 写入 ----

    def _is_near_duplicate(self, a: str, b: str, threshold: float = 0.6) -> bool:
        """用 Jaccard 相似度判断两条记忆是否近似重复"""
        if not a or not b:
            return False
        if len(a) >= 4 and a in b:
            return True
        if len(b) >= 4 and b in a:
            return True
        set_a, set_b = set(a), set(b)
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        return union > 0 and inter / union >= threshold

    def add_fact(self, content: str, category: str = "general", context: Optional[Dict] = None):
        """添加一条中期记忆（重复内容不重复添加）

        查找顺序：长期 → 中期。如果在长期已存在，直接更新长期访问计数。
        context: 可选的对话上下文字典 {"user": str, "assistant": str}，用于丰富后续检索
        """
        medium = self._load(self._medium_file)
        long_data = self._load(self._long_file)

        # 先查长期：已晋升的内容不再入中期（含近似重复检测）
        for le in long_data["entries"]:
            if le["content"] == content or self._is_near_duplicate(le["content"], content):
                le["access_count"] = le.get("access_count", 0) + 1
                le["last_accessed_at"] = _now_ts()
                if context and not le.get("context"):
                    le["context"] = context
                self._save(self._long_file, long_data, force=True)
                return

        # 再查中期：重复内容不重复加（含近似重复检测）
        for entry in medium["entries"]:
            if entry["content"] == content or self._is_near_duplicate(entry["content"], content):
                entry["last_accessed_at"] = _now_ts()
                entry["access_count"] += 1
                if context and not entry.get("context"):
                    entry["context"] = context
                # 达标则晋升
                if entry["access_count"] >= PROMOTE_THRESHOLD:
                    self._promote_if_ready(entry, medium, long_data)
                    self._save(self._long_file, long_data, force=True)
                self._save(self._medium_file, medium, force=True)
                return

        entry = {
            "id": f"mm_{int(time.time())}_{len(medium['entries'])}",
            "content": content,
            "category": category,
            "created_at": _now_ts(),
            "last_accessed_at": _now_ts(),
            "access_count": 1,
        }
        if context:
            entry["context"] = context
        medium["entries"].append(entry)
        medium["meta"]["last_update"] = _now_ts()
        self._save(self._medium_file, medium, force=True)
        # 同步向量索引
        self._vector.add(entry["id"], content, {"category": category, "created_at": entry["created_at"]})

    def add_long_term(self, content: str, category: str = "general", context: Optional[Dict] = None):
        """直接写入长期记忆，跳过中期 → 晋升流程

        用于需要永久保留的重要记忆（如多 Agent 会议结论）。
        重复内容不会重复添加，现有条目会更新 access_count。
        """
        long_data = self._load(self._long_file)

        # 先检查是否已存在（含近似重复）
        for le in long_data["entries"]:
            if le["content"] == content or self._is_near_duplicate(le["content"], content):
                le["access_count"] = le.get("access_count", 0) + 1
                le["last_accessed_at"] = _now_ts()
                if context and not le.get("context"):
                    le["context"] = context
                self._save(self._long_file, long_data, force=True)
                return

        entry = {
            "id": f"ml_{int(time.time())}_{len(long_data['entries'])}",
            "content": content,
            "category": category,
            "created_at": _now_ts(),
            "last_accessed_at": _now_ts(),
            "access_count": 1,
        }
        if context:
            entry["context"] = context
        long_data["entries"].append(entry)
        long_data["meta"]["last_update"] = _now_ts()
        self._save(self._long_file, long_data, force=True)
        # 同步向量索引
        self._vector.add(entry["id"], content, {"category": category, "created_at": entry["created_at"]})

    # ---- 检索（核心改进） ----

    def get_context(self, query: str = "") -> str:
        """
        构建记忆上下文文本，用于 LLM 系统提示

        检索策略（四层递进）：
        1. 关键词匹配 — query 中的关键词命中记忆内容则加 KEYWORD_BONUS 分
        2. 向量语义匹配 — 使用 ChromaDB + sentence-transformers 做语义检索，加 VECTOR_BONUS 分
        3. 访问次数排序 — 被调用多的记忆排在前面（同时受衰减机制影响）
        4. 新条目加分 — 7 天内新条目加 1 分，防止新记忆被埋没

        附带机制：
        - 每次检索刷新选中条目的 access_count + 1
        - 晋升达标自动从中期移到长期
        - 临期条目（距过期不到 REFRESH_BEFORE_DAYS 天）顺带刷新 last_accessed_at
        - 访问计数衰减：每 7 天无人访问自动减 1 分
        - 向量引擎不加载 ChromaDB 时自动降级为纯关键词检索
        """
        self._evict_expired()
        self._apply_decay()

        medium = self._load(self._medium_file)
        long_data = self._load(self._long_file)

        # 标记来源
        for e in long_data["entries"]:
            e["_src"] = "long"
        for e in medium["entries"]:
            e["_src"] = "medium"

        all_entries = long_data["entries"] + medium["entries"]
        if not all_entries:
            return ""

        keywords = _extract_keywords(query) if query else []
        now = datetime.now()

        # 向量检索：语义相似度匹配（加分不替代关键词）
        vector_scores = self._vector.search(query) if query else {}

        # 评分：访问次数 + 关键词加分 + 新条目加分 + 向量加分
        for e in all_entries:
            score = e.get("access_count", 0)
            if keywords:
                for kw in keywords:
                    if kw in e["content"]:
                        score += KEYWORD_BONUS
                        break
            # 向量相似度加分
            eid = e.get("id", "")
            if eid in vector_scores:
                sim = vector_scores[eid]
                # similarity 0~1, 加分为 VECTOR_BONUS * similarity
                score += VECTOR_BONUS * sim
            created = e.get("created_at", "")
            if created:
                try:
                    cdate = datetime.fromisoformat(created)
                    if (now - cdate).days < NEW_BONUS_DAYS:
                        score += 1
                except Exception:
                    pass
            e["_score"] = score

        all_entries.sort(key=lambda x: x["_score"], reverse=True)
        selected = all_entries[:MAX_MEMORIES_IN_CONTEXT]

        # 更新访问统计 & 晋升
        now_ts = _now_ts()
        for e in selected:
            e["last_accessed_at"] = now_ts
            e["access_count"] = e.get("access_count", 0) + 1
            if e.get("_src") == "medium" and e["access_count"] >= PROMOTE_THRESHOLD:
                self._promote_if_ready(e, medium, long_data)

        # 刷新临期条目（距 30 天过期不到 REFRESH_BEFORE_DAYS 天的）
        expiry_trigger = EVICT_DAYS - REFRESH_BEFORE_DAYS  # 30-7=23 天
        near_expiry = [
            e for e in medium["entries"]
            if e.get("last_accessed_at") and _days_since(e["last_accessed_at"]) > expiry_trigger
        ]
        if near_expiry:
            near_expiry.sort(key=lambda x: _days_since(x["last_accessed_at"]), reverse=True)
            for e in near_expiry[:REFRESH_COUNT]:
                e["last_accessed_at"] = now_ts

        medium["meta"]["last_update"] = now_ts
        long_data["meta"]["last_update"] = now_ts
        self._save(self._medium_file, medium)
        self._save(self._long_file, long_data)

        lines = ["\n=== 我记得这些关于你和项目的事情 ==="]
        for e in selected:
            ctx = e.get("context")
            if ctx and ctx.get("user"):
                # 有对话上下文时附带来源，帮助 LLM 理解来龙去脉
                user_preview = ctx["user"][:60]
                asst_preview = ctx.get("assistant", "")[:40]
                lines.append(f"- {e['content']} (来自: \"{user_preview}\" → \"{asst_preview}\")")
            else:
                lines.append(f"- {e['content']}")
        return "\n".join(lines)

    def _evict_expired(self):
        """删除过期中期记忆（30 天无访问），同时从向量索引清理"""
        medium = self._load(self._medium_file)
        before = len(medium["entries"])
        expired = [e for e in medium["entries"]
                   if _days_since(e.get("last_accessed_at", "")) >= EVICT_DAYS]
        medium["entries"] = [
            e for e in medium["entries"]
            if _days_since(e.get("last_accessed_at", "")) < EVICT_DAYS
        ]
        if len(medium["entries"]) < before:
            medium["meta"]["last_evict"] = _now_ts()
            self._save(self._medium_file, medium)
            # 从向量索引清理过期条目
            for e in expired:
                self._vector.remove(e.get("id", ""))

    def _apply_decay(self):
        """对所有中期记忆的 access_count 做时间衰减

        每 DECAY_DAYS 天未访问，access_count 减 DECAY_PER_PERIOD（最低 0）。
        防止高访问记忆永远占据检索前列，让系统更关注近期活跃的记忆。
        长期记忆也做同等衰减（但不删除）。
        """
        medium = self._load(self._medium_file)
        changed = False
        for e in medium["entries"]:
            days = _days_since(e.get("last_accessed_at", ""))
            if days > DECAY_DAYS:
                periods = int(days / DECAY_DAYS)
                if periods > 0 and e["access_count"] > 0:
                    e["access_count"] = max(0, e["access_count"] - periods * DECAY_PER_PERIOD)
                    changed = True

        long_data = self._load(self._long_file)
        for e in long_data["entries"]:
            days = _days_since(e.get("last_accessed_at", ""))
            if days > DECAY_DAYS:
                periods = int(days / DECAY_DAYS)
                if periods > 0 and e["access_count"] > 0:
                    e["access_count"] = max(0, e["access_count"] - periods * DECAY_PER_PERIOD)
                    changed = True

        if changed:
            self._save(self._medium_file, medium)
            self._save(self._long_file, long_data)

    def rebuild_vector_index(self):
        """从所有中期和长期记忆重建向量索引"""
        medium = self._load(self._medium_file)
        long_data = self._load(self._long_file)
        all_entries = medium.get("entries", []) + long_data.get("entries", [])
        self._vector.rebuild(all_entries)

    # ---- 统计 ----

    # ---- 创意 / 想法跟进 ----

    def add_idea(self, content: str, follow_up_days: int = 3, priority: str = "medium"):
        """记录一个想法并设定跟进时间

        follow_up_days: 几天后提醒跟进（针对"随口提的创意，几天后主动问"）
        priority: 紧急程度 "high" / "medium" / "low"
        """
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        ideas = self._load(os.path.join(self.data_dir, IDEAS_FILE))
        if "entries" not in ideas:
            ideas["entries"] = []

        # 查重
        for idea in ideas["entries"]:
            if idea["content"] == content and not idea.get("done"):
                idea["last_mentioned_at"] = _now_ts()
                ideas["meta"]["last_update"] = _now_ts()
                self._save(os.path.join(self.data_dir, IDEAS_FILE), ideas)
                return

        follow_up = _now_ts() if follow_up_days == 0 else (
            datetime.now() + timedelta(days=follow_up_days)
        ).isoformat()

        ideas["entries"].append({
            "id": f"idea_{int(time.time())}_{len(ideas['entries'])}",
            "content": content,
            "priority": priority,
            "created_at": _now_ts(),
            "follow_up_at": follow_up,
            "done": False,
            "last_mentioned_at": _now_ts(),
        })
        ideas["meta"]["last_update"] = _now_ts()
        self._save(os.path.join(self.data_dir, IDEAS_FILE), ideas)

    def get_due_ideas(self) -> List[Dict]:
        """返回待跟进且到期的想法（按优先级排序：high > medium > low）"""
        ideas = self._load(os.path.join(self.data_dir, IDEAS_FILE))
        if not ideas.get("entries"):
            return []
        now = datetime.now()
        prio_order = {"high": 0, "medium": 1, "low": 2}
        due = []
        for idea in ideas["entries"]:
            if idea.get("done"):
                continue
            try:
                fua = datetime.fromisoformat(idea["follow_up_at"])
                if now >= fua:
                    due.append(idea)
            except Exception:
                continue
        due.sort(key=lambda x: (prio_order.get(x.get("priority", "medium"), 1), x.get("follow_up_at", "")))
        return due[:MAX_IDEAS_IN_CONTEXT]

    def get_ideas_context(self, query: str = "") -> str:
        """构建待跟进创意的上下文文本（用于 LLM 提示）"""
        ideas = self._load(os.path.join(self.data_dir, IDEAS_FILE))
        if not ideas.get("entries"):
            return ""

        now = datetime.now()
        pending = []
        for idea in ideas["entries"]:
            if idea.get("done"):
                continue
            try:
                fua = datetime.fromisoformat(idea["follow_up_at"])
                overdue = now >= fua
                follow_up_str = fua.strftime("%m/%d")
                pending.append((idea, overdue, follow_up_str))
            except Exception:
                pending.append((idea, False, ""))

        if not pending:
            return ""

        prio_label = {"high": " [高]", "medium": "", "low": " [低]"}
        lines = ["\n=== 待跟进想法 ==="]
        # 按优先级排序
        prio_order = {"high": 0, "medium": 1, "low": 2}
        pending.sort(key=lambda x: (prio_order.get(x[0].get("priority", "medium"), 1), x[2]))
        for idea, overdue, follow_up_str in pending[:MAX_IDEAS_IN_CONTEXT]:
            tag = " [到期可问]" if overdue else f" (跟进日: {follow_up_str})"
            prio = prio_label.get(idea.get("priority", "medium"), "")
            lines.append(f"- {idea['content']}{prio}{tag}")
        return "\n".join(lines)

    def mark_idea_done(self, idea_id: str) -> bool:
        """标记想法为已完成"""
        ideas_file = os.path.join(self.data_dir, IDEAS_FILE)
        ideas = self._load(ideas_file)
        for idea in ideas.get("entries", []):
            if idea["id"] == idea_id:
                idea["done"] = True
                idea["done_at"] = _now_ts()
                ideas["meta"]["last_update"] = _now_ts()
                self._save(ideas_file, ideas)
                return True
        return False

    def list_pending_ideas(self) -> List[Dict]:
        """返回所有待跟进想法（供管理面板用）"""
        ideas = self._load(os.path.join(self.data_dir, IDEAS_FILE))
        return [i for i in ideas.get("entries", []) if not i.get("done")]

    def stats(self) -> dict:
        medium = self._load(self._medium_file)
        long_data = self._load(self._long_file)
        ideas = self._load(os.path.join(self.data_dir, IDEAS_FILE))
        return {
            "medium_count": len(medium["entries"]),
            "long_count": len(long_data["entries"]),
            "pending_ideas": len([i for i in ideas.get("entries", []) if not i.get("done")]),
            "medium_expired_days": EVICT_DAYS,
            "promote_threshold": PROMOTE_THRESHOLD,
        }

    def get_all_longterm(self) -> List[Dict]:
        """返回所有长期记忆（供管理面板查看）"""
        long_data = self._load(self._long_file)
        return long_data.get("entries", [])

    def get_all_medium(self) -> List[Dict]:
        """返回所有中期记忆（供管理面板查看）"""
        medium = self._load(self._medium_file)
        return medium.get("entries", [])


# ====================================================================
# 向量记忆 (ChromaDB + sentence-transformers)
# ====================================================================

class VectorMemory:
    """向量记忆检索 — ChromaDB + sentence-transformers

    作为 MediumLongMemory 的增强检索层，不替代 JSON 存储。
    每次 add_fact 时同步保存 embedding，get_context 时增加向量相似度评分。
    如果 ChromaDB 或 sentence-transformers 不可用，自动降级为纯关键词检索。
    """

    _INIT_TIMEOUT = 10  # 秒，超时则降级

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._chroma_dir = os.path.join(data_dir, "chroma_db")
        self._collection = None
        self._encoder = None
        self._ready = False
        self._init_attempted = False

    def _lazy_init(self):
        if self._ready:
            return True
        # 用线程+超时避免 HF 下载卡住主线程
        result = [None]

        def _do_init():
            try:
                import chromadb
                from sentence_transformers import SentenceTransformer
                os.makedirs(self._chroma_dir, exist_ok=True)
                self._client = chromadb.PersistentClient(path=self._chroma_dir)
                self._collection = self._client.get_or_create_collection(
                    name="wudao_memory",
                    metadata={"hnsw:space": "cosine"},
                )
                # 设置短路超时，防止 HF 下载重试卡住
                import requests as _req
                _req.adapters.DEFAULT_RETRIES = 0
                import os as _os
                _os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "5"
                self._encoder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
                result[0] = True
            except Exception as e:
                result[0] = False
                print(f"[vector] 初始化异常: {e}")

        t = threading.Thread(target=_do_init, daemon=True)
        t.start()
        t.join(timeout=2.0)

        if result[0] is True:
            self._ready = True
            print("[vector] 向量引擎就绪")
            return True
        else:
            # 失败或超时——标记不再重试，每次重试白等 10 秒
            self._ready = True  # 标记就绪让下次不再进初始化
            self._search_available = False  # 但搜索不可用
            if result[0] is False:
                print("[vector] 向量引擎初始化失败（永久降级为纯关键词检索）")
            else:
                print("[vector] 向量引擎初始化超时（永久降级为纯关键词检索）")
            return False

    def add(self, entry_id: str, content: str, metadata: dict = None):
        """添加或更新向量索引中的条目"""
        if not self._lazy_init():
            return
        try:
            embedding = self._encoder.encode(content).tolist()
            self._collection.upsert(
                ids=[entry_id],
                embeddings=[embedding],
                metadatas=[metadata or {}],
                documents=[content],
            )
        except Exception as e:
            print(f"[vector] add 失败: {e}")

    def remove(self, entry_id: str):
        """从向量索引中移除条目"""
        if not self._lazy_init():
            return
        try:
            self._collection.delete(ids=[entry_id])
        except Exception:
            pass

    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> Dict[str, float]:
        """向量相似度搜索，返回 {entry_id: similarity_score, ...}。初始化失败则返回空。"""
        if not self._search_available or not query:
            return {}
        if not self._lazy_init():
            return {}
        try:
            query_emb = self._encoder.encode(query).tolist()
            results = self._collection.query(
                query_embeddings=[query_emb],
                n_results=top_k,
                include=["distances"],
            )
            scores = {}
            if results["ids"]:
                for i in range(len(results["ids"][0])):
                    eid = results["ids"][0][i]
                    dist = results["distances"][0][i] if results.get("distances") else 1.0
                    scores[eid] = 1.0 - dist
            return scores
        except Exception as e:
            print(f"[vector] search 失败: {e}")
            return {}

    def rebuild(self, entries: List[Dict]):
        """从所有记忆条目重建向量索引"""
        if not self._lazy_init():
            return
        try:
            existing = self._collection.get()
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])

            batch_ids, batch_embs, batch_metas, batch_docs = [], [], [], []
            for entry in entries:
                eid = entry.get("id", "")
                content = entry.get("content", "")
                if not eid or not content:
                    continue
                emb = self._encoder.encode(content).tolist()
                batch_ids.append(eid)
                batch_embs.append(emb)
                batch_metas.append({
                    "category": entry.get("category", "general"),
                    "created_at": entry.get("created_at", ""),
                })
                batch_docs.append(content)

            if batch_ids:
                self._collection.add(
                    ids=batch_ids,
                    embeddings=batch_embs,
                    metadatas=batch_metas,
                    documents=batch_docs,
                )
            print(f"[vector] 索引重建完成，共 {len(batch_ids)} 条")
        except Exception as e:
            print(f"[vector] rebuild 失败: {e}")
