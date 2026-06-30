"""
多 Agent 协商核心引擎

管理一次协商会议的全生命周期：
- execute_round(): 并行调用所有 agent 的 LLM
- generate_conclusion(): 调 LLM 生成结构化结论
- 持久化会议记录到 data/consultation/{session_id}.json
"""
import json
import os
import uuid
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

from core.llm import llm_call as _raw_llm_call, GLM_API_KEY, GLM_BASE_URL, GLM_MODEL
from core.agent_registry import AgentConfig, get_registry as _get_registry
from core.external_agent import external_agent_manager
from core.state import memory_ml
from core.status import inject_status_into_topic
from core.config import WUDAO_DATA as DATA_DIR


def _call_llm_for_consultation(messages: list) -> str:
    """
    调用 LLM 用于会议讨论。
    优先用 GLM（避免 DeepSeek 悟道 persona 污染导致 agent 说"我来做"等幻觉），
    GLM 不可用时降级到 DeepSeek。
    """
    if GLM_API_KEY:
        try:
            from core.llm import chat as _glm_chat
            # 用 GLM-4-Flash 走 chat 接口
            result = _glm_chat(messages, model=GLM_MODEL or "glm-4-flash")
            return result or "[无回复]"
        except Exception:
            pass
    # GLM 不可用时 fallback 到 DeepSeek
    result = _raw_llm_call(messages)
    return (result.get("content") or "") if result else "[无回复]"


