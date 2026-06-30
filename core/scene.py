"""
场景管理器 SceneManager
管理 data/scenes/*.yaml 定义的所有场景，提供 CRUD 和运行时缓存
"""
import os
import json
import yaml
import copy
import time
from pathlib import Path
from glob import glob
from typing import Optional
from threading import Lock

from core.config import WUDAO_DATA

SCENES_DIR = Path(WUDAO_DATA) / "scenes"
MODEL_LOG = Path(WUDAO_DATA) / "model_log.json"


class SceneManager:
    """场景管理器 - 单例"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._cache = {}          # scene_id -> scene dict
        self._cache_time = 0
        self._lock = Lock()

    # ── 读取 ──────────────────────────────────────────

    def list_scenes(self, force_reload=False) -> list:
        """返回所有场景列表"""
        with self._lock:
            if force_reload or not self._cache or (time.time() - self._cache_time) > 30:
                self._reload()
            scenes = list(self._cache.values())
        # 附加 agent 数量和最后调用时间
        log_data = self._load_model_log()
        result = []
        for s in scenes:
            s = copy.deepcopy(s)
            s["agent_count"] = len(s.get("agents", []))
            s["last_call"] = self._last_call_for_scene(s["scene_id"], log_data)
            s["enabled"] = self._is_enabled(s)  # 从 yaml 原始数据读
            result.append(s)
        return result

    def get_scene(self, scene_id: str) -> Optional[dict]:
        scenes = self.list_scenes()
        for s in scenes:
            if s["scene_id"] == scene_id:
                return s
        return None

    # ── 启停 toggle ──────────────────────────────────

    def toggle_enabled(self, scene_id: str) -> Optional[dict]:
        """切换场景 enabled 状态，持久化到 yaml"""
        yaml_path = self._find_yaml(scene_id)
        if not yaml_path:
            return None
        with self._lock:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            current = data.get("enabled", True)
            data["enabled"] = not current
            data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            # 记录到 model_log
            self._log_model_call(
                scene_id=scene_id,
                agent_id="_admin",
                model_type="admin",
                tokens=0,
                cost=0,
                status=f"场景被{'启用' if data['enabled'] else '禁用'}"
            )
            # 刷新缓存
            self._cache_time = 0
        return {"scene_id": scene_id, "enabled": data["enabled"]}

    def clear_cache(self):
        """清空 SceneManager 缓存，下次读取走最新 yaml"""
        with self._lock:
            self._cache = {}
            self._cache_time = 0
        return True

    # ── 内部方法 ──────────────────────────────────────

    def _reload(self):
        self._cache = {}
        SCENES_DIR.mkdir(parents=True, exist_ok=True)
        for yaml_file in SCENES_DIR.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "scene_id" in data:
                    self._cache[data["scene_id"]] = data
            except Exception as e:
                print(f"[scene] 跳过 {yaml_file}: {e}")
        self._cache_time = time.time()

    def _find_yaml(self, scene_id: str) -> Optional[Path]:
        SCENES_DIR.mkdir(parents=True, exist_ok=True)
        for yaml_file in SCENES_DIR.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and data.get("scene_id") == scene_id:
                    return yaml_file
            except:
                pass
        return None

    def _is_enabled(self, scene: dict) -> bool:
        """从原始 yaml 读 enabled 字段"""
        try:
            yaml_path = self._find_yaml(scene["scene_id"])
            if yaml_path:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                return data.get("enabled", True)
        except:
            pass
        return scene.get("enabled", True)

    def _last_call_for_scene(self, scene_id: str, log_data: list) -> str:
        for entry in reversed(log_data):
            if entry.get("scene_id") == scene_id:
                return entry.get("time", "")
        return ""

    def _load_model_log(self) -> list:
        if MODEL_LOG.exists():
            try:
                return json.loads(MODEL_LOG.read_text(encoding="utf-8"))
            except:
                pass
        return []

    def _log_model_call(self, scene_id: str, agent_id: str, model_type: str,
                        tokens: int, cost: float, status: str):
        """写一条 model_log 记录"""
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scene_id": scene_id,
            "agent_id": agent_id,
            "model_type": model_type,
            "tokens": tokens,
            "cost": round(cost, 6),
            "status": status,
        }
        log_data = self._load_model_log()
        log_data.append(entry)
        # 最多保留 50000 条
        if len(log_data) > 50000:
            log_data = log_data[-50000:]
        MODEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        MODEL_LOG.write_text(
            json.dumps(log_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

"""
场景管理器 v0.7.0

