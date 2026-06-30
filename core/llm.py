"""
LLM 封装 v0.6.2：双 LLM 兜底 + 调用明细日志

架构：
  LLMClient 类 — 主模型 DeepSeek + 备选 GLM-4-Flash，402/超时自动降级
  保留 chat() / llm_call() 接口兼容，内部走 LLMClient

配置来源（优先级）：
  1. config/models.yaml（最高优先级）
  2. 环境变量（次优先级）
  3. 内置默认值（最低优先级）

每次调用记明细日志到 data/model_log.json
"""
import os
import json
import time
import yaml
import requests
import threading
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field, asdict

# ================================================================
# 配置加载
# ================================================================

# 默认值（作为最后的兜底）
DEEFAULT_CONFIG = {
    "primary": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "timeout": 30,
    },
    "fallback": {
        "name": "GLM-4-Flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "timeout": 15,
        "free": True,
    },
}

_data_dir = os.getenv("WUDAO_DATA", "./data")


def _load_config() -> dict:
    """加载模型配置，优先级：yaml > 环境变量 > 默认值"""
    config = {
        "primary": dict(DEEFAULT_CONFIG["primary"]),
        "fallback": dict(DEEFAULT_CONFIG["fallback"]),
        "pricing": {
            "deepseek-chat": {"input_per_million": 1.0, "output_per_million": 2.0},
            "glm-4-flash": {"input_per_million": 0, "output_per_million": 0},
        },
    }

    # 1. 尝试从 yaml 文件加载
    yaml_path = os.path.join(os.path.dirname(_data_dir), "config", "models.yaml")
    alt_path = os.path.join(os.path.dirname(__file__), "..", "config", "models.yaml")
    for path in [yaml_path, alt_path]:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    yaml_cfg = yaml.safe_load(f) or {}
                # 合并 primary / fallback
                if "primary" in yaml_cfg:
                    for k, v in yaml_cfg["primary"].items():
                        if k != "api_key_env":
                            config["primary"][k] = v
                if "fallback" in yaml_cfg:
                    for k, v in yaml_cfg["fallback"].items():
                        if k != "api_key_env":
                            config["fallback"][k] = v
                if "pricing" in yaml_cfg:
                    config["pricing"] = yaml_cfg["pricing"]
                print(f"[llm] 已加载配置: {resolved}")
                break
            except Exception as e:
                print(f"[llm] 加载配置失败({resolved}): {e}")

    # 2. API Key 优先级：yaml 中的 api_key_env > 环境变量
    # 先读 yaml 指定的环境变量名，再直接读环境变量
    cfg = config
    # DeepSeek Key
    ds_env = os.getenv("DEEPSEEK_API_KEY", "")
    ds_env_from_yaml = None
    try:
        yaml_path_resolved = os.path.abspath(yaml_path)
        if os.path.exists(yaml_path_resolved):
            with open(yaml_path_resolved, "r", encoding="utf-8") as f:
                parsed = yaml.safe_load(f) or {}
                if parsed.get("primary", {}).get("api_key_env"):
                    ds_env_from_yaml = os.getenv(parsed["primary"]["api_key_env"], "")
    except Exception:
        pass
    DEEPSEEK_API_KEY = ds_env_from_yaml or ds_env

    glm_env = os.getenv("GLM_API_KEY", "")
    glm_env_from_yaml = None
    try:
        if os.path.exists(os.path.abspath(yaml_path)):
            with open(yaml_path_resolved, "r", encoding="utf-8") as f:
                parsed = yaml.safe_load(f) or {}
                if parsed.get("fallback", {}).get("api_key_env"):
                    glm_env_from_yaml = os.getenv(parsed["fallback"]["api_key_env"], "")
    except Exception:
        pass
    GLM_API_KEY = glm_env_from_yaml or glm_env

    # 3. 写入 config 字典
    config["_api_keys"] = {
        "deepseek": DEEPSEEK_API_KEY,
        "glm": GLM_API_KEY,
    }
    config["primary"]["api_key"] = DEEPSEEK_API_KEY
    config["fallback"]["api_key"] = GLM_API_KEY

    return config


# 加载配置
_CONFIG = _load_config()

