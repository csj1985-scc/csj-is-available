"""
自检系统 — 悟道各模块健康检查 + 透明度报告

每个模块实现一个 check() 方法，返回：
{healthy: bool, metrics: dict, issues: [str], last_check: timestamp}
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class ModuleHealth:
    """模块健康状态基类"""

    def __init__(self, name: str):
        self.name = name
        self.history = []

    def report(self, healthy: bool, metrics: dict, issues: List[str]):
        entry = {
            "ts": datetime.now().isoformat(),
            "healthy": healthy,
            "metrics": metrics,
            "issues": issues,
        }
        self.history.append(entry)
        # 只保留最近 100 条
        if len(self.history) > 100:
            self.history = self.history[-100:]
        return entry

    def latest(self) -> Optional[dict]:
        return self.history[-1] if self.history else None

    def summary(self) -> dict:
        """返回模块的透明摘要"""
        latest = self.latest()
        recent = [h for h in self.history[-10:] if not h.get("healthy")]
        return {
            "name": self.name,
            "healthy": latest.get("healthy") if latest else None,
            "last_check": latest.get("ts") if latest else None,
            "recent_issues": [r.get("issues") for r in recent],
            "total_checks": len(self.history),
        }


# ==================== 各模块自检器 ====================

class MemoryHealth(ModuleHealth):
    """记忆模块自检"""

    def __init__(self):
        super().__init__("memory")
        self.data_dir = None

    def set_data_dir(self, path):
        self.data_dir = Path(path)

    def check(self) -> dict:
        issues = []
        metrics = {}
        warnings = []

        if not self.data_dir:
            return self.report(False, {"status": "data_dir_not_set"}, ["DATA_DIR_NOT_SET"])

        # 文件存在性
        conv_file = self.data_dir / "conversations.json"
        keyinfo_file = self.data_dir / "key_info.json"

        conv_exists = conv_file.exists()
        keyinfo_exists = keyinfo_file.exists()

        if not conv_exists:
            issues.append("CONV_FILE_MISSING")
        if not keyinfo_exists:
            issues.append("KEYINFO_FILE_MISSING")

        # 文件大小
        if conv_exists:
            conv_size = conv_file.stat().st_size
            metrics["conv_file_size_bytes"] = conv_size
            if conv_size > 10_000_000:
                warnings.append(f"CONV_FILE_TOO_LARGE ({conv_size} bytes)")
            # 读取条目数
            try:
                with open(conv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions = data.get("sessions", {})
                metrics["session_count"] = len(sessions)
                total_entries = sum(len(v) for v in sessions.values())
                metrics["total_conversation_entries"] = total_entries
                if total_entries > 5000:
                    warnings.append(f"HIGH_ENTRY_COUNT ({total_entries} entries)")
            except Exception as e:
                issues.append(f"CONV_FILE_CORRUPT: {e}")

        if keyinfo_exists:
            try:
                with open(keyinfo_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                profiles = data.get("profiles", {})
                metrics["profile_count"] = len(profiles)
                total_infos = sum(
                    len(p.get("infos", [])) for p in profiles.values()
                )
                metrics["total_key_infos"] = total_infos
            except Exception as e:
                issues.append(f"KEYINFO_FILE_CORRUPT: {e}")

        metrics["warnings"] = warnings
        healthy = len(issues) == 0
        return self.report(healthy, metrics, issues)


class RetrievalHealth(ModuleHealth):
    """检索模块自检"""

    def __init__(self):
        super().__init__("retrieval")

    def check(self, recent_queries: Optional[List[dict]] = None) -> dict:
        issues = []
        metrics = {}

        if recent_queries:
            total = len(recent_queries)
            with_sources = sum(
                1 for q in recent_queries if q.get("sources_count", 0) > 0
            )
            empty_queries = sum(
                1 for q in recent_queries if q.get("sources_count", 0) == 0
            )
            metrics["total_queries"] = total
            metrics["queries_with_sources"] = with_sources
            metrics["queries_empty_result"] = empty_queries
            if total > 0 and empty_queries / total > 0.8:
                issues.append("HIGH_EMPTY_RATE (>80% queries returned no sources)")

        healthy = len(issues) == 0
        return self.report(healthy, metrics, issues)


class SafetyHealth(ModuleHealth):
    """安全审查模块自检"""

    def __init__(self):
        super().__init__("safety")

    def check(self, recent_blocks: Optional[List[dict]] = None,
              recent_passes: int = 0) -> dict:
        issues = []
        metrics = {}

        if recent_blocks is not None:
            metrics["total_blocks"] = len(recent_blocks)
            metrics["total_passes"] = recent_passes
            total = len(recent_blocks) + recent_passes
            if total > 0:
                block_rate = len(recent_blocks) / total
                metrics["block_rate"] = round(block_rate, 3)
                if block_rate > 0.3:
                    issues.append("HIGH_BLOCK_RATE (>30% messages blocked)")
            # 检查拦截类型分布
            types = {}
            for b in recent_blocks:
                r = b.get("reason", "unknown")
                types[r] = types.get(r, 0) + 1
            metrics["block_reason_distribution"] = types

        healthy = len(issues) == 0
        return self.report(healthy, metrics, issues)


class LearningHealth(ModuleHealth):
    """学习模块自检"""

    def __init__(self):
        super().__init__("learning")

    def check(self, recent_learns: Optional[List[dict]] = None) -> dict:
        issues = []
        metrics = {}

        if recent_learns:
            metrics["total_learn_attempts"] = len(recent_learns)
            stored = sum(1 for l in recent_learns if l.get("stored"))
            metrics["stored_count"] = stored
            if recent_learns:
                store_rate = stored / len(recent_learns)
                metrics["store_rate"] = round(store_rate, 3)
                if store_rate < 0.1:
                    issues.append("LOW_STORE_RATE (<10% attempts stored)")
            # 分类分布
            cats = {}
            for l in recent_learns:
                if l.get("stored"):
                    c = l.get("category", "unknown")
                    cats[c] = cats.get(c, 0) + 1
            metrics["category_distribution"] = cats

        healthy = len(issues) == 0
        return self.report(healthy, metrics, issues)


class STTHealth(ModuleHealth):
    """语音识别模块自检"""

    def __init__(self):
        super().__init__("stt")

    def check(self, recent_stt: Optional[List[dict]] = None) -> dict:
        issues = []
        metrics = {}

        if recent_stt:
            total = len(recent_stt)
            empty = sum(1 for s in recent_stt if not s.get("text", "").strip())
            metrics["total_requests"] = total
            metrics["empty_results"] = empty
            if total > 0:
                empty_rate = empty / total
                metrics["empty_rate"] = round(empty_rate, 3)
                if empty_rate > 0.5:
                    issues.append("HIGH_EMPTY_RATE (>50% STT returned no text)")
            # 耗时统计
            durations = [s.get("duration_ms", 0) for s in recent_stt if s.get("duration_ms")]
            if durations:
                metrics["avg_duration_ms"] = round(sum(durations) / len(durations), 1)
                metrics["max_duration_ms"] = max(durations)
                if max(durations) > 10000:
                    issues.append("SLOW_STT (>10s for some requests)")

        healthy = len(issues) == 0
        return self.report(healthy, metrics, issues)


class TTSHealth(ModuleHealth):
    """语音合成模块自检"""

    def __init__(self):
        super().__init__("tts")

    def check(self, recent_tts: Optional[List[dict]] = None) -> dict:
        issues = []
        metrics = {}

        if recent_tts:
            total = len(recent_tts)
            failed = sum(1 for t in recent_tts if t.get("error"))
            metrics["total_requests"] = total
            metrics["failed"] = failed
            if total > 0:
                fail_rate = failed / total
                metrics["fail_rate"] = round(fail_rate, 3)
                if fail_rate > 0.2:
                    issues.append("HIGH_FAIL_RATE (>20% TTS failed)")
            durations = [t.get("duration_ms", 0) for t in recent_tts if t.get("duration_ms")]
            if durations:
                metrics["avg_duration_ms"] = round(sum(durations) / len(durations), 1)

        healthy = len(issues) == 0
        return self.report(healthy, metrics, issues)


# ==================== 总自检器 ====================

class SystemCheck:
    """悟道系统级自检——统一入口"""

    def __init__(self):
        self.memory = MemoryHealth()
        self.retrieval = RetrievalHealth()
        self.safety = SafetyHealth()
        self.learning = LearningHealth()
        self.stt = STTHealth()
        self.tts = TTSHealth()

        self.last_full_check = None
        self.check_history_file = None

    def set_data_dir(self, path: str):
        self.memory.set_data_dir(path)
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        self.check_history_file = p / "self_check_history.json"

    def run_full_check(self, context: Optional[dict] = None) -> dict:
        """运行所有模块自检，返回完整报告"""
        ctx = context or {}
        results = {
            "timestamp": datetime.now().isoformat(),
            "checks": {},
        }

        # 逐个模块检查
        results["checks"]["memory"] = self.memory.check()
        results["checks"]["retrieval"] = self.retrieval.check(
            recent_queries=ctx.get("recent_queries")
        )
        results["checks"]["safety"] = self.safety.check(
            recent_blocks=ctx.get("recent_blocks"),
            recent_passes=ctx.get("recent_passes", 0),
        )
        results["checks"]["learning"] = self.learning.check(
            recent_learns=ctx.get("recent_learns"),
        )
        results["checks"]["stt"] = self.stt.check(
            recent_stt=ctx.get("recent_stt"),
        )
        results["checks"]["tts"] = self.tts.check(
            recent_tts=ctx.get("recent_tts"),
        )

        # 总体健康度
        all_healthy = all(
            c.get("healthy", False)
            for c in results["checks"].values()
        )
        results["overall_healthy"] = all_healthy
        results["module_count"] = len(results["checks"])
        healthy_count = sum(
            1 for c in results["checks"].values() if c.get("healthy")
        )
        results["healthy_count"] = healthy_count
        results["unhealthy_modules"] = [
            name for name, c in results["checks"].items()
            if not c.get("healthy")
        ]

        self.last_full_check = results

        # 附加透明度信息
        results["transparency"] = self._build_transparency()

        # 持久化
        self._save_check(results)

        return results

    def _build_transparency(self) -> dict:
        """透明度报告——模块的边界、规则、限制"""
        return {
            "memory": {
                "边界": "单文件JSON存储，当前会话+跨session关键信息",
                "容量": "目标<5000条对话或<10MB，超出会产生警告",
                "保留期": "不自动清理，手动压缩/归档",
                "丢失条件": "data/ 目录被删除或文件损坏",
                "筛选规则": "L2只存preference/fact类，由learner评估后入库",
            },
            "retrieval": {
                "边界": "三路并行：Web搜索 + PromptLib知识库 + 对话记忆",
                "质量": "无自检，依赖上游API可用性",
                "规则": "search意图时触发，非search不自动搜索",
            },
            "safety": {
                "边界": "规则拦截（付款+敏感词）+ content_filter 二次校验",
                "宽松/严格": "当前仅拦截付款类，可能偏宽松",
                "已知漏项": "未拦截恶意代码/钓鱼链接/隐私泄露类内容",
            },
            "learning": {
                "边界": "LLM评估问答价值→结构化入库→PromptLib",
                "标准": ">10字符回复、非安全拦截、LLM判断valuable=true",
                "验证": "无回溯验证，不知存储内容实际有效性",
            },
            "stt": {
                "边界": "前端浏览器Web Speech API",
                "准确率": "无统计，依赖用户设备和浏览器",
                "已知问题": "环境噪音大时识别率下降",
            },
            "tts": {
                "边界": "Edge TTS云端API / 浏览器SpeechSynthesis",
                "体验": "无用户反馈机制",
            },
        }

    def _save_check(self, result: dict):
        """保存自检记录到文件"""
        if not self.check_history_file:
            return
        history = []
        if self.check_history_file.exists():
            try:
                with open(self.check_history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                print("[health] 读取检查历史失败")
        history.append({
            "ts": result["timestamp"],
            "overall_healthy": result["overall_healthy"],
            "healthy_count": result["healthy_count"],
            "unhealthy_modules": result["unhealthy_modules"],
        })
        # 只保留最近 100 条
        if len(history) > 100:
            history = history[-100:]
        with open(self.check_history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def summary(self) -> dict:
        """简易摘要，适合前端展示"""
        if not self.last_full_check:
            return {"status": "never_checked"}
        r = self.last_full_check
        return {
            "timestamp": r["timestamp"],
            "overall_healthy": r["overall_healthy"],
            "healthy_count": r["healthy_count"],
            "module_count": r["module_count"],
            "unhealthy_modules": r["unhealthy_modules"],
            "transparency": r.get("transparency", {}),
        }


# 全局单例
system_check = SystemCheck()
