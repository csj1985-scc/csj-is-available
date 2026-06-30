"""
WebSocket 路由 - 多模态交互 v0.3.3

事件类型（前后端约定）：
- gesture   客户端发送：识别到的手势
- presence  客户端发送：检测到人在/离开
- message   服务端推送：悟道要说的文字
- stop      服务端推送：停止说话
- learned   服务端推送：新增一条今日所学

v0.3.3 新增语音+触手联动：
- voice_start      客户端发送：空格按下，开始录音
- voice_chunk      客户端发送：录音音频数据块（base64 PCM16）
- voice_end        客户端发送：空格松开，结束录音
- tentacle_state   服务端推送：触手状态切换（listening/thinking/speaking/idle）
- partial_text     服务端推送：实时转写中间结果
"""
import json
import os
import base64
import asyncio
import re
import uuid
from datetime import datetime
from typing import Dict, Set, Optional as OptType
from fastapi import WebSocket, WebSocketDisconnect
from dotenv import load_dotenv

load_dotenv()

from core.agent import WudaoAgent
from core.realtime_voice import get_engine
from core.consultation import ConsultationSession
from core.agent_registry import get_registry as _get_registry, AgentConfig
from core.external_agent import external_agent_manager
from core.status import inject_status_into_topic
from core.usage import record as usage_record

from core.config import WUDAO_DATA as DATA_DIR

# 从全局共享状态取实例（确保 HTTP 和 WS 共用同一个 Agent）
from core.state import memory, memory_ml, learned, guard, agent

# 最新摄像头帧（base64 JPEG），有图时 LLM 调用带上
_latest_camera_frame: Optional[str] = None
_active_camera_ws: Optional[WebSocket] = None   # 最近发摄像头帧的 WS 连接
_vision_task: Optional[asyncio.Task] = None       # 主动视觉观察任务
_wakeup_task: Optional[asyncio.Task] = None        # 主动唤醒引擎
_ws_scenes: Dict[int, str] = {}                   # WS 连接 → 场景 ID

# 屏幕共享帧（base64 JPEG），与摄像头帧互不干扰
_latest_screen_frame: Optional[str] = None
_active_screen_ws: Optional[WebSocket] = None

# 当前正在处理的消息任务（按连接 ID），用于支持取消
_ws_current_tasks: Dict[int, asyncio.Task] = {}


class WSManager:
    """WebSocket 连接管理"""
    def __init__(self):
        self.active: Set[WebSocket] = set()
        self.talking: Dict[WebSocket, bool] = {}

    @property
    def connection_count(self) -> int:
        return len(self.active)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        self.talking[ws] = False
        await self._send(ws, {"type": "system", "msg": "悟道已连接"})
        await self._send(ws, {"type": "presence", "status": "online", "connections": self.connection_count})

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
        self.talking.pop(ws, None)

    async def _send(self, ws: WebSocket, data: dict):
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    async def broadcast(self, data: dict):
        for ws in list(self.active):
            await self._send(ws, data)


# 手势→语义映射
GESTURE_MAP = {
    "wave":      "暂停",
    "open_hand": "继续",
    "ok":        "总结",
    "fist":      "再见",
}

GESTURE_RESPONSE = {
    "wave":      "好，暂停。",
    "open_hand": "继续。",
    "ok":        "好，我总结一下。",
    "fist":      "再见，下次再聊。",
}


async def _broadcast_tentacle_state(ws: WebSocket, manager: WSManager, state: str):
    """发送触手状态变更给当前连接（不再广播到所有连接）"""
    await manager._send(ws, {"type": "tentacle_state", "state": state})


