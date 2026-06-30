"""
API 路由 — 从 main.py 拆分出来
"""
import os
import json as _json
import asyncio
import uuid
import threading
from datetime import datetime
from pathlib import Path
import time
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from core.config import WUDAO_DATA as DATA_DIR, DEEPSEEK_API_KEY, GLM_API_KEY, ADMIN_TOKEN
from core.state import memory, memory_ml, learned, guard, retriever, learner, system_check, agent as _agent
from core.llm import chat as llm_chat, update_runtime_config as llm_update_config
from core.prompts import get_lib as prompts_lib
from core.usage import record as usage_record, get_brain_state
from core.health import system_check as _syscheck
from core.consultation import ConsultationSession, list_history as _list_history, load_session as _load_session, start_consultation_impl
from core.status import inject_status_into_topic
from core.agent_registry import get_registry as _get_registry, AgentConfig
from core.external_agent import external_agent_manager
from core.executor import execute as sandbox_exec
from core.scene import load_all_scenes, list_scenes_summary, create_scene
from core.agent import ProcessResult, strip_consult_tag

router = APIRouter()

# ── 任务执行进度跟踪 ────────────────────────────────────
dispatch_progress: Dict[str, dict] = {}

# ── Pydantic 模型 ─────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = ""
    session_id: str = "default"
    text: str = ""  # 兼容 WebSocket 的 text 字段
    scene_id: str = ""

    @property
    def actual_message(self) -> str:
        return self.text or self.message

class ChatResponse(BaseModel):
    reply: str
    safety_blocked: bool = False
    safety_reason: Optional[str] = None
    workflow_events: List[dict] = []
    consult_info: Optional[dict] = None
    learned_today_count: int = 0
    error_type: Optional[str] = None

class PromptApplyRequest(BaseModel):
    prompt_id: str
    variables: Dict[str, Any] = {}

class PromptFeedbackRequest(BaseModel):
    prompt_id: str
    success: bool = True
    confidence: float = 0.0

class ExecuteRequest(BaseModel):
    action: str
    params: Dict[str, Any] = {}

class ConsultationStartRequest(BaseModel):
    topic: str
    agent_ids: list
    requirements: str = ""
    max_rounds: int = 3

# ── Pydantic 模型 ─────────────────────────────────────────

