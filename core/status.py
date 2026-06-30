"""
项目现状简报 — 为多 Agent 会议提供上下文
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import WUDAO_DATA
from core.llm import GLM_API_KEY, DEEPSEEK_API_KEY
from core.learned import LearnedLog
from core.memory import Memory


def _git_log(n: int = 10) -> str:
    """取最近 n 条 git 提交"""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _file_summary(path: str) -> str:
    """统计文件数量和关键目录"""
    base = Path(path)
    if not base.exists():
        return ""

    py_files = list(base.rglob("*.py"))
    html_files = list(base.rglob("*.html"))
    js_files = list(base.rglob("*.js"))

    parts = [
        f"Python 模块: {len(py_files)} 个",
        f"HTML 模板: {len(html_files)} 个",
        f"JS 文件: {len(js_files)} 个",
    ]
    return "  " + "\n  ".join(parts)


def build_project_brief() -> str:
    """生成当前项目现状简报"""
    git = _git_log()
    brief = ["【悟道当前状态】"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 版本
    try:
        from main import app
        brief.append(f"版本: {app.version}")
    except Exception:
        brief.append("版本: unknown")

    # 模型状态
    models = []
    if DEEPSEEK_API_KEY:
        models.append("DeepSeek-chat (主)")
    if GLM_API_KEY:
        models.append("GLM-4-Flash (备)")
    brief.append(f"模型: {' + '.join(models) if models else '未配置'}")

    # 最近迭代
    if git:
        brief.append(f"\n最近更新:\n{git}")

    # 模块概览
    brief.append(f"\n代码规模:")
    brief.append(_file_summary(root))

    # 记忆/今日所学数量
    try:
        learned = LearnedLog(data_dir=WUDAO_DATA)
        today = learned.today_summary()
        brief.append(f"\n今日所学: {today['count']} 条")
    except Exception:
        pass

    try:
        memory = Memory(data_dir=WUDAO_DATA)
        sessions = len(memory.sessions) if hasattr(memory, "sessions") and memory.sessions else 0
        brief.append(f"记忆会话数: {sessions}")
    except Exception:
        pass

    return "\n".join(brief)


def inject_status_into_topic(topic: str) -> str:
    """在议题前插入现状简报"""
    brief = build_project_brief()
    return f"{brief}\n\n=====\n\n【讨论议题】\n{topic}"
