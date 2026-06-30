"""
悟道提示词库 - 核心模块

功能：
- 启动时一次性加载所有 prompt 模板到内存（避免每请求读盘）
- 支持按分类、标签、关键词查询
- 支持变量填充生成最终 prompt
- 记录使用情况和成功率（confidence 反馈）
- 实战中学到的新提示词可入库（Hermes L3 模式）
- 简单 JSON 文件存储，不上向量库
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from core.usage import record as usage_record  # v0.3.1 3D 躯体数据绑定

# 数据目录：wudao/data/prompts/
DATA_DIR = Path(__file__).parent.parent / "data" / "prompts"


class PromptLib:
    def __init__(self, data_dir: Path = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, dict] = {}  # id -> prompt dict
        self._load_all()
        self._update_index()

    # ---------- 加载 / 持久化 ----------

    def _load_all(self):
        """扫描所有 .json 文件，把所有提示词 load 到内存"""
        self._cache = {}
        if not self.data_dir.exists():
            return
        for json_file in self.data_dir.rglob("*.json"):
            if json_file.name == "index.json":
                continue
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    # 一个文件里多个 prompt（数组形式）
                    for p in data:
                        pid = p.get("id")
                        if pid:
                            self._cache[pid] = p
                elif isinstance(data, dict) and "id" in data:
                    self._cache[data["id"]] = data
            except Exception as e:
                # 加载失败不致命，跳过
                print(f"[prompts] 加载 {json_file.name} 失败: {e}")

    def _save_one(self, prompt: dict):
        """把单个 prompt 写回它所属的分类文件"""
        cat = prompt.get("category", "misc")
        cat_dir = self.data_dir / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        target = cat_dir / f"{prompt['id']}.json"
        with open(target, "w", encoding="utf-8") as f:
            json.dump(prompt, f, ensure_ascii=False, indent=2)
        self._cache[prompt["id"]] = prompt

    def _update_index(self):
        """更新 index.json 总目录（每次新增/修改自动重写）"""
        index = {
            "version": "0.3.0",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(self._cache),
            "by_category": {},
            "by_id": {pid: p.get("title", "") for pid, p in self._cache.items()},
        }
        for pid, p in self._cache.items():
            cat = p.get("category", "misc")
            index["by_category"].setdefault(cat, []).append(pid)
        with open(self.data_dir / "index.json", "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    # ---------- 查询 ----------

    def list_all(self) -> List[dict]:
        """列出所有提示词（不含 template 详情，只含元数据）"""
        return [self._summary(p) for p in self._cache.values()]

    def list_by_category(self, category: str) -> List[dict]:
        return [self._summary(p) for p in self._cache.values() if p.get("category") == category]

    def get(self, prompt_id: str) -> Optional[dict]:
        return self._cache.get(prompt_id)

    def search(self, keyword: str = "", tags: List[str] = None, category: str = None) -> List[dict]:
        """关键词 / 标签 / 分类搜索"""
        results = list(self._cache.values())
        if category:
            results = [p for p in results if p.get("category") == category]
        if tags:
            tags_lower = [t.lower() for t in tags]
            results = [
                p for p in results
                if any(t.lower() in [pt.lower() for pt in p.get("tags", [])] for t in tags_lower)
            ]
        if keyword:
            kw = keyword.lower()
            results = [
                p for p in results
                if kw in p.get("title", "").lower()
                or kw in p.get("id", "").lower()
                or kw in p.get("template", "").lower()
                or any(kw in t.lower() for t in p.get("tags", []))
            ]
        return [self._summary(p) for p in results]

    def _summary(self, p: dict) -> dict:
        """不含 template 的精简版（列表用）"""
        return {
            "id": p.get("id"),
            "category": p.get("category"),
            "title": p.get("title"),
            "tags": p.get("tags", []),
            "applicable_to": p.get("applicable_to", []),
            "variables": p.get("variables", []),
            "source": p.get("source", "seed"),
            "usage_count": p.get("usage_count", 0),
            "success_count": p.get("success_count", 0),
            "confidence_avg": p.get("confidence_avg", 0.0),
            "version": p.get("version", 1),
        }

    # ---------- 使用 ----------

    def apply(self, prompt_id: str, variables: Dict[str, Any] = None) -> Optional[dict]:
        """
        填充变量，返回最终 prompt + 元数据
        返回 None 表示找不到
        返回 dict 包含：
          - prompt: 填好的最终文本
          - missing_variables: 缺失的变量（让用户知道要补什么）
        """
        p = self.get(prompt_id)
        if not p:
            return None
        template = p.get("template", "")
        vars_needed = p.get("variables", [])
        vars_filled = variables or {}
        missing = [v for v in vars_needed if not vars_filled.get(v)]

        # 简单 {var} 替换
        filled = template
        for k, v in vars_filled.items():
            filled = filled.replace("{" + k + "}", str(v))
        # 未填的变量保留 {var} 占位，让用户看到要补什么

        # 记录一次使用
        p["usage_count"] = p.get("usage_count", 0) + 1
        self._save_one(p)

        # v0.3.1: 同步到 3D 躯体统计（失败不影响主流程）
        try:
            usage_record(
                item_id=p["id"],
                category=p.get("category", "coding"),
                title=p.get("title", p["id"]),
                success=True,
            )
        except Exception as e:
            # 统计失败不致命，提示词还能用
            print(f"[prompts.apply] usage 记录失败: {e}")

        return {
            "id": p["id"],
            "title": p.get("title", ""),
            "category": p.get("category", ""),
            "prompt": filled,
            "missing_variables": missing,
            "source": p.get("source", "seed"),
            "version": p.get("version", 1),
        }

    def record_feedback(self, prompt_id: str, success: bool, confidence: float = 0.0):
        """
        记录用户反馈，更新成功率 + 平均 confidence
        success=True 时 success_count+1
        confidence 0-1，会算到 confidence_avg 里（滑动平均）
        """
        p = self.get(prompt_id)
        if not p:
            return False
        if success:
            p["success_count"] = p.get("success_count", 0) + 1
        old_avg = p.get("confidence_avg", 0.0)
        old_n = p.get("confidence_n", 0)
        new_n = old_n + 1
        new_avg = (old_avg * old_n + max(0.0, min(1.0, confidence))) / new_n
        p["confidence_avg"] = round(new_avg, 3)
        p["confidence_n"] = new_n
        self._save_one(p)
        return True

    # ---------- 维护 ----------

    def add_learned(self, prompt_data: dict):
        """
        实战中学到的新提示词入库
        提示词必须带 source="learned"，记录来源
        配合 meta/learn_skill.json 的规则使用
        """
        prompt_data.setdefault("source", "learned")
        prompt_data.setdefault("created_at", time.strftime("%Y-%m-%d"))
        prompt_data.setdefault("usage_count", 0)
        prompt_data.setdefault("success_count", 0)
        prompt_data.setdefault("confidence_avg", 0.0)
        prompt_data.setdefault("version", 1)
        self._save_one(prompt_data)
        self._update_index()

    def reload(self):
        """从盘重新加载（手工触发）"""
        self._load_all()
        self._update_index()
        return len(self._cache)


# 全局单例
_lib: Optional[PromptLib] = None


def get_lib() -> PromptLib:
    global _lib
    if _lib is None:
        _lib = PromptLib()
    return _lib