@router.get("/health")
def health():
    return {
        "status": "ok",
        "name": "悟道",
        "version": "0.7.2-dev",
        "has_deepseek_key": bool(DEEPSEEK_API_KEY),
        "has_glm_key": bool(GLM_API_KEY),
        "active_model": (
            "deepseek" if DEEPSEEK_API_KEY
            else "glm" if GLM_API_KEY
            else "echo"
        ),
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, http_request: Request = None, background_tasks: BackgroundTasks = None):
    msg = req.actual_message
    ok, reason = guard.check(msg)
    if not ok:
        await asyncio.to_thread(learned.add, req.session_id, msg, f"[已拦截] {reason}")
        background_tasks.add_task(usage_record, f"safe_block_{req.session_id}", "self", f"自我保护触发：{msg[:20]}", False)
        return ChatResponse(
            reply=reason,
            safety_blocked=True,
            safety_reason=reason,
            learned_today_count=len(learned.today()),
        )

    # percept 统计异步写入，不阻塞
    background_tasks.add_task(usage_record, f"percept_{req.session_id}", "perception", msg[:30])

    # 获取 WS manager 用于广播工作流步骤到前端
    ws_manager = None
    if http_request is not None:
        try:
            ws_manager = http_request.app.state.ws_manager
        except Exception:
            pass
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            _agent.process(msg, session_id=req.session_id, ws=None, manager=ws_manager, scene_id=req.scene_id or None),
            timeout=120,
        )
    except asyncio.TimeoutError:
        print(f"[perf] /chat session={req.session_id} 处理超时 120s")
        result = ProcessResult(reply="处理超时，请重试或简化你的请求。")
    elapsed = time.time() - t0
    if elapsed > 5:
        print(f"[perf] /chat session={req.session_id} elapsed={elapsed:.1f}s reply_len={len(result.reply or '')}")
    error_type = "empty_reply" if not result.reply else None

    # thinking 统计和 system_check 放入后台任务
    background_tasks.add_task(usage_record, f"think_{req.session_id}", "thinking", msg[:30])
    if not hasattr(chat, "_call_count"):
        chat._call_count = 0
    chat._call_count += 1
    if chat._call_count % 10 == 0:
        _call_count = chat._call_count
        def _run_system_check():
            try:
                ctx = {"recent_passes": _call_count}
                check_result = system_check.run_full_check(context=ctx)
                if not check_result.get("overall_healthy"):
                    print(f"[routes_api] 自检异常: {check_result['unhealthy_modules']}")
            except Exception as e:
                print(f"[routes_api] 自检失败: {e}")
        background_tasks.add_task(_run_system_check)

    learned_today = await asyncio.to_thread(learned.today)
    return ChatResponse(
        reply=result.reply,
        safety_blocked=result.safety_blocked,
        safety_reason=result.safety_reason,
        workflow_events=result.workflow_events,
        consult_info=result.consult_info,
        error_type=error_type,
        learned_today_count=len(learned_today),
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, http_request: Request = None):
    msg = req.actual_message
    ok, reason = guard.check(msg)
    if not ok:
        learned.add(req.session_id, msg, f"[已拦截] {reason}")
        usage_record(
            item_id=f"safe_block_{req.session_id}",
            category="self",
            title=f"自我保护触发：{msg[:20]}",
            success=False,
        )
        async def _blocked():
            yield f"data: {_json.dumps({'token': reason})}\n\n"
            yield f"data: {_json.dumps({'done': True, 'blocked': True})}\n\n"
        return StreamingResponse(_blocked(), media_type="text/event-stream")

    usage_record(
        item_id=f"percept_{req.session_id}",
        category="perception",
        title=msg[:30],
    )

    ws_manager = None
    if http_request is not None:
        try:
            ws_manager = http_request.app.state.ws_manager
        except Exception:
            pass

    async def _agent_stream():
        # 用队列桥接 on_token 回调和 SSE 生成器
        token_queue = asyncio.Queue()
        SSE_TIMEOUT = 120  # 整体 SSE 超时（agent 内部有更精细的超时控制）

        async def on_token(token: str):
            token_queue.put_nowait(token)

        async def _run_process():
            try:
                result = await _agent.process(
                    msg, session_id=req.session_id, ws=None, manager=ws_manager,
                    on_token=on_token, scene_id=req.scene_id or None,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[stream] 处理异常: {e}")
                return ProcessResult(reply=f"处理出错: {e}")
            finally:
                token_queue.put_nowait(None)  # 结束哨兵

        process_task = asyncio.create_task(_run_process())

        reply_parts = []
        deadline = time.monotonic() + SSE_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield f"data: {_json.dumps({'token': '系统繁忙，请稍后重试。'})}\n\n"
                yield f"data: {_json.dumps({'done': True, 'timeout': True})}\n\n"
                process_task.cancel()
                return
            try:
                token = await asyncio.wait_for(token_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                yield f"data: {_json.dumps({'token': '系统繁忙，请稍后重试。'})}\n\n"
                yield f"data: {_json.dumps({'done': True, 'timeout': True})}\n\n"
                process_task.cancel()
                return
            if token is None:
                break
            reply_parts.append(token)
            yield f"data: {_json.dumps({'token': token})}\n\n"

        result = await process_task
        reply = "".join(reply_parts) or result.reply or "抱歉，我暂时无法回答这个问题。"

        done_msg = {'done': True, 'full_reply': reply}
        if result.consult_info:
            done_msg['consult_info'] = result.consult_info
        yield f"data: {_json.dumps(done_msg)}\n\n"

    return StreamingResponse(_agent_stream(), media_type="text/event-stream")


@router.get("/learned/today")
def learned_today():
    return learned.today_summary()


@router.get("/learned/{date}")
def learned_by_date(date: str):
    return {"date": date, "items": learned.get(date)}


@router.get("/memory/{session_id}")
def memory_by_session(session_id: str):
    return {"session_id": session_id, "history": memory.get_history(session_id)}

@router.delete("/memory/{session_id}")
def delete_memory_session(session_id: str):
    ok = memory.trash_session(session_id)
    return {"ok": ok, "session_id": session_id}

@router.get("/trash")
def list_trash():
    return {"sessions": memory.list_trash()}

@router.post("/memory/{session_id}/restore")
def restore_memory_session(session_id: str):
    ok = memory.restore_session(session_id)
    return {"ok": ok, "session_id": session_id}


@router.get("/api/tasks")
def list_tasks():
    """获取所有任务列表"""
    try:
        tasks = _agent.task_store.list()
        return {"tasks": tasks, "total": len(tasks)}
    except Exception as e:
        return {"tasks": [], "total": 0, "error": str(e)}


@router.get("/sessions")
def list_sessions():
    sessions = memory.list_sessions()
    result = []
    for sid in sessions:
        hist = memory.get_history(sid)
        first_msg = ""
        msg_count = 0
        last_ts = ""
        if hist:
            first_msg = (hist[0].get("user") or "")[:60]
            msg_count = len(hist)
            last_ts = hist[-1].get("ts", "")
        result.append({
            "id": sid,
            "title": first_msg,
            "count": msg_count,
            "last_ts": last_ts,
        })
    # 按时间倒序
    result.sort(key=lambda s: s["last_ts"], reverse=True)
    return {"sessions": result}


@router.get("/prompts")
def prompts_list():
    try:
        lib = prompts_lib()
        items = lib.list_all()
        return {"total": len(items), "prompts": items}
    except Exception as e:
        return {"total": 0, "prompts": [], "error": str(e)}


@router.get("/prompts/category/{category}")
def prompts_by_category(category: str):
    try:
        lib = prompts_lib()
        items = lib.list_by_category(category)
        return {"category": category, "total": len(items), "prompts": items}
    except Exception as e:
        return {"category": category, "total": 0, "prompts": [], "error": str(e)}


@router.get("/prompts/search")
def prompts_search(q: str = "", category: Optional[str] = None, tags: Optional[str] = None):
    try:
        lib = prompts_lib()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        results = lib.search(keyword=q, tags=tag_list, category=category)
        return {"query": q, "total": len(results), "results": results}
    except Exception as e:
        return {"query": q, "total": 0, "results": [], "error": str(e)}


@router.get("/prompts/{prompt_id}")
def prompts_get(prompt_id: str):
    try:
        lib = prompts_lib()
        p = lib.get(prompt_id)
        if not p:
            return {"error": "not_found", "prompt_id": prompt_id}
        return p
    except Exception as e:
        return {"error": str(e), "prompt_id": prompt_id}


@router.post("/prompts/apply")
def prompts_apply(req: PromptApplyRequest):
    result = prompts_lib.apply(req.prompt_id, req.variables)
    if not result:
        return {"error": "not_found", "prompt_id": req.prompt_id}
    return result


@router.post("/prompts/feedback")
def prompts_feedback(req: PromptFeedbackRequest):
    ok = prompts_lib.record_feedback(req.prompt_id, req.success, req.confidence)
    return {"ok": ok, "prompt_id": req.prompt_id}


@router.get("/api/health-check")
def api_health_check():
    ctx = {"recent_passes": getattr(chat, "_call_count", 0)}
    result = system_check.run_full_check(context=ctx)
    return result


@router.get("/api/brain-state")
def brain_state():
    return get_brain_state()


@router.get("/agents")
def list_agents():
    registry = _get_registry()
    agents = [a.to_dict(include_prompt=True) for a in registry.list_all()]
    ext_agents = external_agent_manager.list_agents_with_status()
    for ext in ext_agents:
        agents.append({
            "id": ext["id"],
            "name": ext["name"],
            "description": f"外部服务 · {ext.get('endpoint', '') or ext.get('model', '')}",
            "temperature": 0.7,
            "color": [0.5, 0.8, 0.7],
            "is_predefined": False,
            "agent_type": "external",
            "system_prompt": "",
            # 外部 Agent 专有字段
            "endpoint": ext.get("endpoint", ""),
            "provider": ext.get("provider", ""),
            "base_url": ext.get("base_url", ""),
            "model": ext.get("model", ""),
            "connection_type": ext.get("connection_type", ""),
            "calls_total": ext.get("calls_total", 0),
            "success_rate": ext.get("success_rate", 0),
            "last_error": ext.get("last_error", ""),
        })
    return {"agents": agents}


@router.post("/agents")
def create_agent(req: Dict[str, Any]):
    registry = _get_registry()
    name = req.get("name", "").strip()
    description = req.get("description", "").strip()
    if not name:
        return {"error": "name required"}
    temperature = float(req.get("temperature", 0.7))
    config = registry.create_custom(name=name, description=description or f"自定义角色 {name}", temperature=temperature)
    return {"ok": True, "agent": config.to_dict(include_prompt=True)}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str):
    registry = _get_registry()
    ok = registry.delete(agent_id)
    return {"ok": ok, "agent_id": agent_id}


@router.put("/agents/{agent_id}")
def update_agent(agent_id: str, req: Dict[str, Any]):
    registry = _get_registry()
    config = registry.update(agent_id, **req)
    if not config:
        return {"error": "Agent not found", "ok": False}
    return {"ok": True, "agent": config.to_dict(include_prompt=True)}


@router.get("/consultation/agents")
def consultation_agents():
    registry = _get_registry()
    agents = registry.list_all()
    result = [a.to_dict(include_prompt=True) for a in agents]
    ext_agents = external_agent_manager.list_agents_with_status()
    for ext in ext_agents:
        result.append({
            "id": ext["id"],
            "name": ext["name"],
            "description": f"外部服务 · {ext.get('endpoint', '')}",
            "temperature": 0.7,
            "model": "external",
            "color": [0.5, 0.8, 0.7],
            "is_predefined": False,
            "agent_type": "external",
        })
    return {"agents": result}


@router.post("/consultation/start")
def start_consultation(req: ConsultationStartRequest):
    return start_consultation_impl(req.topic, req.agent_ids, req.requirements, req.max_rounds)


@router.get("/consultation/history")
def consultation_history():
    raw = _list_history(str(Path(DATA_DIR) / "consultation")) or []
    # 过滤掉测试会话，按时间倒序，取最近 30 条
    sessions = []
    for s in raw:
        sid = s.get("session_id", "")
        if sid.startswith("test_") or sid.startswith("fix_"):
            continue
        if not s.get("topic"):
            continue
        sessions.append(s)
    sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    total = len(sessions)
    sessions = sessions[:30]

    # 清理话题中的系统上下文污染（inject_status_into_topic 追加的状态简报）
    for s in sessions:
        topic = s.get("topic", "")
        marker = "【讨论议题】"
        if marker in topic:
            topic = topic.split(marker, 1)[-1].strip()
        elif "\n" in topic:
            topic = topic.split("\n")[0].strip()
        if len(topic) > 100:
            topic = topic[:100]
        s["topic"] = topic

    return {"sessions": sessions, "total": total}


@router.get("/consultation/{session_id}")
def consultation_detail(session_id: str):
    s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
    if not s:
        return {"error": "not_found"}
    # 清理话题中的状态简报
    topic = s.get("topic", "")
    marker = "【讨论议题】"
    if marker in topic:
        topic = topic.split(marker, 1)[-1].strip()
    elif "\n" in topic:
        topic = topic.split("\n")[0].strip()
    s["topic"] = topic
    return {"session": s}


_session_locks: Dict[str, asyncio.Lock] = {}

def _rebuild_session(s: dict) -> ConsultationSession:
    """从 JSON dict 重建 ConsultationSession"""
    agent_configs = []
    for a in s.get("agents", []):
        kw = dict(
            agent_id=a["id"],
            name=a["name"],
            description=a.get("description", ""),
            system_prompt=a.get("system_prompt", ""),
            temperature=a.get("temperature", 0.7),
            model=a.get("model", "default"),
            color=a.get("color", [0.5, 0.5, 0.5]),
            is_predefined=a.get("is_predefined", False),
            agent_type=a.get("agent_type", "local"),
            external=a.get("external", {}),
        )
        agent_configs.append(AgentConfig(**kw))
    obj = ConsultationSession(
        session_id=s["session_id"],
        topic=s["topic"],
        agents=agent_configs,
        max_rounds=s.get("max_rounds", 3),
        consultation_dir=str(Path(DATA_DIR) / "consultation"),
    )
    obj.current_round = s.get("current_round", 0)
    obj.status = s.get("status", "created")
    obj.rounds = s.get("rounds", [])
    return obj


@router.post("/consultation/{session_id}/round")
async def consultation_round(session_id: str):
    s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
    if not s:
        return {"error": "not_found"}
    if s.get("status") == "completed":
        return {"error": "session already completed"}

    async with _session_locks.setdefault(session_id, asyncio.Lock()):
        s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
        if not s:
            return {"error": "not_found"}
        session_obj = _rebuild_session(s)
        result = await session_obj.execute_round()
        session_obj._save()
    return {"ok": True, "result": result}


@router.post("/consultation/{session_id}/conclude")
async def consultation_conclude(session_id: str):
    s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
    if not s:
        return {"error": "not_found"}

    async with _session_locks.setdefault(session_id, asyncio.Lock()):
        s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
        if not s:
            return {"error": "not_found"}
        session_obj = _rebuild_session(s)
        conclusion = await session_obj.generate_conclusion(memory=memory, learned=learned)
        session_obj._save()
    return {"ok": True, "conclusion": conclusion}


# ── 会议流式 SSE 端点 ────────────────────────────────────

@router.post("/consultation/{session_id}/round/stream")
async def consultation_round_stream(session_id: str):
    """
    SSE 流式执行一轮会议发言。
    每完成一个 agent 就推一条 utterance，不用等全部。
    """
    s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
    if not s:
        return {"error": "not_found"}
    if s.get("status") == "completed":
        return {"error": "session already completed"}

    async def event_stream():
        try:
            # Yield health check immediately
            yield f"data: {_json.dumps({'type': 'heartbeat', 'ts': datetime.now().isoformat()})}\n\n"

            lock = _session_locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
                if not s:
                    yield f"data: {_json.dumps({'type': 'error', 'error': 'not_found'})}\n\n"
                    return
                session_obj = _rebuild_session(s)

            # 直接用 execute_round（非流式），拿到结果后分批 yield
            yield f"data: {_json.dumps({'type': 'status', 'msg': 'executing'})}\n\n"

            result = await session_obj.execute_round()
            session_obj._save()

            for utt in result.get("utterances", []):
                yield f"data: {_json.dumps({'type': 'utterance', **utt})}\n\n"

            yield f"data: {_json.dumps({'type': 'round_complete', 'result': result})}\n\n"

        except asyncio.CancelledError:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/consultation/{session_id}/conclude/stream")
async def consultation_conclude_stream(session_id: str):
    """
    SSE 流式生成会议结论。
    生成后推送一条 conclusion 事件包含结论全文。
    """
    s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
    if not s:
        return {"error": "not_found"}

    token_queue: asyncio.Queue = asyncio.Queue()

    async def _run_conclude():
        try:
            async with _session_locks.setdefault(session_id, asyncio.Lock()):
                s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
                if not s:
                    await token_queue.put(("error", "session not found"))
                    return
                session_obj = _rebuild_session(s)
                conclusion = await session_obj.generate_conclusion(memory=memory, learned=learned)
                session_obj._save()
                await token_queue.put(("conclusion", conclusion))
        except Exception as e:
            print(f"[conclude/stream] 异常: {e}")
            await token_queue.put(("error", str(e)))

    async def event_stream():
        task = asyncio.create_task(_run_conclude())
        try:
            while True:
                typ, data = await token_queue.get()
                if typ == "error":
                    yield f"data: {_json.dumps({'type': 'error', 'error': data})}\n\n"
                    break
                elif typ == "conclusion":
                    yield f"data: {_json.dumps({'type': 'consultation_conclusion', **data})}\n\n"
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/consultation/{session_id}/dispatch")
async def consultation_dispatch(session_id: str):
    """会议结束后，根据结论派团队执行任务（后台执行，立即返回）"""
    s = _load_session(session_id, str(Path(DATA_DIR) / "consultation"))
    if not s:
        return {"error": "not_found"}
    if not s.get("conclusion"):
        return {"error": "会议尚未生成结论"}

    conclusion = s["conclusion"]
    summary = conclusion.get("summary", "") or conclusion.get("raw", "会议结束")
    action_items = conclusion.get("action_items", [])
    topic = s.get("topic", "")

    # 清理话题中的状态简报用于任务标题
    clean_topic = topic
    marker = "【讨论议题】"
    if marker in clean_topic:
        clean_topic = clean_topic.split(marker, 1)[-1].strip()
    elif "\n" in clean_topic:
        clean_topic = clean_topic.split("\n")[0].strip()
    if len(clean_topic) > 100:
        clean_topic = clean_topic[:100]

    # 创建主任务
    task = _agent.task_store.create(
        title=clean_topic,
        description=f"会议结论：{summary[:200]}" if summary else clean_topic,
    )

    # 创建子任务（行动项）
    subtask_ids = []
    for item in action_items[:3]:
        try:
            title = str(item)[:200] if not isinstance(item, str) else item[:200]
            subtask = _agent.task_store.create(title=title, description=f"子任务: {session_id}")
            subtask_ids.append(subtask["id"])
        except Exception:
            pass

    # 后台派团队执行（不设超时，跑完为止）
    progress = {
        "task_id": task["id"],
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "elapsed_seconds": 0,
        "current_step": "",
        "steps": [],
        "result": None,
    }
    dispatch_progress[task["id"]] = progress

    async def _run_dispatch():
        try:
            def _on_step(step_info: dict):
                progress["steps"].append(step_info)
                progress["current_step"] = f"{step_info['tool']} ({step_info['status']})"
            await _agent._execute_tool("dispatch_task_team", {
                "task_id": task["id"],
                "team_type": "",
            }, None, None, on_step=_on_step)
            task_final = _agent.task_store.list()
            t = next((t for t in task_final if t["id"] == task["id"]), None)
            status = t["status"] if t else "unknown"
            progress["status"] = "completed" if status == "completed" else "error"
            progress["result"] = {"task_status": status}
            print(f"[dispatch] 任务 {task['id']} 执行完毕，状态: {status}")
        except Exception as e:
            import traceback
            progress["status"] = "error"
            progress["result"] = {"error": str(e)}
            print(f"[dispatch] 后台执行异常: {e}")
            traceback.print_exc()

    asyncio.create_task(_run_dispatch())

    return {
        "ok": True,
        "task_id": task["id"],
        "summary": "任务已创建，正在后台执行",
        "subtask_ids": subtask_ids,
        "action_items": action_items[:3],
    }


@router.get("/dispatch/{task_id}/status")
def get_dispatch_status(task_id: str):
    """前端轮询任务执行进度"""
    p = dispatch_progress.get(task_id)
    if not p:
        return {"error": "not_found", "task_id": task_id}
    if p["status"] == "running":
        try:
            started = datetime.fromisoformat(p["started_at"])
            p["elapsed_seconds"] = int((datetime.now() - started).total_seconds())
        except Exception:
            pass
    return p


# ── 工具步骤自然语言描述 ─────────────────────────────
_TOOL_DESC = {
    "read_file": "读取了文件 {path}",
    "write_file": "写入了文件 {path}",
    "create_file": "创建了文件 {path}",
    "run_command": "执行了命令 {command}",
    "read_url": "访问了网页 {url}",
    "knowledge_search": "搜索了知识库：{query}",
    "task_list": "查看了任务列表",
    "task_update": "更新了任务状态",
    "task_create": "创建了新任务",
    "create_plan": "制定了执行计划",
    "get_current_time": "查看了当前时间",
    "query_weather": "查询了天气",
    "query_wudao_state": "查询了内部状态",
    "generate_image": "生成了一张图片",
    "dispatch_task_team": "派团队执行任务",
    "dispatch_to_agent": "调派了子 Agent 执行任务",
    "browser_do": "操作了浏览器",
    "python_toolkit": "调用了 Python 工具箱",
    "debug_check": "执行了代码调试",
    "api_tool": "调用了 API 接口",
    "recognize_image": "识别了图片内容",
    "template_use": "使用了模板",
    "create_project": "创建了项目",
    "step_limit": "执行达到步数限制",
}


def _describe_step(step: dict) -> str:
    """将工具调用步骤转为自然语言描述"""
    tool = step.get("tool", "")
    summary = step.get("summary", "")
    tmpl = _TOOL_DESC.get(tool, f"执行了 {tool}")
    desc = tmpl
    if tool == "dispatch_to_agent" and summary:
        desc = summary[:80]
    elif "{" in tmpl:
        desc = tmpl.replace("{path}", summary[:50]).replace("{command}", summary[:60]).replace("{query}", summary[:40]).replace("{url}", summary[:50])
    if step.get("status") == "error":
        desc += "（失败：" + summary[:60] + "）"
    elif step.get("status") == "success":
        desc += "（成功）"
    desc += f" — {step.get('time_ms', 0):.0f}ms"
    return desc


@router.get("/dispatch/history")
def get_dispatch_history():
    """返回所有任务执行记录（含自然语言描述）"""
    items = []
    for tid, p in dispatch_progress.items():
        task_info = {"task_id": tid}
        # 从 task_store 查任务标题
        try:
            all_tasks = _agent.task_store.list()
            t = next((t for t in all_tasks if t["id"] == tid), None)
            if t:
                task_info["title"] = t.get("title", "")
        except Exception:
            pass
        # 给每个步骤加自然语言描述
        steps = p.get("steps", [])
        described = []
        for s in steps:
            s = dict(s)
            s["description"] = _describe_step(s)
            described.append(s)
        items.append({
            "task_id": tid,
            "title": task_info.get("title", tid),
            "status": p.get("status", "unknown"),
            "started_at": p.get("started_at", ""),
            "elapsed_seconds": p.get("elapsed_seconds", 0),
            "steps": described,
            "result": p.get("result"),
        })
    # 按开始时间倒序
    items.sort(key=lambda x: x.get("started_at", ""), reverse=True)
    return items


@router.post("/api/tts")
async def api_tts(req: Dict[str, Any], bg: BackgroundTasks):
    text = req.get("text", "")
    if not text or len(text) > 500:
        return {"error": "text too long or empty"}
    # 清理文本：只保留中英文、数字和基本标点，TTS 不读特殊符号
    import re as _re
    clean_text = text.encode('utf-8', 'replace').decode('utf-8')
    clean_text = _re.sub(r'[^一-鿿㐀-䶿a-zA-Z0-9\s，。！？、；：""''（）]', '', clean_text).strip()
    if not clean_text:
        return {"error": "text empty after cleaning"}
    import edge_tts
    import tempfile
    voice = req.get("voice", "zh-CN-XiaoxiaoNeural")
    tts = edge_tts.Communicate(text=clean_text, voice=voice)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    await tts.save(tmp.name)
    bg.add_task(_cleanup_tts_file, tmp.name)
    return FileResponse(tmp.name, media_type="audio/mpeg", filename="tts.mp3",
                        headers={"Content-Disposition": "inline"})


def _cleanup_tts_file(path: str):
    try:
        os.unlink(path)
    except Exception:
        pass


@router.post("/api/stt")
async def api_stt(req: Dict[str, Any]):
    audio_b64 = req.get("audio", "")
    if not audio_b64:
        return {"text": ""}
    import base64
    audio_bytes = base64.b64decode(audio_b64)
    api_key = GLM_API_KEY
    if not api_key:
        return {"text": "", "error": "no GLM API key"}
    import httpx
    tmp = Path(DATA_DIR) / "_stt_tmp.webm"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(audio_bytes)
    url = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            files = {"file": ("audio.webm", audio_bytes, "audio/webm")}
            data = {"model": "glm-4-voice"}
            resp = await client.post(url, files=files, data=data,
                headers={"Authorization": f"Bearer {api_key}"})
            result = resp.json()
        text = result.get("text", "")
        return {"text": text}
    except Exception as e:
        return {"text": "", "error": str(e)}
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/api/stt/upload")
async def api_stt_upload(file: UploadFile = File(...)):
    api_key = GLM_API_KEY
    if not api_key:
        return {"text": "", "error": "no GLM API key"}
    audio_bytes = await file.read()
    if not audio_bytes or len(audio_bytes) < 100:
        return {"text": ""}
    import httpx
    url = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            files = {"file": (file.filename or "audio.webm", audio_bytes, file.content_type or "audio/webm")}
            data = {"model": "glm-4-voice"}
            resp = await client.post(url, files=files, data=data,
                headers={"Authorization": f"Bearer {api_key}"})
            result = resp.json()
        text = result.get("text", "")
        return {"text": text}
    except Exception as e:
        return {"text": "", "error": str(e)}


@router.post("/api/external/call/openclaw_pm")
def api_external_call_openclaw_pm(req: Dict[str, Any]):
    prompt = req.get("prompt", "")
    if not prompt:
        return {"reply": ""}
    system = "你是老柯（OpenClaw），曹峰的朋友和 Collaborator，技术背景强，说话直接务实。回答简洁，聚焦实际产出。"
    try:
        reply = llm_chat(f"[角色设定]\n{system}\n\n{prompt}", history=[])
        return {"reply": reply or "嗯，说完了。"}
    except Exception as e:
        return {"reply": f"[调用老柯出错: {e}]"}


# ── 旧版兼容 API ────────────────────────────────────────

SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


def _check_admin(req: Request) -> bool:
    token = req.headers.get("x-admin-token", "")
    return token == ADMIN_TOKEN


def _load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return _json.load(f)
    return {}


def _save_settings(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


@router.post("/api/v1/chat")
async def unified_chat(request: Request):
    """
    统一场景路由接口（v0.7+）
    现在走完整 agent.process() 链路，含安全检测 + 记忆 + 统计
    """
    body = await request.json()
    prompt = body.get("prompt", "") or body.get("message", "") or body.get("text", "")
    session_id = body.get("session_id", "default")
    scene_id = body.get("scene_id", "")

    if not prompt:
        return {"reply": ""}

    ok, reason = guard.check(prompt)
    if not ok:
        return {"reply": reason, "safety_blocked": True, "safety_reason": reason}

    usage_record(
        item_id=f"v1chat_{session_id}",
        category="perception",
        title=prompt[:30],
    )

    result = await _agent.process(prompt, session_id=session_id, scene_id=scene_id or None)
    reply = result.reply or "抱歉，我暂时无法回答这个问题。"

    usage_record(
        item_id=f"v1chat_think_{session_id}",
        category="thinking",
        title=prompt[:30],
    )

    return {
        "reply": reply,
        "safety_blocked": result.safety_blocked,
        "safety_reason": result.safety_reason,
        "learned_today_count": len(learned.today()),
    }


@router.post("/execute")
def execute_action(req: ExecuteRequest):
    try:
        result = sandbox_exec(req.action, req.params)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/v1/scenes")
def list_scenes_api():
    from core.scene import SceneManager
    scenes = SceneManager().list_scenes()
    return {"scenes": scenes, "total": len(scenes)}

@router.post("/api/v1/scenes/{scene_id}/activate")
async def activate_scene(scene_id: str, request: Request):
    """切换当前会话的场景"""
    body = await request.json()
    session_id = body.get("session_id", "default")
    _agent.set_session_scene(session_id, scene_id)
    from core.scene import SceneManager
    cfg = SceneManager().get_scene(scene_id)
    return {"ok": True, "scene_id": scene_id, "scene": cfg}


@router.get("/api/v1/scenes/{scene_id}")
def get_scene_detail(scene_id: str):
    configs = load_all_scenes()
    scene = configs.get(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail=f"场景 {scene_id} 不存在")
    return scene


@router.post("/api/v1/scenes/{scene_id}/toggle")
def toggle_scene_api(scene_id: str):
    from core.scene import SceneManager
    result = SceneManager().toggle_enabled(scene_id)
    if result is None:
        raise HTTPException(status_code=404, detail="场景不存在")
    return {"ok": True, **result}


@router.delete("/api/v1/scenes/{scene_id}")
def delete_scene_api(scene_id: str):
    from core.scene import delete_scene as _delete_scene
    result = _delete_scene(scene_id)
    if not result.get("success"):
        status = result.get("status", 400)
        raise HTTPException(status_code=status, detail=result.get("error", "删除失败"))
    return result


@router.put("/api/v1/scenes/{scene_id}")
def update_scene_api(scene_id: str, req: Dict[str, Any]):
    from core.scene import update_scene as _update_scene
    req["scene_id"] = scene_id
    result = _update_scene(scene_id, req)
    if not result.get("success"):
        raise HTTPException(status_code=result.get("status", 400), detail=result.get("error", "更新失败"))
    return result


@router.post("/api/v1/scenes")
async def create_scene_api(req: Request):
    body = await req.json()
    scene_id = body.get("scene_id", "")
    if not scene_id or not scene_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="scene_id 只允许字母数字下划线")
    result = create_scene(scene_id, body)
    if not result.get("success"):
        status = result.get("status", 500)
        raise HTTPException(status_code=status, detail=result.get("error", "创建失败"))
    return result


@router.get("/api/settings")
def get_settings():
    return _load_settings()


@router.post("/api/settings")
async def update_settings(req: Request):
    body = await req.json()
    current = _load_settings()
    current.update(body)
    _save_settings(current)
    return {"status": "ok"}


@router.get("/api/settings/model")
def get_model_config():
    """返回当前模型配置"""
    from core.llm import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, GLM_API_KEY, GLM_MODEL, DEEPSEEK_BASE_URL, GLM_BASE_URL
    settings = _load_settings()
    return {
        "deepseek_key": bool(DEEPSEEK_API_KEY),
        "deepseek_key_prefix": (DEEPSEEK_API_KEY[:8] + '...') if DEEPSEEK_API_KEY else '',
        "deepseek_model": settings.get("deepseek_model", DEEPSEEK_MODEL),
        "deepseek_base_url": DEEPSEEK_BASE_URL,
        "glm_key": bool(GLM_API_KEY),
        "glm_key_prefix": (GLM_API_KEY[:8] + '...') if GLM_API_KEY else '',
        "glm_model": settings.get("glm_model", GLM_MODEL),
        "glm_base_url": GLM_BASE_URL,
    }


@router.post("/api/settings/model")
async def update_model_config(req: Request):
    """更新模型配置（API Key / 模型名），保存到文件并立即生效"""
    body = await req.json()
    settings = _load_settings()

    for key in ["deepseek_key", "deepseek_model", "glm_key", "glm_model"]:
        if key in body:
            val = body[key]
            llm_update_config(key, val)
            settings[key] = val

    _save_settings(settings)
    return {"ok": True}


# ── 语音转文字（Vosk 离线） ────────────────────────────────
_vosk_model = None

def _get_vosk_model():
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model
    try:
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        # 模型路径：desktop/models/vosk-model-small-cn-0.22
        model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "desktop", "models", "vosk-model-small-cn-0.22")
        _vosk_model = Model(model_path)
        print(f"[vosk] 模型加载成功: {model_path}")
        return _vosk_model
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音模型加载失败: {e}")

