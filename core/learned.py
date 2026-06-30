"""
今日所学 - 抄Hermes L3+Skill 0.7门槛

设计：
- 每轮对话增量记录到当日文件
- 未来加confidence自评：≥0.7才标记为"提炼"
- 未来加增量摘要：只更新前次摘要，不重生成
"""
import json
import os
from datetime import datetime
from typing import List, Dict


class LearnedLog:
    def __init__(self, data_dir: str):
        self.learned_dir = os.path.join(data_dir, "learned")
        os.makedirs(self.learned_dir, exist_ok=True)

    def _file_for(self, date: str) -> str:
        return os.path.join(self.learned_dir, f"{date}.json")

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _load(self, path: str) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, path: str, data: list):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add(self, session_id: str, user: str, assistant: str, confidence: float = None):
        """
        增量记录每轮对话
        v0.2+ 加 confidence 自评门槛（≥0.7才标 "提炼"）
        """
        path = self._file_for(self._today())
        data = self._load(path)
        entry = {
            "ts": datetime.now().isoformat(),
            "session_id": session_id,
            "user": user,
            "assistant": assistant,
        }
        if confidence is not None:
            entry["confidence"] = confidence
            entry["distilled"] = confidence >= 0.7
        data.append(entry)
        self._save(path, data)

    def today(self) -> List[Dict]:
        return self._load(self._file_for(self._today()))

    def get(self, date: str) -> List[Dict]:
        return self._load(self._file_for(date))

    def today_summary(self) -> Dict:
        """今日所学统计"""
        data = self.today()
        return {
            "date": self._today(),
            "count": len(data),
            "items": data,
        }