职责：
  1. 从 data/scenes/ 加载所有 yaml 场景配置
  2. 提供场景 CRUD（文件级）
  3. 提供场景路由接口（第 2 晚实现）

存储：每个场景一个 yaml 文件，data/scenes/{scene_id}.yaml
加载：启动时一次性读到内存字典
"""
import os
import yaml
import json
import shutil
from typing import Dict, List, Optional

# ================================================================
# 配置
# ================================================================

# 已由新版 SceneManager 定义 SCENES_DIR = Path(WUDAO_DATA) / "scenes"
# 旧版兼容独立函数用 Path 版本
import pathlib
from core.config import WUDAO_DATA
SCENES_DIR = pathlib.Path(WUDAO_DATA) / "scenes"
DEFAULT_SCENE_ID = "default"


# ================================================================
# 场景加载
# ================================================================

def load_all_scenes() -> Dict[str, dict]:
    """加载 data/scenes/ 下所有 yaml 文件"""
    scenes = {}
    if not os.path.isdir(SCENES_DIR):
        os.makedirs(SCENES_DIR, exist_ok=True)
        return scenes

    for fname in sorted(os.listdir(SCENES_DIR)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(SCENES_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                scene = yaml.safe_load(f)
            if not isinstance(scene, dict):
                continue
            sid = scene.get("scene_id")
            if sid:
                scenes[sid] = scene
        except Exception as e:
            print(f"[scene] 加载 {fname} 失败: {e}")

    return scenes


def get_scene(scenes: Dict[str, dict], scene_id: str) -> Optional[dict]:
    """获取场景配置，找不到则返回 default"""
    return scenes.get(scene_id, scenes.get(DEFAULT_SCENE_ID))


# ================================================================
# 场景 CRUD
# ================================================================

def _scene_path(scene_id: str) -> str:
    return os.path.join(SCENES_DIR, f"{scene_id}.yaml")


def scene_exists(scene_id: str) -> bool:
    return os.path.exists(_scene_path(scene_id))


def create_scene(scene_id: str, config: dict) -> dict:
    """创建新场景（写入 yaml）"""
    if scene_exists(scene_id):
        return {"success": False, "error": f"场景 {scene_id} 已存在", "status": 409}

    config["scene_id"] = scene_id
    path = _scene_path(scene_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"[scene] 创建场景 {scene_id}: {path}")
        return {"success": True, "scene_id": scene_id, "message": f"场景 {scene_id} 创建成功"}
    except Exception as e:
        return {"success": False, "error": f"写入失败: {e}", "status": 500}


def update_scene(scene_id: str, config: dict) -> dict:
    """更新场景配置（完整替换）"""
    if not scene_exists(scene_id):
        return {"success": False, "error": f"场景 {scene_id} 不存在", "status": 404}

    config["scene_id"] = scene_id
    path = _scene_path(scene_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"[scene] 更新场景 {scene_id}: {path}")
        return {"success": True, "scene_id": scene_id, "message": f"场景 {scene_id} 已更新"}
    except Exception as e:
        return {"success": False, "error": f"写入失败: {e}", "status": 500}


def delete_scene(scene_id: str) -> dict:
    """删除场景"""
    if scene_id == DEFAULT_SCENE_ID:
        return {"success": False, "error": "不能删除 default 场景", "status": 400}
    if not scene_exists(scene_id):
        return {"success": False, "error": f"场景 {scene_id} 不存在", "status": 404}

    path = _scene_path(scene_id)
    try:
        os.remove(path)
        print(f"[scene] 删除场景 {scene_id}: {path}")
        return {"success": True, "scene_id": scene_id, "message": f"场景 {scene_id} 已删除"}
    except Exception as e:
        return {"success": False, "error": f"删除失败: {e}", "status": 500}


def list_scenes_summary(scenes: Dict[str, dict]) -> list:
    """返回场景列表摘要（供 API 使用）"""
    result = []
    for sid, cfg in scenes.items():
        result.append({
            "scene_id": sid,
            "name": cfg.get("name", sid),
            "mode": cfg.get("mode", "agentic"),
            "agent_count": len(cfg.get("agents", [])),
        })
    # default 排第一
    result.sort(key=lambda x: (0 if x["scene_id"] == DEFAULT_SCENE_ID else 1, x["scene_id"]))
    return result


# ================================================================
# SceneManager（场景路由）
# ================================================================


