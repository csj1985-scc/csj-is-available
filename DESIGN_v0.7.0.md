# 悟道 v0.7.0 设计方案：场景路由 + 多 Agent 执行模式

## 目标

悟道从"单一对话后端"升级为**多场景 AI 服务终端**——看护助手、记工App、防丢器等前端都调悟道一个接口，悟道根据场景自动调度不同模型和 Agent。

---

## 章节 1：场景配置 Schema（完整字段表）

### 完整字段定义

```yaml
# data/scenes/{scene_id}.yaml

# 基础信息
scene_id: "elder_care_emergency"        # 唯一标识，字母数字下划线
name: "独居老人看护应急"                   # 人类可读的名字
description: "老人端语音交互 + 应急响应"    # 简短描述
version: 1                               # 配置版本号，用于向后兼容

# 运行模式
mode: "agentic"                          # chat | agentic | discuss | execute

# 模型选择
llm:
  primary:
    provider: "deepseek"
    model: "deepseek-chat"
    timeout: 60
  fallback:
    provider: "zhipu"
    model: "glm-4-flash"
    timeout: 30
  vision:                                # 选填，只在需要识图时配置
    provider: "zhipu"
    model: "glm-4v"
    timeout: 30

# Agent 列表
agents:
  - id: "guardian"                       # Agent 唯一 ID
    role: "系统调度员"                     # 人可读的角色名
    class_path: "core.agents.GuardianAgent"  # Python 类路径
    system_prompt: "你是看护调度员..."       # 覆盖默认 system prompt
    tools: ["knowledge_search", "read_file", "run_command"]
    capabilities: ["risk_assessment", "emergency_response"]
  - id: "medication"
    role: "用药管理员"
    class_path: "core.agents.MedicationAgent"
    tools: ["query_database", "send_reminder"]
    capabilities: ["schedule", "reminder"]

# 额外的系统提示（追加到 prompts/agents/{agent_id}.json 之后）
system_prompt_extra: |
  用户是独居老人，75岁，患高血压。
  语速要慢，每句话不超过 30 字。
  不要用专业术语，用口语。

# 工具白名单（该场景允许使用的工具）
allowed_tools:
  - "knowledge_search"
  - "read_file"
  - "create_file"
  - "run_command"
  - "query_database"
  - "send_reminder"
  - "send_sms"

# 会话配置
session:
  max_history: 20                        # 保留最近多少轮对话历史
  idle_timeout_minutes: 30               # 无操作自动结束会话
  memory_enabled: true                   # 是否启用长期记忆

# 安全限制
security:
  require_confirm: ["payment", "delete", "shutdown"]  # 需要用户确认的动作
  rate_limit: 30                         # 每分钟最大请求数
  allowed_users: ["laoren_001", "laoren_002"]  # 空=允许所有

# 上下文配置（前端传入的额外数据）
context_schema:
  required: ["age", "medications"]
  optional: ["blood_pressure", "heart_rate", "device"]
```

### 模式（mode）的 4 种类型

| 模式 | 流程 | LLM 调用方式 | 适用场景 |
|------|------|-------------|----------|
| `chat` | 收到 query → LLM → 回复 | 单次调用，无 tools | 日常聊天、客服 |
| `agentic` | 收到 query → LLM→tools→LLM 循环 | 循环调用，有 tools | 看护助手、开发助手 |
| `discuss` | 多个 Agent 各出一轮观点 → 综合 | 多 Agent 并行调用 | 方案评审、风险评估、战略分析 |
| `execute` | 主 Agent 拆任务 → 子 Agent 执行 → 合并 | 多 Agent 独立调用 + 合并 | 自动开发、数据处理、自动化流水线 |

---

## 章节 2：场景存储方案

### 对比

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **YAML 文件** `data/scenes/{scene_id}.yaml` | git 友好、人类可读、零依赖 | 并发写冲突、不适合大量场景 | ⭐⭐⭐ 推荐 |
| JSON 文件 `data/scenes/{scene_id}.json` | 同上 | 不支持注释 | ⭐⭐ |
| SQLite | 支持 CRUD 查询、事务 | 需要额外依赖、修改后需重启悟道 | ⭐ |
| 内存 | 启动快 | 每次重启丢失 | ⭐ |