@router.post("/api/approve-path")
async def approve_path(req: Request):
    """用户确认授权某路径 → 写入 allowed_actions.json"""
    try:
        body = await req.json()
        path = body.get("path", "").strip().replace("\\", "/")
        if not path:
            return {"ok": False, "error": "缺少 path 参数"}
        # 禁止 C 盘
        if path.upper().startswith("C:"):
            return {"ok": False, "error": "禁止授权 C 盘路径"}
        # 去掉末尾的 /
        if path.endswith("/") and path != "/":
            path = path[:-1]
        cfg_path = Path(__file__).resolve().parent.parent / "allowed_actions.json"
        cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
        ext_paths = cfg.get("external_paths", [])
        # 去重
        if path not in ext_paths:
            ext_paths.append(path)
        cfg["external_paths"] = ext_paths
        cfg_path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}

import struct as _struct

@router.post("/api/transcribe")
async def transcribe_audio(request: Request):
    """前端发送 webm 音频 → 后端用 ffmpeg 转 PCM → vosk 识别"""
    try:
        from vosk import KaldiRecognizer
        import numpy as np
        import subprocess
        import tempfile
        
        model = _get_vosk_model()
        raw_bytes = await request.body()
        
        if len(raw_bytes) < 100:
            return {"text": "", "error": "录音太短"}
        
        # 获取 ffmpeg 路径（从 imageio-ffmpeg）
        try:
            import imageio_ffmpeg
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            ffmpeg_path = "ffmpeg"  # 尝试系统 PATH
        
        # 用 ffmpeg 把 webm 转为 16kHz 单声道 int16 PCM
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
            f.write(raw_bytes)
            webm_path = f.name
        
        try:
            ff_result = subprocess.run(
                [ffmpeg_path, '-i', webm_path, '-ar', '16000', '-ac', '1', 
                 '-f', 's16le', '-acodec', 'pcm_s16le', '-'],
                capture_output=True, timeout=10
            )
            pcm_data = ff_result.stdout
        finally:
            import os
            os.unlink(webm_path)
        
        print(f"[vosk] webm={len(raw_bytes)}B pcm={len(pcm_data)}B")
        if len(pcm_data) < 100:
            print(f"[vosk] ffmpeg stderr: {ff_result.stderr[:500] if ff_result.stderr else 'none'}")
            return {"text": "", "error": "音频转换失败"}
        
        rec = KaldiRecognizer(model, 16000)
        
        # 分块送入识别器
        chunk_size = 4000
        partial_texts = []
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i:i + chunk_size]
            if rec.AcceptWaveform(chunk):
                result = _json.loads(rec.Result())
                if result.get("text"):
                    partial_texts.append(result["text"])
        
        # 获取最后的文本
        final = _json.loads(rec.FinalResult())
        if final.get("text"):
            partial_texts.append(final["text"])
        
        text = " ".join(partial_texts).strip()
        print(f"[vosk] 识别结果: {text}")
        return {"text": text}
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"依赖未安装: {e}")
    except Exception as e:
        print(f"[vosk] 识别错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 团队模板管理 API
# ============================================================

_TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "team_templates.json")

