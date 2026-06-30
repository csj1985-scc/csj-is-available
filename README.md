# 悟道 (Wudao) — AI 伙伴，会自己长本事

悟道是一个多 Agent 协作平台，以 LLM 为核心引擎，能够自主调用工具、召开多 Agent 会议、执行复杂任务。它不是简单的对话机器人，而是一个**能理解诉求、组织资源、落地执行**的 AI 伙伴。

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **智能对话** | 上下文感知的聊天，支持流式输出 |
| **工具调用** | LLM 自动选择工具：读文件、搜网页、查知识库、画图、执行代码等 |
| **多 Agent 会议** | 根据议题自动匹配专家 Agent，召开多轮讨论会议，输出结论 |
| **任务派发** | 会议结束后自动将结论拆解为可执行任务，派团队后台执行 |
| **工作室** | 查看任务执行进度、实时步骤、历史记录 |
| **知识库** | 长期记忆 + 知识检索，让 AI 记住你的偏好和历史 |
| **画图** | 一句话生成图片（调用 SiliconFlow API） |
| **场景路由** | 根据场景自动切换系统提示词，适配不同用途 |
| **外部 Agent** | 支持调用 Coze、OpenClaw 等外部 Agent 平台 |
| **管理面板** | Web 后台，查看状态、调用统计、余额告警 |
| **告警通知** | 余额不足等异常通过企业微信/飞书机器人推送 |
| **WebSocket 实时通信** | 支持语音对话、实时状态推送 |
| **桌面客户端** | Electron 桌面应用，Windows/Mac/Linux |

---

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+（仅桌面端需要）

### 1. 克隆

```bash
git clone https://github.com/csj1985-scc/csj-is-available.git wudao
cd wudao
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入 LLM API Key（至少填一个）：

| 配置项 | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（推荐，效果好） |
| `GLM_API_KEY` | 智谱 GLM API Key（备选） |

### 3. 启动后端

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python main.py
```

服务默认运行在 `http://localhost:8002`。

### 4. 启动桌面客户端（可选）

```bash
cd desktop
npm install
npm start
```

### 5. 打开 Web 端

浏览器访问 `http://localhost:8002` 即可使用 Web 版。

---

## 系统架构

```
┌──────────────────────────────────────────────────┐
│                  桌面客户端 (Electron)              │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  聊天界面  │  │  会议室   │  │   工作室       │  │
│  └────┬─────┘  └────┬─────┘  └──────┬────────┘  │
└───────┼──────────────┼───────────────┼───────────┘
        │              │               │
        │  HTTP/SSE    │  HTTP/WS      │  HTTP
        ▼              ▼               ▼
┌──────────────────────────────────────────────────┐
│               FastAPI 后端 (端口 8002)              │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ /chat    │  │ 会议协商   │  │   dispatch      │  │
│  │ /stream  │  │ /consult  │  │   /dispatch     │  │
│  └────┬─────┘  └────┬─────┘  └──────┬─────────┘  │
└───────┼──────────────┼───────────────┼───────────┘
        │              │               │
        ▼              ▼               ▼
┌──────────────────────────────────────────────────┐
│                Agent 核心层 (agent.py)              │
│                                                   │
│   ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│   │  LLM 调用  │  │ 工具循环   │  │  task_team   │   │
│   │ (llm.py)  │  │ 执行沙箱   │  │  团队调度    │   │
│   └──────────┘  └──────────┘  └──────────────┘   │
│                                                   │
│   工具集: read_file / write_file / read_url /      │
│   knowledge_search / generate_image / run_command  │
│   browser_do / dispatch_to_agent / task_* 等 30+   │
└──────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| Agent 核心 | `core/agent.py` | 工具循环、意图检测、多轮对话管理 |
| LLM 调用 | `core/llm.py` | LLM API 封装、系统提示词、结果解析 |
| API 路由 | `core/routes_api.py` | REST API 端点（聊天、流式、dispatch） |
| 会议协商 | `core/consultation.py` | 多 Agent 会议管理、轮次控制 |
| 外部 Agent | `core/external_agent.py` | Coze/OpenClaw 等外部平台调用 |
| 任务团队 | `core/task_team.py` | 任务分类、团队配置、Lead 提示词 |
| 执行沙箱 | `core/executor.py` | 安全沙箱，限制敏感文件访问 |
| 知识库 | `core/retriever.py` | 语义检索（ChromaDB） |
| 配置 | `core/config.py` | 统一配置入口 |
| 看护模块 | `core/care/` | 异常记录与自检 |
| 管理面板 | `core/admin.py` | Web 管理后台 |
| WebSocket | `core/ws.py` | 实时通信、语音支持 |

---

## 配置说明

所有配置通过 `.env` 文件设置，详见 `.env.example`：

```ini
# LLM API（至少填一个）
DEEPSEEK_API_KEY=sk-xxx
GLM_API_KEY=xxx

# 服务端口
PORT=8002