### 推荐方案：YAML 文件

- 每个场景一个 yaml 文件，放 `data/scenes/`
- 启动时一次性加载到内存字典
- 修改文件后可通过 API 或手动重启重新加载

```
data/
  scenes/
    _index.yaml           # 场景列表索引（可选）
    default.yaml          # 默认场景（兼容老接口）
    elder_care.yaml
    worknote.yaml
    anti_loss.yaml
```

### 加载代码片段

```python
import os
import yaml

SCENES_DIR = "data/scenes"

def load_all_scenes() -> dict:
    """加载 data/scenes/ 下所有 yaml 文件"""
    scenes = {}
    if not os.path.exists(SCENES_DIR):
        return scenes
    for fname in os.listdir(SCENES_DIR):
        if fname.endswith(".yaml"):
            path = os.path.join(SCENES_DIR, fname)
            with open(path, "r", encoding="utf-8") as f:
                scene = yaml.safe_load(f)
            scene_id = scene.get("scene_id")
            if scene_id:
                scenes[scene_id] = scene
    return scenes
```

### 场景找不到的兜底

```python
# 请求的 scene_id 不存在时，回退到 default 场景
scene_config = scenes.get(scene_id, scenes.get("default", {}))
```

---

## 章节 3：场景 CRUD API 详细规范

### API 概览

| 方法 | 路径 | 说明 | v0.7.0 鉴权 |
|------|------|------|-------------|
| GET | `/api/v1/scenes` | 列出所有场景 | 无（所有访问者可见） |
| GET | `/api/v1/scenes/{scene_id}` | 查看场景详情 | 无 |
| POST | `/api/v1/scenes` | 创建场景 | **需 admin_token** |
| PUT | `/api/v1/scenes/{scene_id}` | 更新场景 | **需 admin_token** |
| DELETE | `/api/v1/scenes/{scene_id}` | 删除场景 | **需 admin_token** |

### 鉴权方案（v0.7.0 简化版）

```
管理端 API 统一验证请求头 X-Admin-Token：
- 值 = admin_token（字符串，启动时配置）
- 没有 token 或错误 → 401 Unauthorized
- 只保护 POST / PUT / DELETE，不保护 GET

启动时从环境变量取：
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "wudao-admin-2024")
```

### 请求/响应体示例

#### POST /api/v1/scenes

```
请求头: Content-Type: application/json, X-Admin-Token: wudao-admin-2024
请求体:
{
  "scene_id": "worknote",
  "name": "工地记工笔记",
  "description": "记工App的AI后端",
  "mode": "agentic",
  "llm": {
    "primary": {"provider": "deepseek", "model": "deepseek-chat", "timeout": 60},
    "fallback": {"provider": "zhipu", "model": "glm-4-flash", "timeout": 30}
  },
  "agents": [
    {
      "id": "worknote_assistant",
      "role": "记工助手",
      "class_path": "core.agents.WorknoteAgent"
    }
  ],
  "allowed_tools": ["query_database", "create_file"],
  "system_prompt_extra": "你是工地记工助手，帮工人记录工时和工程内容"
}

响应 201:
{
  "success": true,
  "scene_id": "worknote",
  "message": "场景 worknote 创建成功"
}
```

#### GET /api/v1/scenes

```
响应 200:
{
  "scenes": [
    {"scene_id": "default", "name": "默认对话", "mode": "agentic", "agent_count": 4},
    {"scene_id": "elder_care", "name": "独居老人看护", "mode": "agentic", "agent_count": 3},
    {"scene_id": "worknote", "name": "工地记工笔记", "mode": "agentic", "agent_count": 1}
  ],
  "total": 3
}
```

#### PUT /api/v1/scenes/{scene_id}

