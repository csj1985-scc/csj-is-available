"""
后台学习器 — 评估问答价值 → 提炼结构化知识 → PromptLib 入库
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List

from core.config import WUDAO_DATA
from core.llm import chat as llm_chat
from core.prompts import get_lib as prompts_lib
from core.usage import record as usage_record
from core.memory import Memory
from core.config import WUDAO_DATA as _DATA


class Learner:
    def __init__(self):
        self.data_dir = Path(WUDAO_DATA)
        self.distill_file = self.data_dir / "learned_distilled.json"

    @staticmethod
    def _extract_json(raw: str) -> dict | None:
        """从 LLM 回复中提取 JSON，兼容 markdown 代码块包装"""
        if not raw:
            return None
        # 尝试直接解析
        text = raw.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试提取 ```json ... ``` 块
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
        # 尝试提取 {...} 最外层
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None

    async def evaluate_and_learn(self, query: str, answer: str,
                                  session_id: str = "default") -> bool:
        """评估一次问答是否值得学，有价值则提炼入库"""
        if not answer or len(answer) < 10:
            return False

        prompt = (
            f"判断这段问答是否包含值得记住的知识、技巧、偏好或事实。\n"
            f"只返回 JSON：{{\"valuable\": true/false, \"title\": \"简短标题\", "
            f"\"category\": \"tech|life|concept|preference\", "
            f"\"tags\": [\"标签\"], \"reason\": \"判断理由\"}}\n"
            f"用户问: {query}\n悟道答: {answer}"
        )

        result = llm_chat(prompt, history=[]) or ""
        eval_data = self._extract_json(result)
        if eval_data is None:
            return False

        if not eval_data.get("valuable"):
            return False

        entry = {
            "id": f"learned_{int(time.time())}",
            "title": eval_data.get("title", query[:30]),
            "category": eval_data.get("category", "concept"),
            "tags": eval_data.get("tags", []),
            "template": f"关于{eval_data.get('title', '')}：{answer[:300]}",
            "source": "learner",
            "applicable_to": [],
            "variables": [],
            "created_at": datetime.now().isoformat(),
            "usage_count": 0,
            "success_count": 0,
            "confidence_avg": 0.0,
            "version": 1,
        }
        prompts_lib().add_learned(entry)

        # L2：如果属于偏好或事实类，同时存到关键信息
        cat = eval_data.get("category", "concept")
        if cat in ("preference", "fact"):
            _mem = Memory(data_dir=str(_DATA))
            _mem.save_key_info(session_id, {
                "type": cat,
                "content": f"{entry['title']}: {answer[:200]}",
                "source_ts": datetime.now().isoformat(),
                "confidence": 0.8,
            })

        usage_record(
            item_id=entry["id"],
            category="learning",
            title=entry["title"],
            success=True,
        )
        print(f"[learner] 新学: {entry['title']}")
        return True

    async def daily_distill(self) -> List[dict]:
        """每日批量提炼 — 扫当天对话，聚类去重后入库"""
        from core.learned import LearnedLog
        learned = LearnedLog(data_dir=str(self.data_dir))
        today = learned.today()
        if not today:
            return []

        valuable = [
            e for e in today
            if e.get("distilled") or e.get("confidence", 0) >= 0.7
        ]
        if not valuable:
            return []

        topics = {}
        for entry in valuable:
            key = entry.get("user", "")[:20]
            topics.setdefault(key, {"count": 0, "entries": []})
            topics[key]["count"] += 1
            topics[key]["entries"].append(entry)

        results = []
        for key, topic in topics.items():
            if topic["count"] < 2:
                continue
            latest = topic["entries"][-1]
            entry = {
                "id": f"distill_{int(time.time())}_{len(results)}",
                "title": latest.get("user", "")[:30],
                "category": "preference",
                "tags": ["distilled", "daily"],
                "template": f"{latest.get('user', '')} → {latest.get('assistant', '')[:200]}",
                "source": "daily_distill",
                "applicable_to": [],
                "variables": [],
                "created_at": datetime.now().isoformat(),
                "appearances": topic["count"],
                "usage_count": 0,
                "success_count": 0,
                "confidence_avg": 0.0,
                "version": 1,
            }
            prompts_lib().add_learned(entry)
            usage_record(
                item_id=entry["id"],
                category="learning",
                title=entry["title"],
                success=True,
            )
            results.append(entry)

        existing = []
        if self.distill_file.exists():
            existing = json.loads(self.distill_file.read_text(encoding="utf-8"))
        existing.extend(results)
        self.distill_file.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[learner] 今日蒸馏: {len(results)} 条")
        return results
