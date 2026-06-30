"""
悟道后端 - v0.7.2

路由在 core/routes_api.py、core/admin.py、core/todo.py 中定义。
"""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import PORT, WECHAT_WEBHOOK
from core.state import memory, learned, guard, router, retriever, learner, system_check
from core.ws import WSManager, ws_endpoint
from core.admin import router as admin_router
from core.routes_api import router as api_router
from core.todo import router as todo_router          # <-- 待办事项模块
from core.alerts import AlertWatcher
from core.care import care_router, init_db as care_init_db

app = FastAPI(title="悟道", description="曹峰的AI伙伴——会自己长本事", version="0.7.2-dev")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app.state.ws_manager = WSManager()


# ── 启动任务 ─────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    import asyncio
    from datetime import datetime
    async def _daily_distill_loop():
        while True:
            now = datetime.now()
            if now.hour == 23 and now.minute == 0:
                await learner.daily_distill()
                await asyncio.sleep(3600)
            await asyncio.sleep(60)
    asyncio.create_task(_daily_distill_loop())

    # 初始化看护模块数据库
    care_init_db()

    # 启动告警监控（企业微信 / 飞书 webhook）
    alert_watcher = AlertWatcher(webhook=WECHAT_WEBHOOK)
    alert_watcher.start()


# ── 页面路由 ─────────────────────────────────────────────

@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"msg": "悟道 v0.3.2 - 前端未上传，放到 wudao/static/index.html"}


@app.get("/room")
def room_page():
    room_path = Path(__file__).parent / "static" / "room.html"
    if room_path.exists():
        return FileResponse(str(room_path))
    return {"error": "room.html not found"}


@app.get("/todos")
def todo_page():
    """待办事项应用页面"""
    todo_file = STATIC_DIR / "todos.html"
    if todo_file.exists():
        return FileResponse(todo_file)
    return {"error": "todos.html not found"}


# ── WebSocket ────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_endpoint(ws)


# ── 静态文件 + 路由注册 ──────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(api_router)
app.include_router(admin_router)
app.include_router(care_router)
app.include_router(todo_router)          # <-- 注册待办事项 API


# ── SSE 调试端点 ──────────────────────────────────────────

@app.post("/test-sse")
async def test_sse():
    from fastapi.responses import StreamingResponse
    import asyncio, json

    async def gen():
        for i in range(5):
            yield f"data: {json.dumps({'i': i, 'msg': f'hello {i}'})}\n\n"
            await asyncio.sleep(0.2)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── 启动入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    pid_file = Path(str(Path(__file__).parent / "data")) / "server.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid > 0:
                import subprocess
                ret = subprocess.run(["taskkill", "//PID", str(old_pid), "//F"],
                                     capture_output=True, text=True)
                if ret.returncode == 0:
                    print(f"[pidlock] 杀掉旧实例 PID={old_pid}")
                import time
                time.sleep(0.5)
        except Exception:
            pass
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    print(f"[pidlock] PID={os.getpid()} -> {pid_file}")

    uvicorn.run(app, host="0.0.0.0", port=PORT)