def _load_templates() -> list:
    try:
        with open(_TEMPLATES_PATH, "r", encoding="utf-8") as f:
            return _json.load(f).get("templates", [])
    except Exception:
        return []

def _save_templates(templates: list):
    dir_name = os.path.dirname(_TEMPLATES_PATH)
    os.makedirs(dir_name, exist_ok=True)
    with open(_TEMPLATES_PATH, "w", encoding="utf-8") as f:
        _json.dump({"templates": templates}, f, ensure_ascii=False, indent=2)

@router.get("/team-templates")
def list_templates():
    """列出所有团队模板"""
    return {"templates": _load_templates()}

@router.get("/team-templates/{template_id}")
def get_template(template_id: str):
    """获取单个模板详情"""
    for t in _load_templates():
        if t["id"] == template_id:
            return {"template": t}
    return {"error": "not_found"}

@router.post("/team-templates")
def create_template(req: dict):
    """创建新模板"""
    templates = _load_templates()
    if any(t["id"] == req.get("id") for t in templates):
        return {"error": "模板 ID 已存在"}
    templates.append({
        "id": req.get("id", ""),
        "name": req.get("name", ""),
        "description": req.get("description", ""),
        "classify_keywords": req.get("classify_keywords", []),
        "roles": req.get("roles", []),
    })
    _save_templates(templates)
    return {"ok": True, "template_id": req.get("id")}

