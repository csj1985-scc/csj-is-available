"""
外部 Agent 管理
读取 data/external_agent_config.json 中的配置
管理外部 agent 的鉴权状态和调用统计
"""
import os
import json
import time
from pathlib import Path
from typing import Optional, List
from threading import Lock

from core.config import WUDAO_DATA

CONFIG_FILE = Path(WUDAO_DATA) / "external_agent_config.json"
LOG_FILE = Path(WUDAO_DATA) / "external_log.json"

_lock = Lock()


class ExternalAgentManager:
    """外部 Agent 管理器 - 单例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._ensure_files()
        self._prune_log()

    def _ensure_files(self):
        if not CONFIG_FILE.exists():
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps({"agents": []}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        if not LOG_FILE.exists():
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            LOG_FILE.write_text(
                json.dumps({"calls": []}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    def _prune_log(self):
        """启动时清理日志，最多保留 500 条"""
        try:
            if LOG_FILE.exists():
                data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
                calls = data.get("calls", [])
                if len(calls) > 500:
                    data["calls"] = calls[-500:]
                    LOG_FILE.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                    print(f"[external_agent] 日志已裁剪: {len(calls)} → 500 条")
        except Exception as e:
            print(f"[external_agent] 日志裁剪失败: {e}")

    def list_agents_with_status(self) -> list:
        """返回所有外部 agent 及其鉴权状态"""
        config = self._load_config()
        log = self._load_log()
        agents = config.get("agents", [])
        result = []
        for agent in agents:
            agent_id = agent.get("id", "unknown")
            stats = self._calc_stats(agent_id, log)
            is_model = bool(agent.get("provider") or agent.get("base_url"))
            entry = {
                "id": agent_id,
                "name": agent.get("name", agent_id),
                "endpoint": agent.get("endpoint", ""),
                "auth_status": "已配" if (agent.get("api_key") if is_model else agent.get("auth_token")) else "未配",
                "connection_type": "model" if is_model else "endpoint",
                "provider": agent.get("provider", ""),
                "base_url": agent.get("base_url", ""),
                "model": agent.get("model", ""),
                "calls_total": stats["calls_total"],
                "success_count": stats["success_count"],
                "success_rate": round(stats["success_count"] / stats["calls_total"], 2) if stats["calls_total"] > 0 else 0,
                "last_error": stats["last_error"],
            }
            result.append(entry)
        return result

    def _load_config(self) -> dict:
        self._ensure_files()
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except:
            return {"agents": []}

    def _load_log(self) -> list:
        self._ensure_files()
        try:
            return json.loads(LOG_FILE.read_text(encoding="utf-8")).get("calls", [])
        except:
            return []

    def _calc_stats(self, agent_id: str, log_calls: list) -> dict:
        calls_total = 0
        success_count = 0
        last_error = ""
        for c in log_calls:
            if c.get("agent_id") == agent_id:
                calls_total += 1
                if c.get("success", False):
                    success_count += 1
                if not c.get("success") and c.get("error"):
                    last_error = c["error"]
        return {
            "calls_total": calls_total,
            "success_count": success_count,
            "last_error": last_error,
        }

    def add_agent(self, agent_id: str, name: str, endpoint: str = "", auth_token: str = "",
                  provider: str = "", base_url: str = "", model: str = "", api_key: str = "") -> dict:
        """添加外部 Agent 配置（支持 endpoint 或直接模型 API）"""
        config = self._load_config()
        agents = config.get("agents", [])

        # 检查 ID 是否已存在
        for a in agents:
            if a.get("id") == agent_id:
                return {"ok": False, "error": f"Agent ID '{agent_id}' 已存在"}

        if not endpoint and not provider:
            return {"ok": False, "error": "必须填写 Endpoint URL 或模型 API 配置"}

        agent = {
            "id": agent_id,
            "name": name,
            "endpoint": endpoint,
            "auth_token": auth_token,
        }
        if provider:
            agent["provider"] = provider
        if base_url:
            agent["base_url"] = base_url
        if model:
            agent["model"] = model
        if api_key:
            agent["api_key"] = api_key

        agents.append(agent)
        config["agents"] = agents
        with _lock:
            CONFIG_FILE.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        return {"ok": True, "agent": agent}

    def update_agent(self, agent_id: str, name: str = None, endpoint: str = None, auth_token: str = None,
                     provider: str = None, base_url: str = None, model: str = None, api_key: str = None) -> dict:
        """更新外部 Agent 配置"""
        config = self._load_config()
        agents = config.get("agents", [])
        found = None
        for a in agents:
            if a.get("id") == agent_id:
                found = a
                break
        if not found:
            return {"ok": False, "error": f"Agent '{agent_id}' 不存在"}
        if name is not None:
            found["name"] = name
        if endpoint is not None:
            found["endpoint"] = endpoint
        if auth_token is not None:
            found["auth_token"] = auth_token
        if provider is not None:
            found["provider"] = provider
        if base_url is not None:
            found["base_url"] = base_url
        if model is not None:
            found["model"] = model
        if api_key is not None:
            found["api_key"] = api_key
        config["agents"] = agents
        with _lock:
            CONFIG_FILE.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        return {"ok": True, "agent": found}

    def delete_agent(self, agent_id: str) -> dict:
        """删除外部 Agent 配置"""
        config = self._load_config()
        agents = config.get("agents", [])
        new_agents = [a for a in agents if a.get("id") != agent_id]
        if len(new_agents) == len(agents):
            return {"ok": False, "error": f"Agent '{agent_id}' 不存在"}
        config["agents"] = new_agents
        with _lock:
            CONFIG_FILE.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        return {"ok": True}

    def log_call(self, agent_id: str, endpoint: str, success: bool, error: str = ""):
        self._ensure_files()
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "agent_id": agent_id,
            "endpoint": endpoint,
            "success": success,
            "error": error,
        }
        with _lock:
            try:
                data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
            except:
                data = {"calls": []}
            data["calls"].append(entry)
            if len(data["calls"]) > 10000:
                data["calls"] = data["calls"][-10000:]
            LOG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )


    def call_agent(self, agent_id: str, prompt: str, history: list = None) -> str:
        """调用外部 Agent，返回回复文本

        支持两种模式：
        1. 直接模型 API：配了 provider/base_url 就走 LLM
        2. HTTP endpoint：否则 POST 到 endpoint
        """
        config = self._load_config()
        agent = None
        for a in config.get("agents", []):
            if a.get("id") == agent_id:
                agent = a
                break
        if not agent:
            return f"[外部 Agent '{agent_id}' 未配置]"

        # 模式 1：直接模型 API
        provider = agent.get("provider", "")
        base_url = agent.get("base_url", "")
        if provider or base_url:
            return self._call_llm_api(agent, prompt, history or [])

        # 模式 2：HTTP endpoint
        endpoint = agent.get("endpoint", "")
        auth_token = agent.get("auth_token", "")

        if not endpoint:
            return f"[外部 Agent '{agent_id}' 端点或 API 配置为空]"

        import httpx
        headers = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        payload = {
            "agent_id": agent_id,
            "prompt": prompt,
            "history": history or [],
        }

        try:
            resp = httpx.post(endpoint, json=payload, timeout=30)
            success = resp.is_success
            if success:
                try:
                    data = resp.json()
                    reply = data.get("reply") or data.get("response") or data.get("content") or data.get("text", "")
                except Exception:
                    reply = resp.text
            else:
                reply = f"[外部 Agent HTTP {resp.status_code}]"
            self.log_call(agent_id, endpoint, success, "" if success else f"HTTP {resp.status_code}")
            return reply
        except Exception as e:
            err = str(e)
            self.log_call(agent_id, endpoint, False, err)
            return f"[外部 Agent 调用出错: {err}]"

    def _call_llm_api(self, agent: dict, prompt: str, history: list) -> str:
        """通过 OpenAI 兼容 API 调用 LLM（支持 Ollama / OpenAI / 自定义）"""
        import httpx

        base_url = agent.get("base_url", "").rstrip("/")
        model = agent.get("model", "")
        api_key = agent.get("api_key", "")
        agent_id = agent.get("id", "unknown")
        agent_name = agent.get("name", agent_id)

        if not base_url:
            return f"[{agent_name} base_url 为空]"
        if not model:
            return f"[{agent_name} model 为空]"

        # 构建消息：从 prompt 首行提取角色定义作为 system message
        messages = []
        system_msg = None
        prompt_body = prompt
        parts = prompt.strip().split('\n', 1)
        first_line = parts[0]
        if first_line.startswith("你是"):
            system_msg = first_line
            prompt_body = parts[1] if len(parts) > 1 else ""

        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        for h in history:
            role = "assistant" if h.get("role") == "assistant" else "user"
            content = h.get("content", "")
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt_body})

        # OpenAI 兼容格式
        chat_url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2048,
        }

        try:
            resp = httpx.post(chat_url, json=payload, headers=headers, timeout=45)
            success = resp.is_success
            if success:
                data = resp.json()
                reply = (data.get("choices", [{}])[0]
                         .get("message", {})
                         .get("content", ""))
                if not reply:
                    reply = str(data)
            else:
                reply = f"[{agent_name} API {resp.status_code}]"
            self.log_call(agent_id, chat_url, success,
                          "" if success else f"HTTP {resp.status_code}: {resp.text[:200]}")
            return reply
        except Exception as e:
            err = str(e)
            self.log_call(agent_id, chat_url, False, err)
            return f"[{agent_name} 调用出错: {err}]"


# 全局单例
external_agent_manager = ExternalAgentManager()