```
请求体: 同 POST（完整替换）
响应: { "success": true, "scene_id": "worknote", "message": "场景 worknote 已更新" }
```

#### DELETE /api/v1/scenes/{scene_id}

```
响应: { "success": true, "scene_id": "worknote", "message": "场景 worknote 已删除" }
```

### 错误码

| 状态码 | 场景 |
|--------|------|
| 200 | GET/PUT/DELETE 成功 |
| 201 | POST 创建成功 |
| 400 | 请求体格式错误（缺必填字段、scene_id 格式不对） |
| 401 | admin_token 缺失或错误 |
| 404 | 指定的 scene_id 不存在 |
| 409 | 创建时 scene_id 已存在 |
| 500 | 服务端写入文件失败 |

---

## 章节 4：Agent 注册机制

### 设计

每个场景引用 Agent 通过 `class_path`（字符串形式的 Python 类路径），启动时动态导入。

```
agent 脚本位置：core/agents/{agent_id}.py（约定优于配置）
引用方式：class_path = "core.agents.{AgentClassName}"
```

### 注册表设计

```python
# core/agent_registry.py（扩展现有文件）

class AgentRegistry:
    """Agent 注册表，管理所有可用 Agent 类"""

    _agents = {}  # agent_id -> AgentClass

    @classmethod
    def register(cls, agent_id: str, agent_class):
        """注册一个 Agent 类"""
        cls._agents[agent_id] = agent_class

    @classmethod
    def get(cls, agent_id: str):
        """通过 ID 获取 Agent 类"""
        return cls._agents.get(agent_id)

    @classmethod
    def load_from_scene(cls, scene_config: dict) -> list:
        """从场景配置加载所有 Agent 实例"""
        agents = []
        for agent_cfg in scene_config.get("agents", []):
            agent_id = agent_cfg["id"]
            class_path = agent_cfg.get("class_path")
            if class_path:
                # 动态导入
                module_path, class_name = class_path.rsplit(".", 1)
                module = import_module(module_path)
                agent_class = getattr(module, class_name)
                cls.register(agent_id, agent_class)
            agent_class = cls.get(agent_id)
            if agent_class:
                agents.append(agent_class(agent_id=agent_id))
        return agents
```

### 现有 4 个预设 Agent 的注册

现有 `core/agents/` 下的 agent 配置是 JSON 文件（`data/prompts/agents/agent_engineer.json`），v0.7.0 保持不动，**新增场景内的 Agent 配置覆盖**。

```python
# 启动时自动注册预设 Agent
from core.agents import AgentEngineer, AgentDesigner, AgentMarketing, AgentRisk

AgentRegistry.register("agent_engineer", AgentEngineer)
AgentRegistry.register("agent_designer", AgentDesigner)
AgentRegistry.register("agent_marketing", AgentMarketing)
AgentRegistry.register("agent_risk", AgentRisk)
```

### 场景自定义 Agent

场景可以配置自己的 Agent（通过 `class_path` 引用），也可以复用已有的预设 Agent：

```yaml
# 场景配置示例：场景用已有 Agent + 自定义 Agent
agents:
  - id: "agent_engineer"     # 复用已有的工程师 Agent
    class_path: "core.agents.AgentEngineer"
  - id: "medication"         # 自定义 Agent
    class_path: "core.agents.MedicationAgent"
    tools: ["query_database", "send_reminder"]
```

---

## 章节 5：场景路由完整流程图

### `/api/v1/chat` 请求处理流程