async def _handle_text_message(ws, manager, text, images=None, scene_id=None):
    """
    统一处理文字/语音消息：通过 WudaoAgent 本能执行链路。
    意图检测 → 预执行 → LLM（只看真实结果）→ 标签处理 → 记忆保存
    """
    try:
        # 记录 WS 路径的调用统计
        usage_record(
            item_id="ws_percept_main",
            category="perception",
            title=text[:30] if text else "(voice/image)",
        )

        result = await agent.process(text, session_id="main", images=images, ws=ws, manager=manager, scene_id=scene_id)

        # 检查是否被取消（CancelledError 会在 await 中抛出，但 agent.process 可能吞掉）
        ws_id = id(ws)
        task = _ws_current_tasks.get(ws_id)
        if task and task.cancelled():
            return

        if result.safety_blocked:
            await manager._send(ws, {"type": "message", "text": result.reply, "safety_blocked": True})
            return

        usage_record(
            item_id="ws_think_main",
            category="thinking",
            title=text[:30] if text else str(result.reply)[:30],
        )

        await manager._send(ws, {"type": "message", "text": result.reply})

        # [CONSULT] 讨论启动
        if result.consult_info:
            ci = result.consult_info
            await manager._send(ws, {
                "type": "consultation_update", "status": "starting",
                "topic": ci["topic"], "agents": ci["agent_names"],
            })
            asyncio.create_task(_run_consultation(ws, manager, ci))

        await _broadcast_tentacle_state(ws, manager, "speaking")
        if result.reply:
            asyncio.create_task(_tts_and_send(ws, manager, result.reply))
        await _broadcast_tentacle_state(ws, manager, "idle")
    except asyncio.CancelledError:
        print("[_handle_text_message] 被用户取消")
        # 任务被取消，清理 ws_current_tasks
        _ws_current_tasks.pop(id(ws), None)
        raise
    finally:
        # 清理任务引用
        _ws_current_tasks.pop(id(ws), None)


async def _cleanup_ws(ws: WebSocket, manager: "WSManager", engine, close_ws: bool = False):
    """WebSocket 断开通用清理"""
    # 取消正在处理的消息任务
    ws_id = id(ws)
    task = _ws_current_tasks.pop(ws_id, None)
    if task and not task.done():
        task.cancel()
        print("[ws] 清理: 取消消息任务")
    if engine.is_recording():
        await engine.stop_recording()
    count_before = manager.connection_count
    manager.disconnect(ws)
    # 有其他人时广播连接数变化
    if count_before > 1:
        asyncio.ensure_future(manager.broadcast({"type": "presence", "status": "online", "connections": manager.connection_count}))
    global _active_camera_ws, _latest_camera_frame, _active_screen_ws, _latest_screen_frame
    if _active_camera_ws is ws:
        _active_camera_ws = None
        _latest_camera_frame = None
    if _active_screen_ws is ws:
        _active_screen_ws = None
        _latest_screen_frame = None
    if close_ws:
        try:
            await ws.close()
        except Exception:
            pass


