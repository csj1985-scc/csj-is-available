"""
悟道管理面板 v0.7.2 - 后台路由与数据聚合

7 个 tab 数据逻辑 + API 端点
单文件，不引数据库
"""
import os
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from core.config import WUDAO_DATA, PROJECT_ROOT, FEISHU_WEBHOOK, WECHAT_WEBHOOK
import io
import csv

# ---------- 路径 ----------
DATA_DIR = Path(WUDAO_DATA)
MODEL_LOG = DATA_DIR / "model_log.json"
SCENES_DIR = DATA_DIR / "scenes"
ALERTS_FILE = DATA_DIR / "alerts_history.json"
CONVERSATIONS_FILE = DATA_DIR / "conversations.json"
EXTERNAL_CONFIG = DATA_DIR / "external_agent_config.json"
EXTERNAL_LOG = DATA_DIR / "external_log.json"

TEMPLATES_DIR = PROJECT_ROOT / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# 修复 Jinja2 模板编码（Windows GBK 问题）
import jinja2
loader = jinja2.FileSystemLoader(str(TEMPLATES_DIR), encoding='utf-8')
templates.env.loader = loader

# ── 路由 ────────────────────────────────────────────
router = APIRouter(prefix="/admin")


# ============================================================
#  主页面 - Jinja2 渲染，7 tab 合一
# ============================================================

def _get_version():
    """读主入口版本号"""
    main_py = PROJECT_ROOT / "main.py"
    try:
        src = main_py.read_text(encoding="utf-8")
        for line in src.splitlines():
            if 'version=' in line or '"version":' in line:
                parts = line.strip().strip(',').strip('"').split('"')
                for i, p in enumerate(parts):
                    if p.startswith("0."):
                        return p
                # try splitting by =
                if '=' in line:
                    v = line.split('=')[-1].strip().strip('",')
                    if v.startswith("0."):
                        return v
    except:
        print("[admin] 读取 main.py 版本失败")
    return "0.7.2"


@router.get("", response_class=HTMLResponse)
def admin_page(request: Request,
               tab: str = "overview",
               scene: str = "",
               model: str = "",
               days: int = 1):
    """管理面板主页，根据 tab 参数渲染不同内容"""
    version = _get_version()
    pid = os.getpid()
    uptime_seconds = _get_uptime()
    uptime_str = _format_uptime(uptime_seconds)

    # 全局变量
    start_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_log = _load_model_log()

    context = {
        "request": request,
        "tab": tab,
        "version": version,
        "pid": pid,
        "uptime": uptime_str,
        "start_time": start_time_str,
        "model_log": model_log,
        "model_log_len": len(model_log),
    }

    # 动态加载各 tab 数据
    if tab == "scenes":
        context["scenes"] = _get_scenes_data()
    elif tab == "agents":
        context["agents"] = _get_agents_data()
    elif tab == "llm":
        filter_agent = request.query_params.get("filter_agent", "")
        time_around = request.query_params.get("time_around", "")
        if time_around:
            # 如果传了 time_around，把 days 设为 1 并清空 model/agent 筛选
            days = 1
        context["logs"] = _get_llm_logs(scene, model, days, filter_agent)
        context["filter_scenes"] = _get_filter_scenes()
        context["filter_models"] = _get_filter_models()
        context["current_scene"] = scene
        context["current_model"] = model
        context["current_days"] = days
        context["filter_agent"] = filter_agent
    elif tab == "cost":
        cost_scene = request.query_params.get("cost_scene", "")
        cost_window = request.query_params.get("cost_window", "month")
        context["cost"] = _get_cost_data(cost_scene)
        context["cost_window"] = cost_window
    elif tab == "sessions":
        context["sessions"] = _get_sessions_data()
    elif tab == "alerts":
        context["alerts"] = _get_alerts_data()
        context["webhook_configured"] = bool(WECHAT_WEBHOOK) or bool(FEISHU_WEBHOOK)
    elif tab == "external":
        context["agents"] = _get_external_agents_data()

    # Tab 1: 读取场景原始 yaml
    if tab == "scenes":
        scene_data_list = []
        for s in context.get("scenes", []):
            sid = s["scene_id"]
            scene_data_list.append({
                "scene_id": sid,
                "yaml_content": _get_scene_yaml(sid),
            })
        context["scene_yamls"] = {d["scene_id"]: d["yaml_content"] for d in scene_data_list}

    return templates.TemplateResponse(request, f"admin/tab_{tab}.html", context)


