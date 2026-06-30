"""
全局共享状态 — main.py 和各路由模块从这里取实例
"""
from pathlib import Path

from core.config import WUDAO_DATA as DATA_DIR
from core.memory import Memory, MediumLongMemory
from core.learned import LearnedLog
from core.safety import SafetyGuard
from core.router import IntentRouter
from core.retriever import Retriever
from core.learner import Learner
from core.health import system_check
from core.agent import WudaoAgent

memory = Memory(data_dir=DATA_DIR)
memory_ml = MediumLongMemory(data_dir=DATA_DIR)
learned = LearnedLog(data_dir=DATA_DIR)
guard = SafetyGuard()
router = IntentRouter()
retriever = Retriever()
learner = Learner()
system_check.set_data_dir(DATA_DIR)

# 共享 Agent 实例（HTTP 和 WebSocket 共用，确保审批事件互通）
agent = WudaoAgent(memory=memory, memory_ml=memory_ml, learned=learned, guard=guard)
