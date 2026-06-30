"""
检索器 — 三路并行：Web 搜索 + PromptLib 知识库 + Memory 相关对话
"""
import re
import time
import requests
from typing import Dict, List, Optional
from urllib.parse import quote

from core.config import WUDAO_DATA
from core.memory import Memory
from core.prompts import get_lib as prompts_lib
from core.usage import record as usage_record


class Retriever:
    def __init__(self):
        self.memory = Memory(data_dir=WUDAO_DATA)

    def retrieve(self, query: str, session_id: str = "default",
                 intent: str = "chat") -> Dict:
        """三路检索，返回合并后的 sources 上下文"""
        web_results = self._web_search(query) if intent == "search" else []
        prompt_results = self._promptlib_search(query)
        memory_results = self._memory_search(query, session_id)

        self._record_retrieval(query, intent, len(web_results),
                                len(prompt_results), len(memory_results))
        return {
            "web": web_results,
            "prompts": prompt_results,
            "memory": memory_results,
            "sources": self._format_context(
                web_results, prompt_results, memory_results
            ),
        }

    def _record_retrieval(self, query: str, intent: str, web_count: int,
                           prompt_count: int, mem_count: int):
        """记录检索行为到触手统计"""
        usage_record(
            item_id=f"retrieve_{int(time.time())}",
            category="thinking",
            title=f"检索: {query[:25]}",
            success=web_count + prompt_count + mem_count > 0,
        )

    def _web_search(self, query: str, max_results: int = 3) -> List[Dict]:
        """DuckDuckGo 搜索（无需 API Key），失败则返回空"""
        results = []
        try:
            # Instant Answer API
            resp = requests.get(
                f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1",
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                abstract = data.get("AbstractText", "")
                if abstract:
                    results.append({
                        "title": data.get("Heading", query),
                        "snippet": abstract,
                        "url": data.get("AbstractURL", ""),
                    })
                for topic in data.get("RelatedTopics", [])[:max_results]:
                    if "Text" in topic:
                        results.append({
                            "title": topic.get("Text", query)[:60],
                            "snippet": topic.get("Text", ""),
                            "url": topic.get("FirstURL", ""),
                        })
        except Exception as e:
            print(f"[retriever] 搜索失败: {e}")

        return results[:max_results]

    def _promptlib_search(self, query: str) -> List[Dict]:
        """PromptLib 标签/关键词检索"""
        keywords = self._extract_keywords(query)
        results = prompts_lib().search(keyword=query, tags=keywords)
        return [{
            "id": r["id"],
            "title": r["title"],
            "category": r.get("category", ""),
            "tags": r.get("tags", []),
            "usage_count": r.get("usage_count", 0),
            "confidence_avg": r.get("confidence_avg", 0.0),
        } for r in results[:5]]

    def _memory_search(self, query: str, session_id: str) -> List[Dict]:
        """关键词粗筛最近对话"""
        history = self.memory.get_history(session_id)
        if not history:
            return []

        keywords = set(self._extract_keywords(query))
        if not keywords:
            return history[-3:]

        scored = []
        for h in history[-20:]:
            text = (h.get("user", "") + " " + h.get("assistant", "")).lower()
            score = sum(1 for kw in keywords if kw.lower() in text)
            if score > 0:
                scored.append((score, h))

        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:3]]

    def _extract_keywords(self, text: str) -> List[str]:
        """提取中英文关键词"""
        chinese = re.findall(r'[一-鿿]{2,}', text)
        english = re.findall(r'[a-zA-Z]{3,}', text)
        return [w.lower() for w in chinese + english]

    def _format_context(self, web: List[Dict], prompts: List[Dict],
                        memory: List[Dict]) -> str:
        parts = []
        if web:
            lines = "\n".join(
                f"- {r['title']}: {r['snippet'][:200]}" for r in web
            )
            parts.append(f"## 网络搜索结果\n{lines}")
        if prompts:
            lines = "\n".join(
                f"- [{r['category']}] {r['title']}" for r in prompts
            )
            parts.append(f"## 知识库匹配\n{lines}")
        if memory:
            lines = "\n".join(
                f"- 用户: {m['user'][:80]} → 悟道: {m['assistant'][:80]}"
                for m in memory
            )
            parts.append(f"## 相关历史\n{lines}")
        return "\n\n".join(parts)