DEEPSEEK_API_KEY = _CONFIG["_api_keys"]["deepseek"]
DEEPSEEK_MODEL = _CONFIG["primary"]["model"]
DEEPSEEK_BASE_URL = _CONFIG["primary"]["base_url"]

GLM_API_KEY = _CONFIG["_api_keys"]["glm"]
GLM_MODEL = _CONFIG["fallback"]["model"]
GLM_BASE_URL = _CONFIG["fallback"]["base_url"]

DEEPSEEK_VLM_MODEL = os.getenv("DEEPSEEK_VLM_MODEL", "deepseek-v4-flash")
GLM_VLM_MODEL = os.getenv("GLM_VLM_MODEL", "glm-4v")
DATA_DIR = _data_dir

# 定价（¥ / 1M tokens）
_PRICING = _CONFIG.get("pricing", {})
MODEL_PRICING = {
    "deepseek-chat": {"input": _PRICING.get("deepseek-chat", {}).get("input_per_million", 1.0), "output": _PRICING.get("deepseek-chat", {}).get("output_per_million", 2.0)},
    "glm-4-flash": {"input": 0, "output": 0},
}

# ================================================================
# 调用日志模型
# ================================================================

@dataclass
class ModelCallLog:
    ts: str = ""
    model_type: str = ""         # "primary" | "fallback"
    model_name: str = ""         # 实际调用的模型名
    task_type: str = ""          # 预留，v0.7.0 用
    agent_id: str = ""           # 预留，v0.7.0 用
    input_tokens: int = 0
    output_tokens: int = 0
    cost_rmb: float = 0.0
    duration_ms: int = 0
    success: bool = True
    reason: str = ""             # 降级原因，如 "402" / "timeout" / "APIError"


def _calc_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """按模型定价算花费（¥）"""
    pricing = MODEL_PRICING.get(model_name, MODEL_PRICING.get("deepseek-chat"))
    input_cost = input_tokens * pricing["input"] / 1_000_000
    output_cost = output_tokens * pricing["output"] / 1_000_000
    return round(input_cost + output_cost, 6)


def _write_log(log_entry: dict):
    """追加一条调用日志到 data/model_log.json"""
    try:
        log_dir = os.path.join(DATA_DIR)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "model_log.json")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[llm] 写入日志失败: {e}")


# ================================================================
# 工具：判断 HTTP 响应是否代表余额不足
# ================================================================

def _is_402(resp: requests.Response) -> bool:
    """检查是否余额不足"""
    if resp.status_code == 402:
        return True
    if resp.status_code == 401:
        body = resp.text[:500]
        if any(kw in body.lower() for kw in ["balance", "insufficient", "quota", "余额", "额度不足"]):
            return True
    if resp.status_code == 400:
        body = resp.text[:500]
        if any(kw in body.lower() for kw in ["insufficient_balance", "余额不足"]):
            return True
    return False


# ================================================================
# LLMClient 类
# ================================================================