@router.put("/team-templates/{template_id}")
def update_template(template_id: str, req: dict):
    """更新模板"""
    templates = _load_templates()
    for i, t in enumerate(templates):
        if t["id"] == template_id:
            if "name" in req: t["name"] = req["name"]
            if "description" in req: t["description"] = req["description"]
            if "classify_keywords" in req: t["classify_keywords"] = req["classify_keywords"]
            if "roles" in req: t["roles"] = req["roles"]
            _save_templates(templates)
            return {"ok": True}
    return {"error": "not_found"}

@router.delete("/team-templates/{template_id}")
def delete_template(template_id: str):
    """删除模板"""
    templates = _load_templates()
    new_templates = [t for t in templates if t["id"] != template_id]
    if len(new_templates) == len(templates):
        return {"error": "not_found"}
    _save_templates(new_templates)
    return {"ok": True}

@router.get("/team-templates/{template_id}/roles")
def list_roles(template_id: str):
    """列出模板的所有角色"""
    for t in _load_templates():
        if t["id"] == template_id:
            return {"roles": t.get("roles", [])}
    return {"error": "not_found"}

@router.post("/team-templates/{template_id}/roles")
def add_role(template_id: str, req: dict):
    """给模板新增角色"""
    templates = _load_templates()
    for t in templates:
        if t["id"] == template_id:
            role_name = req.get("name", "")
            if any(r["name"] == role_name for r in t.get("roles", [])):
                return {"error": "角色名已存在"}
            t.setdefault("roles", []).append({
                "name": role_name,
                "label": req.get("label", ""),
                "prompt": req.get("prompt", ""),
                "tools": req.get("tools", []),
            })
            _save_templates(templates)
            return {"ok": True}
    return {"error": "not_found"}

@router.put("/team-templates/{template_id}/roles/{role_name}")
def update_role(template_id: str, role_name: str, req: dict):
    """更新角色"""
    templates = _load_templates()
    for t in templates:
        if t["id"] == template_id:
            for r in t.get("roles", []):
                if r["name"] == role_name:
                    if "label" in req: r["label"] = req["label"]
                    if "prompt" in req: r["prompt"] = req["prompt"]
                    if "tools" in req: r["tools"] = req["tools"]
                    _save_templates(templates)
                    return {"ok": True}
            return {"error": "role_not_found"}
    return {"error": "template_not_found"}

@router.delete("/team-templates/{template_id}/roles/{role_name}")
def delete_role(template_id: str, role_name: str):
    """删除角色"""
    templates = _load_templates()
    for t in templates:
        if t["id"] == template_id:
            roles = [r for r in t.get("roles", []) if r["name"] != role_name]
            if len(roles) == len(t.get("roles", [])):
                return {"error": "role_not_found"}
            t["roles"] = roles
            _save_templates(templates)
            return {"ok": True}
    return {"error": "template_not_found"}

