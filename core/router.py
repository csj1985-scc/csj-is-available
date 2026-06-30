"""
意图路由器 — 规则快速通道 + LLM 兜底
"""
import re
from typing import Dict, Optional

from core.llm import chat as llm_chat


class IntentRouter:
    # 快速通道：操作类关键词
    OPERATION_PATTERNS = [
        (r"^(记一下|记住|以后记住|记好|别忘了)", "learn_record"),
        (r"^(搜索|查一下|查查|查找)", "search"),
    ]

    SEARCH_KEYWORDS = ["最新", "今天", "新闻", "天气", "搜索", "查一下", "查找",
                       "什么情况", "怎么回事", "怎么样"]

    GREETINGS = {"你好", "嗨", "在吗", "hi", "hello", "hey", "嘿", "喂"}

    def route(self, text: str) -> Dict:
        """返回 {intent, confidence, params}"""
        text = text.strip()
        result = self._fast_route(text)
        if result:
            return result
        return self._llm_route(text)

    def _fast_route(self, text: str) -> Optional[Dict]:
        if text.lower() in self.GREETINGS or len(text) <= 5:
            return {"intent": "chat", "confidence": 0.9, "params": {}}
        if text in ("暂停", "继续", "总结", "再见"):
            return {"intent": "operation", "confidence": 1.0, "params": {}}

        for pattern, intent in self.OPERATION_PATTERNS:
            if re.search(pattern, text):
                return {"intent": intent, "confidence": 1.0, "params": {}}

        if any(kw in text for kw in self.SEARCH_KEYWORDS):
            return {"intent": "search", "confidence": 0.8, "params": {}}

        return None

    def _llm_route(self, text: str) -> Dict:
        prompt = (
            f"判断下面用户输入的意图，只返回一个词：\n"
            f"search=需要实时信息/查知识\n"
            f"chat=闲聊/日常/情感\n"
            f"learn_record=用户明确让你记住什么\n\n"
            f"用户: {text}\n\n意图:"
        )
        result = llm_chat(prompt, history=[]) or ""
        intent = result.strip().lower()
        if intent in ("search", "learn_record"):
            return {"intent": intent, "confidence": 0.7, "params": {}}
        return {"intent": "chat", "confidence": 0.5, "params": {}}
