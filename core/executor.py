"""
沙箱执行器 - v0.6.0 最大权限模式
AI 可以操作任意路径和命令，重要决定走用户审批链。

安全策略：
- 仅拦截 .env/.git/allowed_actions.json 等真正敏感文件
- 危险操作（shutdown/format/kill 等）转审批 noti，不直接拒绝
- C 盘允许操作
- 命令超时 60s，安装类命令 300s
"""
import json
import subprocess
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from typing import Dict, Any

CONFIG_FILE = "allowed_actions.json"

# 敏感文件名/目录名（任何操作都拦截，不可绕过）
_SENSITIVE_NAMES = {".env", ".git", "allowed_actions.json"}

# 写操作自动跳过目录
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", ".git"}

# 危险命令子串 — 不直接拒绝，转审批
_NEEDS_APPROVAL_CMDS = [
    "shutdown", "format", "taskkill", "takeown", "del ",
    "rmdir", "rd ", "attrib", "diskpart", "reg ",
    "net user", "net localgroup", "sc ",
    "chmod", "chown", "cacls", "icacls",
]


def _load_config() -> dict:
    """加载沙箱配置文件"""
    root = Path(__file__).resolve().parent.parent
    cfg_path = root / CONFIG_FILE
    if not cfg_path.exists():
        return {"allowed": [], "denied": [], "sandbox_path": str(root / "sandbox")}
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def _sandbox_path() -> Path:
    """返回沙箱根目录的 Path 对象"""
    cfg = _load_config()
    return Path(cfg.get("sandbox_path", "sandbox")).resolve()


def _project_root() -> Path:
    """项目根目录 = sandbox 的父目录"""
    return _sandbox_path().parent


def _resolve_project_path(relative: str, for_write: bool = False) -> Path:
    """
    解析项目内路径。含敏感文件拦截 + 目录跳过。
    最大权限模式：不限制必须在项目根目录。
    """
    root = _project_root()
    p = Path(relative)
    if p.is_absolute():
        candidate = p.resolve()
    else:
        candidate = (root / relative).resolve()
    _check_sensitive(candidate)
    if for_write:
        for part in candidate.parts:
            if part in _SKIP_DIRS:
                raise PermissionError(f"禁止写入跳过目录: {part}")
    return candidate


def _check_sensitive(target: Path):
    """检查路径是否包含敏感文件/目录"""
    for part in target.parts:
        if part in _SENSITIVE_NAMES:
            raise PermissionError(f"禁止访问敏感文件: {part}")


def check_action(action: str) -> None:
    """检查操作是否被允许。拒绝名单优先。"""
    cfg = _load_config()
    if action in cfg.get("denied", []):
        raise PermissionError(f"操作被禁止: {action}")
    if action not in cfg.get("allowed", []):
        raise PermissionError(f"操作不在白名单中: {action}")


def _is_system_path(target: Path) -> bool:
    """检查是否在系统保护目录下（Windows 大小写不敏感）"""
    _SYS_PROTECT = {"windows", "system32", "program files", "program files (x86)"}
    target_lower = str(target).lower()
    for part in Path(target_lower).parts:
        if part in _SYS_PROTECT:
            return True
    return False