async def ws_endpoint(ws: WebSocket):
    global _latest_camera_frame, _active_camera_ws, _vision_task
    global _latest_screen_frame, _active_screen_ws, _wakeup_task
    manager: WSManager = ws.app.state.ws_manager
    await manager.connect(ws)

    # 启动主动唤醒引擎（只启动一次，跨连接生命周期持续运行）
    if not _wakeup_task or _wakeup_task.done():
        _wakeup_task = asyncio.create_task(_proactive_wakeup_loop(manager))

    # 当前连接的语音引擎
    engine = get_engine()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager._send(ws, {"type": "error", "msg": "invalid json"})
                continue

            event_type = msg.get("type")

            # ====================================================================
            # 手势事件（已有逻辑）
            # ====================================================================
            if event_type == "gesture":
                gesture = msg.get("gesture", "")
                intent = GESTURE_MAP.get(gesture, "")
                if not intent:
                    await manager._send(ws, {
                        "type": "message",
                        "text": f"未识别手势: {gesture}"
                    })
                    continue

                if gesture == "fist":
                    await manager._send(ws, {
                        "type": "message",
                        "text": GESTURE_RESPONSE["fist"],
                        "intent": intent,
                    })
                    await ws.close()
                    break

                if gesture in ("wave", "open_hand"):
                    manager.talking[ws] = (gesture == "open_hand")
                    await manager._send(ws, {
                        "type": "state",
                        "talking": manager.talking[ws],
                        "text": GESTURE_RESPONSE[gesture],
                    })
                    continue

                if gesture == "ok":
                    summary = learned.today_summary()
                    text = (
                        f"今天学了{summary['count']}条。"
                        f"最近一条是「{summary['items'][-1]['user'] if summary['items'] else '还没聊过'}」。"
                    )
                    await manager._send(ws, {
                        "type": "message",
                        "text": text,
                        "intent": "总结",
                    })
                    continue

            # ====================================================================
            # 摄像头帧
            # ====================================================================
            elif event_type == "camera_frame":
                global _active_camera_ws, _vision_task
                b64 = msg.get("image", "")
                if b64:
                    if not _latest_camera_frame:
                        print("[ws] 收到第一帧摄像头画面，启动主动观察")
                    _latest_camera_frame = b64
                    _active_camera_ws = ws
                    if not hasattr(ws, '_camera_notified'):
                        ws._camera_notified = True
                        await manager._send(ws, {"type": "camera_status", "active": True})
                    # 启动后台主动观察（只启动一次）
                    if not _vision_task or _vision_task.done():
                        _vision_task = asyncio.create_task(_proactive_vision_loop())
                else:
                    _latest_camera_frame = None
                    _active_camera_ws = None
                    print("[ws] 摄像头已关闭")

            # ====================================================================
            # 屏幕共享帧
            # ====================================================================
            elif event_type == "screen_frame":
                global _active_screen_ws
                b64 = msg.get("image", "")
                if b64:
                    if not _latest_screen_frame:
                        print("[ws] 收到第一帧屏幕共享画面")
                    _latest_screen_frame = b64
                    _active_screen_ws = ws
                    if not hasattr(ws, '_screen_notified'):
                        ws._screen_notified = True
                        await manager._send(ws, {"type": "screen_status", "active": True})
                else:
                    _latest_screen_frame = None
                    _active_screen_ws = None
                    print("[ws] 屏幕共享已关闭")

            # ====================================================================
            # v0.3.3: 语音边录边想事件
            # ====================================================================
            elif event_type == "voice_start":
                """空格按下：开始录音 + 启动预思考"""
                session_id = msg.get("session_id", "main")
                _ws_scenes[id(ws)] = msg.get("scene_id", "") or _ws_scenes.get(id(ws), "")

                # 如果已经在录音，先结束旧的
                if engine.is_recording():
                    await engine.stop_recording()

                await engine.start_recording(
                    session_id=session_id,
                    on_state=lambda state: _broadcast_tentacle_state(ws, manager, state),
                )

            elif event_type == "voice_chunk":
                """录音数据块（base64 PCM16 16000Hz mono）"""
                chunk_b64 = msg.get("audio", "")
                if chunk_b64 and engine.is_recording():
                    try:
                        chunk = base64.b64decode(chunk_b64)
                        engine.push_audio(chunk)
                    except Exception as e:
                        print(f"[ws] voice_chunk decode error: {e}")

            elif event_type == "voice_end":
                """空格松开：结束录音，触发最终回复（后台处理，不阻塞 WS 循环）"""
                if not engine.is_recording():
                    print("[ws] voice_end 但引擎不在录音状态")
                    continue

                print("[ws] voice_end -> 后台处理...")
                ws_id = id(ws)
                old = _ws_current_tasks.get(ws_id)
                if old and not old.done():
                    old.cancel()
                _ws_current_tasks[ws_id] = asyncio.create_task(
                    _process_voice_result(ws, manager, engine)
                )

            # ====================================================================
            # 文字消息（后台任务，支持取消）
            # ====================================================================
            elif event_type == "message":
                text = msg.get("text", "").strip()
                sid = msg.get("scene_id", "") or _ws_scenes.get(id(ws), "")
                if not text and not msg.get("images"):
                    continue
                # 优先使用用户上传的图片，没有则用摄像头帧 + 屏幕帧
                uploaded = msg.get("images")
                if uploaded:
                    imgs = uploaded
                else:
                    imgs = []
                    if _latest_camera_frame:
                        imgs.append(_latest_camera_frame)
                    if _latest_screen_frame:
                        imgs.append(_latest_screen_frame)
                    if not imgs:
                        imgs = None
                # 取消旧任务（如果有正在回复的）
                ws_id = id(ws)
                old = _ws_current_tasks.get(ws_id)
                if old and not old.done():
                    old.cancel()
                # 新任务
                _ws_current_tasks[ws_id] = asyncio.create_task(
                    _handle_text_message(ws, manager, text, images=imgs, scene_id=sid)
                )

            elif event_type == "scene_activate":
                """客户端切换场景"""
                ws_id = id(ws)
                _ws_scenes[ws_id] = msg.get("scene_id", "")
                await manager._send(ws, {"type": "scene_activated", "scene_id": _ws_scenes[ws_id]})

            elif event_type == "cancel":
                """客户端取消当前正在生成的回复"""
                ws_id = id(ws)
                task = _ws_current_tasks.get(ws_id)
                if task and not task.done():
                    task.cancel()
                    print("[ws] 用户取消回复")
                await manager._send(ws, {
                    "type": "message",
                    "text": "\n\n⏹️ *已停止回复*",
                    "canceled": True,
                })
                # 如果 TTS 在播，保持静音
                await _broadcast_tentacle_state(ws, manager, "idle")

            # ====================================================================
            # 步数限制审批事件（20步后继续/取消）
            # ====================================================================
            elif event_type == "wf_step_continue":
                """用户点击继续 - 解除 _run_tool_loop 审批阻塞"""
                agent.resolve_approval("main", True)
                await manager._send(ws, {"type": "wf_step_approved", "continue": True})

            elif event_type == "wf_step_cancel":
                """用户点击取消 - 解除 _run_tool_loop 审批阻塞"""
                agent.resolve_approval("main", False)
                await manager._send(ws, {"type": "wf_step_approved", "continue": False})

            elif event_type == "ping":
                await manager._send(ws, {"type": "pong"})

            else:
                await manager._send(ws, {
                    "type": "error",
                    "msg": f"unknown event type: {event_type}"
                })

    except WebSocketDisconnect:
        await _cleanup_ws(ws, manager, engine)
    except Exception as e:
        await _cleanup_ws(ws, manager, engine, close_ws=True)
        print(f"[ws] 连接异常: {e}")


