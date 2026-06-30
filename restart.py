"""
热重启悟道后端 — 供 run_command 调用 (python restart.py)

流程：
  1. 读取 data/server.pid 找到旧服务器 PID 和端口
  2. 杀旧进程（释放端口）
  3. 启动新进程 python main.py（绑定端口）
  4. 等新进程健康检查通过
"""
import os
import sys
import time
import subprocess
import urllib.request

root = os.path.dirname(os.path.abspath(__file__))
pid_file = os.path.join(root, "data", "server.pid")

# 1. 读旧 PID 和端口
old_pid = None
port = os.environ.get("PORT", "8002")
if os.path.exists(pid_file):
    with open(pid_file) as f:
        raw = f.read().strip()
    if ":" in raw:
        parts = raw.split(":")
        if parts[0].isdigit():
            old_pid = int(parts[0])
        if len(parts) > 1 and parts[1].isdigit():
            port = parts[1]
    elif raw.isdigit():
        old_pid = int(raw)

my_pid = os.getpid()

# 2. 先杀旧进程，释放端口（Windows 上新旧不能同时绑定同一端口）
if old_pid and old_pid != my_pid:
    try:
        os.kill(old_pid, 9)
        print(f"[重启] 旧进程已终止 PID={old_pid}")
        time.sleep(0.5)  # 等端口释放
    except (OSError, PermissionError) as e:
        print(f"[重启] 杀旧进程失败: {e}")

# 3. 启动新进程
env = os.environ.copy()
env["PORT"] = port
new = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=root,
    env=env,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(f"[重启] 新进程已启动 PID={new.pid} PORT={port}")

# 4. 等新进程健康检查通过（最多 8s）
for i in range(80):
    time.sleep(0.1)
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
        if r.status == 200:
            print(f"[重启] 新进程就绪 (耗时 {i//10 + 1}.{i%10}s)")
            break
    except Exception:
        pass
else:
    print(f"[重启] 警告：新进程启动超时，请检查日志")

print("[重启] 完成")