```
请求到达 /api/v1/chat
  │
  ├─ 1. 解析请求体（JSON）
  │     { scene_id, query, user_id, session_id, context, images }
  │
  ├─ 2. 查场景配置
  │     scene = scene_manager.get(scene_id)
  │     │
  │     ├─ 找到 → 用此配置
  │     └─ 没找到 → 用 default 场景配置，日志记 warning
  │
  ├─ 3. 验证请求
  │     ├─ scene_id 格式合法？
  │     ├─ 用户是否在 allowed_users 里（如果配了）？
  │     ├─ rate_limit 是否超限？
  │     └─ context_schema 必填字段是否齐全？
  │
  ├─ 4. 根据 mode 分流
  │     │
  │     ├─ chat ──────────→ LLM.chat(query, scene.llm) → 回复
  │     │
  │     ├─ agentic ───────→ Agent.run(query, scene.agents[0], scene.tools)
  │     │                    → LLM + tools 循环 → 回复
  │     │
  │     ├─ discuss ───────→ ConsultationSession.run(
  │     │                      topic=query,
  │     │                      agents=scene.agents,
  │     │                      rounds=3,
  │     │                      llm_config=scene.llm
  │     │                   ) → 结论 → 回复
  │     │
  │     └─ execute ───────→ AgentOrchestrator.execute(
  │                           task=query,
  │                           agents=scene.agents,
  │                           llm_config=scene.llm
  │                        ) → 各 Agent 执行结果 → 合并 → 回复
  │
  ├─ 5. 构造响应
  │     {
  │       "reply": "...",
  │       "scene_id": "...",
  │       "model_used": "deepseek-chat",
  │       "cost_rmb": 0.0012,
  │       "tokens": {"input": 234, "output": 45},
  │       "agent_costs": [{"agent_id": "...", "cost": 0.0005}, ...]
  │     }
  │
  └─ 6. 记日志
        ✓ 调用明细 → data/model_log.json
        ✓ 场景调用量 +1 到 data/usage_stats.json
```

### 内部函数设计（代码片段）

```python
# core/scene.py

class SceneRouter:
    def __init__(self, scenes_dir="data/scenes"):
        self.scenes = load_all_scenes(scenes_dir)
        self.default_scene = self.scenes.get("default", {})

    def route(self, request: dict) -> dict:
        scene_id = request.get("scene_id", "default")
        scene = self.scenes.get(scene_id, self.default_scene)

        if not scene:
            return {"error": f"场景 {scene_id} 不存在", "status": 404}

        # 检查 rate limit
        if not self._check_rate_limit(scene, request.get("user_id", "")):
            return {"error": "请求频率超限", "status": 429}

        mode = scene.get("mode", "agentic")

        if mode == "chat":
            return self._handle_chat(request, scene)
        elif mode == "agentic":
            return self._handle_agentic(request, scene)
        elif mode == "discuss":
            return self._handle_discuss(request, scene)
        elif mode == "execute":
            return self._handle_execute(request, scene)
        else:
            return {"error": f"不支持的 mode: {mode}", "status": 400}

    def _handle_agentic(self, request, scene):
        """走 Agent 工具调用循环"""
        from core.agent import WudaoAgent
        agent = WudaoAgent(scene_config=scene)
        reply = agent.chat(request.get("query"))
        return {"reply": reply, "scene_id": scene["scene_id"], "model_used": "..."}
```

---

## 章节 6：错误处理矩阵

### 4 种典型错误

| 错误场景 | 触发条件 | 处理方式 | 响应 | 是否记日志 |
|----------|----------|----------|------|-----------|
| **scene_id 不存在** | 请求携带的 scene_id 在场景目录中没找到 | 降级到 default 场景 | 正常回复，log 记 warning | ✅ 记 |
| **场景配置加载失败** | yaml 文件解析错误 | 用内置默认配置 | 正常回复 | ✅ 记 |
| **Agent 动态导入失败** | class_path 指向不存在的类 | 跳过该 Agent，用下一个 | 降级回复 | ✅ 记 |
| **LLM 全部失败** | 主+备 LLM 都挂了 | 返回错误提示 | `{"reply": "当前无法提供服务", "error": "LLM unavailable"}` | ✅ 记 |

### 错误日志规范

```python
# 所有错误统一记到 data/error_log.json
{
  "ts": "2026-06-24T09:00:00",
  "type": "scene_not_found",
  "scene_id": "non_existent_scene",
  "user_id": "user_001",
  "action": "fallback_to_default",
  "detail": "场景 non_existent_scene 不存在，已降级到 default"
}
```