async def _tts_and_send(ws: WebSocket, manager: WSManager, text: str):
    """Generate TTS audio and push via WS to frontend"""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
        if audio_bytes:
            import base64 as b64mod
            b64 = b64mod.b64encode(audio_bytes).decode("ascii")
            await manager._send(ws, {
                "type": "tts_audio",
                "audio": b64,
            })
    except Exception as e:
        print(f"[_tts_and_send] error: {e}")


async def _process_voice_result(ws: WebSocket, manager: WSManager, engine):
    """后台处理语音结果 → 走统一 _handle_text_message（和文字同路径）"""
    try:
        result = await engine.stop_recording()
        print(f"[ws] stop_recording 完成: text={result.full_text!r}")

        if not result.full_text:
            await manager.broadcast({
                "type": "voice_result", "text": "", "reply": "",
                "thinking_time": 0,
            })
            await _broadcast_tentacle_state(ws, manager, "idle")
            return

        # 和文字走完全相同的处理路径
        imgs = [_latest_camera_frame] if _latest_camera_frame else None
        sid = _ws_scenes.get(id(ws), "")
        await _handle_text_message(ws, manager, result.full_text, images=imgs, scene_id=sid)
    except Exception as e:
        print(f"[ws] _process_voice_result 异常: {e}")


# ---- 主动视觉观察（摄像头开着时每60秒看一眼） ----
_VISION_INTERVAL = 60  # 秒

async def _proactive_vision_loop():
    """后台任务：摄像头激活时主动观察画面"""
    global _latest_camera_frame, _active_camera_ws
    print("[ws/vision] 主动观察任务启动")
    while True:
        try:
            await asyncio.sleep(_VISION_INTERVAL)
            if not _latest_camera_frame or not _active_camera_ws:
                continue
            ws = _active_camera_ws
            # 检查连接是否还在
            try:
                await ws.send_text(json.dumps({"type": "ping"}))
            except Exception:
                _active_camera_ws = None
                continue
            # 调用 GLM-4V 快速观察
            from core.llm import chat as llm_chat_v
            prompt = "快速看一眼环境，曹峰在做什么、表情如何。用一句话概括，像朋友随口说。"
            obs = llm_chat_v(prompt, images=[_latest_camera_frame])
            if obs and not obs.startswith("[GLM"):
                print(f"[ws/vision] 观察: {obs[:60]}")
                # 作为普通消息推送给前端（融入对话流）+ TTS
                mgr: WSManager = ws.app.state.ws_manager
                await mgr._send(ws, {"type": "message", "text": obs})
                asyncio.create_task(_tts_and_send(ws, mgr, obs))
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[ws/vision] 异常: {e}")


# ---- 主动唤醒引擎：到期创意/想法主动推送 ----
_WAKEUP_INTERVAL = 60  # 秒
_wakeup_notified: set = set()

