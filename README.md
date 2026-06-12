# 企业工单智能处理 Agent 系统

一个面向企业 IT 服务台场景的智能工单处理后端项目。

系统在员工提交工单后，会自动完成工单分类、知识库检索、优先级判断、风险评估、处理建议生成，并在中高风险场景下进入人工审批流程。项目适合作为 Agent 后端、RAG 检索、工具调用、审批治理和 Docker 部署的综合练习项目。

## 项目亮点

- 基于 FastAPI 提供中文 Web 页面和 HTTP API。
- 使用 LangGraph 编排工单处理 Agent 流程。
- 内置轻量 RAG 检索，从本地企业知识库中匹配处理建议。
- 使用规则工具完成分类、优先级评估、风险判断和工单编号生成。
- 支持 OpenAI 兼容大模型接口，未配置或调用失败时自动降级为本地规则答复。
- 返回结果会标记“回答来源”，可区分大模型生成、本地知识库兜底和人工审批。
- 使用 SQLite 持久化工单和审计日志，便于追踪处理过程。
- 内置人工审批机制，中高风险工单不会被 Agent 自动执行到底。
- 支持 Docker Compose 一键容器化启动。

## 系统流程

```text
员工提交工单
-> FastAPI 接收请求并校验参数
-> LangGraph 启动 Agent 流程
-> 规则工具识别分类、优先级和风险等级
-> 本地知识库检索相关处理流程
-> 创建工单编号
-> 判断是否需要人工审批
-> 大模型生成处理建议；失败时使用本地兜底建议
-> 保存工单和审计日志到 SQLite
-> 返回处理结果
```

## 技术栈

| 模块 | 技术 |
| --- | --- |
| Web/API | FastAPI, Uvicorn |
| Agent 编排 | LangGraph |
| 数据校验 | Pydantic v2 |
| 大模型调用 | OpenAI Python SDK，兼容自定义 Base URL |
| 知识库检索 | 本地文本知识库 + 中文关键词加权检索 |
| 数据持久化 | SQLite |
| 部署 | Docker, Docker Compose |

## 项目结构

```text
app/
  main.py          FastAPI 应用入口、中文页面、API 路由
  agent.py         LangGraph 工单处理流程
  tools.py         分类、优先级、风险、审批判断等工具函数
  rag.py           本地知识库检索
  llm.py           大模型调用和异常降级
  schemas.py       请求和响应数据模型
  config.py        环境变量配置
  database.py      SQLite 初始化
  repository.py    工单和审计日志读写

data/
  knowledge_base.txt  企业 IT 工单知识库
  tickets.db          本地 SQLite 数据库，运行后生成或更新

docs/                 学习路线和面试复习材料
Dockerfile            Docker 镜像构建文件
docker-compose.yml    Docker Compose 服务定义
.env.example          环境变量示例模板
requirements.txt      Python 依赖
启动系统.bat           Windows 本地一键启动脚本
启动Docker.bat         Windows Docker 一键启动脚本
停止Docker.bat         Windows Docker 停止脚本
查看Docker日志.bat      Windows Docker 日志查看脚本
查看Docker状态.bat      Windows Docker 状态查看脚本
README.md             项目说明
```

## 快速启动

### 方式一：Docker Compose 推荐

使用 Docker 前，请先打开 Docker Desktop，并等待它进入运行状态。

如果你在 Windows 上操作，最简单是直接双击：

```text
启动Docker.bat
```

脚本会自动检查 `.env`，如果不存在就从 `.env.example` 创建一份默认配置，然后执行 Docker 构建和启动。

启动后打开：

```text
http://127.0.0.1:8000/
```

常用双击脚本：

```text
查看Docker状态.bat
查看Docker日志.bat
停止Docker.bat
```

也可以手动执行命令：

```powershell
docker compose up -d --build
```

常用命令：

```powershell
docker compose ps
docker compose logs -f ticket-agent
docker compose down
```

日常启动时，如果没有修改依赖或 Dockerfile，也可以使用：

```powershell
docker compose up -d
```

### 方式二：Windows 一键启动

双击项目根目录下的：

```text
启动系统.bat
```

脚本会自动创建虚拟环境、安装依赖并启动服务。

### 方式三：命令行本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
uvicorn app.main:app --reload
```

## 环境变量

项目会从 `.env` 读取配置。未配置大模型 Key 时，系统仍可使用本地规则和知识库正常处理工单。

如果是首次使用，可以从模板复制：

```powershell
copy .env.example .env
```

```env
APP_NAME=企业工单智能处理 Agent 系统
APP_ENV=dev
DATABASE_URL=data/tickets.db

AUTO_APPROVE_LOW_RISK=false
HIGH_RISK_KEYWORDS=生产,支付,订单,财务,法务,高管,管理员,批量,数据删除,权限提升,海外访问

LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=gpt-5-mini
```

说明：

- `LLM_API_KEY` 为空时，不调用大模型。
- `LLM_BASE_URL` 可填写 OpenAI 兼容服务地址。
- `LLM_MODEL` 默认为 `gpt-5-mini`，可按实际服务调整。
- `.env` 包含本地私密配置，不要提交到 Git。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 中文 Web 操作页面 |
| `POST` | `/工单` | 提交工单并触发智能处理 |
| `GET` | `/工单` | 查看全部工单 |
| `GET` | `/工单/{ticket_id}` | 查看单个工单详情 |
| `POST` | `/工单/{ticket_id}/审批` | 人工审批中高风险工单 |
| `GET` | `/工单/{ticket_id}/审计日志` | 查看工单审计日志 |
| `GET` | `/模型状态` | 查看大模型配置状态 |
| `GET` | `/health` | 服务健康检查 |
| `GET` | `/接口结构.json` | OpenAPI 结构 |

项目也提供英文兼容路径，如 `/tickets`、`/tickets/{ticket_id}`、`/tickets/{ticket_id}/approval`，方便外部系统集成。

## 请求示例

### 提交普通工单

```powershell
curl -X POST http://127.0.0.1:8000/工单 `
  -H "Content-Type: application/json" `
  -d '{
    "工单标题": "打印机无法打印",
    "工单描述": "办公室打印机一直显示缺纸，但纸盒里有纸",
    "提交人": "张三"
  }'
```

### 提交高风险工单

```powershell
curl -X POST http://127.0.0.1:8000/工单 `
  -H "Content-Type: application/json" `
  -d '{
    "工单标题": "生产系统支付失败",
    "工单描述": "线上订单支付大面积失败，需要立即排查数据库和支付接口",
    "提交人": "李四"
  }'
```

这类工单通常会被识别为高优先级、高风险，并进入“待人工审批”状态。

### 人工审批

```powershell
curl -X POST http://127.0.0.1:8000/工单/IT-XXXXXXXX/审批 `
  -H "Content-Type: application/json" `
  -d '{
    "是否通过": true,
    "审批人": "王经理",
    "审批意见": "确认是生产故障，允许进入应急处理流程"
  }'
```

## 核心模块说明

### `app/main.py`

应用入口，负责创建 FastAPI 实例、初始化数据库、提供中文页面和接口路由。提交工单时会调用：

```python
ticket_graph.invoke(payload.model_dump())
```

也就是把工单交给 LangGraph Agent 流程处理。

### `app/agent.py`

定义工单处理状态和 LangGraph 节点：

```text
classify_node       工单分类、优先级判断、风险评估
retrieve_node       检索企业知识库
action_node         创建工单编号，判断是否需要人工审批
draft_answer_node   生成最终处理建议
```

### `app/tools.py`

模拟企业服务台的工具能力，包括分类、优先级、风险等级、审批判断和工单号生成。规则集中在这里，便于后续替换为真实业务系统接口。

### `app/rag.py`

读取 `data/knowledge_base.txt`，基于中文业务关键词、标题权重和风险词进行轻量检索。它不是向量数据库版本，但结构上已经预留了升级空间。

### `app/llm.py`

封装 OpenAI 兼容模型调用。没有 API Key 或调用失败时返回 `None`，由 Agent 使用本地规则和知识库生成兜底答复，保证核心流程可用。

### `app/database.py` 与 `app/repository.py`

`database.py` 负责建表，`repository.py` 负责工单和审计日志的增查改写。当前包含两张表：

```text
tickets      工单主表
audit_logs   审计日志表
```

## 适合展示的能力点

- Agent 工作流不是单次问答，而是可拆分、可追踪、可扩展的业务流程。
- RAG 让回答基于企业内部知识库，减少脱离业务语境的泛化建议。
- 高风险场景通过人工审批拦截，体现企业级 Agent 的安全边界。
- 审计日志记录工单创建和审批动作，满足可追踪、可复盘要求。
- 大模型异常时自动降级，避免核心工单流程被模型可用性拖垮。
- Docker Compose 统一运行环境，便于迁移、演示和部署。


## 学习建议

如果你是从 Agent 后端项目角度学习，建议按下面顺序阅读：

```text
1. 运行项目，打开 http://127.0.0.1:8000/
2. 提交普通工单，观察分类、知识库命中和处理建议
3. 提交高风险工单，观察人工审批流程
4. 阅读 app/main.py，理解 API 入口
5. 阅读 app/agent.py，理解 LangGraph 流程
6. 阅读 app/tools.py，理解规则工具
7. 阅读 app/rag.py 和 data/knowledge_base.txt，理解轻量 RAG
8. 阅读 app/llm.py，理解模型调用和降级
9. 阅读 app/database.py 与 app/repository.py，理解数据持久化
10. 阅读 Dockerfile 与 docker-compose.yml，理解部署方式
```
