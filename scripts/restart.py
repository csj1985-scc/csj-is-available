"""
热重启悟道后端 — 供 run_command 调用 (python restart.py)

流程：
  1. 读取 data/server.pid 找到旧服务器 PID 和端口
  2. 启动新进程 python main.py（同端口）
  3. 等新进程就绪后杀掉旧进程
"""
import os
import sys
import time
import subprocess

root = os.path.dirname(os.path.abspath(__file__))
pid_file = os.path.join(root, "data", "server.pid")

# 1. 读旧 PID 和端口
old_pid = None
port = os.environ.get("PORT", "8000")
if os.path.exists(pid_file):
    with open(pid_file) as f:
        raw = f.read().strip()
    if ":" in raw:
        parts = raw.split(":")
        if parts[0].isdigit():
            old_pid = int(parts[0])
        if parts[1].isdigit():
            port = parts[1]
    elif raw.isdigit():
        old_pid = int(raw)

my_pid = os.getpid()

# 2. 启动新进程（detached，同端口）
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

# 3. 等新进程就绪（最多等 5s，轮询 server.pid 变化）
for _ in range(50):
    time.sleep(0.1)
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            fresh = f.read().strip()
        fresh_pid = fresh.split(":")[0] if ":" in fresh else fresh
        if fresh_pid.isdigit() and int(fresh_pid) not in (my_pid, old_pid, new.pid):
            new_pid = int(fresh_pid)
            print(f"[重启] 新进程就绪 PID={new_pid}")
            break

# 4. 杀旧进程
if old_pid and old_pid != my_pid:
    try:
        os.kill(old_pid, 9)
        print(f"[重启] 旧进程已终止 PID={old_pid}")
    except (OSError, PermissionError) as e:
        print(f"[重启] 杀旧进程失败: {e}")

print("[重启] 完成")