# ============================================================
#  API 端点
# ============================================================

# ── Tab 1: 场景管理 ─────────────────────────────────

from core.scene import SceneManager
_scene_mgr = SceneManager()


@router.post("/api/scenes/{scene_id}/toggle")
def api_toggle_scene(scene_id: str):
    """启停 toggle"""
    result = _scene_mgr.toggle_enabled(scene_id)
    if result is None:
        return {"ok": False, "error": "场景不存在"}
    return {"ok": True, **result}


@router.post("/api/scenes/create")
def api_create_scene(scene_id: str = "", template: str = ""):
    """创建新场景 yaml"""
    if not scene_id.strip():
        return {"ok": False, "error": "scene_id 不能为空"}
    # 检查是否已存在
    existing = _scene_mgr.get_scene(scene_id.strip())
    if existing:
        return {"ok": False, "error": f"场景 '{scene_id}' 已存在"}
    # 模板场景
    scene_data = {
        "scene_id": scene_id.strip(),
        "name": scene_id.strip(),
        "mode": template or "single",
        "enabled": True,
        "agents": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    yaml_path = SCENES_DIR / f"{scene_id.strip()}.yaml"
    try:
        import yaml
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(scene_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        _scene_mgr.clear_cache()
        return {"ok": True, "scene_id": scene_id.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


from pydantic import BaseModel

class UpdateSceneBody(BaseModel):
    yaml_content: str = ""


@router.post("/api/scenes/{scene_id}/update")
def api_update_scene(scene_id: str, body: UpdateSceneBody):
    """更新场景 yaml 内容"""
    yaml_content = body.yaml_content
    yaml_path = _find_yaml_file(scene_id)
    if not yaml_path:
        return {"ok": False, "error": "场景不存在"}
    if not yaml_content.strip():
        return {"ok": False, "error": "yaml 内容不能为空"}
    try:
        # 验证 yaml 合法性
        import yaml
        parsed = yaml.safe_load(yaml_content)
        if not isinstance(parsed, dict):
            return {"ok": False, "error": "yaml 格式无效，必须是一个对象"}
        # 保留 scene_id
        parsed["scene_id"] = scene_id
        parsed["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(parsed, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        _scene_mgr.clear_cache()
        return {"ok": True, "scene_id": scene_id}
    except yaml.YAMLError as e:
        return {"ok": False, "error": f"yaml 解析错误: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/scenes/clear-cache")
def api_clear_cache():
    """清空 SceneManager 缓存"""
    _scene_mgr.clear_cache()
    return {"ok": True, "msg": "缓存已清空"}


# ── 场景 Agent 管理 ─────────────────────────────────

class SceneAgentBody(BaseModel):
    role: str = ""
    class_path: str = ""
    agent_type: str = "local"


@router.get("/api/scenes/{scene_id}/agents")
def api_scene_agents(scene_id: str):
    """获取场景的 Agent 列表"""
    yaml_path = _find_yaml_file(scene_id)
    if not yaml_path:
        return {"ok": False, "error": "场景不存在"}
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        agents = data.get("agents", [])
        return {"ok": True, "agents": agents}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/scenes/{scene_id}/agents")
def api_add_scene_agent(scene_id: str, body: SceneAgentBody):
    """向场景添加一个 Agent"""
    if not body.role:
        return {"ok": False, "error": "role 不能为空"}
    yaml_path = _find_yaml_file(scene_id)
    if not yaml_path:
        return {"ok": False, "error": "场景不存在"}
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        agents = data.get("agents", [])
        # 检查是否已有同名 role
        for a in agents:
            if a.get("role") == body.role:
                return {"ok": False, "error": f"角色 '{body.role}' 已存在"}
        new_agent = {"role": body.role, "class_path": body.class_path, "type": body.agent_type}
        agents.append(new_agent)
        data["agents"] = agents
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        _scene_mgr.clear_cache()
        return {"ok": True, "agent": new_agent}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.delete("/api/scenes/{scene_id}/agents/{role}")
def api_remove_scene_agent(scene_id: str, role: str):
    """从场景删除一个 Agent"""
    yaml_path = _find_yaml_file(scene_id)
    if not yaml_path:
        return {"ok": False, "error": "场景不存在"}
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        agents = data.get("agents", [])
        new_agents = [a for a in agents if a.get("role") != role]
        if len(new_agents) == len(agents):
            return {"ok": False, "error": f"角色 '{role}' 未找到"}
        data["agents"] = new_agents
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        _scene_mgr.clear_cache()
        return {"ok": True, "role": role}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Tab 5: 会话管理 ──────────────────────────────────

@router.post("/api/sessions/{session_id}/end")
def api_end_session(session_id: str):
    """强制结束会话（从 conversations.json / memory.json 删除）"""
    ended = False
    # 从 conversations.json 删除
    if CONVERSATIONS_FILE.exists():
        try:
            data = json.loads(CONVERSATIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and session_id in data:
                del data[session_id]
                CONVERSATIONS_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                ended = True
            elif isinstance(data, list):
                new_data = [s for s in data if s.get("session_id") != session_id]
                if len(new_data) < len(data):
                    data = new_data
                    CONVERSATIONS_FILE.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    ended = True
        except:
            print(f"[admin] 清理 conversations.json 失败")

    mem_file = DATA_DIR / "memory.json"
    if mem_file.exists() and not ended:
        try:
            data = json.loads(mem_file.read_text(encoding="utf-8"))
            sessions = data.get("sessions", {})
            if session_id in sessions:
                del sessions[session_id]
                data["sessions"] = sessions
                mem_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                ended = True
        except:
            print(f"[admin] 清理 memory.json 失败")

    if not ended:
        pass

    return {"ok": True, "session_id": session_id, "ended": ended}


@router.post("/api/sessions/end-all")
def api_end_all_sessions():
    """结束所有会话"""
    count = 0
    sessions = _get_sessions_data()
    for s in sessions:
        api_end_session(s["session_id"])
        count += 1
    return {"ok": True, "ended": count}


# ── Tab 6: 异常报警 ──────────────────────────────────

@router.get("/api/llm/export-csv")
def api_llm_export_csv(scene: str = "", model: str = "", days: int = 0):
    """导出 LLM 流水为 CSV 文件"""
    log_data = _get_llm_logs(scene, model, days)
    from fastapi.responses import StreamingResponse
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间", "场景", "Agent", "模型", "Token", "费用", "状态"])
    for entry in log_data:
        writer.writerow([
            entry.get("time", ""),
            entry.get("scene_id", ""),
            entry.get("agent_id", ""),
            entry.get("model_type", ""),
            entry.get("tokens", 0),
            entry.get("cost", 0),
            entry.get("status", ""),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=llm_logs_{int(time.time())}.csv"},
    )


@router.post("/api/alerts/test-webhook")
def api_test_webhook():
    if not WECHAT_WEBHOOK and not FEISHU_WEBHOOK:
        return {"ok": False, "msg": "未配置 WECHAT_WEBHOOK，无法推送"}
    return {"ok": True, "msg": "webhook 已配置"}


# ── Tab 7: 外部 Agent ────────────────────────────────

@router.post("/api/external/{agent_id}/test-call")
def api_external_test_call(agent_id: str):
    """触发外部 Agent 测试调用（支持 endpoint 和模型 API 两种模式）"""
    from core.external_agent import external_agent_manager

    config = external_agent_manager._load_config()
    agent = None
    for a in config.get("agents", []):
        if a.get("id") == agent_id:
            agent = a
            break
    if not agent:
        return {"ok": False, "error": f"外部 Agent '{agent_id}' 未找到"}

    # 模型 API 模式
    provider = agent.get("provider", "")
    base_url = agent.get("base_url", "")
    if provider or base_url:
        model = agent.get("model", "unknown")
        reply = external_agent_manager._call_llm_api(agent, "你好，请用一句话回复测试。", [])
        if reply.startswith("["):
            return {"ok": False, "error": reply}
        return {"ok": True, "status_code": 200, "body": f"[{provider}/{model}] {reply[:500]}"}

    # Endpoint 模式
    import httpx
    endpoint = agent.get("endpoint", "")
    auth_token = agent.get("auth_token", "")
    if not endpoint:
        return {"ok": False, "error": "端点 URL 为空"}

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        resp = httpx.post(endpoint, json={"test": True, "agent_id": agent_id}, timeout=15)
        success = resp.is_success
        error = "" if success else f"HTTP {resp.status_code}: {resp.text[:200]}"
        external_agent_manager.log_call(agent_id, endpoint, success, error)
        return {
            "ok": success,
            "status_code": resp.status_code,
            "body": resp.text[:500] if success else error,
        }
    except Exception as e:
        error = str(e)
        external_agent_manager.log_call(agent_id, endpoint, False, error)
        return {"ok": False, "error": error}


# ── JSON API for all sections ───────────────────────────────

@router.get("/api/overview")
def api_overview_stats():
    """Overview stats for header bar (today calls, cost)"""
    cost_data = _get_cost_data()
    return {
        "today_calls": cost_data.get("today_total_calls", 0),
        "today_cost": round(cost_data.get("today_total_cost", 0), 6),
        "versions": _get_version(),
    }


@router.get("/api/stats")
def api_stats():
    """Overview stats"""
    cost_data = _get_cost_data()
    scenes = _get_scenes_data()
    agents = _get_agents_data()
    sessions = _get_sessions_data()
    alerts = _get_alerts_data()
    return {
        "scenes_count": len(scenes),
        "agents_count": len(agents),
        "sessions_count": len(sessions),
        "alerts_count": len(alerts),
        "today_calls": cost_data.get("today_total_calls", 0),
        "today_cost": round(cost_data.get("today_total_cost", 0), 6),
        "month_calls": cost_data.get("month_total_calls", 0),
        "month_cost": round(cost_data.get("month_total_cost", 0), 6),
    }


@router.get("/api/scenes")
def api_scenes_json():
    """场景 JSON 数据"""
    scenes = _get_scenes_data()
    yamls = {}
    for s in scenes:
        yamls[s["scene_id"]] = _get_scene_yaml(s["scene_id"])
    return {"scenes": scenes, "yamls": yamls}


@router.delete("/api/scenes/delete/{scene_id}")
def api_delete_scene(scene_id: str):
    """删除场景"""
    yaml_path = _find_yaml_file(scene_id)
    if not yaml_path:
        return {"ok": False, "error": "场景不存在"}
    try:
        yaml_path.unlink()
        _scene_mgr.clear_cache()
        return {"ok": True, "scene_id": scene_id, "msg": "已删除"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/agents")
def api_agents_json():
    """Agent JSON 数据"""
    return {"agents": _get_agents_data()}


@router.get("/api/llm")
def api_llm_json(scene: str = "", model: str = "", days: int = 7):
    """LLM JSON 数据"""
    logs = _get_llm_logs(scene, model, days)
    scenes = _get_filter_scenes()
    models = _get_filter_models()
    return {"logs": logs, "scenes": scenes, "models": models}


@router.get("/api/cost")
def api_cost_json(window: str = "month", scene: str = ""):
    """成本 JSON 数据"""
    data = _get_cost_data(scene)
    return {
        "today_calls": data["today_total_calls"],
        "today_cost": data["today_total_cost"],
        "week_calls": data["week_total_calls"],
        "week_cost": data["week_total_cost"],
        "month_calls": data["month_total_calls"],
        "month_cost": data["month_total_cost"],
        "by_model": data["by_model"],
        "by_scene_top5": data["by_scene_top5"],
        "all_scenes": data["all_scenes"],
    }


@router.get("/api/sessions")
def api_sessions_json():
    """会话 JSON 数据"""
    return {"sessions": _get_sessions_data()}


@router.get("/api/alerts")
def api_alerts_json():
    """告警 JSON 数据"""
    return {
        "alerts": _get_alerts_data(),
        "webhook_configured": bool(WECHAT_WEBHOOK) or bool(FEISHU_WEBHOOK),
    }


@router.get("/api/external")
def api_external_json():
    """外部 Agent JSON 数据"""
    return {"agents": _get_external_agents_data()}


@router.post("/api/external/create")
def api_external_create(name: str = "", endpoint: str = "", auth_token: str = "",
                        agent_id: str = "", provider: str = "", base_url: str = "",
                        model: str = "", api_key: str = ""):
    """创建外部 Agent（支持 endpoint 或直接模型 API）"""
    from core.external_agent import external_agent_manager
    if not name.strip():
        return {"ok": False, "error": "名称不能为空"}
    aid = agent_id.strip() or f"ext_{int(__import__('time').time())}"
    return external_agent_manager.add_agent(
        aid, name.strip(),
        endpoint=endpoint.strip(),
        auth_token=auth_token.strip(),
        provider=provider.strip(),
        base_url=base_url.strip(),
        model=model.strip(),
        api_key=api_key.strip(),
    )


@router.post("/api/external/{agent_id}/update")
def api_external_update(agent_id: str, name: str = "", endpoint: str = "", auth_token: str = "",
                        provider: str = "", base_url: str = "", model: str = "", api_key: str = ""):
    """更新外部 Agent"""
    from core.external_agent import external_agent_manager
    return external_agent_manager.update_agent(
        agent_id,
        name=name.strip() or None,
        endpoint=endpoint.strip() or None,
        auth_token=auth_token.strip() if auth_token else None,
        provider=provider.strip() or None,
        base_url=base_url.strip() or None,
        model=model.strip() or None,
        api_key=api_key.strip() if api_key else None,
    )


@router.delete("/api/external/{agent_id}/delete")
def api_external_delete(agent_id: str):
    """删除外部 Agent"""
    from core.external_agent import external_agent_manager
    return external_agent_manager.delete_agent(agent_id)


# ============================================================
#  数据函数
# ============================================================

def _get_uptime() -> int:
    usage_file = DATA_DIR / "usage_stats.json"
    if usage_file.exists():
        try:
            data = json.loads(usage_file.read_text(encoding="utf-8"))
            updated = data.get("updated_at", "")
            if updated:
                dt = datetime.strptime(updated[:19], "%Y-%m-%dT%H:%M:%S")
                return max(0, int(time.time() - dt.timestamp()))
        except:
            print("[admin] 解析 usage_stats.json 时间失败")
    return 0


def _format_uptime(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    elif seconds < 86400:
        return f"{seconds // 3600}时{(seconds % 3600) // 60}分"
    else:
        return f"{seconds // 86400}天{(seconds % 86400) // 3600}时"


# ── Tab 1 ────────────────────────────────────────────

def _get_scenes_data() -> list:
    """获取场景列表数据"""
    return _scene_mgr.list_scenes(force_reload=True)


# ── Tab 2 ────────────────────────────────────────────

def _get_agents_data() -> list:
    """从 model_log + 场景 yaml + AgentRegistry 聚合所有 agent"""
    agents_map = {}

    # 1. 从场景 yaml 收集 agent 定义
    _scene_mgr.list_scenes(force_reload=True)
    for scene in _scene_mgr.list_scenes():
        for agent in scene.get("agents", []):
            agent_id = agent.get("class_path", agent.get("role", "unknown"))
            if agent_id not in agents_map:
                agents_map[agent_id] = {
                    "agent_id": agent_id,
                    "name": agent.get("role", "unknown"),
                    "role": agent.get("role", "unknown"),
                    "class_path": agent.get("class_path", ""),
                    "type": agent.get("type", "local"),
                    "source": "scene",
                    "calls_total": 0,
                    "success_count": 0,
                    "last_call": "",
                }

    # 2. 从 AgentRegistry 收集（会议室角色）
    try:
        from core.agent_registry import get_registry
        reg = get_registry()
        for a in reg.list_all():
            aid = a.id
            if aid not in agents_map:
                agents_map[aid] = {
                    "agent_id": aid,
                    "name": a.name,
                    "role": a.name,
                    "class_path": "",
                    "type": "registry",
                    "source": "registry",
                    "is_predefined": a.is_predefined,
                    "calls_total": 0,
                    "success_count": 0,
                    "last_call": "",
                }
    except Exception:
        print("[admin] 加载 agent_registry 失败")

    # 3. 从 model_log 统计调用
    log_data = _load_model_log()
    for entry in log_data:
        aid = entry.get("agent_id", "")
        if aid in agents_map:
            agents_map[aid]["calls_total"] += 1
            if entry.get("status") == "success":
                agents_map[aid]["success_count"] += 1
            ts = entry.get("time", "")
            if ts > agents_map[aid]["last_call"]:
                agents_map[aid]["last_call"] = ts

    result = list(agents_map.values())
    for a in result:
        if a["calls_total"] > 0:
            a["success_rate"] = a["success_count"] / a["calls_total"]
        else:
            a["success_rate"] = 0.0
    return result


# ── Tab 3 ────────────────────────────────────────────

def _load_model_log() -> list:
    """从 model_log.json 读取 LLM 调用流水（JSON Lines 格式，逐行解析）"""
    if MODEL_LOG.exists():
        try:
            content = MODEL_LOG.read_text(encoding="utf-8")
            lines = [l.strip() for l in content.split("\n") if l.strip()]
            entries = []
            for line in lines:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            return entries
        except Exception:
            print("[admin] 读取 model_log.json 失败")
    return []


def _get_filter_scenes() -> list:
    log_data = _load_model_log()
    scenes = set()
    for e in log_data:
        s = e.get("scene_id", "")
        if s:
            scenes.add(s)
    return sorted(scenes)


def _get_filter_models() -> list:
    log_data = _load_model_log()
    models = set()
    for e in log_data:
        m = e.get("model_type", "")
        if m:
            models.add(m)
    return sorted(models)


def _get_llm_logs(scene: str = "", model: str = "", days: int = 0, filter_agent: str = "") -> list:
    log_data = _load_model_log()
    result = []
    now = datetime.now()

    for entry in reversed(log_data):
        # 场景筛选
        if scene and entry.get("scene_id") != scene:
            continue
        # 模型筛选
        if model and entry.get("model_type") != model:
            continue
        # Agent 筛选
        if filter_agent and entry.get("agent_id") != filter_agent:
            continue
        # 时间筛选
        if days > 0:
            try:
                t = entry.get("time", "")
                if t:
                    dt = datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
                    if (now - dt).days >= days:
                        continue
            except:
                print("[admin] 解析日志时间戳失败")
        result.append(entry)
    return result


# ── Tab 4 ────────────────────────────────────────────

def _get_scene_yaml(scene_id: str) -> str:
    """读取场景的 yaml 原始内容"""
    yaml_path = _find_yaml_file(scene_id)
    if yaml_path:
        try:
            return yaml_path.read_text(encoding="utf-8")
        except:
            print(f"[admin] 读取场景 yaml 失败: {yaml_path}")
    return ""


def _find_yaml_file(scene_id: str):
    """查找场景对应的 yaml 文件路径"""
    if not SCENES_DIR.exists():
        return None
    for yaml_file in SCENES_DIR.glob("*.yaml"):
        try:
            import yaml
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and data.get("scene_id") == scene_id:
                return yaml_file
        except:
            print(f"[admin] 解析 yaml 文件失败: {yaml_file}")
    return None


def _get_cost_data(scene_id: str = "") -> dict:
    log_data = _load_model_log()
    now = datetime.now()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    def in_range(ts_str, start, end):
        try:
            t = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            return start <= t < end
        except:
            print("[admin] 解析成本时间戳失败")
            return False

    # 按模型统计
    by_model = defaultdict(lambda: {
        "today_calls": 0, "today_cost": 0.0,
        "week_calls": 0, "week_cost": 0.0,
        "month_calls": 0, "month_cost": 0.0,
    })
    by_scene_month = defaultdict(lambda: {"calls": 0, "cost": 0.0})
    today_total = week_total = month_total = 0
    today_cost = week_cost = month_cost = 0.0

    for entry in log_data:
        ts = entry.get("ts", "") or entry.get("time", "")
        model_type = entry.get("model_type", "unknown")
        cost = entry.get("cost_rmb", 0) or entry.get("cost", 0)
        entry_scene = entry.get("task_type", "") or entry.get("scene_id", "unknown")

        if not ts:
            continue
        if scene_id and entry_scene != scene_id:
            continue

        # 本月（所有统计都基于月份范围）
        if in_range(ts, month_start, today_end):
            month_total += 1
            month_cost += cost
            by_model[model_type]["month_calls"] += 1
            by_model[model_type]["month_cost"] += cost
            by_scene_month[entry_scene]["calls"] += 1
            by_scene_month[entry_scene]["cost"] += cost

            if in_range(ts, week_start, today_end):
                by_model[model_type]["week_calls"] += 1
                by_model[model_type]["week_cost"] += cost
                week_total += 1
                week_cost += cost

                if in_range(ts, today_start, today_end):
                    by_model[model_type]["today_calls"] += 1
                    by_model[model_type]["today_cost"] += cost
                    today_total += 1
                    today_cost += cost

    model_list = []
    for m, counts in sorted(by_model.items()):
        model_list.append({"model": m, **counts})

    # 按场景前 5
    scene_ranked = sorted(by_scene_month.items(), key=lambda x: -x[1]["cost"])
    scene_top5 = []
    for sid, sc in scene_ranked[:5]:
        scene_top5.append({"scene_id": sid, "calls": sc["calls"], "cost": sc["cost"]})

    # 所有场景列表（用于下拉筛选）
    all_scenes = sorted({e.get("scene_id", "unknown") for e in log_data})

    return {
        "today_total_calls": today_total,
        "today_total_cost": round(today_cost, 6),
        "week_total_calls": week_total,
        "week_total_cost": round(week_cost, 6),
        "month_total_calls": month_total,
        "month_total_cost": round(month_cost, 6),
        "by_model": model_list,
        "by_scene_top5": scene_top5,
        "all_scenes": all_scenes,
        "current_scene": scene_id,
    }


# ── Tab 5 ────────────────────────────────────────────

def _get_sessions_data() -> list:
    """从 conversations.json 和 memory.json 读会话列表"""
    sessions_map = {}

    # 从 conversations.json
    if CONVERSATIONS_FILE.exists():
        try:
            data = json.loads(CONVERSATIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for sid, sdata in data.items():
                    sessions_map[sid] = _parse_session(sid, sdata)
            elif isinstance(data, list):
                for sdata in data:
                    sid = sdata.get("session_id", sdata.get("id", "unknown"))
                    sessions_map[sid] = _parse_session(sid, sdata)
        except:
            print("[admin] 加载 conversations.json 失败")

    # 从 memory.json
    mem_file = DATA_DIR / "memory.json"
    if mem_file.exists():
        try:
            data = json.loads(mem_file.read_text(encoding="utf-8"))
            sessions = data.get("sessions", {})
            for sid, msgs in sessions.items():
                if sid not in sessions_map:
                    sessions_map[sid] = {
                        "session_id": sid,
                        "scene_id": "default",
                        "last_active": "",
                        "idle": "未知",
                        "msg_count": len(msgs) if isinstance(msgs, list) else 0,
                    }
                else:
                    if isinstance(msgs, list):
                        sessions_map[sid]["msg_count"] = max(sessions_map[sid].get("msg_count", 0), len(msgs))
        except:
            print("[admin] 解析 memory.json 失败")

    # 计算 idle
    now = datetime.now()
    result = []
    for sid, s in sessions_map.items():
        last_active = s.get("last_active", "")
        if last_active:
            try:
                dt = datetime.strptime(last_active[:19], "%Y-%m-%dT%H:%M:%S")
                idle_seconds = (now - dt).total_seconds()
                if idle_seconds < 60:
                    s["idle"] = f"{int(idle_seconds)}秒"
                elif idle_seconds < 3600:
                    s["idle"] = f"{int(idle_seconds // 60)}分钟"
                elif idle_seconds < 86400:
                    s["idle"] = f"{int(idle_seconds // 3600)}小时"
                else:
                    s["idle"] = f"{int(idle_seconds // 86400)}天"
            except:
                s["idle"] = "未知"
        result.append(s)

    result.sort(key=lambda x: x.get("last_active", ""), reverse=True)
    return result


def _parse_session(sid: str, sdata) -> dict:
    if isinstance(sdata, dict):
        return {
            "session_id": sid,
            "scene_id": sdata.get("scene_id", "default"),
            "last_active": sdata.get("last_active") or sdata.get("timestamp") or sdata.get("time", ""),
            "idle": "计算中",
            "msg_count": len(sdata.get("messages", [])) if isinstance(sdata.get("messages"), list) else 0,
        }
    return {
        "session_id": sid,
        "scene_id": "default",
        "last_active": "",
        "idle": "未知",
        "msg_count": 0,
    }


# ── Tab 6 ────────────────────────────────────────────

def _get_alerts_data() -> list:
    if ALERTS_FILE.exists():
        try:
            data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
            alerts = data.get("alerts", [])
            alerts.reverse()
            return alerts[:100]
        except:
            print("[admin] 加载 alerts_history.json 失败")
    return []


# ── Tab 7 ────────────────────────────────────────────

def _get_external_agents_data() -> list:
    from core.external_agent import external_agent_manager
    return external_agent_manager.list_agents_with_status()
