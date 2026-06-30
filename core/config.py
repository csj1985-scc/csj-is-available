"""
统一配置入口 — 所有硬编码收敛到这里
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WUDAO_DATA = os.getenv("WUDAO_DATA", str(PROJECT_ROOT / "data"))
# 默认8002（唯一实例端口）
PORT = int(os.getenv("PORT", "8002"))

# DeepSeek
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 智谱 GLM（备选）
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_MODEL = os.getenv("GLM_MODEL", "glm-4-flash")
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# 余额配置（从 DeepSeek 平台查到的充值余额）
WUDAO_BALANCE = float(os.getenv("WUDAO_BALANCE", "0"))
BALANCE_ALERT_FILE = str(PROJECT_ROOT / "data" / "balance_alert.json")
BALANCE_ALERT_THRESHOLDS = (20, 10, 5)  # 剩余百分比阈值

# 管理面板
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "wudao-admin-2024")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")

# 企业微信机器人 webhook（告警通知）
WECHAT_WEBHOOK = os.getenv("WECHAT_WEBHOOK", "")