# 管理面板 Token
ADMIN_TOKEN=wudao-admin-2024

# 可选：告警通知
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/...
FEISHU_WEBHOOK=https://open.feishu.cn/...
```

---

## 使用手册

### 💬 聊天

在输入框输入任意内容，悟道会自动判断：

- **普通聊天** → 直接对话回复
- **画图** → 以"画"开头的内容 → 自动调用画图工具生成图片
- **开会** → "开会讨论XXX" → 自动创建多 Agent 会议
- **需要查资料/执行操作** → 自动调用相应工具

### 🏢 多 Agent 会议

输入"开会讨论[议题]"进入会议模式：

1. 系统根据议题自动匹配相关 Agent（最多 4 个）
2. 设置讨论轮次
3. 点击"开始讨论"，Agent 们轮流发言
4. 结束后自动生成结论
5. 可选：将结论派发为后台任务

支持 Agent 类型：

| Agent | 专长 |
|-------|------|
| 工程师 | 技术、开发、架构、实现 |
| 风控官 | 安全、风控、合规、权限 |
| 设计师 | 用户体验、界面、交互 |
| 市场分析师 | 市场、运营、增长、竞品 |

### 🛠 工作室

会议派发的任务会在工作室跟踪：

- **任务面板** — 实时显示执行中的任务进度
- **步骤列表** — 每个工具的调用记录、耗时、状态
- **历史记录** — 已完成/失败的任务列表，点击查看详情

### 🎨 画图

输入"画[描述]"即可生成图片，例如：
- "画一只橘色的猫坐在草地上"
- "画一个中年男人坐在电脑前工作"

图片会自动显示在聊天中，并保存在 `static/images/` 目录。

### 🔧 工具清单

悟道可以自主调用的工具（约 30+ 个）：

| 工具 | 用途 |
|------|------|
| `read_file` | 读取文件内容 |
| `write_file` | 写入/修改文件 |
| `read_url` | 访问网页 |
| `knowledge_search` | 搜索知识库 |
| `generate_image` | AI 画图 |
| `run_command` | 执行命令 |
| `browser_do` | 浏览器自动化 |
| `dispatch_to_agent` | 派子任务给专业 Agent |
| `task_create/update/list` | 任务管理 |
| `get_current_time` | 获取当前时间 |
| `query_weather` | 天气查询 |
| `template_use` | 使用模板 |
| `python_toolkit` | Python 工具箱 |

---

## 开发

### 项目结构

```
wudao/
├── core/                  # Python 后端核心
│   ├── agent.py           # Agent 核心（工具循环 + 意图检测）
│   ├── llm.py             # LLM API 调用 + 系统提示词
│   ├── routes_api.py      # REST API 路由
│   ├── consultation.py    # 多 Agent 会议协商
│   ├── task_team.py       # 任务团队调度
│   ├── executor.py        # 安全执行沙箱
│   ├── config.py          # 统一配置入口
│   ├── external_agent.py  # 外部 Agent 集成
│   ├── retriever.py       # 知识库检索
│   ├── memory.py          # 记忆管理
│   ├── ws.py              # WebSocket
│   ├── admin.py           # 管理面板
│   ├── router.py          # 场景路由
│   ├── care/              # 看护模块
│   └── ...
├── desktop/               # Electron 桌面客户端
│   ├── main.js            # 主进程
│   ├── preload.js         # 预加载脚本
│   └── src/
│       └── index.html     # 前端界面（单页应用）
├── data/                  # 数据目录
│   ├── prompts/           # 提示词配置
│   ├── scenes/            # 场景配置
│   ├── chroma_knowledge/  # 知识库向量存储
│   └── ...
├── static/                # 静态文件
│   └── images/            # 生成的图片
├── config/                # 模型配置
├── main.py                # 后端入口
├── .env.example           # 环境配置示例
└── requirements.txt       # Python 依赖
```

### 添加新工具

在 `core/agent.py` 的 `TOOLS` 或 `EXTRA_TOOLS` 列表中添加工具定义即可，无需修改核心逻辑：

```python
{
    "name": "my_tool",
    "description": "工具描述",
    "parameters": {
        "param1": "参数说明",
    },
}
```

然后在 `_execute_tool` 方法中添加对应的 `if name == "my_tool":` 处理分支。

---

## API 参考

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 普通聊天 |
| `/chat/stream` | POST | 流式聊天（SSE） |
| `/consultation/start` | POST | 创建会议 |
| `/consultation/{sid}/conclude` | POST | 结束会议并生成结论 |
| `/consultation/{sid}/dispatch` | POST | 将结论派发为任务 |
| `/dispatch/{task_id}/status` | GET | 查询任务执行进度 |
| `/dispatch/history` | GET | 任务历史列表 |
| `/knowledge/search` | POST | 知识库搜索 |
| `/ws` | WebSocket | 实时通信 |
| `/admin/*` | GET | 管理面板 |

---

## License

MIT