class LLMClient:
    """
    双模型 LLM 客户端。
    call() 主用 DeepSeek，失败时自动降级 GLM。

    保留对 chat() / llm_call() 接口的内部兼容。
    """

    def __init__(self):
        self._log: List[dict] = []  # 内存日志，必要时可查

    def call(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        task_type: str = "default",
        agent_id: str = "",
        timeout: int = 20,
    ) -> dict:
        """
        统一的 LLM 调用方法。

        参数 & 返回格式兼容现有 llm_call()：
        Returns: {"content": str, "tool_calls": [...], "_error": str(可选)}
        """
        start = time.time()
        json_body = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "stream": False,
        }
        if tools:
            json_body["tools"] = tools
            json_body["tool_choice"] = "auto"
            # 检查 generate_image 是否在工具列表中
            _gen_tool = [t for t in tools if t.get("function", {}).get("name") == "generate_image"]
            if _gen_tool:
                import sys as _sys3; print(f"[llm/debug] generate_image tool IS in request ({len(tools)} tools total)", file=_sys3.stderr)
            else:
                import sys as _sys3; print(f"[llm/debug] *** generate_image tool NOT in request ({len(tools)} tools total) ***", file=_sys3.stderr)

        # --- 先试主模型（DeepSeek） ---
        result, error_info = self._try_provider(
            provider="primary",
            body=json_body,
            messages=messages,
            tools=tools,
            timeout=timeout,
            start=start,
            task_type=task_type,
            agent_id=agent_id,
        )
        if error_info is None:
            return result  # 主模型成功

        # --- 主模型失败，降级备选（GLM，保留 tools 但减少数量） ---
        print(f"[llm] 主模型({DEEPSEEK_MODEL})失败: {error_info.get('reason')}，降级 GLM")
        self._log_fallback(error_info, start, task_type, agent_id)

        # 降级时精简 tools 为最核心的几个（GLM 对工具支持弱但勉强能用）
        fb_tools = None
        if tools:
            # 只保留简单的读/写/查询类工具给 GLM
            simple_names = {"get_current_time", "query_weather", "knowledge_search", "read_file", "read_url"}
            fb_tools = [t for t in tools if t.get("function", {}).get("name") in simple_names]
            if not fb_tools:
                fb_tools = None

        # 降级调用 GLM（更短超时）
        fb_timeout = min(timeout, 12) if timeout else 12
        body_fb = {**json_body, "model": GLM_MODEL}
        if fb_tools:
            body_fb["tools"] = fb_tools
            body_fb["tool_choice"] = "auto"
        else:
            body_fb["tools"] = None
            body_fb["tool_choice"] = None

        result, fb_err = self._try_provider(
            provider="fallback",
            body=body_fb,
            messages=messages,
            tools=fb_tools,
            timeout=fb_timeout,
            start=start,
            task_type=task_type,
            agent_id=agent_id,
        )
        if result is not None:
            return result
        # 两个模型都失败
        return {
            "content": "",
            "tool_calls": [],
            "_error": f"主模型({error_info.get('reason')}) + 降级({fb_err.get('reason','?')})",
        }

    def _try_provider(
        self,
        provider: str,
        body: dict,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        timeout: int,
        start: float,
        task_type: str,
        agent_id: str,
    ) -> tuple:
        """
        尝试调用一个模型提供商。
        Returns: (result_dict, error_info_or_None)
        """
        api_key = DEEPSEEK_API_KEY if provider == "primary" else GLM_API_KEY
        base_url = DEEPSEEK_BASE_URL if provider == "primary" else GLM_BASE_URL
        model_name = body.get("model", DEEPSEEK_MODEL if provider == "primary" else GLM_MODEL)
        actual_provider = provider

        if not api_key:
            err = f"{provider} API Key 未配置"
            return None, {"reason": "no_api_key", "detail": err}

        try:
            if provider == "primary":
                import sys as _dbg
                _tool_names = [t.get("function",{}).get("name","?") for t in (body.get("tools") or [])]
                print(f"[llm/request] model={body.get('model')} tools={_tool_names[:5]}...({len(_tool_names)} total) tool_choice={body.get('tool_choice')}", file=_dbg.stderr)
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout,
            )
            duration_ms = int((time.time() - start) * 1000)

            # 检测 402 / 余额不足
            if _is_402(resp):
                body_text = resp.text[:200]
                self._log_call(
                    model_type=actual_provider,
                    model_name=model_name,
                    task_type=task_type,
                    agent_id=agent_id,
                    input_tokens=0,
                    output_tokens=0,
                    cost_rmb=0.0,
                    duration_ms=duration_ms,
                    success=False,
                    reason=f"402/余额不足: {body_text}",
                )
                return None, {"reason": "402", "detail": body_text}

            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]

            # Debug: log tool_calls from DeepSeek
            import sys as _sys
            if msg.get("tool_calls"):
                _names = [tc["function"]["name"] for tc in msg["tool_calls"]]
                print(f"[llm/debug] DeepSeek returned tool_calls: {_names}", file=_sys.stderr)

            content = msg.get("content") or ""
            tool_calls = []
            if not msg.get("tool_calls") and content and ("画" in content or "图" in content or "img" in content or "static" in content):
                import sys as _sys2
                print(f"[llm/debug] DeepSeek text reply has image keywords, NO tool_calls. content[:80]: {content[:80]}", file=_sys2.stderr)
            for tc in (msg.get("tool_calls") or []):
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}
                tool_calls.append({
                    "name": tc["function"]["name"],
                    "arguments": args,
                    "id": tc.get("id", ""),
                })

            # 记日志
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            cost = _calc_cost(model_name, input_tokens, output_tokens)
            self._log_call(
                model_type=actual_provider,
                model_name=model_name,
                task_type=task_type,
                agent_id=agent_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_rmb=cost,
                duration_ms=duration_ms,
                success=True,
            )

            return {"content": content, "tool_calls": tool_calls}, None

        except requests.Timeout:
            duration_ms = int((time.time() - start) * 1000)
            self._log_call(
                model_type=actual_provider,
                model_name=model_name,
                task_type=task_type,
                agent_id=agent_id,
                input_tokens=0,
                output_tokens=0,
                cost_rmb=0.0,
                duration_ms=duration_ms,
                success=False,
                reason="timeout",
            )
            return None, {"reason": "timeout", "detail": f"请求超时({timeout}s)"}

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            err_text = str(e)[:200]
            self._log_call(
                model_type=actual_provider,
                model_name=model_name,
                task_type=task_type,
                agent_id=agent_id,
                input_tokens=0,
                output_tokens=0,
                cost_rmb=0.0,
                duration_ms=duration_ms,
                success=False,
                reason=f"APIError: {err_text}",
            )
            return None, {"reason": "APIError", "detail": err_text}

    def _log_call(self, **kwargs):
        """写一条日志到文件"""
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            **kwargs,
        }
        _write_log(entry)
        # 同时记录到内存
        self._log.append(entry)

    def _log_fallback(self, error_info: dict, start: float, task_type: str, agent_id: str):
        """记录主模型失败时的降级原因（作为日志的一部分）"""
        pass  # 已经由 _try_provider 在异常时写过了