async def _proactive_wakeup_loop(wake_manager: "WSManager"):
    """后台任务：每分钟检查待跟进创意，到期时主动推送通知给用户

    不会在对话进行中打扰（检测到 ws_current_tasks 活跃时跳过）。
    每通知一条就加入 _wakeup_notified 防止重复推送。
    """
    print("[wakeup] 主动唤醒引擎启动")
    global _wakeup_notified
    while True:
        try:
            await asyncio.sleep(_WAKEUP_INTERVAL)
            due = memory_ml.get_due_ideas()
            if not due:
                continue

            # 排除已通知过的
            fresh = [d for d in due if d["id"] not in _wakeup_notified]
            if not fresh:
                continue

            # 对话进行中不打扰
            if any(not t.done() for t in list(_ws_current_tasks.values())):
                continue

            top = fresh[0]
            _wakeup_notified.add(top["id"])
            # 防止集合无限膨胀
            if len(_wakeup_notified) > 500:
                _wakeup_notified = set()

            text = f"记得想做的事：{top['content']}"
            print(f"[wakeup] 推送: {text[:60]}")
            # 推送给所有活跃连接
            for ws in list(wake_manager.active):
                await wake_manager._send(ws, {"type": "wakeup", "text": text})

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[wakeup] 异常: {e}")
            await asyncio.sleep(10)


async def _run_consultation(ws: WebSocket, manager: WSManager, info: dict):
    """后台执行讨论，推送进度和结论"""
    try:
        registry = _get_registry()
        agents = []
        for aid in info["agent_ids"]:
            agent = registry.get(aid)
            if agent:
                agents.append(agent)
                continue
            # 外部 Agent 从 external_agent_manager 查找
            for ext in external_agent_manager.list_agents_with_status():
                if ext["id"] == aid:
                    agents.append(AgentConfig(
                        agent_id=aid,
                        name=ext["name"],
                        description=f"外部服务 · {ext.get('endpoint', '')}",
                        system_prompt="",
                        temperature=0.7,
                        model="external",
                        color=[0.5, 0.8, 0.7],
                        is_predefined=False,
                        agent_type="external",
                    ))
                    break

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = f"cs_{ts}_{uuid.uuid4().hex[:8]}"

        session = ConsultationSession(
            session_id=session_id,
            topic=inject_status_into_topic(info["topic"]),
            agents=agents,
            max_rounds=info["rounds"],
        )

        await manager._send(ws, {
            "type": "consultation_update",
            "status": "running",
            "topic": info["topic"],
            "agents": info["agent_names"],
            "current_round": 0,
            "total_rounds": info["rounds"],
        })

        # 逐轮执行
        for r in range(info["rounds"]):
            round_result = await session.execute_round()
            utterances = round_result.get("utterances", [])
            await manager._send(ws, {
                "type": "consultation_update",
                "status": "running",
                "topic": info["topic"],
                "agents": info["agent_names"],
                "current_round": r + 1,
                "total_rounds": info["rounds"],
                "utterances": [
                    {"agent": u["agent_name"], "content": u["content"][:120]}
                    for u in utterances
                ],
            })

        # 生成结论
        conclusion = await session.generate_conclusion(memory=memory, learned=learned)
        summary = conclusion.get("summary", "")
        disagreements = conclusion.get("disagreements", [])
        actions = conclusion.get("action_items", [])

        # 主动沉淀到长期记忆（不走中期晋升，直接保存）
        memory_ml.add_long_term(
            f"多Agent会议已完成，议题：{info['topic']}。结论摘要：{summary[:200]}",
            category="consultation",
        )

        report = f"【讨论结果】{info['topic']}\n\n"
        report += f"{summary}\n"
        if disagreements:
            report += "\n分歧点：\n"
            for d in disagreements:
                report += f"- {d}\n"
        if actions:
            report += "\n行动项：\n"
            for a in actions:
                report += f"- {a}\n"

        await manager._send(ws, {
            "type": "message",
            "text": report,
        })

        await manager._send(ws, {
            "type": "consultation_update",
            "status": "completed",
            "topic": info["topic"],
            "agents": info["agent_names"],
        })

        # 通知前端会议已完成
        await manager._send(ws, {
            "type": "consult_done",
            "topic": info["topic"],
            "summary": summary[:200],
        })

        print(f"[ws] 讨论完成: {info['topic']}")

    except Exception as e:
        print(f"[ws] 讨论异常: {e}")
        await manager._send(ws, {
            "type": "consultation_update",
            "status": "error",
            "error": str(e),
        })