### 前端能看到的错误

统一响应格式：

```json
// 正常
{"reply": "你好", "scene_id": "default", "model_used": "deepseek-chat", "cost_rmb": 0.0}

// 场景不存在（降级）
{"reply": "你好", "scene_id": "default", "warning": "scene_id not found, using default"}

// LLM 全挂
{"reply": "悟道暂时无法回复，请稍后再试", "error": "llm_unavailable"}

// 请求频率超限
{"error": "rate_limited", "retry_after_seconds": 30}

// 鉴权失败
{"error": "unauthorized", "detail": "admin_token required"}
```

---

## 章节 7：老接口兼容方案

### 路由设计

路由入口处判**断路径**来决定走新路由还是走老接口：

```python
# main.py

@app.post("/api/v1/chat")
async def unified_chat(request: dict):
    """新统一接口 — 走场景路由"""
    return scene_router.route(request)

@app.post("/chat")
async def legacy_chat(request: dict):
    """老接口 — 内部走 default 场景"""
    # 伪装成场景路由请求
    fake_request = {
        **request.dict(),
        "scene_id": "default",
    }
    return scene_router.route(fake_request)

@app.post("/execute")
async def legacy_execute(request: dict):
    """老执行接口 — 走 default agentic 模式"""
    fake_request = {
        "scene_id": "default",
        "query": request.get("command", ""),
        "images": request.get("images", []),
    }
    return scene_router.route(fake_request)
```

### 场景配置的目录结构

```
data/
  scenes/
    default.yaml            # 默认场景（v0.7.0 必须有的最低配置）
    elder_care.yaml         # 看护助手
    worknote.yaml           # 记工笔记（等前端接入了再创建）
    anti_loss.yaml          # 防丢器（等前端接入了再创建）
```

### 默认场景配置（`default.yaml`，v0.7.0 必备）

此配置要保证现有 index.html 功能不降级——能用全部 4 个预设 Agent、能开会讨论、能知识检索。

```yaml
scene_id: "default"
name: "默认对话"
mode: "agentic"

llm:
  primary:
    provider: "deepseek"
    model: "deepseek-chat"
    timeout: 60
  fallback:
    provider: "zhipu"
    model: "glm-4-flash"
    timeout: 30
  vision:
    provider: "zhipu"
    model: "glm-4v"
    timeout: 30

agents:
  - id: "agent_engineer"
    class_path: "core.agents.AgentEngineer"
  - id: "agent_designer"
    class_path: "core.agents.AgentDesigner"
  - id: "agent_marketing"
    class_path: "core.agents.AgentMarketing"
  - id: "agent_risk"
    class_path: "core.agents.AgentRisk"

allowed_tools:
  - "knowledge_search"
  - "read_file"
  - "create_file"
  - "run_command"

session:
  max_history: 20
  idle_timeout_minutes: 30
  memory_enabled: true
```

### 验证清单（改完老接口后测试）

- [ ] `curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"query": "你好"}'` → 正常回复
- [ ] 打开 `http://localhost:8000/` → index.html 能正常对话
- [ ] 打开 `/room` → 多 Agent 会议能正常启动
- [ ] `curl -X POST http://localhost:8000/api/v1/chat -d '{"scene_id": "default", "query": "你好"}'` → 正常回复（新接口）
- [ ] `curl -X POST http://localhost:8000/api/v1/chat -d '{"scene_id": "not_exist", "query": "你好"}'` → 正常回复 + warning

---

## 执行计划（分 3 晚完成）

### 第 1 晚：场景骨架

**改动文件**：
- 新建 `core/scene.py` — SceneManager（加载、CRUD、查询）
- 新建 `data/scenes/default.yaml` — 默认场景
- 现有 `core/agent_registry.py` — 加 AgentRegistry 类
- `main.py` — 加 5 个场景 CRUD API + 启动时加载场景
- `.gitignore` — 加 `data/scenes/`（场景配置不提交 git，由 admin 创建）