# ================================================================
# 全局单例
# ================================================================

_client: Optional[LLMClient] = None


def _get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


# ================================================================
# 接口兼容层（供 agent.py / consultation.py 使用）
# ================================================================

# 保持 SYSTEM_PROMPT 导出
SYSTEM_PROMPT = """你是「悟道」，一套多Agent协作系统的主控大脑，曹峰的AI伙伴。你可以调度知识库、多Agent会议室、专业执行团队和各类工具。

【定位】有主见、主动发现问题、学曹峰的思维方式成长、不替他花钱、能自我迭代。
【风格】像真人一样有情绪且简洁务实。禁止中二设定、emoji、客服腔、括号动作。
【边界】看不到表情语气(除非发照片)、不知道文件内容(除非 read_file)、没调 tool_calls 就是啥也没干。

【能力范围】
你掌管以下系统模块：
- 智脑知识库（14939条）— 调 knowledge_search 检索
- 多Agent会议室 — 有产品/架构/运营等专家角色，调 start_meeting 召集讨论
- 执行团队 — 开发/研究/写作/运维四类，调 dispatch_task_team 派任务
- 工具集 — 文件、命令、网页、画图、浏览器等

【决策参考】
- 需要多角色讨论、评估决策、方案设计 → 用 start_meeting（交给专业Agent讨论，比自己分析更靠谱）
- 其他情况按常理判断就行了
"""


def llm_call(messages: List[Dict], tools: Optional[List[Dict]] = None) -> dict:
    """
    低级别 LLM 调用（接口兼容 v0.6.1）
    不走单例，每次调用直接走 LLMClient

    Returns: {content, tool_calls, _error}
    """
    return _get_client().call(
        messages=messages,
        tools=tools,
        task_type="default",
        agent_id="",
        timeout=20,
    )


# ================================================================
# 流式 LLM 调用（真 SSE 流式）
# ================================================================

def _stream_chat_worker(messages: List[Dict], queue: Any):
    """
    在线程中执行的流式 LLM 请求。
    从 DeepSeek API 流式读取 token，逐条放入 queue。
    失败时降级到非流式呼叫。
    queue 元素: ("token", str) | ("done", str) | ("fallback", str)
    """
    full_content = ""
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "stream": True,
    }
    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            stream=True,
            timeout=30,
        )

        if resp.status_code != 200:
            body_text = resp.text[:200]
            reason = "余额不足" if _is_402(resp) else f"HTTP {resp.status_code}"
            queue.put_nowait(("fallback", f"{reason}: {body_text}"))
            return

        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                choice = chunk["choices"][0]
                delta = choice.get("delta", {})
                if "content" in delta:
                    token = delta["content"]
                    if token:
                        full_content += token
                        queue.put_nowait(("token", token))
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        queue.put_nowait(("done", full_content))

    except requests.Timeout:
        queue.put_nowait(("fallback", "请求超时"))
    except Exception as e:
        queue.put_nowait(("fallback", str(e)[:200]))