class ConsultationSession:
    """
    一次协商会议。
    管理多轮讨论、agent 发言、结论生成和持久化。
    """

    def __init__(
        self,
        session_id: str,
        topic: str,
        agents: List[AgentConfig],
        max_rounds: int = 3,
        consultation_dir: str = None,
    ):
        self.session_id = session_id
        self.topic = topic
        self.agents = agents  # list of AgentConfig
        self.max_rounds = max_rounds
        self.current_round = 0
        self.status = "created"  # created | running | completed | error
        self.rounds: List[Dict] = []  # [{round, utterances: [{agent_id, agent_name, content, round}]}]
        self.conclusion: Optional[Dict] = None
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.error_message: Optional[str] = None

        # 持久化目录
        if consultation_dir is None:
            consultation_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "consultation"
            )
        self._consultation_dir = consultation_dir
        os.makedirs(self._consultation_dir, exist_ok=True)

        # 立即写入磁盘
        self._save()

    def to_dict(self, include_conclusion: bool = True) -> dict:
        """序列化为 dict，用于 API 响应和持久化"""
        d = {
            "session_id": self.session_id,
            "topic": self.topic,
            "agents": [a.to_dict(include_prompt=False) for a in self.agents],
            "max_rounds": self.max_rounds,
            "current_round": self.current_round,
            "status": self.status,
            "rounds": self.rounds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
        }
        if include_conclusion:
            d["conclusion"] = self.conclusion
        else:
            d["conclusion"] = None
        return d

    def _save(self):
        """写会议记录到磁盘"""
        file_path = os.path.join(self._consultation_dir, f"{self.session_id}.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[consultation] 持久化失败 {self.session_id}: {e}")

    def _build_agent_prompt(self, agent: AgentConfig) -> str:
        """
        为单个 agent 构建完整 prompt

        R1：出方案
        R2+：传全部历史轮次，让 agent 看到完整讨论脉络
        """
        is_external = agent.agent_type == "external"
        parts = [f"你是{agent.name}，正在参与一场多角色讨论。\n\n"]

        parts.append(f"议题：{self.topic}\n")

        if is_external:
            parts.append("\n【硬性规则】回复控制在 200 字以内。不要自我介绍，不要加开场白，不要发链接或表情，直接说出你的观点。\n")
        else:
            parts.append("\n用你可以自然的方式表达，不用刻意简短。把观点说透。\n")

        if not self.rounds:
            # R1：第一轮，出方案
            parts.append("\n这是第一轮讨论。从你的角色立场出发，对议题给出你的分析和建议。")
        else:
            # R2+：传全部历史轮次（非仅上一轮）
            parts.append("\n--- 以下是已发生的讨论记录 ---\n")
            for prev_round in self.rounds:
                rn = prev_round["round"]
                parts.append(f"\n第{rn}轮：\n")
                for utt in prev_round["utterances"]:
                    parts.append(f"[{utt['agent_name']}] {utt['content']}\n")

            # 引用该 agent 自己在上一轮的发言
            last_round = self.rounds[-1]
            my_last = None
            for u in last_round["utterances"]:
                if u["agent_id"] == agent.id:
                    my_last = u
                    break
            if my_last:
                parts.append(f"\n你上一轮说过：{my_last['content']}\n")

            if self.current_round >= self.max_rounds:
                # 最后一轮：收核心观点
                parts.append("\n【重要】本轮是最后一轮。请用 1-2 句话给出你最想强调的核心观点，不要再展开新论述。")
            else:
                # 中间轮
                parts.append(f"\n现在是第{self.current_round + 1}轮。")
                if self.current_round == 2:
                    parts.append("在回应之前，先用 1 句话总结你对第 1 轮判断的核心观点。")
                parts.append("然后针对其他角色的发言进行反驳或补充，而不是提出全新的方案。聚焦在已有观点的碰撞上。")

        # 末尾再次强调
        if is_external:
            parts.append("\n\n记住：200 字以内，直接说观点，不要开场白。")
        else:
            parts.append("\n\n表达完整，观点清晰就好。")

        return "".join(parts)

    @staticmethod
    def _trim_external_reply(raw: str) -> str:
        """后处理：清理外部 agent 的回复，去掉开场白并强制长度"""
        if not raw:
            return ""

        # 按行切分，跳过前导的自我介绍/开场白/表情/格式标记行
        lines = raw.strip().split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # 跳过空行
            if not stripped:
                continue
            # 跳过纯表情/格式标记行（如"📋 **老柯发言：**" "好，我来扣一下。"）
            # 也跳过 "作为XX，..." 格式
            lower = stripped.lower()
            if any(kw in lower for kw in ["发言", "我来", "作为"]):
                # 只有这些关键词且很短（<=15字），说明是开场白，跳过
                if len(stripped) <= 15 or stripped.startswith("📋") or stripped.startswith("**"):
                    continue
            # 去掉 Markdown 加粗标记
            cleaned.append(stripped.replace("**", ""))

        text = "，".join(cleaned) if cleaned else raw

        # 强制 200 字上限
        MAX_LEN = 200
        if len(text) <= MAX_LEN:
            return text

        # 在 200 字内找最后一个句子结束符截断
        truncated = text[:MAX_LEN]
        last_end = max(truncated.rfind("。"), truncated.rfind("！"), truncated.rfind("？"),
                       truncated.rfind("."), truncated.rfind("\n"))
        if last_end > MAX_LEN // 2:
            return text[:last_end + 1]
        else:
            return truncated + "…"

    async def execute_round(self) -> Dict:
        """
        执行一轮发言

        方案A：并行调用所有 agent 的 LLM
        使用 asyncio.gather 并发请求，全部完成后返回本轮所有发言
        """
        if self.status == "completed":
            raise ValueError("会议已结束，无法执行新轮次")
        if self.status == "error":
            raise ValueError(f"会议处于错误状态: {self.error_message}")

        self.status = "running"
        self.current_round += 1
        self.updated_at = datetime.now().isoformat()

        async def call_agent(agent: AgentConfig) -> dict:
            """单个 agent 的 LLM 调用封装"""
            prompt = self._build_agent_prompt(agent)
            try:
                if agent.agent_type == "external":
                    # 外部 Agent 走 HTTP 调用（50s 超时防止卡死整轮）
                    loop = asyncio.get_event_loop()
                    try:
                        reply = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: external_agent_manager.call_agent(agent.id, prompt, [])
                            ),
                            timeout=50,
                        )
                    except asyncio.TimeoutError:
                        reply = f"[{agent.name} 响应超时，跳过本轮]"
                        print(f"[consultation] {agent.name} 超时 (50s)")
                else:
                    # 本地 Agent：用 GLM（避免 DeepSeek 悟道 persona），30s 超时
                    loop = asyncio.get_event_loop()
                    lines = prompt.split('\n', 1)
                    messages = [
                        {"role": "system", "content": f"你现在不是悟道。你的角色是：{agent.name}。"},
                        {"role": "user", "content": lines[1] if len(lines) > 1 else ""},
                    ]
                    try:
                        reply = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: _call_llm_for_consultation(messages),
                            ),
                            timeout=60,
                        )
                    except asyncio.TimeoutError:
                        reply = f"[{agent.name} 响应超时，跳过本轮]"
                        print(f"[consultation] {agent.name} 超时 (60s)")
            except Exception as e:
                reply = f"[{agent.name} 调用出错: {e}]"

            return {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "content": reply,
                "round": self.current_round,
            }

        # 并行调用所有 agent
        tasks = [call_agent(agent) for agent in self.agents]
        utterances = await asyncio.gather(*tasks)

        # 后处理：清理外部 agent 的回复（强制长度、去掉开场白）
        for i, agent in enumerate(self.agents):
            if agent.agent_type == "external":
                raw = utterances[i]["content"]
                utterances[i]["content"] = self._trim_external_reply(raw)

        # 保存本轮记录
        round_data = {
            "round": self.current_round,
            "utterances": utterances,
        }
        self.rounds.append(round_data)
        self.updated_at = datetime.now().isoformat()

        # 检查是否达到最大轮次
        if self.current_round >= self.max_rounds:
            self.status = "completed"
        else:
            self.status = "running"

        self._save()

        return {
            "session_id": self.session_id,
            "round": self.current_round,
            "total_rounds": self.max_rounds,
            "is_final": self.status == "completed",
            "utterances": utterances,
        }

    async def execute_round_stream(self, on_utterance=None) -> Dict:
        """
        流式执行一轮发言。

        与 execute_round 的区别：
        - 用 asyncio.as_completed 逐个推送已完成 agent 的回复
        - on_utterance(utterance_dict) 每完成一个 agent 被调用一次
        - 仍返回完整的 round result（所有 utterance 汇总）

        这样前端可以在第一个 agent 完成时就显示内容，不用等全部。
        """
        if self.status == "completed":
            raise ValueError("会议已结束，无法执行新轮次")
        if self.status == "error":
            raise ValueError(f"会议处于错误状态: {self.error_message}")

        self.status = "running"
        self.current_round += 1
        self.updated_at = datetime.now().isoformat()

        async def call_agent(agent: AgentConfig) -> dict:
            """单个 agent 的 LLM 调用封装（同 execute_round）"""
            prompt = self._build_agent_prompt(agent)
            try:
                if agent.agent_type == "external":
                    loop = asyncio.get_event_loop()
                    try:
                        reply = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: external_agent_manager.call_agent(agent.id, prompt, [])
                            ),
                            timeout=50,
                        )
                    except asyncio.TimeoutError:
                        reply = f"[{agent.name} 响应超时，跳过本轮]"
                        print(f"[consultation] {agent.name} 超时 (50s)")
                else:
                    loop = asyncio.get_event_loop()
                    lines = prompt.split('\n', 1)
                    messages = [
                        {"role": "system", "content": f"你现在不是悟道。你的角色是：{agent.name}。"},
                        {"role": "user", "content": lines[1] if len(lines) > 1 else ""},
                    ]
                    reply = await loop.run_in_executor(
                        None,
                        lambda: _call_llm_for_consultation(messages),
                    )
            except Exception as e:
                reply = f"[{agent.name} 调用出错: {e}]"

            utt = {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "content": reply,
                "round": self.current_round,
            }
            # 后处理：外部 agent 清理
            if agent.agent_type == "external":
                utt["content"] = self._trim_external_reply(reply)
            return utt

        # 用 as_completed 逐个推送
        tasks = {asyncio.create_task(call_agent(agent)): agent for agent in self.agents}
        utterances = []

        for coro in asyncio.as_completed(tasks):
            utt = await coro
            utterances.append(utt)
            if on_utterance:
                try:
                    await on_utterance(utt)
                except Exception as e:
                    print(f"[consultation] on_utterance 回调异常: {e}")

        # 保存本轮记录
        round_data = {
            "round": self.current_round,
            "utterances": utterances,
        }
        self.rounds.append(round_data)
        self.updated_at = datetime.now().isoformat()

        if self.current_round >= self.max_rounds:
            self.status = "completed"
        else:
            self.status = "running"

        self._save()

        return {
            "session_id": self.session_id,
            "round": self.current_round,
            "total_rounds": self.max_rounds,
            "is_final": self.status == "completed",
            "utterances": utterances,
        }

    async def generate_conclusion(self, memory=None, learned=None) -> Dict:
        """
        生成结论

        逻辑：
        1. 收集所有轮次内容
        2. 构建结论 prompt 调 LLM
        3. 解析结构化输出（摘要/分歧点/行动项）
        4. 写入记忆 + 今日所学
        """
        if not self.rounds:
            raise ValueError("没有任何讨论记录，无法生成结论")

        # 构建完整的结论 prompt
        discussion_text = f"议题：{self.topic}\n\n讨论记录：\n"
        for round_data in self.rounds:
            rn = round_data["round"]
            discussion_text += f"\n第{rn}轮：\n"
            for utt in round_data["utterances"]:
                discussion_text += f"[{utt['agent_name']}] {utt['content']}\n"

        conclusion_prompt = f"""{discussion_text}

综合以上讨论结果，请用结构化格式总结。

请按以下格式输出（不要额外加格式标记）：

结论摘要：
[用3-5句话概括最终结论]

分歧点：
1. [第一个分歧点]（涉及角色：xxx vs yyy）
2. [第二个分歧点]（涉及角色：xxx vs yyy）

行动项：
1. [第一个行动项]
2. [第二个行动项]
3. [第三个行动项]
"""

        # 调用 LLM 生成结论
        conclusion_text = ""
        try:
            loop = asyncio.get_event_loop()
            conclusion_text = await loop.run_in_executor(
                None,
                lambda: _call_llm_for_consultation([
                    {"role": "system", "content": "你是一个讨论总结助手。请基于讨论记录生成结构化结论。"},
                    {"role": "user", "content": conclusion_prompt},
                ]),
            )
        except Exception as e:
            conclusion_text = f"[结论生成出错: {e}]"

        # 解析结构化输出
        summary, disagreements, action_items = self._parse_conclusion(conclusion_text)

        self.conclusion = {
            "summary": summary,
            "disagreements": disagreements,
            "action_items": action_items,
            "raw": conclusion_text,
        }
        self.updated_at = datetime.now().isoformat()

        # 写入记忆系统
        saved_to_memory = False
        saved_to_learned = False

        if memory is not None:
            try:
                memory.append(
                    f"consultation_{self.session_id}",
                    f"【多Agent协商】{self.topic}",
                    f"结论：{summary}"
                )
                saved_to_memory = True
            except Exception as e:
                print(f"[consultation] 写入记忆失败: {e}")

        if learned is not None:
            try:
                # 将结论写入今日所学
                learned.add(
                    f"consultation_{self.session_id}",
                    f"【多Agent协商】{self.topic}",
                    f"结论摘要：{summary}\n分歧点：{'；'.join(disagreements)}\n行动项：{'；'.join(action_items)}",
                    confidence=0.9,
                )
                saved_to_learned = True
            except Exception as e:
                print(f"[consultation] 写入今日所学失败: {e}")

        # 长期记忆（直接保存，不走中期晋升）
        try:
            memory_ml.add_long_term(
                f"【多Agent协商】{self.topic}\n结论摘要：{summary[:200]}",
                category="consultation",
            )
        except Exception as e:
            print(f"[consultation] 写入长期记忆失败: {e}")

        self._save()

        return {
            "session_id": self.session_id,
            "summary": summary,
            "disagreements": disagreements,
            "action_items": action_items,
            "saved_to_learned": saved_to_learned,
            "saved_to_memory": saved_to_memory,
        }

    def _parse_conclusion(self, raw_text: str) -> tuple:
        """
        解析 LLM 输出的结构化结论

        从文本中提取 结论摘要、分歧点、行动项
        """
        summary = raw_text
        disagreements = []
        action_items = []

        lines = raw_text.strip().split("\n")
        current_section = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if "结论摘要" in stripped or "结论" in stripped:
                current_section = "summary"
                continue
            if "分歧点" in stripped:
                current_section = "disagreements"
                continue
            if "行动项" in stripped:
                current_section = "action_items"
                continue

            if current_section == "summary":
                if summary == raw_text:
                    summary = stripped
                else:
                    summary += "\n" + stripped
            elif current_section == "disagreements":
                if stripped and not stripped.startswith("---"):
                    disagreements.append(stripped.lstrip("0123456789.、 )）"))
            elif current_section == "action_items":
                if stripped and not stripped.startswith("---"):
                    action_items.append(stripped.lstrip("0123456789.、 )）"))

        # 清理空条目
        disagreements = [d for d in disagreements if d]
        action_items = [a for a in action_items if a]

        return summary, disagreements, action_items