**验收**：
1. 启动后 `GET /api/v1/scenes` 能看到 default
2. `POST /api/v1/scenes`（带 token）能创建新场景
3. `GET /api/v1/scenes/{id}` 能查详情
4. `PUT /api/v1/scenes/{id}` 能更新
5. `DELETE /api/v1/scenes/{id}` 能删除
6. 场景不存在时返回 404

### 第 2 晚：场景路由 + 统一接口

**改动文件**：
- `core/scene.py` — 加 route() 方法和 4 种模式处理
- `main.py` — 加 `POST /api/v1/chat` + 改造 `/chat` 和 `/execute` 内部走路由
- `core/llm.py` — LLMClient 集成 scene.llm 配置

**验收**：
1. `POST /api/v1/chat` 用 default 场景能正常对话
2. `POST /chat` 老接口还能用
3. `POST /execute` 老接口还能用
4. index.html 聊天正常
5. /room 会议正常

### 第 3 晚：多场景验证

**改动文件**：
- 新建 `data/scenes/elder_care.yaml` — 看护助手场景（验证用）
- 如需要，新建 `core/agents/MedicationAgent.py` — 演示自定义 Agent

**验收**：
1. 创建 elder_care 场景
2. 用 `POST /api/v1/chat` + scene_id=elder_care 走通
3. 用 `POST /api/v1/chat` + scene_id=not_exist 能看到降级 warning
4. 现有功能零破坏

---

## 附录：场景配置 yaml 完整示例（看护助手）

```yaml
scene_id: "elder_care_emergency"
name: "独居老人看护应急"
description: "老人端语音交互 + 应急响应，检测到危险自动报警"
version: 1
mode: "agentic"

llm:
  primary:
    provider: "deepseek"
    model: "deepseek-chat"
    timeout: 60
  fallback:
    provider: "zhipu"
    model: "glm-4-flash"
    timeout: 30

agents:
  - id: "guardian"
    role: "看护调度员"
    class_path: "core.agents.GuardianAgent"
    system_prompt: |
      你是独居老人看护助手，负责日常关怀和危险检测。
      老人可能耳背、反应慢。
      每句话不超过 20 字。
    tools:
      - "knowledge_search"
      - "read_file"
    capabilities:
      - "risk_assessment"
      - "emergency_response"
  - id: "medication"
    role: "用药管理员"
    class_path: "core.agents.MedicationAgent"
    tools:
      - "query_database"
      - "send_reminder"
    capabilities:
      - "schedule"
      - "reminder"

allowed_tools:
  - "knowledge_search"
  - "query_database"
  - "send_sms"

session:
  max_history: 30
  idle_timeout_minutes: 60
  memory_enabled: true

security:
  require_confirm: ["send_sms", "call_emergency"]
  rate_limit: 60

context_schema:
  required: ["age", "medications"]
  optional: ["blood_pressure", "heart_rate", "device"]
```

---

## 附录：场景配置完整示例（记工助手）

```yaml
scene_id: "worknote"
name: "工地记工笔记"
description: "工地记工App的AI后端，语音记录工时和工程内容"
version: 1
mode: "agentic"

llm:
  primary:
    provider: "deepseek"
    model: "deepseek-chat"
    timeout: 30
  fallback:
    provider: "zhipu"
    model: "glm-4-flash"
    timeout: 20

agents:
  - id: "worknote_assistant"
    role: "记工助手"
    class_path: "core.agents.WorknoteAgent"
    system_prompt: |
      你是工地记工助手，帮工人用语音记录工时。
      听懂工地术语（砌筑/支模/绑钢筋/浇筑/抹灰）。
      有不确定的先问清楚再记。
    tools:
      - "query_database"
    capabilities:
      - "voice_to_text"
      - "record_work_hours"

allowed_tools:
  - "query_database"

session:
  max_history: 10
  idle_timeout_minutes: 15
  memory_enabled: false

context_schema:
  required: ["project_name", "worker_name"]
  optional: ["location"]
```
