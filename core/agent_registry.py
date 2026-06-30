"""
Agent 配置管理与注册

从 data/prompts/agents/ 下加载 JSON 角色配置
支持预设4个角色和自定义创建新角色
"""
import json
import os
import copy
from typing import Dict, List, Optional
from pathlib import Path

# 自定义角色颜色池（按顺序分配）
CUSTOM_COLORS = [
    [0.678, 0.847, 0.902],  # 浅蓝
    [0.957, 0.643, 0.376],  # 杏色
    [0.557, 0.267, 0.678],  # 紫色
    [0.180, 0.800, 0.443],  # 绿色
    [0.804, 0.361, 0.361],  # 红色
    [0.529, 0.808, 0.922],  # 天蓝
]


class AgentConfig:
    """一个 Agent 角色的配置"""

    def __init__(
        self,
        agent_id: str,
        name: str,
        description: str,
        system_prompt: str,
        temperature: float = 0.7,
        model: str = "default",
        color: Optional[List[float]] = None,
        is_predefined: bool = False,
        agent_type: str = "local",
        external: Optional[dict] = None,
    ):
        self.id = agent_id
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.model = model
        self.color = color or [0.5, 0.5, 0.5]
        self.is_predefined = is_predefined
        self.agent_type = agent_type  # "local" | "external"
        self.external = external or {}  # external 配置（provider/base_url/bot_id/api_key_env）

    def to_dict(self, include_prompt: bool = False) -> dict:
        """序列化，默认不输出完整 system_prompt（列表接口节省流量）"""
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "temperature": self.temperature,
            "model": self.model,
            "color": self.color,
            "is_predefined": self.is_predefined,
            "agent_type": self.agent_type,
        }
        if include_prompt:
            d["system_prompt"] = self.system_prompt
        return d

    @staticmethod
    def from_dict(data: dict) -> "AgentConfig":
        return AgentConfig(
            agent_id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt", ""),
            temperature=data.get("temperature", 0.7),
            model=data.get("model", "default"),
            color=data.get("color", [0.5, 0.5, 0.5]),
            is_predefined=data.get("is_predefined", False),
            agent_type=data.get("agent_type", "local"),
            external=data.get("external", {}),
        )


