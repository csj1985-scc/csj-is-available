"""
告警监控模块（v0.7.2+）

监控 model_log.json 中的异常记录 + 余额跟踪，通过飞书 webhook 发送通知。
后台线程轮询，每 30 秒扫描新行。

配置：
  FEISHU_WEBHOOK 环境变量（可选），不配置则不启动
  WUDAO_BALANCE 环境变量（可选），充值总额，不配则不检查余额
"""
import os
import json
import time
import threading
from typing import Optional

from core.config import WUDAO_DATA
from core.health import SystemCheck

# 触发告警的错误类型
ALERT_REASONS = {
    "llm_unavailable",    # LLM 完全不可用
    "402",                # 余额不足
    "scene_load_failed",  # 场景加载失败
    "agent_import_failed", # Agent 导入失败
    "no_default_scene",   # 默认场景不存在
    "no_agents",          # 场景无 Agent
    "route_failed",       # 路由异常
}


class AlertWatcher:
    """
    告警监控器
    扫描 model_log.json 中的新行，匹配错误类型后发飞书通知。
    """

    def __init__(self, log_path: str = None, webhook: Optional[str] = None):
        if log_path is None:
            log_path = os.path.join(WUDAO_DATA, "model_log.json")
        self.log_path = log_path
        self.webhook = webhook
        # 跳过已有内容，只监控启动后的新日志
        self.seen_lines = self._count_lines()
        self._last_error_ts = ""  # 防重复报警
        self._last_balance_pct = 100.0
        self._balance_lock = threading.Lock()
        self._balance_file = os.path.join(WUDAO_DATA, "balance_alert.json")
        self._health_checker = SystemCheck()
        self._loop_cycles = 0
        self._last_unhealthy = set()
        self._last_alert_time = {}

    def _count_lines(self) -> int:
        """统计日志文件当前行数，用于启动时跳过已有内容"""
        try:
            if os.path.exists(self.log_path):
                with open(self.log_path, "r", encoding="utf-8") as f:
                    return sum(1 for _ in f)
        except Exception:
            pass
        return 0

    def start(self):
        if not self.webhook:
            print("[AlertWatcher] 未配置 WEBHOOK，跳过启动")
            return
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        webhook_type = "企业微信" if self._is_wechat_webhook() else "Server酱" if self._is_serverchan() else "飞书"
        print(f"[AlertWatcher] 启动成功 ({webhook_type})")

    def _is_wechat_webhook(self) -> bool:
        return "qyapi.weixin.qq.com" in self.webhook

    def _is_serverchan(self) -> bool:
        return "sctapi.ftqq.com" in self.webhook

    def _loop(self):
        while True:
            try:
                self._check()
            except Exception as e:
                print(f"[AlertWatcher] 扫描异常: {e}")
            # 每 30 秒一圈，每 10 圈（5 分钟）自检一次
            self._loop_cycles += 1
            if self._loop_cycles % 10 == 0:
                try:
                    self._health_check()
                except Exception as e:
                    print(f"[AlertWatcher] 自检异常: {e}")
            time.sleep(30)

    def _check(self):
        if not os.path.exists(self.log_path):
            return
        with open(self.log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= self.seen_lines:
            return
        for line in lines[self.seen_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                self._evaluate(entry)
            except json.JSONDecodeError:
                continue
        self.seen_lines = len(lines)
        # 每次扫描顺便检查余额
        self._check_balance()

    def _evaluate(self, entry: dict):
        """评估一条日志是否触发告警"""
        # 检查 LLM 错误（model_log.json 格式）
        reason = entry.get("reason", "")
        if not reason:
            # 检查场景路由错误（路由返回中的 error 字段，写入路由日志后解析）
            error = entry.get("error", "")
            if error in ALERT_REASONS and self._should_alert(entry.get("ts", ""), error):
                self._send({
                    "ts": entry.get("ts", ""),
                    "error_reason": error,
                    "scene_id": entry.get("scene_id", "?"),
                    "model": entry.get("model_name", "?"),
                    "detail": entry.get("reply", "")[:100],
                })
            return

        if not self._should_alert(entry.get("ts", ""), reason):
            return

        # LLM/模型错误
        if reason.startswith("402") or reason.startswith("APIError"):
            self._send({
                "ts": entry.get("ts", ""),
                "error_reason": reason,
                "scene_id": entry.get("task_type", "?"),
                "model": entry.get("model_name", "?"),
                "detail": entry.get("reason", "")[:100],
            })

    def _should_alert(self, ts: str, error_type: str = "") -> bool:
        """同类错误 5 分钟内不重复推送"""
        if not ts:
            return False
        now = time.time()
        key = error_type or ts
        last_ts = self._last_alert_time.get(key, 0)
        if now - last_ts < 300:  # 5 分钟冷却
            return False
        self._last_alert_time[key] = now
        self._last_error_ts = ts
        return True

    # ── 余额跟踪 ──────────────────────────────────────────────

    def _calc_spent(self) -> float:
        """扫描 model_log.json，累计 cost_rmb"""
        if not os.path.exists(self.log_path):
            return 0.0
        total = 0.0
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        cost = entry.get("cost_rmb", 0)
                        if isinstance(cost, (int, float)):
                            total += cost
                    except json.JSONDecodeError:
                        continue
        except Exception:
            print("[AlertWatcher] 计算余额失败")
            return 0.0
        return round(total, 4)

    def get_balance_info(self) -> dict:
        """返回当前余额快照"""
        from core.config import WUDAO_BALANCE
        spent = self._calc_spent()
        remaining = round(WUDAO_BALANCE - spent, 4)
        pct = round(remaining / WUDAO_BALANCE * 100, 1) if WUDAO_BALANCE > 0 else 0
        return {
            "total_recharged": WUDAO_BALANCE,
            "spent": spent,
            "remaining": remaining,
            "percent": pct,
        }

    def _check_balance(self):
        """按阈值报警：剩余 20% / 10% / 5%"""
        from core.config import WUDAO_BALANCE, BALANCE_ALERT_FILE, BALANCE_ALERT_THRESHOLDS
        if WUDAO_BALANCE <= 0:
            return
        info = self.get_balance_info()
        pct = info["percent"]
        with self._balance_lock:
            # 只在上次记录之后继续下降才报警
            if pct >= self._last_balance_pct:
                self._last_balance_pct = pct
                return
            self._last_balance_pct = pct

        # 阈值判定
        prev = self._load_balance_alert(BALANCE_ALERT_FILE)
        for threshold in BALANCE_ALERT_THRESHOLDS:
            if pct <= threshold and prev.get("threshold") != threshold:
                self._write_alert({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "type": "balance",
                    "severity": "warning" if threshold > 5 else "critical",
                    "message": f"余额剩余 {pct}%（¥{info['remaining']}），已达 {threshold}% 阈值",
                })
                self._save_balance_alert(BALANCE_ALERT_FILE, {"threshold": threshold})
                break

    def _load_balance_alert(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            print("[AlertWatcher] 读取余额告警状态失败")
            return {}

    def _save_balance_alert(self, path: str, data: dict):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            print("[AlertWatcher] 保存余额告警状态失败")

    def _write_alert(self, entry: dict):
        """写入 alerts_history.json（保留最近 20 条）"""
        from core.config import PROJECT_ROOT
        path = PROJECT_ROOT / "data" / "alerts_history.json"
        try:
            if path.exists():
                alerts = json.loads(path.read_text(encoding="utf-8"))
            else:
                alerts = []
            if not isinstance(alerts, list):
                alerts = []
            alerts.append(entry)
            if len(alerts) > 20:
                alerts = alerts[-20:]
            path.write_text(
                json.dumps(alerts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[AlertWatcher] 写入告警历史失败: {e}")

    # ── 健康巡检 ──────────────────────────────────────────────

    def _health_check(self):
        """每 5 分钟自检，仅状态变化时才推送（异常→恢复 / 新异常）"""
        result = self._health_checker.run_full_check()
        current = set(result.get("unhealthy_modules", []))
        # 状态没变：之前异常现在还是异常 → 不重复推
        if current == self._last_unhealthy:
            return
        # 恢复正常：之前有异常现在没了 → 推恢复通知
        if not current and self._last_unhealthy:
            self._send({
                "ts": result.get("timestamp", ""),
                "error_reason": "health_recovered",
                "scene_id": "system",
                "model": "",
                "detail": f"模块已恢复: {', '.join(self._last_unhealthy)}",
            })
        # 新异常 → 推告警
        if current:
            new_bad = current - self._last_unhealthy
            detail = "; ".join(
                f"{name}: {', '.join(result['checks'][name].get('issues', ['未知']))}"
                for name in current
            )
            self._send({
                "ts": result.get("timestamp", ""),
                "error_reason": "health_check_failed",
                "scene_id": "system",
                "model": "",
                "detail": f"异常模块: {', '.join(current)}。新增: {', '.join(new_bad) if new_bad else '无'}\n{detail}",
            })
        self._last_unhealthy = current

    # ── 发送飞书 ──────────────────────────────────────────────

    def _send(self, entry: dict):
        """发送企业微信/飞书消息，根据 URL 自动识别格式"""
        try:
            msg = (
                f"悟道告警\n"
                f"时间: {entry.get('ts', '?')}\n"
                f"类型: {entry.get('error_reason', '?')}\n"
                f"场景: {entry.get('scene_id', '?')}\n"
                f"模型: {entry.get('model', '?')}\n"
                f"详情: {entry.get('detail', '?')}"
            )
            import requests as _req
            if self._is_serverchan():
                _req.post(self.webhook, data={"title": "悟道告警", "desp": msg}, timeout=5)
            elif self._is_wechat_webhook():
                _req.post(self.webhook, json={"msgtype": "text", "text": {"content": msg}}, timeout=5)
            else:
                _req.post(self.webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=5)
            print(f"[AlertWatcher] 已发送告警: {entry.get('error_reason', '?')}")
        except Exception as e:
            print(f"[AlertWatcher] 发送告警失败: {e}")