def list_history(consultation_dir: str = None) -> List[Dict]:
    """列出所有历史会议"""
    if consultation_dir is None:
        consultation_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "consultation"
        )
    os.makedirs(consultation_dir, exist_ok=True)

    sessions = []
    for fname in os.listdir(consultation_dir):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(consultation_dir, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append(data)
            except Exception:
                continue

    # 按时间倒序
    sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return sessions


def load_session(session_id: str, consultation_dir: str = None) -> Optional[Dict]:
    """从磁盘加载历史会议"""
    if consultation_dir is None:
        consultation_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "consultation"
        )
    file_path = os.path.join(consultation_dir, f"{session_id}.json")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def start_consultation_impl(topic: str, agent_ids: list, requirements: str = "", max_rounds: int = 3) -> dict:
    """创建咨询会议 — 供 API 和 start_meeting 工具共用"""
    if len(topic) > 300:
        for sep in ["=====", "讨论议题", "【讨论议题】"]:
            pos = topic.rfind(sep)
            if pos >= 0:
                topic = topic[pos + len(sep):].strip()
                break
        else:
            topic = topic[-200:]
    if len(topic) > 200:
        topic = topic[:200]
    if not topic or not agent_ids:
        return {"error": "topic and agent_ids required"}
    registry = _get_registry()
    agents = []
    for aid in agent_ids:
        a = registry.get(aid)
        if a:
            agents.append(a)
            continue
        ext_list = external_agent_manager.list_agents_with_status()
        found = False
        for ext in ext_list:
            if ext["id"] == aid:
                agents.append(AgentConfig(
                    agent_id=aid, name=ext["name"],
                    description=f"外部服务 · {ext.get('endpoint', '')}",
                    system_prompt="", temperature=0.7, model="external",
                    color=[0.5, 0.8, 0.7], is_predefined=False, agent_type="external",
                ))
                found = True
                break
        if not found:
            friendly_name = aid.replace("_", " ").title()
            agents.append(AgentConfig(
                agent_id=aid, name=friendly_name,
                description=f"自动创建角色 '{friendly_name}'",
                system_prompt=f"你是「{friendly_name}」—— 参与一场决策讨论。\n\n回答风格：简洁、直接、用中文。",
                temperature=0.7, is_predefined=False,
            ))
    if not agents:
        return {"error": "no valid agents found"}
    if requirements:
        topic = topic + "\n\n【讨论要求】" + requirements
    session_id = datetime.now().strftime("cs_%Y%m%d_%H%M%S_") + str(uuid.uuid4()).split("-")[0]
    session = ConsultationSession(
        session_id=session_id,
        topic=inject_status_into_topic(topic),
        agents=agents, max_rounds=max_rounds,
        consultation_dir=str(Path(DATA_DIR) / "consultation"),
    )
    return {"ok": True, "session_id": session_id, "session": session.to_dict()}