def execute(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行沙箱操作，返回结果字典。

    支持的 action:
      - create_file:     创建新文件
      - read_file:       读取文件内容
      - write_file:      写入已有文件或创建新文件
      - network_access:  HTTP GET 请求
      - run_command:     在项目根目录执行终端命令
    """
    check_action(action)

    # ---- 文件操作（统一路径解析） ----
    if action in ("create_file", "read_file", "write_file"):
        path = params.get("path", "")
        if not path:
            return {"success": False, "error": "缺少 path 参数"}
        target = _resolve_project_path(path, for_write=(action != "read_file"))

        if _is_system_path(target):
            return {"success": False, "error": f"禁止操作系统目录", "needs_approval": True}

        if action == "create_file":
            if target.exists():
                return {"success": False, "error": f"文件已存在: {path}"}
            content = params.get("content", "")
            if not content:
                ext = Path(path).suffix.lower()
                templates = {
                    ".py": "# 请在此处编写代码\n",
                    ".html": "<!DOCTYPE html>\n<html>\n<head><meta charset=\"UTF-8\"><title>文档</title></head>\n<body>\n\n</body>\n</html>\n",
                    ".txt": "",
                    ".json": "{}\n",
                    ".md": "",
                    ".css": "/* 样式 */\n",
                    ".js": "// 代码\n",
                }
                content = templates.get(ext, "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(target), "content": content, "size": len(content)}

        if action == "read_file":
            if not target.exists():
                return {"success": False, "error": f"文件不存在: {path}"}
            content = target.read_text(encoding="utf-8")
            return {"success": True, "path": str(target), "content": content, "size": len(content)}

        if action == "write_file":
            target.parent.mkdir(parents=True, exist_ok=True)
            content = params.get("content", "")
            target.write_text(content or "", encoding="utf-8")
            return {"success": True, "path": str(target), "size": len(content)}

    # ---- 终端执行 ----
    elif action == "run_command":
        command = params.get("command", "").strip()
        if not command:
            return {"success": False, "error": "缺少 command 参数"}

        cmd_lower = command.lower()
        for blocked in _NEEDS_APPROVAL_CMDS:
            if blocked in cmd_lower:
                return {
                    "success": False,
                    "error": f"命令包含敏感操作: {blocked}",
                    "needs_approval": True,
                    "command": command,
                    "summary": f"需要你确认是否执行: {command[:120]}",
                }

        install_cmds = {"pip install", "pip3 install", "npm install", "npm run build"}
        timeout = 300 if any(cmd_lower.startswith(ic) for ic in install_cmds) else 60

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=str(_project_root()),
            )
            # Windows 控制台默认 GBK，尝试 UTF-8 回退 GBK
            raw_stdout = proc.stdout
            raw_stderr = proc.stderr
            for enc in ['utf-8', 'gbk', 'gb18030']:
                try:
                    stdout_text = raw_stdout.decode(enc, errors='replace')
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                stdout_text = raw_stdout.decode('utf-8', errors='replace')
            for enc in ['utf-8', 'gbk', 'gb18030']:
                try:
                    stderr_text = raw_stderr.decode(enc, errors='replace')
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                stderr_text = raw_stderr.decode('utf-8', errors='replace')
            output = (stdout_text + stderr_text)[:10000]
            return {
                "success": True,
                "output": output,
                "returncode": proc.returncode,
                "truncated": len(stdout_text + stderr_text) > 10000,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"命令执行超时（{timeout}秒）"}
        except Exception as e:
            return {"success": False, "error": f"执行异常: {e}"}

    # ---- 网络 ----
    elif action == "network_access":
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        if not url:
            return {"success": False, "error": "缺少 url 参数"}
        if method != "GET":
            return {"success": False, "error": f"不支持的方法: {method}（仅 GET）"}
        try:
            import socket as _socket
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"})
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                try:
                    sock = resp.fp.raw._sock
                    sock.settimeout(10)
                except Exception:
                    pass
                MAX_BODY = 500 * 1024
                body = resp.read(MAX_BODY + 1)
                truncated = len(body) > MAX_BODY
                body = body[:MAX_BODY]
                content_type = resp.headers.get("Content-Type", "")
                if "text" in content_type or "json" in content_type or "xml" in content_type:
                    text = body.decode("utf-8", errors="replace")
                    return {
                        "success": True, "url": url, "status": resp.status,
                        "content_type": content_type, "content": text[:5000], "size": len(text),
                        "truncated": truncated or len(text) > 5000,
                    }
                else:
                    return {
                        "success": True, "url": url, "status": resp.status,
                        "content_type": content_type, "size": len(body),
                        "note": "二进制内容，未返回正文",
                        "truncated": truncated,
                    }
        except _socket.timeout:
            return {"success": False, "error": "读响应超时（10秒无数据），页面可能太大或服务器太慢"}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
        except urllib.error.URLError as e:
            return {"success": False, "error": f"请求失败: {e.reason}"}
        except Exception as e:
            return {"success": False, "error": f"网络异常: {e}"}

    else:
        return {"success": False, "error": f"不支持的操作: {action}"}