class AgentRegistry:
    """
    Agent 注册中心
    - 启动时从 data/prompts/agents/ 加载预设 JSON
    - 支持运行时注册自定义角色
    """

    def __init__(self, agents_dir: str = None):
        self._agents: Dict[str, AgentConfig] = {}
        self._custom_color_index = 0

        # 默认 agents 目录
        if agents_dir is None:
            agents_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "prompts", "agents"
            )
        self._agents_dir = agents_dir
        os.makedirs(self._agents_dir, exist_ok=True)

        self._load_all()

    def _is_predefined_id(self, agent_id: str) -> bool:
        return agent_id in ("agent_engineer", "agent_designer", "agent_marketing", "agent_risk")

    def _load_all(self):
        """扫描 data/prompts/agents/ 下所有 JSON 加载（预设 + 自定义）"""
        if not os.path.isdir(self._agents_dir):
            return
        for fname in os.listdir(self._agents_dir):
            if not fname.endswith(".json"):
                continue
            file_path = os.path.join(self._agents_dir, fname)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                config = AgentConfig.from_dict(data)
                config.is_predefined = self._is_predefined_id(config.id)
                self._agents[config.id] = config
            except Exception as e:
                print(f"[AgentRegistry] 加载 {fname} 失败: {e}")

    def _save_custom(self, config: AgentConfig):
        """将自定义角色持久化到 JSON 文件"""
        file_path = os.path.join(self._agents_dir, f"{config.id}.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(config.to_dict(include_prompt=True), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[AgentRegistry] 持久化 {config.id} 失败: {e}")

    def delete(self, agent_id: str) -> bool:
        """删除自定义角色（预设不可删）"""
        if self._is_predefined_id(agent_id):
            return False
        if agent_id not in self._agents:
            return False
        del self._agents[agent_id]
        # 删除文件
        file_path = os.path.join(self._agents_dir, f"{agent_id}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"[AgentRegistry] 删除 {agent_id} 文件失败: {e}")
        return True

    def list_all(self) -> List[AgentConfig]:
        """返回所有可用角色（list 副本）"""
        return list(self._agents.values())

    def get(self, agent_id: str) -> Optional[AgentConfig]:
        return self._agents.get(agent_id)

    def create_custom(self, name: str, description: str, temperature: float = 0.7) -> AgentConfig:
        """
        创建自定义角色
        自动生成 ID，从颜色池分配颜色
        """
        agent_id = f"custom_{len(self._agents) + 1}_{int(__import__('time').time() * 1000)}"

        # 从颜色池分配
        color = CUSTOM_COLORS[self._custom_color_index % len(CUSTOM_COLORS)]
        self._custom_color_index += 1

        # 使用 description 作为 system_prompt 的基础，加上默认结构
        system_prompt = f"你是「{name}」—— 参与一场决策讨论。\n\n你的立场：\n{description}\n\n回答风格：简洁、直接、用中文。不啰嗦，说出你的观点即可。"

        config = AgentConfig(
            agent_id=agent_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            temperature=temperature,
            color=color,
            is_predefined=False,
        )
        self._agents[config.id] = config
        self._save_custom(config)  # 持久化到磁盘
        return config

    def update(self, agent_id: str, **kwargs) -> Optional[AgentConfig]:
        """更新自定义角色的字段（预设只允许修改 temperature/description）"""
        config = self._agents.get(agent_id)
        if not config:
            return None
        if "name" in kwargs:
            config.name = kwargs["name"]
        if "description" in kwargs:
            config.description = kwargs["description"]
        if "temperature" in kwargs:
            config.temperature = float(kwargs["temperature"])
        if "system_prompt" in kwargs:
            config.system_prompt = kwargs["system_prompt"]
        self._save_custom(config)
        return config


# ================================================================
# v0.7.0 扩展：动态加载场景里的 Agent
# ================================================================


def load_agents_from_scene(scene_config: dict) -> list:
    """
    从场景配置加载 Agent 实例（AgentConfig 列表）
    如果 agent 已经注册过，直接用；否则创建纯配置 AgentConfig。
    """
    registry = get_registry()
    agents = []
    for agent_cfg in scene_config.get("agents", []):
        agent_id = agent_cfg.get("id", "")
        class_path = agent_cfg.get("class_path", "")

        # 已注册 -> 直接用
        existing = registry.get(agent_id)
        if existing:
            agents.append(existing)
            continue

        # 尝试动态导入（静默失败则用纯配置）
        if class_path:
            try:
                module_path, class_name = class_path.rsplit(".", 1)
                import importlib
                module = importlib.import_module(module_path)
                agent_class = getattr(module, class_name)
            except Exception:
                print(f"[agent_registry] 动态加载 {class_path} 失败")  # 静默失败，用纯配置

        # 创建纯配置 AgentConfig（不从动态类实例化）
        config = AgentConfig(
            agent_id=agent_id,
            name=agent_cfg.get("role", agent_id),
            description=agent_cfg.get("role", agent_id),
            system_prompt=agent_cfg.get("system_prompt", agent_cfg.get("role", agent_id)),
            is_predefined=False,
            agent_type=agent_cfg.get("type", "local"),
            external=agent_cfg.get("external", {}),
        )
        agents.append(config)

    return agents


def register_default_agents():
    """启动时注册预设的 4 个 Agent（已有加载逻辑不变，显式调用）"""
    get_registry()
    print("[AgentRegistry] 预设 Agent 注册完成")


# 全局单例
_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """获取全局 AgentRegistry 单例"""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