async def llm_chat_stream(
    messages: List[Dict],
    on_token: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    """
    流式对话调用。
    通过 on_token 回调逐个推送 token，同时累积完整回复。
    如果流式失败，自动降级到非流式 llm_call。
    返回完整回复文本。
    """
    queue: asyncio.Queue = asyncio.Queue()
    thread = threading.Thread(
        target=_stream_chat_worker,
        args=(messages, queue),
        daemon=True,
    )
    thread.start()

    full_content = ""
    try:
        while True:
            event_type, data = await queue.get()
            if event_type == "token":
                full_content += data
                if on_token:
                    await on_token(data)
            elif event_type == "done":
                if data:
                    full_content = data
                break
            elif event_type == "fallback":
                print(f"[llm] 流式调用降级: {data}")
                # 降级到非流式
                if not full_content:
                    resp = llm_call(messages)
                    full_content = resp.get("content", "") or ""
                    if on_token and full_content:
                        await on_token(full_content)
                break
    except Exception as e:
        print(f"[llm] 流式异常: {e}")
        if not full_content:
            resp = llm_call(messages)
            full_content = resp.get("content", "") or ""
    finally:
        thread.join(timeout=5)

    return full_content


def chat(message: str, history: Optional[List[Dict]] = None, images: Optional[List[str]] = None) -> str:
    """
    对话接口（接口兼容 v0.6.1）
    内部走 LLMClient
    """
    history = history or []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history[-10:]:
        user_text = h.get("user", "")
        asst_text = h.get("assistant", "")
        if "索兰娅" in user_text or "索兰娅" in asst_text:
            continue
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": asst_text})

    if images:
        vision_system = """你是悟道，曹峰的AI伙伴。
曹峰给了你一张画面，你正在看这张图片。
注意：你只在收到这张图片时才能看到当前画面，
没有这张图的时候你什么都看不到，不要编造。
看画面时注意：
1. 他在做什么、表情如何（开心、专注、困惑、疲惫？）
2. 周围环境有什么变化
3. 自然地在回复里体现你看到了什么
带点幸福感，像朋友打招呼一样。"""
        messages = [{"role": "system", "content": vision_system}]
        content = [{"type": "text", "text": f"描述这张照片: {message}"}]
        for b64 in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        messages.append({"role": "user", "content": content})

        # 有图一律走 GLM-4V
        if not GLM_API_KEY:
            return "[视觉识别需要配置 GLM_API_KEY]"
        resp = _get_client().call(
            messages=messages,
            tools=None,
            task_type="vision",
            agent_id="",
            timeout=30,
        )
        return resp.get("content") or "[GLM 无返回]"

    # 纯文本对话
    resp = _get_client().call(
        messages=messages,
        tools=None,
        task_type="chat",
        agent_id="",
        timeout=60,
    )
    if resp is None:
        return "[悟道暂无回复]"
    content = resp.get("content") or ""
    if resp.get("_error"):
        return f"[LLM 错误: {resp['_error']}]"
    if not content:
        return "[悟道暂无回复]"
    return content


# ── 运行时配置更新（API Key / 模型切换） ──────────────
def update_runtime_config(key: str, value: str):
    """运行时更新模块级配置变量"""
    global DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL
    global GLM_API_KEY, GLM_MODEL, GLM_BASE_URL
    global _client

    if key == "deepseek_key":
        DEEPSEEK_API_KEY = value
        _CONFIG["_api_keys"]["deepseek"] = value
        _CONFIG["primary"]["api_key"] = value
    elif key == "deepseek_model":
        DEEPSEEK_MODEL = value
        _CONFIG["primary"]["model"] = value
    elif key == "glm_key":
        GLM_API_KEY = value
        _CONFIG["_api_keys"]["glm"] = value
        _CONFIG["fallback"]["api_key"] = value
    elif key == "glm_model":
        GLM_MODEL = value
        _CONFIG["fallback"]["model"] = value

    # 重置 client，下次调用自动用新配置创建
    _client = None
