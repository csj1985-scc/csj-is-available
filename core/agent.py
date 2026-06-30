# -*- coding: utf-8 -*-
"""
悟道本能执行核心层 v0.8.0

架构：纯 Function Calling，无关键词意图分类
  所有输入直接进工具循环，LLM 自行决定调工具或文字回复
  连续任务用 session message cache 保持上下文

核心原则：LLM 只负责"选哪个工具"，服务端负责"必须执行"。
加新能力 = 在 TOOLS 列表加一条 JSON 定义，不改逻辑代码。
"""
import asyncio
import json
import re
import time
import sys
import urllib.error
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple, Callable

from core.executor import execute as sandbox_execute
from core.llm import llm_call, SYSTEM_PROMPT as _BASE_SYSTEM_PROMPT
from core.tools_ext import (
    run_browser, query_knowledge,
    list_templates, apply_template,
    suggest_agent_role,
)
from core.debug_toolkit import run_debug_check
from core.api_toolkit import run_api_tool
from core.task_team import classify_task, get_team_config, build_lead_system_prompt, get_allowed_tools


def _safe_print(*args, **kwargs):
    """Windows GBK 安全的 print，遇到不能编码的字符自动替换"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # 降级：替换非编码字符为 ?
        text = " ".join(str(a) for a in args)
        text_enc = text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
        print(text_enc, **kwargs)


# ============================================================
# 数据类
# ============================================================

@dataclass
class ExecutionResult:
    """沙箱执行结果"""
    action: str
    success: bool
    raw_result: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: Optional[str] = None
    execution_time_ms: float = 0.0


@dataclass
class ProcessResult:
    """消息处理结果"""
    reply: str = ""
    safety_blocked: bool = False
    safety_reason: str = ""
    execution_result: Optional[ExecutionResult] = None
    consult_info: Optional[dict] = None
    learned_count: int = 0
    workflow_events: List[dict] = field(default_factory=list)


# ============================================================
# 工具定义（分层设计，减少每次 LLM 调用的工具量）
# ============================================================
# CORE_TOOLS：日常对话常用，每次必传（5个）
# EXTRA_TOOLS：场景拓展，按需动态加入

CORE_TOOLS = [
    {
        "name": "get_current_time",
        "description": "获取当前时间（年月日时分，星期几）",
        "parameters": {}
    },
    {
        "name": "query_weather",
        "description": "查询城市实时天气和预报",
        "parameters": {
            "city": "城市名，中文英文均可"
        }
    },
    {
        "name": "knowledge_search",
        "description": "智脑知识库检索（14939条）。搜到直接引用回复，不需要再查别的。",
        "parameters": {
            "query": "搜索关键词",
            "top_k": "返回数量，默认5，最大10"
        }
    },
    {
        "name": "read_file",
        "description": "【只读】读取文件内容",
        "parameters": {
            "path": "文件名或全路径"
        }
    },
    {
        "name": "read_url",
        "description": "访问网页或 HTTP API，获取文本内容",
        "parameters": {
            "url": "完整 URL，http/https 开头"
        }
    },
    {
        "name": "start_meeting",
        "description": "启动多Agent会议。系统有多个专业Agent（产品、架构、运营等），各有专长。当话题需要多角色讨论、可行性评估、风险分析、方案设计等，用此工具召集他们讨论。",
        "parameters": {
            "topic": "讨论主题"
        }
    },
]

EXTRA_TOOLS = [
    {
        "name": "generate_image",
        "description": "AI 画图：根据文字描述生成图片，返回图片 URL。用户让画图时使用此工具。",
        "parameters": {
            "prompt": "图片描述，越详细效果越好，如「一只橘色的猫坐在草地上」",
            "size": "1024x1024（默认）| 1024x768 | 768x1024（可选）"
        }
    },
    {
        "name": "recognize_image",
        "description": "识别图片中的内容、文字、场景。用户发了图片时用此工具。",
        "parameters": {
            "description": "对图片的具体提问（可选，留空则默认详细描述）"
        }
    },
    {
        "name": "create_file",
        "description": "【新建】创建新文件。文件已存在则失败，覆盖用 write_file。",
        "parameters": {
            "path": "文件名或全路径",
            "content": "文件内容（可选，复杂内容可留空自动生成）"
        }
    },
    {
        "name": "write_file",
        "description": "【覆盖/追加】写文件。文件不存在自动创建。",
        "parameters": {
            "path": "文件名或全路径",
            "content": "新的文件内容"
        }
    },
    {
        "name": "run_command",
        "description": "在终端执行 shell 命令。用于 git、pip install、热重启等操作。",
        "parameters": {
            "command": "命令字符串"
        }
    },
    {
        "name": "create_project",
        "description": "创建多文件项目。一次创建整个目录结构。",
        "parameters": {
            "root": "项目根目录名",
            "files": "文件列表 JSON，每项含 path 和 content"
        }
    },
    {
        "name": "python_toolkit",
        "description": "Python 编程技巧工具箱。按需选 module 和 kwargs 调用。",
        "parameters": {
            "module": "模块名：genetic_algorithm | github_search | build_agent | browser_agent | solve_puzzle | process_documents | rag_query | search_recommend | prompt_template",
            "kwargs": "JSON 参数字典，各 module 不同"
        }
    },
    {
        "name": "debug_check",
        "description": "代码调试与错误排查工具。选 action 和 params 调用。",
        "parameters": {
            "action": "debug.check_function | debug.check_boundaries | debug.check_var_consistency | debug.check_return_type | debug.merge_messages | debug.min_run_length",
            "params": "JSON 参数字典，各 action 不同"
        }
    },
    {
        "name": "api_tool",
        "description": "API 接口工具：HTTP GET 封装、WebSocket 路由、搜索免费 API",
        "parameters": {
            "action": "api.fetch_get | api.websocket_route | api.find_free_api",
            "params": "JSON 参数字典"
        }
    },
    {
        "name": "browser_do",
        "description": "浏览器自动化：打开网页、搜索、点击、截图",
        "parameters": {
            "action": "open | search | click | screenshot",
            "url": "网页地址",
            "query": "搜索关键词",
            "selector": "CSS 选择器"
        }
    },
    {
        "name": "template_use",
        "description": "提示词模板：list 列出模板，apply 填充生成提示词",
        "parameters": {
            "action": "list | apply",
            "name": "模板名（apply 时必填）",
            "params": "JSON 模板参数（apply 时必填）",
            "category": "分类过滤（list 时可选）"
        }
    },
    {
        "name": "task_create",
        "description": "【任务管理】创建新任务跟踪工作进度",
        "parameters": {
            "title": "任务标题",
            "description": "任务描述（可选）",
            "status": "pending | in_progress | completed（默认 pending）"
        }
    },
    {
        "name": "task_update",
        "description": "【任务管理】更新任务状态或信息",
        "parameters": {
            "task_id": "任务 ID",
            "title": "新标题（可选）",
            "description": "新描述（可选）",
            "status": "pending | in_progress | completed"
        }
    },
    {
        "name": "task_list",
        "description": "【任务管理】列出所有任务，可按状态筛选",
        "parameters": {
            "status": "pending | in_progress | completed（可选，不传则全部）"
        }
    },
    {
        "name": "create_plan",
        "description": "【计划制定】为多步骤任务制定执行计划，自动创建任务列表",
        "parameters": {
            "goal": "目标描述",
            "steps": "步骤列表 JSON，每项：step、action、expected"
        }
    },
    {
        "name": "dispatch_task_team",
        "description": "【团队调度】派专业 Agent 团队执行复杂任务。简单任务直接用工具做。",
        "parameters": {
            "task_id": "任务 ID",
            "team_type": "dev | research | writing | ops（可选，自动判断）"
        }
    },
    {
        "name": "dispatch_to_agent",
        "description": "【子 Agent 调度】将子任务派给专业 Agent 单独执行。Team Lead 专用，用于把任务拆解后分配给不同角色的 Agent。",
        "parameters": {
            "agent_name": "research | dev | review | ops | writing",
            "task": "子任务描述，清晰说明要做什么",
            "context": "背景信息（已有调研结果、代码上下文等，可选）"
        }
    },
    {
        "name": "query_wudao_state",
        "description": "【内部】查悟道内部数据：协商记录、记忆、今日所学。比 grep 快百倍。",
        "parameters": {
            "keyword": "搜索关键词",
            "scope": "all | consultation | memory | learned（默认 all）"
        }
    },
]

# 保持 TOOLS 兼容性（旧代码引用），实际上已不再直接使用
TOOLS = CORE_TOOLS + EXTRA_TOOLS

TOOL_LABELS = {
    "create_file": "创建文件",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "query_weather": "查天气",
    "read_url": "看网页",
    "recognize_image": "看图片",
    "run_command": "跑命令",
    "create_project": "建项目",
    "python_toolkit": "工具箱",
    "debug_check": "调代码",
    "api_tool": "API",
    "browser_do": "刷网页",
    "knowledge_search": "搜知识",
    "template_use": "套模板",
    "get_current_time": "看时间",
    "task_create": "建任务",
    "task_update": "改任务",
    "task_list": "看任务",
    "create_plan": "定计划",
    "dispatch_task_team": "派团队",
    "dispatch_to_agent": "派子任务",
    "query_wudao_state": "查内部",
    "generate_image": "画图",
    "step_limit": "执行限制",
}

_OPENAI_TOOLS_CACHE = None


def _detect_drawing_intent(text: str) -> str:
    """检测画图意图：消息以"画"开头且不是"画法""画面"等非画图词。返回图片描述或空字符串。"""
    text = text.strip()
    if not text or len(text) <= 1 or not text.startswith("画"):
        return ""
    if any(text.startswith(s) for s in ("画法", "画家", "画面", "画风", "画质", "画线", "画框")):
        return ""
    return text[1:].strip()


def _detect_meeting_request(text: str) -> str:
    """检测开会意图。返回讨论主题或空字符串。"""
    if not text:
        return ""
    text = text.strip()
    # 开会讨论X / 开个会讨论X / 重新开会讨论X
    for prefix in ("重新开会讨论", "重新开个会讨论", "开会讨论", "开个会讨论", "开个会", "开会"):
        if text.startswith(prefix):
            topic = text[len(prefix):].strip()
            if topic:
                return topic
    return ""


def _detect_tool_needs(text: str) -> bool:
    """检测用户输入是否需要工具执行（快速通道过滤用）"""
    if not text:
        return False
    text_lower = text.lower()
    # 文件/代码/命令操作（覆盖各类表达）
    if any(kw in text for kw in ["创建", "新建", "写文件", "读文件", "读取", "修改",
                                   "删除文件", "复制文件", "重命名",
                                   "执行", "运行", "安装", "重启", "git", "pip",
                                   "npx", "npm", "yarn", "mkdir", "cd ",
                                   "项目", "目录", "文件夹",
                                   "第一行", "内容", "列出"]):
        return True
    if any(kw in text_lower for kw in ["create", "write", "read", "delete", "run",
                                        "install", "restart", "deploy", "build",
                                        "list", "show", "print"]):
        return True
    # 浏览器/搜索
    if any(kw in text for kw in ["搜索", "查询", "百度", "打开网页", "浏览器", "访问"]):
        return True
    # 任务/团队
    if any(kw in text for kw in ["任务", "计划", "待办", "todo", "开会",
                                   "讨论", "派任务", "团队"]):
        return True
    # 画图/图片
    if any(kw in text for kw in ["画", "图片", "生成图片", "image"]):
        return True
    # 调试
    if any(kw in text for kw in ["debug", "调试", "报错", "错误", "异常"]):
        return True
    return False


def _is_fake_file_claim(text: str) -> Optional[str]:
    """
    检测 LLM 是否在文本中声称执行了操作但没调对应工具。
    返回伪造的操作类型（create_file / write_file / run_command / task_create / create_plan / fake_system）或 None。
    """
    import re

    # 预检查：标签式伪造（LLM 自己发明标签假装调用工具）
    TAG_PATTERNS = [
        (r'\[TASK_CREATE\]', 'task_create'),
        (r'\[TASK_UPDATE\]', 'task_update'),
        (r'\[TASK_LIST\]', 'task_list'),
        (r'\[CREATE_PLAN\]', 'create_plan'),
        (r'\[PLAN\]', 'create_plan'),
        (r'\[Read\s+file[:\]]', 'read_file'),
        (r'\[Write\s+file[:\]]', 'write_file'),
        (r'\[Run\s+command[:\]]', 'run_command'),
    ]
    for p, action in TAG_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return action

    # 文字声明伪造任务管理
    TASK_CLAIM_PATTERNS = [
        (r'(?:已经|已).{0,5}(?:创建|建立|建好)了?\s*(?:任务|计划)', 'task_create'),
        (r'任务(?:已经|已).{0,5}(?:创建|建立|建好|完成)', 'task_create'),
        (r'(?:任务|计划).{0,10}(?:已经|已).{0,5}(?:更新|修改|完成|标记)', 'task_update'),
        (r'把我刚说的.*?(?:创建|建立)成(?:任务|计划)', 'task_create'),
        (r'制定.*?计划[，,].*?步', 'create_plan'),
        (r'计划已(?:制定|创建|建立)', 'create_plan'),
        (r'(?:任务|计划).{0,10}(?:创建|建立)成功', 'task_create'),
        (r'任务已创建', 'task_create'),
    ]
    for p, action in TASK_CLAIM_PATTERNS:
        if re.search(p, text):
            return action

    # === 预检查路径引用语境 ===
    has_path_ref = bool(re.search(r'[\w]+/[\w.\-]+\.(?:txt|md|py|html|json|css|js)', text))
    if has_path_ref:
        has_completion = bool(re.search(r'(?:存到|存了|写了|写到|创建了|创建到|写到了|生成了|成功了|好了|写好了|存好了|修改了|更新了|编辑了|新建了|放进去了|写入)', text))
        if not has_completion:
            if not re.search(r'\[系统(?:执行|说|返回|提示|确认|警告)\]', text):
                return None

    # 无路径依赖的直接声明检测
    no_path_patterns = [
        (r'修好了[。，.!！\s].{0,30}(?:函数|变量|加了|改成了|改为了|计数器|检查|判断|分支|逻辑|代码|路径|import|export|class|def|return)', 'write_file'),
        (r'改好了[。，.!！\s].{0,30}(?:函数|变量|加了|改成了|改为了|计数器|检查|判断|分支|逻辑|代码|路径)', 'write_file'),
        (r'(?:已经|已).{0,5}(?:修|改)好了[，,]\s*(?:加了|改了|新增)', 'write_file'),
        (r'文件已(?:修改|更新|创建|写入|保存)', 'write_file'),
        (r'加了\s*[_a-zA-Z一-鿿]+[/_一-鿿\w]*', 'write_file'),
        # 口头说改了但没调工具
        (r'我把.{0,30}(?:改了|改好了|修好了|更新了)', 'write_file'),
        (r'(?:已经|已).{0,8}(?:热重启|重启)(?:生效|成功|好了|完成)', 'run_command'),
        (r'热重启已生效', 'run_command'),
    ]
    for p, action in no_path_patterns:
        if re.search(p, text):
            return action

    # 文字声明伪造会议/团队调度
    DISPATCH_CLAIM_PATTERNS = [
        (r'(?:我[会来去]|让我|马上|立即|现在|这就).{0,15}(?:安排|召开|组织|发起|召开|召集).{0,8}(?:会议|讨论|会)', 'dispatch_task_team'),
        (r'邀请.{0,10}(?:参加|参与|加入).{0,10}(?:会议|讨论|会)', 'dispatch_task_team'),
        (r'(?:会议|讨论).{0,10}(?:议程|目标|主题|时间|地点|安排)', 'dispatch_task_team'),
        (r'(?:召集|组建).{0,10}(?:团队|小组|专家组)', 'dispatch_task_team'),
    ]
    for p, action in DISPATCH_CLAIM_PATTERNS:
        if re.search(p, text):
            return action

    patterns = [
        (r'(?:存到|写到|保存在?|存放[到至]?|创建了?|新建了?)\s*[\S]*?\.(?:txt|md|py|html|json|css|js)\b', 'create_file'),
        (r'(?:已经|刚才|已).{0,5}存到\s*[\S]*?\.(?:txt|md|py|html|json|css|js)\b', 'create_file'),
        (r'(?:文件|文档).{0,10}(?:已经|已).{0,5}(?:保存|创建|写好)', 'create_file'),
        (r'(?:修改|更新|编辑|改了|更新了)\s*[\S]*?\.(?:txt|md|py|html|json|css|js)\b', 'write_file'),
        (r'(?:已经|已).{0,5}(?:修改|更新|编辑)了?\s*(?:文件|代码|配置)', 'write_file'),
        (r'(?:已经|已).{0,5}(?:写入|写到)\s*[\S]*?\.(?:txt|md|py|html|json|css|js)\b', 'create_file'),
        (r'(?:在项目|项目下|项目根).{0,10}(?:修改|更新|编辑|创建|新建).{0,10}(?:文件|代码|脚本)', 'write_file'),
        (r'(?:已经|已).{0,5}(?:执行|运行)了?\s*(?:命令|git|python|npm|pip|npx)\s', 'run_command'),
        (r'(?:已经|已).{0,5}(?:git push|git commit|git pull|git add|npm install|pip install|python restart)', 'run_command'),
        (r'用\s*git\s*(?:命令)?\s*(?:推送|提交|拉取|push|commit|pull|add)', 'run_command'),
        (r'\[系统(?:执行|说|返回|提示|确认|警告)\]', 'fake_system'),
        (r'系统说(?:文件|目录|命令|操作|已经|还没|不)', 'fake_system'),
    ]
    for p, action in patterns:
        if re.search(p, text, re.IGNORECASE):
            return action
    return None


# 连续性检测：用户说"继续"时复用上次工具调用的消息历史（结构性修复）
_CONTINUATION_WORDS = {"继续", "接着", "下一步", "做吧", "干完"}


def _get_openai_tools(allowed_tools: Optional[list] = None) -> list:
    """将扁平 TOOLS 转为 OpenAI function calling 格式。allowed_tools 可限制可用工具子集。"""
    global _OPENAI_TOOLS_CACHE
    if allowed_tools is None and _OPENAI_TOOLS_CACHE:
        return _OPENAI_TOOLS_CACHE
    source = TOOLS if allowed_tools is None else [t for t in TOOLS if t["name"] in allowed_tools]
    result = []
    for t in source:
        props = {}
        required = []
        for key, desc in t["parameters"].items():
            props[key] = {"type": "string", "description": desc}
            if "可选" not in desc:
                required.append(key)
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    if allowed_tools is None:
        _OPENAI_TOOLS_CACHE = result
    return result


# ============================================================
# 安全模块（用户已关闭支付拦截）
# ============================================================

SAFETY_BLOCKED_KEYWORDS = []
SAFETY_BLOCKED_PARTIAL = []
TOOL_PARAM_BLOCKED_PARTIAL = []


def _safety_check_tool_params(params: dict) -> Optional[str]:
    for key, value in params.items():
        if isinstance(value, str):
            value_lower = value.lower()
            for kw in TOOL_PARAM_BLOCKED_PARTIAL:
                if kw.lower() in value_lower:
                    return f"工具参数检测到安全敏感词: {kw}"
    return None


# ============================================================
# 标签解析
# ============================================================

def parse_consult_tag(text: str) -> Optional[dict]:
    pattern = r'\[CONSULT\]\s*topic:\s*(.+?)\s*agents:\s*(.+?)\s*rounds:\s*(\d+)\s*\[/CONSULT\]'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return {
            "topic": match.group(1).strip(),
            "agents": [a.strip() for a in match.group(2).split(",")],
            "rounds": int(match.group(3)),
        }
    return None


def _select_tools_for_context(messages: list, allowed_tools: Optional[list] = None) -> list:
    """
    根据对话上下文动态选择工具集：
    - 日常对话仅 CORE_TOOLS（5个），减轻 LLM 决策负担
    - 根据 user_input 关键词自动扩展 EXTRA_TOOLS
    - 如果本轮已有工具调用记录，保持 full TOOLS 不变
    """
    if allowed_tools is not None:
        # 外部指定了 tool 子集，直接用
        all_tools = CORE_TOOLS + EXTRA_TOOLS
        return [t for t in all_tools if t["name"] in allowed_tools]

    # 检查最近的 user 消息中是否有需要拓展工具的线索
    user_texts = [m.get("content", "") for m in messages if m.get("role") == "user"]
    recent_input = " ".join(user_texts[-3:]).lower() if user_texts else ""

    # 基础集：先只给 CORE_TOOLS
    selected = list(CORE_TOOLS)

    # 按意图自动扩展
    if any(kw in recent_input for kw in ["画", "生成图片", "image"]):
        _add_tool(selected, "generate_image")
    if any(kw in recent_input for kw in ["图片", "照片", "截图", "这张", "识别"]):
        _add_tool(selected, "recognize_image")
    if any(kw in recent_input for kw in ["创建文件", "新建文件", "写文件", "写一个", "改代码", "修改代码",
                                          "读取文件", "打开文件", "内容", "第一行", "读文件"]):
        _add_tool(selected, "create_file")
        _add_tool(selected, "write_file")
        _add_tool(selected, "run_command")
        _add_tool(selected, "create_project")
    if any(kw in recent_input for kw in ["改文件", "修改", "更新", "追加"]):
        _add_tool(selected, "write_file")
        _add_tool(selected, "run_command")
    if any(kw in recent_input for kw in ["执行命令", "运行", "重启", "安装", "git", "pip"]):
        _add_tool(selected, "run_command")
    if any(kw in recent_input for kw in ["搜索网页", "百度一下", "上网查"]):
        _add_tool(selected, "read_url")
    if any(kw in recent_input for kw in ["任务", "计划", "todo", "待办", "工作流"]):
        _add_tool(selected, "task_create")
        _add_tool(selected, "task_update")
        _add_tool(selected, "task_list")
        _add_tool(selected, "create_plan")
    if any(kw in recent_input for kw in ["开会", "讨论", "团队", "协商", "派"]):
        _add_tool(selected, "dispatch_task_team")
        _add_tool(selected, "dispatch_to_agent")
    if any(kw in recent_input for kw in ["子任务", "子 Agent", "子代理"]):
        _add_tool(selected, "dispatch_task_team")
        _add_tool(selected, "dispatch_to_agent")
    if any(kw in recent_input for kw in ["浏览器", "打开网页", "点击", "自动化"]):
        _add_tool(selected, "browser_do")
    if any(kw in recent_input for kw in ["调试", "debug", "报错", "bug"]):
        _add_tool(selected, "debug_check")
        _add_tool(selected, "python_toolkit")
        _add_tool(selected, "api_tool")
    if any(kw in recent_input for kw in ["模板", "提示词"]):
        _add_tool(selected, "template_use")
    if any(kw in recent_input for kw in ["我记得", "之前说过", "查一下", "搜索知识"]):
        _add_tool(selected, "query_wudao_state")

    return selected


def _add_tool(tool_list: list, name: str):
    """从 EXTRA_TOOLS 添加单个工具到列表（去重）"""
    if any(t["name"] == name for t in tool_list):
        return
    for t in EXTRA_TOOLS:
        if t["name"] == name:
            tool_list.append(t)
            return


def parse_memorize_tag(text: str) -> Optional[dict]:
    pattern = r'\[MEMORIZE\]\s*content:\s*(.+?)\s*category:\s*(.+?)\s*\[/MEMORIZE\]'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return {
            "content": match.group(1).strip(),
            "category": match.group(2).strip(),
        }
    return None


def parse_idea_tag(text: str) -> Optional[dict]:
    pattern = r'\[IDEA\]\s*content:\s*(.+?)\s*follow_up_days:\s*(\d+)\s*priority:\s*(.+?)\s*\[/IDEA\]'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return {
            "content": match.group(1).strip(),
            "follow_up_days": int(match.group(2)),
            "priority": match.group(3).strip(),
        }
    return None


def strip_consult_tag(text: str) -> str:
    return re.sub(r'\[CONSULT\].*?\[/CONSULT\]', '', text, flags=re.DOTALL).strip()


def strip_memorize_tag(text: str) -> str:
    return re.sub(r'\[MEMORIZE\].*?\[/MEMORIZE\]', '', text, flags=re.DOTALL).strip()


def strip_idea_tag(text: str) -> str:
    return re.sub(r'\[IDEA\].*?\[/IDEA\]', '', text, flags=re.DOTALL).strip()


def sanitize_context(text: str, max_len: int = 200) -> str:
    if not text:
        return ""
    cleaned = re.sub(r'\s+', ' ', text).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "..."
    return cleaned


# ============================================================
# 轻量任务管理
# ============================================================

class TaskStore:
    """简单内存任务存储器 — 类似 Claude Code 的任务跟踪"""

    def __init__(self):
        self._tasks: Dict[str, dict] = {}
        self._counter = 0

    def create(self, title: str, description: str = "", status: str = "pending") -> dict:
        self._counter += 1
        task_id = f"task_{self._counter}"
        task = {
            "id": task_id,
            "title": title,
            "description": description,
            "status": status,
            "created_at": time.strftime("%H:%M:%S"),
        }
        self._tasks[task_id] = task
        return task

    def update(self, task_id: str, **kwargs) -> Optional[dict]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        for k in ("title", "description", "status"):
            if k in kwargs and kwargs[k] is not None:
                task[k] = kwargs[k]
        task["updated_at"] = time.strftime("%H:%M:%S")
        return task

    def list(self, status: str = "") -> list:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        tasks.sort(key=lambda t: t.get("created_at", ""))
        return tasks

    def get_context(self) -> str:
        """返回活跃任务摘要，注入到 system prompt"""
        parts = []
        in_progress = [t for t in self._tasks.values() if t["status"] == "in_progress"]
        if in_progress:
            lines = [f"  - [{t['id']}] {t['title']}（进行中）" for t in in_progress[:5]]
            parts.append("【当前进行中的任务】\n" + "\n".join(lines))
        pending = [t for t in self._tasks.values() if t["status"] == "pending"]
        if pending:
            lines = [f"  - [{t['id']}] {t['title']}（待办）" for t in pending[:5]]
            parts.append("【待办任务】\n" + "\n".join(lines))
        if parts:
            return "\n" + "\n\n".join(parts) + "\n"
        return ""

    def next_pending(self, after_id: str = "") -> Optional[dict]:
        """找到下一个待办任务（可选：排在指定任务之后）"""
        pending = [t for t in self._tasks.values() if t["status"] == "pending"]
        pending.sort(key=lambda t: t.get("created_at", ""))
        if after_id:
            found = False
            for t in pending:
                if t["id"] == after_id:
                    found = True
                    continue
                if found:
                    return t
        return pending[0] if pending else None


# ============================================================
# 主 Agent 类
# ============================================================

class WudaoAgent:
    """悟道 Agent 核心类 - Claude Code 风格多轮任务执行"""

    MAX_STEPS = 20

    def __init__(self, memory=None, memory_ml=None, learned=None, guard=None):
        self.memory = memory
        self.memory_ml = memory_ml
        self.learned = learned
        self.guard = guard
        self._current_images = None
        self._session_scenes: Dict[str, str] = {}
        self._scene_configs: Dict[str, dict] = {}
        self.task_store = TaskStore()
        self._approval_events: Dict[str, asyncio.Event] = {}
        self._approval_results: Dict[str, bool] = {}
        self._session_messages: Dict[str, List[dict]] = {}
        self._team_roles: Optional[dict] = None

    async def _wait_approval_async(self, session_id: str, timeout: float = 120) -> bool:
        """等待用户审批（继续/取消），返回 True=继续"""
        event = asyncio.Event()
        self._approval_events[session_id] = event
        self._approval_results[session_id] = False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False
        return self._approval_results.get(session_id, False)

    def resolve_approval(self, session_id: str, continue_: bool):
        """WebSocket 处理器调用，解除 _wait_approval 的阻塞"""
        self._approval_results[session_id] = continue_
        event = self._approval_events.pop(session_id, None)
        if event:
            event.set()

    def _has_continuation_intent(self, user_input: str) -> bool:
        """检查用户输入是否表示'继续上次任务'"""
        text = user_input.strip()
        if text in _CONTINUATION_WORDS:
            return True
        if text.startswith("继续") or text.startswith("接着"):
            return True
        return False

    # ── 场景管理 ────────────────────────────────────────

    def set_session_scene(self, session_id: str, scene_id: str = "default"):
        self._session_scenes[session_id] = scene_id

    def get_session_scene(self, session_id: str) -> str:
        return self._session_scenes.get(session_id, "default")

    def _load_scene_config(self, scene_id: str) -> Optional[dict]:
        if scene_id in self._scene_configs:
            return self._scene_configs[scene_id]
        try:
            from core.scene import SceneManager
            cfg = SceneManager().get_scene(scene_id)
            if cfg:
                self._scene_configs[scene_id] = cfg
            return cfg
        except Exception:
            return None

    async def process(self, user_input: str, session_id: str = "main",
                      images: Optional[List[str]] = None,
                      ws=None, manager=None,
                      on_token: Optional[Callable] = None,
                      scene_id: Optional[str] = None) -> ProcessResult:
        result = ProcessResult()

        if self.guard:
            passed, reason = self.guard.check(user_input)
            if not passed:
                result.safety_blocked = True
                result.safety_reason = reason
                result.reply = reason
                return result

        self._current_images = images
        if scene_id:
            self.set_session_scene(session_id, scene_id)

        # ── 画图请求代码级前置处理（不依赖 LLM tool calling）──
        drawing_prompt = _detect_drawing_intent(user_input)
        if drawing_prompt:
            _safe_print(f"[agent] 画图请求: {drawing_prompt}")
            img_result = await asyncio.to_thread(_generate_image, drawing_prompt)
            if "error" not in img_result:
                result.reply = f"![{drawing_prompt}]({img_result['url']})"
                result.workflow_events = [{"step": 0, "tool": "generate_image", "success": True, "summary": img_result.get("summary", "")}]
                self._current_images = None
                return result
            _safe_print(f"[agent] 画图API失败，回退LLM: {img_result['error']}")

        # ── 开会请求代码级前置处理（不依赖 LLM tool calling）──
        meeting_topic = _detect_meeting_request(user_input)
        if meeting_topic:
            _safe_print(f"[agent] 开会请求: {meeting_topic}")
            try:
                from core.agent_registry import get_registry
                from core.consultation import start_consultation_impl
                registry = get_registry()
                agents = registry.list_all()
                if not agents:
                    result.reply = "没有可用的 Agent，无法召开会议"
                    self._current_images = None
                    return result
                cons = start_consultation_impl(meeting_topic, [a.id for a in agents], max_rounds=2)
                if cons.get("error"):
                    result.reply = f"会议创建失败: {cons['error']}"
                else:
                    result.reply = f"✅ 已发起会议讨论「{meeting_topic}」"
                    result.consult_info = {"topic": meeting_topic, "session_id": cons["session_id"]}
                self._current_images = None
                return result
            except Exception as e:
                import traceback
                _safe_print(f"[agent] 创建会议失败: {e}\n{traceback.format_exc()}")
                # 出错时回退到正常 LLM 流程

        # 会话连续性检测：说"继续"时复用上次工具调用的消息历史，保持 tool_call 上下文
        _is_cont = self._has_continuation_intent(user_input) and session_id in self._session_messages
        if _is_cont:
            messages = list(self._session_messages[session_id])
            # 更新系统提示中的时间
            _t_now = time.localtime()
            _time_str = time.strftime("%Y-%m-%d %H:%M", _t_now)
            _weekday = "一二三四五六日"[_t_now.tm_wday]
            messages[0]["content"] = re.sub(
                r'【当前真实时间：.*?】',
                f'【当前真实时间：{_time_str}，周{_weekday}】',
                messages[0]["content"]
            )
            if images:
                content = [{"type": "text", "text": user_input or "描述这张照片"}]
                for b64 in images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    })
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "user", "content": user_input})
        else:
            # _build_messages 含文件 I/O（get_context 加载保存记忆），放在线程跑避免阻塞事件循环
            messages = await asyncio.to_thread(self._build_messages, user_input, session_id, images)

        await self._broadcast_state(ws, manager, "thinking")

        # ── 快速通道：纯聊天不走工具循环，直调 LLM 流式 ──
        _is_chat_only = not _detect_tool_needs(user_input) and not images and not _is_cont
        if _is_chat_only:
            # 纯聊天用最精简的消息（跳过 history 等上下文膨胀）
            _quick_msgs = await self._quick_messages(user_input, session_id)
            _quick_reply = await self._quick_chat(_quick_msgs, on_token=on_token)
            reply, events = _quick_reply, []
            # 快速通道也记录对话历史
            if self.memory and reply:
                await asyncio.to_thread(self.memory.append, session_id, user_input, reply)
            self._current_images = None
            result.reply = reply
            result.workflow_events = []
            await self._broadcast_state(ws, manager, "idle")
            return result
        else:
            reply, events = await self._run_tool_loop(messages, ws, manager, session_id=session_id, on_token=on_token)

        result.reply = reply
        result.workflow_events = events

        # 检查是否发起了会议
        if getattr(self, '_started_meeting', None):
            result.consult_info = self._started_meeting
            self._started_meeting = None

        if session_id:
            self._session_messages[session_id] = messages
        await self._broadcast_state(ws, manager, "idle")

        if self.memory:
            await asyncio.to_thread(self.memory.append, session_id, user_input, reply)
        result.reply, consult_info = await asyncio.to_thread(self._process_tags, result.reply, user_input, session_id)
        result.consult_info = consult_info

        if self.learned:
            await asyncio.to_thread(self.learned.add, session_id, user_input, result.reply)
            result.learned_count = 1

        self._current_images = None
        return result

    def _build_messages(self, user_input: str, session_id: str,
                        images: Optional[List[str]] = None) -> List[dict]:
        from datetime import datetime
        _now = datetime.now()
        _time_str = _now.strftime("%Y-%m-%d %H:%M")
        _weekday = "一二三四五六日"[_now.weekday()]
        _time_prompt = f"【当前真实时间：{_time_str}，周{_weekday}】\n\n"

        # 注入场景上下文
        active_scene_id = self._session_scenes.get(session_id, "default")
        scene_cfg = self._load_scene_config(active_scene_id)
        if scene_cfg and scene_cfg.get("name"):
            scene_name = scene_cfg.get("name", active_scene_id)
            scene_mode = scene_cfg.get("mode", "chat")
            scene_desc = scene_cfg.get("description", "")
            _time_prompt += f"【当前场景：{scene_name}（{scene_mode}模式）】\n"
            if scene_desc:
                _time_prompt += f"场景说明：{scene_desc}\n"
            if scene_mode == "learn":
                _time_prompt += "注意：当前为学习模式，重点在于记录和整理新知识。\n"
            elif scene_mode == "agentic":
                _time_prompt += "注意：当前为任务模式，你可以使用工具来完成任务。\n"

        messages = [{"role": "system", "content": _time_prompt + _BASE_SYSTEM_PROMPT}]

        # 注入活跃任务上下文（不做向量检索）
        task_context = self.task_store.get_context() if self.task_store else ""
        if task_context:
            messages[0]["content"] += "\n" + task_context

        if self.memory:
            for h in self.memory.get_history(session_id)[-3:]:
                if "索兰娥" in h.get("user", "") or "索兰娥" in h.get("assistant", ""):
                    continue
                messages.append({"role": "user", "content": h["user"]})
                messages.append({"role": "assistant", "content": h["assistant"]})

        if images:
            content: list = [{"type": "text", "text": user_input or "描述这张照片"}]
            for b64 in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_input})

        return messages

    async def _quick_messages(self, user_input: str, session_id: str) -> List[dict]:
        from datetime import datetime
        _now = datetime.now()
        _time_str = _now.strftime("%Y-%m-%d %H:%M")
        _weekday = "一二三四五六日"[_now.weekday()]
        _time_prompt = f"【当前真实时间：{_time_str}，周{_weekday}】\n\n"
        system = {"role": "system", "content": _time_prompt + _BASE_SYSTEM_PROMPT}
        return [system, {"role": "user", "content": user_input}]

    async def _quick_chat(self, messages: List[dict],
                             on_token: Optional[Callable] = None) -> str:
        """纯聊天快速通道：优先本地 Ollama，更快更省"""
        
        # 1. 尝试本地 Ollama（通常 0.1~0.5 秒）
        _result = await asyncio.to_thread(self._ollama_chat, messages)
        if not _result.get("_error"):
            content = _result.get("content", "")
            if content:
                await self._stream_text(content, on_token)
            return content
        
        # 2. 本地失败→云端 DeepSeek（带 CORE_TOOLS 让 LLM 了解自身能力）
        _result = await asyncio.to_thread(llm_call, messages, tools=CORE_TOOLS)
        if _result.get("_error"):
            return ""  # 都失败了，回退到完整 _run_tool_loop
        content = _result.get("content", "")
        if content:
            await self._stream_text(content, on_token)
        return content

    def _ollama_chat(self, messages: List[dict]) -> dict:
        """调本地 Ollama（兼容 OpenAI API），返回 {content, _error}"""
        try:
            import requests
            _body = {
                "model": "qwen2.5:1.5b",
                "messages": messages,
                "stream": False,
                "temperature": 0.7,
            }
            _resp = requests.post(
                "http://127.0.0.1:11434/v1/chat/completions",
                json=_body, timeout=10
            )
            if _resp.status_code != 200:
                return {"content": "", "tool_calls": [], "_error": f"Ollama HTTP {_resp.status_code}"}
            _data = _resp.json()
            _choice = _data["choices"][0]
            return {"content": _choice["message"].get("content", ""), "tool_calls": []}
        except Exception as e:
            return {"content": "", "tool_calls": [], "_error": str(e)}

    async def _run_tool_loop(self, messages: List[dict],
                             ws=None, manager=None,
                             allowed_tools: Optional[list] = None,
                             suppress_state: bool = False,
                             session_id: str = "",
                             on_token: Optional[Callable] = None,
                             on_step: Optional[Callable] = None,
                             timeout: int = 60) -> Tuple[str, List[dict]]:
        """
        多轮工具执行循环：
          第一步就不选工具直接回文字，直接拦截
          上一步是读工具且成功，LLM 必须继续调执行工具
        """
        events: List[dict] = []
        _pending_warning: Optional[str] = None
        _read_block_count = 0
        _error_retry_count = 0
        _WRITE_TOOLS = {"create_file", "write_file", "run_command"}
        step = 0

        # 整个工具循环硬超时，防止 LLM 死循环撑爆上下文
        _loop_start = time.time()

        while True:
            # 整体超时保护：防止 DeepSeek 慢响应 + 重试导致死循环
            if time.time() - _loop_start > timeout:
                if events:
                    return "我已尽力执行，目前已完成的操作如上所示。如果还有未完成的部分，请继续告诉我。", events
                return "请求处理超时。请分步描述，一次只提一个操作。", events

            if not suppress_state:
                await self._broadcast_state(ws, manager, "thinking")

            # 达到最大步数时暂停等待用户审批
            if step >= self.MAX_STEPS:
                if session_id:
                    await self._send_step(ws, manager, "step_limit", "approval",
                                          f"已执行{self.MAX_STEPS}步，是否继续执行？")
                    if not await self._wait_approval_async(session_id):
                        return "已暂停执行。如需继续执行，请告诉我。", events
                    step = 0
                    await self._send_step(ws, manager, "step_limit", "success", "已继续执行")
                    continue
                else:
                    return f"已达最大执行步数（{self.MAX_STEPS}步）。如需继续请告诉我。", events

            if _pending_warning:
                messages.append({"role": "system", "content": _pending_warning})
                messages.append({
                    "role": "user",
                    "content": "上面的系统消息是刚刚发生的工具执行错误。请如实告诉我出了什么问题。",
                })
                _pending_warning = None
                continue

            # 动态选择工具集：每次循环按上下文缩减工具数量
            _current_tools = _select_tools_for_context(messages, allowed_tools)
            _openai_tools = _get_openai_tools([t["name"] for t in _current_tools])

            # LLM 调用失败自动重试（最多 2 次，间隔 3s）
            response = None
            for retry in range(3):
                response = await asyncio.to_thread(llm_call, messages, _openai_tools)
                if not response.get("_error"):
                    break
                if retry < 2:
                    _safe_print(f"[agent] LLM 调用失败（{response['_error']}），{retry+1}/2 次重试...")
                    await asyncio.sleep(3)
            else:
                _error_retry_count += 1
                if _error_retry_count >= 3:
                    err_msg = f"LLM 连续 {_error_retry_count} 次调用失败（{response.get('_error','')}），终止执行"
                    _safe_print(f"[agent] {err_msg}")
                    return err_msg, events
                if step == 0 and not events:
                    return f"系统繁忙，请稍后重试。", events
                messages.append({
                    "role": "system",
                    "content": (
                        f"【系统错误】调用 LLM API 时出错：{response.get('_error','')}。"
                        "如果上一步的工具执行成功了，你就直接引用工具返回的结果回复用户。"
                        "如果上一步的工具也没跑成功，如实告诉用户当前无法继续执行。"
                        "不准假装执行成功、不准编造结果。"
                    ),
                })
                messages.append({
                    "role": "user",
                    "content": "上面的系统消息是 LLM API 调用出错。请如实告诉我当前情况。",
                })
                _pending_warning = None
                continue

            # LLM 调用成功，重置连续失败计数
            _error_retry_count = 0

            content_preview = (response.get("content") or "")[:80]
            tcs = response.get("tool_calls")
            if tcs:
                tc_names = [(tc["name"], str(tc.get("arguments", {}))[:60]) for tc in tcs]
                _safe_print(f"[agent/debug] step={step} tool_calls: {tc_names}")
                # 有 tool_calls 时 count 重置为 0
                _no_tool_text_count = 0
            else:
                _safe_print(f"[agent/debug] step={step} text: {content_preview}")
                _no_tool_text_count = getattr(self, '_no_tool_text_count', 0) + 1
                self._no_tool_text_count = _no_tool_text_count
                # 连续3次纯文字回复直接退出，防止 LLM 死循环
                if _no_tool_text_count >= 3:
                    reply = response.get("content", "")
                    await self._stream_text(reply, on_token)
                    return reply, events
                # 第一次没选工具时，明确提醒可用工具（避免 LLM 一直纯文字回复）
                if _no_tool_text_count == 1:
                    _tool_names = [t["name"] for t in _current_tools]
                    messages.append({"role": "system", "content": (
                        f"【可用工具】{_tool_names}。如果用户的操作需要这些工具，请用 function calling 调它们。"
                        "如果不需要，直接文字回复即可。"
                    )})
                continue

            tool_calls = response.get("tool_calls", [])

            if not tool_calls:
                reply = response.get("content", "")

                # 规则2：读后必写（结构规则，不依赖关键词）
                # knowledge_search 排除在外——搜索后应总结汇报，不是写文件
                _READ_TOOLS = {"read_file", "read_url", "recognize_image"}
                if events:
                    _last = events[-1]
                    _tool = _last.get("tool", "")
                    _ok = _last.get("success", False)
                    if _tool in _READ_TOOLS and _ok:
                        _read_block_count += 1
                        if _read_block_count >= 3:
                            await self._stream_text(reply, on_token)
                            return reply, events
                        messages.append({
                            "role": "system",
                            "content": "【系统检测】你刚读了文件/知识但还没执行操作。现在必须调 write_file/create_file/run_command 等工具去实际执行，不准打字结束对话。如果确实不需要修改，请再次确认（连续 3 次确认后自动退出）。",
                        })
                        continue

                # 规则3：写/创建文件后不准停，必须继续做下一步
                if events:
                    _write_last = events[-1]
                    _write_tool = _write_last.get("tool", "")
                    _write_ok = _write_last.get("success", False)
                    if _write_tool in _WRITE_TOOLS and _write_ok:
                        _write_count_in_chain = sum(1 for e in events[-5:] if e.get("tool") in _WRITE_TOOLS and e.get("success"))
                        if _write_count_in_chain >= 2:  # 已经写过至少2次还停？
                            messages.append({
                                "role": "system",
                                "content": "【系统检测】你正在执行多步骤任务，创建/修改文件后不要停下来等确认。直接选下一个工具继续执行，全部任务完成后再一次性汇报结果。不准打字确认、不准等用户说'继续'。",
                            })
                            continue

                # 规则4：工具报错后不准直接停，必须尝试其他方式或问用户
                if events:
                    _has_err = not events[-1].get("success", True)
                    if not _has_err:
                        _has_err = bool(events[-1].get("error"))
                else:
                    _has_err = False
                if not _has_err:
                    _has_err = any(e.get("error") for e in events[-3:])

                if _has_err:
                    _error_retry_count += 1
                    if _error_retry_count < 3:
                        messages.append({
                            "role": "system",
                            "content": "【系统检测】刚才的工具执行出错了，你不能就这样停。要么换一种方式重新尝试，要么告诉用户出了什么问题、询问用户希望你怎么处理。不准假装成功、不准直接结束。",
                        })
                        continue

                # 规则4：检测假执行——嘴上说做了但没调工具
                # 豁免：如果上一步刚成功执行了同类型工具，不触发假执行检测
                _last_event = events[-1] if events else None
                claim = _is_fake_file_claim(reply)
                if claim:
                    if _last_event and _last_event.get("tool") == claim and _last_event.get("success"):
                        # 刚成功执行完，LLM 只是描述结果，不是伪造
                        await self._stream_text(reply, on_token)
                        return reply, events
                    _TASK_TOOLS = {"task_create", "task_update", "task_list", "create_plan"}
                    _DISPATCH_TOOLS = {"dispatch_task_team", "dispatch_to_agent"}
                    if claim in _TASK_TOOLS:
                        _hint = "task_create / task_update / task_list / create_plan"
                    elif claim in _DISPATCH_TOOLS:
                        _hint = "dispatch_task_team / dispatch_to_agent"
                    else:
                        _hint = "write_file / create_file / 执行命令"
                    messages.append({
                        "role": "system",
                        "content": f"【系统检测】你说你\"{claim}\"了，但你没有调对应的工具去真正执行。不准光说不做——现在直接调 {_hint} 去实际执行。",
                    })
                    continue

                await self._stream_text(reply, on_token)
                return reply, events

            # 插入 assistant 消息（含 tool_calls）
            _content = response.get("content", "") or ""
            if tool_calls and _content:
                _content = ""  # 调工具时的解释文字会污染历史，清空（结构性修复）
            assistant_msg = {"role": "assistant", "content": _content}
            assistant_msg["tool_calls"] = []
            for tc in tool_calls:
                assistant_msg["tool_calls"].append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                    },
                })
            messages.append(assistant_msg)

            for tc in tool_calls:
                name = tc["name"]
                args = tc.get("arguments", {})

                reason = _safety_check_tool_params(args)
                if reason:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps({"error": reason}, ensure_ascii=False),
                    })
                    events.append({"step": step, "tool": name, "error": reason})
                    await self._send_step(ws, manager, name, "error", reason)
                    continue

                # 提取工具输入参数用于前端展示
                tool_input = _format_tool_input(name, args)

                await self._send_step(ws, manager, name, "running",
                                      f"正在执行 {name}", input_hint=tool_input)

                start = time.time()
                try:
                    result = await self._execute_tool(name, args, ws, manager, on_step=on_step)
                except PermissionError as e:
                    result = {"error": f"沙箱权限拒绝: {e}"}
                except Exception as e:
                    result = {"error": f"工具执行异常: {type(e).__name__}: {e}"}
                elapsed_ms = round((time.time() - start) * 1000, 1)

                if name == "create_file" and "error" not in result and result.get("path"):
                    try:
                        read_back = await asyncio.to_thread(sandbox_execute, "read_file", {"path": result["path"]})
                    except Exception:
                        read_back = {"error": "读回验证失败"}
                    if "error" not in read_back:
                        result["content"] = read_back.get("content", "")
                        result["size"] = read_back.get("size", 0)
                        actual_content = result["content"]
                        result["_actual_preview"] = actual_content[:300] + ("..." if len(actual_content) > 300 else "")

                if name == "write_file" and "error" not in result and result.get("path"):
                    read_back = await asyncio.to_thread(sandbox_execute, "read_file", {"path": result["path"]})
                    if "error" not in read_back:
                        actual_content = read_back.get("content", "")
                        result["_actual_preview"] = actual_content[:300] + ("..." if len(actual_content) > 300 else "")


                if name == "run_command" and "error" not in result:
                    result["_actual_preview"] = (result.get("output", "")[:300] +
                        f" (returncode={result.get('returncode', -1)})")

                if name == "knowledge_search" and "error" not in result:
                    direct_content = result.get("_direct_reply", "")
                    prompt = result.get("_prompt", "")
                    if direct_content:
                        result["_formatted"] = direct_content + "\n\n" + prompt
                        for k in ["_direct_reply", "_prompt"]:
                            result.pop(k, None)

                summary = result.get("error") or result.get("summary", "") or json.dumps(result, ensure_ascii=False)[:100]
                safe_summary = summary[:120]
                _safe_print(f"[agent] 步骤{step} {name} ({elapsed_ms}ms): {safe_summary}")

                tool_content = json.dumps(result, ensure_ascii=False)
                if name == "knowledge_search" and result.get("_formatted"):
                    tool_content = result["_formatted"]

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_content,
                })
                events.append({
                    "step": step,
                    "tool": name,
                    "success": "error" not in result,
                    "summary": summary,
                    "time_ms": elapsed_ms,
                })

                if result.get("_action") == "start_meeting":
                    self._started_meeting = {
                        "topic": result.get("topic", ""),
                        "session_id": result.get("session_id", ""),
                    }
                    reply = result.get("_direct_reply", f"✅ 已发起会议讨论")
                    await self._stream_text(reply, on_token)
                    return reply, events

                if result.get("needs_approval"):
                    status = "approval"
                elif "error" in result:
                    status = "error"
                else:
                    status = "success"
                await self._send_step(ws, manager, name, status, summary,
                    extra={"path": result.get("path")} if result.get("needs_approval") else None,
                    input_hint=tool_input)

                if on_step:
                    try:
                        on_step({
                            "tool": name,
                            "status": status,
                            "summary": summary[:200],
                            "time_ms": elapsed_ms,
                            "step": step,
                        })
                    except Exception:
                        pass

                if "error" in result:
                    err_msg = result["error"]
                    if name == "create_file" and "文件已存在" in err_msg:
                        _pending_warning = (
                            f"【系统警告】你刚才调用的 {name} 工具执行失败了。"
                            f"错误信息：{err_msg}。"
                            f"文件已存在时你不能直接覆盖。请先 read_file 查看该文件的现有内容，"
                            f"自己判断：如果内容是无用测试代码就 write_file 覆盖；"
                            f"如果是有价值的内容就问用户是否要覆盖或换路径。"
                            f"不准隐瞒失败，不准反复重试同一个操作。"
                        )
                    else:
                        _pending_warning = (
                            f"【系统警告】你刚才调用的 {name} 工具执行失败了。"
                            f"错误信息：{err_msg}。"
                            f"你不能停在原地——要么换一个工具或参数重新尝试，要么问用户要怎么处理。"
                            f"不准假装成功，不准编造结果，也不准什么都不做就结束。"
                        )

            step += 1

    async def _stream_text(self, text: str, on_token: Optional[Callable]) -> None:
        """将最终回复文本通过 on_token 流式推送给前端"""
        if not on_token or not text:
            return
        chunk_size = 4
        for i in range(0, len(text), chunk_size):
            await on_token(text[i:i+chunk_size])
            await asyncio.sleep(0.006)

    def _load_team_template_roles(self, team_type: str) -> dict:
        """从 data/team_templates.json 加载指定模板的角色列表"""
        import os as _os
        import json as _json
        _TEMPLATES_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "data", "team_templates.json")
        _TYPE_TO_TEMPLATE = {
            "dev": "general_dev", "research": "research", "writing": "writing",
            "ops": "general_dev", "general": "general_dev",
            "frontend": "frontend", "backend": "backend", "fullstack": "fullstack",
        }
        template_id = _TYPE_TO_TEMPLATE.get(team_type, "general_dev")
        try:
            if _os.path.exists(_TEMPLATES_PATH):
                with open(_TEMPLATES_PATH, "r", encoding="utf-8") as _f:
                    _data = _json.load(_f)
                for _t in _data.get("templates", []):
                    if _t["id"] == template_id:
                        roles = _t.get("roles", [])
                        if roles:
                            return {r["name"]: r for r in roles}
        except Exception as _e:
            _safe_print(f"[agent] 加载团队模板失败: {_e}")
        # 降级：硬编码默认角色
        return {
            "research": {"name": "research", "label": "研究员", "prompt": "你是一个研究员，负责信息调研和分析。使用 read_url、read_file 查找资料，整理分析结果并汇报。", "tools": ["read_file", "read_url", "knowledge_search", "get_current_time"]},
            "dev": {"name": "dev", "label": "工程师", "prompt": "你是一个软件工程师，负责代码开发和实现。使用 read_file 了解现有代码，write_file/create_file/run_command 实现功能。", "tools": ["read_file", "write_file", "create_file", "run_command", "read_url", "get_current_time"]},
            "review": {"name": "review", "label": "审查员", "prompt": "你是一个代码/内容审查员，负责质量检查。用 read_file 阅读代码，检查问题并给出改进建议。", "tools": ["read_file", "read_url", "get_current_time"]},
            "writing": {"name": "writing", "label": "内容创作者", "prompt": "你是一个内容创作者，负责文档编写。用 read_file 了解背景，write_file/create_file 撰写内容。", "tools": ["read_file", "write_file", "create_file", "read_url", "get_current_time"]},
        }

    async def _execute_tool(self, name: str, args: dict,
                             ws=None, manager=None,
                             on_step: Optional[Callable] = None) -> dict:
        if name == "create_file":
            result = await asyncio.to_thread(sandbox_execute, name, args)
            if result.get("content"):
                content = result["content"]
                result["_actual_preview"] = content[:200] + ("..." if len(content) > 200 else "")
            return result

        if name in ("read_file", "write_file"):
            return await asyncio.to_thread(sandbox_execute, name, args)

        if name == "read_url":
            return await asyncio.to_thread(
                sandbox_execute, "network_access",
                {"url": args.get("url", ""), "method": "GET"},
            )

        if name == "run_command":
            return await asyncio.to_thread(sandbox_execute, name, args)

        if name == "get_current_time":
            return _get_current_time()

        if name == "query_weather":
            return await asyncio.to_thread(_query_weather, args.get("city", ""))

        if name == "start_meeting":
            topic = args.get("topic", "")
            try:
                from core.agent_registry import get_registry
                from core.consultation import start_consultation_impl
                registry = get_registry()
                agents = registry.list_all()
                if not agents:
                    return {"error": "没有可用的 Agent"}
                result = start_consultation_impl(topic, [a.id for a in agents], max_rounds=2)
                return {
                    "topic": topic,
                    "session_id": result.get("session_id"),
                    "_action": "start_meeting",
                    "_direct_reply": f"✅ 已发起会议讨论「{topic}」",
                }
            except Exception as e:
                import traceback
                return {"error": f"发起会议失败: {e}"}

        if name == "recognize_image":
            return await asyncio.to_thread(
                _recognize_image, args.get("description", ""), self._current_images,
            )

        if name == "create_project":
            return await asyncio.to_thread(
                _create_project, args.get("root", "project"), args.get("files", "[]"),
            )

        if name == "python_toolkit":
            return await asyncio.to_thread(
                _run_python_toolkit,
                args.get("module", ""),
                args.get("kwargs", "{}"),
            )

        if name == "debug_check":
            return await asyncio.to_thread(run_debug_check, args)

        if name == "api_tool":
            return await asyncio.to_thread(run_api_tool, args)

        if name == "browser_do":
            return await run_browser(
                action=args.get("action", "open"),
                url=args.get("url", ""),
                query=args.get("query", ""),
                selector=args.get("selector", ""),
            )

        if name == "knowledge_search":
            result = query_knowledge(
                query=args.get("query", ""),
                top_k=int(args.get("top_k", 5)),
            )
            if result.get("results") and result["summary"]:
                items = result["results"]
                formatted = f"\n\n{result['summary']}\n"
                for i, item in enumerate(items, 1):
                    src = item["source"][:60]
                    title = item["title"][:60]
                    snippet = item["content"][:300]
                    formatted += f"\n--- #{i} [{src}] {title} ---\n{snippet}\n"
                result["_direct_reply"] = f"知识库中找到了以下相关内容：{formatted}"
                result["_prompt"] = "以上是知识库检索结果，请直接引用这些内容来回答用户的问题，不要再试图执行其他命令或工具。"
            return result

        if name == "template_use":
            action = args.get("action", "list")
            if action == "list":
                return list_templates(category=args.get("category", ""))
            elif action == "apply":
                params_str = args.get("params", "{}")
                try:
                    params = json.loads(params_str)
                except json.JSONDecodeError:
                    params = {}
                return apply_template(args.get("name", ""), params)
            return {"error": f"未知模板操作: {action}"}

        # ── 图片生成 ────────────────────────────────
        if name == "generate_image":
            return await asyncio.to_thread(
                _generate_image,
                args.get("prompt", ""),
                args.get("size", "1024x1024"),
            )

        # ── 任务管理 ────────────────────────────────
        if name == "task_create":
            title = args.get("title", "").strip()
            if not title:
                return {"error": "任务标题不能为空"}
            task = self.task_store.create(
                title=title,
                description=args.get("description", ""),
                status=args.get("status", "pending"),
            )
            return {"ok": True, "task": task, "summary": f"已创建任务 [{task['id']}] {title}"}

        if name == "task_update":
            tid = args.get("task_id", "").strip()
            if not tid:
                return {"error": "task_id 不能为空"}
            kwargs = {}
            for k in ("title", "description", "status"):
                if k in args and args[k]:
                    kwargs[k] = args[k]
            task = self.task_store.update(tid, **kwargs)
            if not task:
                return {"error": f"任务 {tid} 不存在"}
            return {"ok": True, "task": task, "summary": f"任务 [{tid}] 已更新: {task['status']}"}

        if name == "task_list":
            status_filter = args.get("status", "")
            tasks = self.task_store.list(status_filter)
            if not tasks:
                return {"summary": "当前没有任务", "tasks": []}
            lines = [f"[{t['id']}] [{t['status']}] {t['title']}" for t in tasks]
            return {"tasks": tasks, "summary": f"共 {len(tasks)} 个任务\n" + "\n".join(lines)}

        # ── 团队调度 ────────────────────────────────
        if name == "dispatch_task_team":
            _safe_print(f"[agent] dispatch_task_team 开始, args keys={list(args.keys())}")
            try:
                task_id = args.get("task_id", "").strip()
                if not task_id:
                    return {"error": "task_id 不能为空"}
                all_tasks = self.task_store.list()
                task = None
                for t in all_tasks:
                    if t["id"] == task_id:
                        task = t
                        break
                if not task:
                    return {"error": f"任务 {task_id} 不存在"}
                team_type = args.get("team_type", "").strip() or ""
                if not team_type or team_type not in ("dev", "research", "writing", "ops", "frontend", "backend", "fullstack", "general"):
                    team_type = classify_task(task["title"], task.get("description", ""))
                _team_config = get_team_config(team_type) or get_team_config("general")
                team_name = _team_config.get("name", "执行团队") if _team_config else "执行团队"
                self._team_roles = self._load_team_template_roles(team_type)
                role_lines = []
                for rname, rdef in self._team_roles.items():
                    tools_str = ", ".join(rdef["tools"])
                    role_lines.append(f"- {rname}: {rdef['label']}（工具: {tools_str}）")
                roles_text = "\n".join(role_lines)
                lead_prompt = build_lead_system_prompt(team_type, task)
                _safe_print(f"[agent] lead_prompt type={type(lead_prompt).__name__}, repr={repr(lead_prompt)[:60]}")
                suffix = (
                    "\n\n【多 Agent 协作规则】"
                    "你必须将任务拆解为多个子任务，用 dispatch_to_agent 分配给专业 Agent。\n\n"
                    "可用角色：\n"
                    f"{roles_text}\n\n"
                    "调度规则：\n"
                    "1. 独立任务必须并行发 dispatch_to_agent，不要串行等一个完成再发下一个\n"
                    "2. 调研类任务（research）中，禁止搜索无关知识库，优先 read_url 读官方文档和现有源码\n"
                    "3. 给子 Agent 的 task 参数写清楚目标，context 参数必须传文件路径和项目背景\n"
                    "4. 所有子 Agent 都派发完后，用 task_update 把主任务标记为 completed\n"
                    "5. 你不准自己执行 read/write/create/run 工具，只准用 dispatch_to_agent 和 task_* 工具"
                )
                lead_prompt += suffix
                lead_tools = ["dispatch_to_agent", "task_update", "task_list", "task_create", "get_current_time"]
                self.task_store.update(task_id, status="in_progress")
                lead_messages = [{"role": "system", "content": lead_prompt}]
                task_context = self.task_store.get_context()
                if task_context:
                    lead_messages[0]["content"] += "\n" + task_context
                lead_messages.append({
                    "role": "user",
                    "content": f"请执行任务「{task['title']}」（{task_id}）。\n\n要求：\n1. 先分析任务，拆成多个独立子任务\n2. 用 dispatch_to_agent 并行派发给专业 Agent（不要串行等待）\n3. context 参数必须传相关文件路径和项目背景，让子 Agent 知道读什么文件\n4. 你只准用 dispatch_to_agent 和 task_* 工具，不准自己读写文件\n5. 所有子任务派发完后，用 task_update 把状态改为 completed"
                })
                _safe_print(f"[agent] dispatch_task_team 开始执行 team_type={team_type} tools={lead_tools}")
                team_reply, team_events = await self._run_tool_loop(
                    lead_messages, ws, manager,
                    allowed_tools=lead_tools,
                    suppress_state=True,
                    on_step=on_step,
                    timeout=300,
                )
                updated = self.task_store.list()
                task_final = next((t for t in updated if t["id"] == task_id), task)
                is_done = task_final.get("status") == "completed"
                return {
                    "ok": True if is_done else False,
                    "team": team_name,
                    "task_id": task_id,
                    "summary": (
                        f"{team_name}已完成任务「{task['title']}」"
                        if is_done else
                        f"{team_name}执行任务「{task['title']}」未标记完成，当前状态：{task_final['status']}"
                    ),
                    "task_status": task_final["status"],
                    "team_reply": team_reply,
                    "_events": team_events,
                }
            except Exception as e:
                import traceback as _tb
                _tb.print_exc()
                task_desc = (task or {}).get("description", "") or ""
                self.task_store.update(task_id if 'task_id' in dir() else 'unknown', status="pending",
                    description=task_desc + f" [团队执行异常：{type(e).__name__}，已重置为待办]")
                _safe_print(f"[agent] dispatch_task_team 异常: {type(e).__name__}: {e}")
                return {
                    "ok": False,
                    "team": team_name if 'team_name' in dir() else "执行团队",
                    "task_id": task_id if 'task_id' in dir() else 'unknown',
                    "summary": f"执行出错（{type(e).__name__}），任务已重置为待办",
                    "team_reply": "",
                    "_events": [],
                }
        if name == "dispatch_to_agent":
            agent_name = args.get("agent_name", "").strip()
            sub_task = args.get("task", "").strip()
            context = args.get("context", "")
            if not agent_name or not sub_task:
                return {"error": "agent_name 和 task 不能为空"}

            AGENT_ROLES = self._team_roles or self._load_team_template_roles("general_dev")

            role = AGENT_ROLES.get(agent_name)
            if not role:
                return {"error": f"未知 Agent 类型: {agent_name}，可选: {', '.join(AGENT_ROLES.keys())}"}

            system = role["prompt"]
            if context:
                system += f"\n\n背景信息:\n{context}"

            # 自动注入项目核心文件结构（让子 Agent 知道该读什么文件）
            system += (
                "\n\n【项目文件指引】\n"
                "核心源码目录：\n"
                "- core/agent.py — 主 Agent 逻辑（工具循环、团队调度）\n"
                "- core/routes_api.py — HTTP API 路由（consultation / dispatch 接口）\n"
                "- core/executor.py — 沙箱执行器（文件读写、命令执行）\n"
                "- core/llm.py — LLM 调用（模型切换、降级）\n"
                "- core/consultation.py — 会议协商管理\n"
                "- core/ws.py — WebSocket 处理器\n"
                "- core/tools_ext.py — 扩展工具（knowledge_search 等）\n"
                "- core/config.py — 配置项\n"
                "前端：desktop/src/index.html — 主前端页面（含聊天、会议室、工作室）\n"
                "数据：data/consultation/ — 会议记录 JSON\n"
                "启动：main.py — 服务入口\n\n"
                "read_file 用相对路径即可，如 core/agent.py"
            )

            agent_messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": sub_task},
            ]

            try:
                reply, events = await self._run_tool_loop(
                    agent_messages, ws, manager,
                    allowed_tools=role["tools"],
                    suppress_state=True,
                    on_step=on_step,
                    timeout=180,
                )
                return {
                    "ok": True,
                    "agent": agent_name,
                    "agent_label": role["label"],
                    "reply": reply,
                    "summary": f"{role['label']}完成任务：{reply[:200]}",
                    "_events": events,
                }
            except Exception as e:
                _safe_print(f"[agent] dispatch_to_agent ({agent_name}) 异常: {type(e).__name__}: {e}")
                return {
                    "ok": False,
                    "agent": agent_name,
                    "agent_label": role["label"],
                    "error": str(e),
                    "summary": f"{role['label']}执行出错（{type(e).__name__}）",
                }

        # ── 制定计划 ────────────────────────────────
        if name == "create_plan":
            goal = args.get("goal", "").strip()
            steps_str = args.get("steps", "[]")
            try:
                steps = json.loads(steps_str) if isinstance(steps_str, str) else steps_str
            except json.JSONDecodeError:
                return {"error": "steps 格式无效，需要 JSON 数组"}
            if not goal:
                return {"error": "目标不能为空"}
            if not steps or not isinstance(steps, list):
                return {"error": "至少需要一个步骤"}
            # 为每一步创建任务
            created = []
            for s in steps:
                step_num = s.get("step", 1)
                action_desc = s.get("action", f"步骤{step_num}")
                task = self.task_store.create(
                    title=f"{goal[:40]} - {action_desc[:30]}",
                    description=s.get("expected", ""),
                    status="pending",
                )
                created.append(task)
            return {
                "ok": True,
                "goal": goal,
                "total_steps": len(created),
                "tasks": created,
                "summary": f"已为「{goal[:40]}」制定 {len(created)} 步计划。按步骤逐一执行，每完成一步用 task_update 标记为 completed。",
            }

        # ── 查询悟道内部状态 ────────────────────────────
        if name == "query_wudao_state":
            keyword = args.get("keyword", "").strip()
            scope = args.get("scope", "all").strip()
            return _query_wudao_state(keyword, scope)

        return {"error": f"未知工具: {name}"}

    def _process_tags(self, reply: str, user_input: str,
                      session_id: str) -> Tuple[str, Optional[dict]]:
        consult_info = None

        mem_info = parse_memorize_tag(reply)
        if mem_info and self.memory_ml:
            safe_ctx = sanitize_context(user_input)[:200]
            self.memory_ml.add_fact(
                mem_info["content"],
                mem_info.get("category", "general"),
                context={"user": user_input, "assistant": strip_memorize_tag(reply)[:200]},
            )
        reply = strip_memorize_tag(reply)

        idea_info = parse_idea_tag(reply)
        if idea_info and self.memory_ml:
            self.memory_ml.add_idea(
                idea_info["content"],
                follow_up_days=idea_info.get("follow_up_days", 3),
                priority=idea_info.get("priority", "medium"),
            )
        reply = strip_idea_tag(reply)

        consult_info = parse_consult_tag(reply)
        if consult_info:
            agent_id_to_name = {
                "agent_engineer": "工程师",
                "agent_designer": "设计师",
                "agent_marketing": "市场分析师",
                "agent_risk": "风控官",
            }
            consult_info["agent_ids"] = consult_info["agents"]
            consult_info["agent_names"] = [
                agent_id_to_name.get(a, a) for a in consult_info["agents"]
            ]
        reply = strip_consult_tag(reply)

        return reply, consult_info

    @staticmethod
    async def _broadcast_state(ws, manager, state: str):
        if manager is None:
            return
        try:
            msg = {"type": "tentacle_state", "state": state}
            if ws is not None:
                await manager._send(ws, msg)  # 只发给发起者
            else:
                await manager.broadcast(msg)  # 没有 WS 时群发（HTTP 路径）
        except Exception:
            pass

    @staticmethod
    async def _send_step(ws, manager, name: str, status: str, detail: str, extra: dict = None, input_hint: str = ""):
        if manager is None:
            return
        try:
            label = TOOL_LABELS.get(name, name)
            msg = {
                "type": "workflow_step",
                "action": name,
                "label": label,
                "detail": str(detail)[:200],
                "input": input_hint,
                "status": status,
            }
            if extra:
                msg.update(extra)
            if ws is not None:
                await manager._send(ws, msg)  # 只发给发起者
            else:
                await manager.broadcast(msg)  # HTTP 路径广播给所有 WS 客户端
        except Exception:
            pass


def _format_tool_input(name: str, args: dict) -> str:
    """提取工具输入中的关键参数，用于前端工作流展示"""
    key_fields = {
        "read_url": "url",
        "browser_do": ["url", "query"],
        "write_file": "path",
        "create_file": "path",
        "run_command": "command",
        "knowledge_search": "query",
        "task_create": "title",
        "task_update": "task_id",
    }
    fields = key_fields.get(name)
    if not fields:
        return ""
    if isinstance(fields, str):
        val = args.get(fields, "")
    else:
        for f in fields:
            val = args.get(f, "")
            if val:
                break
        else:
            return ""
    s = str(val).strip()
    return s if len(s) <= 80 else s[:77] + "..."
def _query_wudao_state(keyword: str, scope: str = "all") -> dict:
    """查询悟道内部数据（consultation / memory / learned）"""
    import json, os
    from core.config import WUDAO_DATA as DATA_DIR

    keyword = keyword.strip().lower()
    if not keyword:
        return {"error": "关键词不能为空", "summary": "请提供搜索关键词"}
    
    results = []
    data_dir = DATA_DIR
    
    # 1. consultation 协商记录
    if scope in ("all", "consultation"):
        consult_dir = os.path.join(data_dir, "consultation")
        if os.path.isdir(consult_dir):
            for fn in sorted(os.listdir(consult_dir)):
                if not fn.endswith(".json"):
                    continue
                fp = os.path.join(consult_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    topic = data.get("topic", "")
                    summary = data.get("conclusion", {}).get("summary", "")
                    if keyword in topic.lower() or keyword in summary.lower():
                        agents = [a.get("name", "") for a in data.get("agents", [])]
                        results.append({
                            "source": "consultation",
                            "file": fn,
                            "topic": topic[:100],
                            "summary": summary[:200],
                            "agents": agents,
                            "status": data.get("status", ""),
                        })
                except Exception:
                    continue
    
    # 2. memory 记忆
    if scope in ("all", "memory"):
        for fname in ("memory_long.json", "memory_medium.json"):
            fp = os.path.join(data_dir, fname)
            if not os.path.exists(fp):
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for entry in data.get("entries", []):
                    content = entry.get("content", "")
                    if keyword in content.lower():
                        results.append({
                            "source": fname.replace(".json", ""),
                            "content": content[:200],
                            "category": entry.get("category", ""),
                            "created_at": entry.get("created_at", ""),
                        })
            except Exception:
                continue
    
    # 3. learned 今日所学
    if scope in ("all", "learned"):
        learned_dir = os.path.join(data_dir, "learned")
        if os.path.isdir(learned_dir):
            for fn in sorted(os.listdir(learned_dir)):
                if not fn.endswith(".json"):
                    continue
                fp = os.path.join(learned_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for entry in data.get("entries", []) if isinstance(data, dict) else data:
                        user = entry.get("user", "") if isinstance(entry, dict) else ""
                        if keyword in user.lower():
                            results.append({
                                "source": "learned/" + fn,
                                "user": user[:150],
                                "ts": entry.get("ts", ""),
                            })
                except Exception:
                    continue
    
    if not results:
        return {"summary": f'未找到与"{keyword}"相关的记录'}
    
    consult_count = sum(1 for r in results if r["source"] == "consultation")
    memory_count = sum(1 for r in results if "memory" in r["source"])
    learned_count = sum(1 for r in results if r["source"].startswith("learned"))
    
    summary = f"找到 {len(results)} 条相关记录（consultation {consult_count} 条，memory {memory_count} 条，learned {learned_count} 条）"
    return {"results": results, "summary": summary, "total": len(results)}


def _get_current_time() -> dict:
    from datetime import datetime
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M")
    weekday = "一二三四五六日"[now.weekday()]
    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "weekday": f"周{weekday}",
        "summary": f"当前时间：{time_str}，周{weekday}。",
    }


def _query_weather(city: str) -> dict:
    try:
        import urllib.request
        import urllib.parse
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=%C+%t+%h+%w&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "Wudao/0.7.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
            return {
                "city": city,
                "weather": text.strip(),
                "summary": f"{city} 天气: {text.strip()}",
            }
    except Exception as e:
        return {"error": f"查询天气失败: {e}"}


def _recognize_image(description: str, images: Optional[List[str]] = None) -> dict:
    if not images:
        return {"error": "没有可用的图片", "summary": "没有图片可识别"}
    try:
        from core.llm import chat as llm_chat
        prompt = description or "详细描述这张图片中的内容、场景、文字等"
        text = llm_chat(prompt, images=images)
        return {"description": text, "summary": text[:100]}
    except Exception as e:
        return {"error": f"图片识别失败: {e}"}


def _create_project(root: str, files_json: str) -> dict:
    try:
        files = json.loads(files_json) if isinstance(files_json, str) else files_json
    except json.JSONDecodeError as e:
        return {"error": f"files 参数不是合法 JSON: {e}"}

    created = []
    errors = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")
        if not path:
            errors.append("某条目缺少 path")
            continue
        try:
            full_path = f"{root}/{path}" if root else path
            result = sandbox_execute("write_file", {"path": full_path, "content": content})
            if "error" in result:
                errors.append(f"{path}: {result['error']}")
            else:
                created.append(path)
        except Exception as e:
            errors.append(f"{path}: {e}")

    msg = f"创建了 {len(created)} 个文件"
    if errors:
        msg += f"，{len(errors)} 个失败"
    return {
        "root": root,
        "created_count": len(created),
        "error_count": len(errors),
        "files": created,
        "errors": errors,
        "summary": msg,
    }


def _generate_image(prompt: str, size: str = "1024x1024") -> dict:
    """调用硅基流动生成图片"""
    import json, os, time, urllib.request, urllib.error

    API_KEY = "sk-sacdhpuxtircbughhculltkekwpynzljtnnwdyomaaoehzjv"
    API_URL = "https://api.siliconflow.cn/v1/images/generations"
    MODEL = "Tongyi-MAI/Z-Image-Turbo"

    payload = {"model": MODEL, "prompt": prompt, "n": 1, "size": size}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"API 错误 ({e.code}): {body[:200]}"}
    except urllib.error.URLError as e:
        return {"error": f"网络错误: {e.reason}"}
    except Exception as e:
        return {"error": f"请求失败: {e}"}

    images = result.get("data", [])
    if not images:
        return {"error": "API 返回空结果"}
    image_url = images[0].get("url", "")
    if not image_url:
        return {"error": "API 返回无图片 URL"}

    # 下载到 static/images/
    save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images")
    os.makedirs(save_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    filename = f"img_{ts}.png"
    filepath = os.path.join(save_dir, filename)
    try:
        urllib.request.urlretrieve(image_url, filepath)
    except Exception as e:
        return {"error": f"图片下载失败: {e}"}

    return {
        "url": f"/static/images/{filename}",
        "filepath": filepath,
        "size_bytes": os.path.getsize(filepath),
        "prompt": prompt,
        "model": MODEL,
        "summary": f"图片已生成: /static/images/{filename}",
    }


def _run_python_toolkit(module: str, kwargs_json: str) -> dict:
    import sys as _sys
    from pathlib import Path
    _sandbox_dir = str(Path(__file__).resolve().parent.parent / "sandbox")
    if _sandbox_dir not in _sys.path:
        _sys.path.insert(0, _sandbox_dir)
    try:
        from python_toolkit import run as toolkit_run
        kwargs = json.loads(kwargs_json) if isinstance(kwargs_json, str) else kwargs_json
        return toolkit_run(module=module, **kwargs)
    except Exception as e:
        return {"error": f"Python 工具箱调用失败: {type(e).__name__}: {e}"}


