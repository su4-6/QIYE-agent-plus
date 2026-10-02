# Atlas Desk · 智能工单处理 Agent

一个面向 IT 服务台的个人演示项目：员工提交问题后，AI 根据知识库和后续补充提供排查建议；需要 IT 介入时，带着已知情况和处理记录转交人工。软件安装、设备借用等服务申请走独立审批规则，避免用模型猜测企业政策。

包含员工服务台、管理员工作台、Agent 编排、知识库与持久化存储，可在本地运行，也提供在线演示。

## 在线体验

| 入口 | 内容 |
| --- | --- |
| [员工服务台](https://ticket.su46proj.site/) | 注册演示账号、提交问题或服务申请、跟进自己的工单 |
| [管理员工作台](https://ticket.su46proj.site/admin) | 人工处理、审批规则、知识库、检索监控、模型 API 配置 |
| [项目介绍与演示账号](https://su46proj.site/atlas-desk/index.html) | 项目背景、实现说明与公开的演示管理员账号和密码 |

演示环境使用模拟知识和规则，请提交模拟问题。公开管理员账号仅用于体验；不要在共享演示环境填入私人模型 Key。管理员可切换演示模型，也可能消耗已配置 Key 的额度。详见 [模型配置与密钥边界](docs/model-api.md)。

## 可以做什么

**故障排查：** 员工描述问题 → 风险分诊与知识检索 → AI 追问或提供有来源的下一步 → 员工补充或确认解决。超出支持范围、涉及敏感操作、无法可靠回答或员工主动申请时，转 IT 接单处理；IT 标记完成后仍需员工确认。

**服务申请：** 员工填写软件安装或设备借用条件 → 按版本化规则自动批准、拒绝或转人工 → IT 执行交付 → 员工验收。默认模拟规则允许公司设备安装 7-Zip / Visual Studio Code、借用键盘 / 鼠标 / 显示器不超过 7 天，禁用破解或盗版软件。管理员可以发布新规则或关闭自动审批。

- 服务端按账号 ID 和租户过滤“我的工单”，同名员工也不会共享工单。
- 同一工单支持多轮补充、人工接管、确认解决与重新打开；版本检查防止重复操作和过期回复覆盖新状态。
- 管理台支持接单、回复、要求补充、审批、知识导入、审计记录及检索健康统计。
- 模型 API 支持 MiMo、DeepSeek、OpenAI 和维护者允许的兼容服务；测试成功后保存切换，失败保留原配置。
- 生产环境使用 Turnstile、限流、签名会话与 CSRF 校验；模型 Key 加密保存，读取接口不返回 Key。

AI 提供排查建议，**不会直接修电脑、修改企业权限或自动执行安装**；自动获批也不等于已经交付或解决。当前是单租户演示、单管理员配置，未接入企业 SSO、真实资产库存或软件分发系统。

## 技术与处理链

| 层次 | 实现 |
| --- | --- |
| 接口与页面 | FastAPI、Pydantic、原生 HTML / CSS / JavaScript |
| 流程编排 | LangGraph；服务申请策略分支与故障排查分支 |
| 知识检索 | BGE 中文向量 + sqlite-vec，失败回退 SQLite FTS5 / BM25；保留 RRF 混合对照 |
| AI 答复 | OpenAI 兼容 Chat Completions；多轮上下文、引用校验、建议审查与有限修正 |
| 数据与安全 | SQLite WAL、事务审计、scrypt、签名 Cookie、CSRF、Fernet、Turnstile |
| 运行与部署 | Python 3.11、Uvicorn、Docker、K3s 单副本持久化 |

故障排查沿“分诊 → 查询改写 → 检索 → 证据判断 → 生成与校验”执行，服务申请走前置策略分支。详细流程图和访问边界见 [架构说明](docs/architecture.md)、[员工与 IT 工作流](docs/employee-workflow.md)。

## 本地快速启动

**前置条件：** Git、Python **3.11** 和可用的 pip。首次安装依赖需要联网；默认启动不需要模型 Key、Conda、Docker、云服务器或 Turnstile，也不下载 BGE 模型。从仓库根目录执行命令。

~~~bash
git clone https://github.com/su4-6/QIYE-agent-plus.git
cd QIYE-agent-plus
python -m venv .venv
~~~

Windows PowerShell：

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup-local.py
.\.venv\Scripts\python.exe -m app.knowledge
.\.venv\Scripts\python.exe scripts/import-demo.py --without-vectors
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

Linux / macOS（创建环境时可用 python3.11 -m venv .venv）：

~~~bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/setup-local.py
.venv/bin/python -m app.knowledge
.venv/bin/python scripts/import-demo.py --without-vectors
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

配置脚本会要求输入并确认不少于 8 位的本地管理员密码，生成随机会话密钥和密码哈希，写入被 Git 忽略的 **.env**。**已有 .env 时拒绝覆盖**；已有环境请参考 [本地配置说明](docs/local-development.md) 手动补齐配置。首次启动会创建 SQLite 数据库并加载基础知识，导入脚本补充模拟 SOP 和员工自助资料。

启动后打开：

- 员工端：<http://127.0.0.1:8000/>，先注册一个本地演示账号。
- 管理端：<http://127.0.0.1:8000/admin>，账号 **admin**，密码为刚才设置的值。
- 健康检查：<http://127.0.0.1:8000/health>。
- 开发 API 文档：<http://127.0.0.1:8000/api/docs>。

此时为 **BM25 + 无模型** 模式，可验证账号、工单、人工协作和规则审批；不会生成 AI 答复。Ctrl+C 停止服务，SQLite 数据保留。端口被占用时改成空闲端口，并使用对应地址。

### 启用 AI 排查

停止服务，在 .env 中设置：

~~~dotenv
LOW_RISK_ASSISTANCE=true
AUTO_APPROVE_LOW_RISK=true
LLM_PROVIDER=mimo
MIMO_API_KEY=填写自己的演示专用Key
MIMO_MODEL=mimo-v2.6-flash
~~~

重启后，支持范围内的低风险故障可以进入 AI 多轮辅助。也可设置 LLM_PROVIDER=auto、LOW_RISK_ASSISTANCE=true，重启后从管理台「模型 API」配置供应商；测试和保存会产生真实模型请求，可能计费。已有管理台模型配置优先于 .env 的默认连接，恢复部署默认后才使用环境配置。

### 启用 BGE 向量检索

将 .env 的 EMBEDDING_PROVIDER 改为 local，在启动服务前执行：

~~~powershell
# Windows；Linux / macOS 使用 .venv/bin/python
.\.venv\Scripts\python.exe -m app.knowledge reindex
~~~

首次运行需要联网下载模型，完成后重启。/health 应显示向量状态 ready；模型或索引不可用时标记降级并回退 BM25。Conda 启动、环境变量优先级和排错见 [完整本地指南](docs/local-development.md)。

## 测试与评测

~~~powershell
# Windows；Linux / macOS 的准备命令见本地指南
New-Item -ItemType Directory -Force work | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

工程测试覆盖员工隔离、会话与 CSRF、工单流转、服务审批、多轮上下文、引用校验、向量降级、备份和模型配置。模型调用被模拟，不需要真实 Key；测试使用独立数据库，详见 [本地指南](docs/local-development.md#运行工程测试)。

原冻结测试的 **95% 是检索放行精确率目标，目前未通过**，不是单条工单的正确概率或自动解决率。LOW_RISK_ASSISTANCE=false 保留正式模式的评测保护；在线演示显式开启只读辅助，并经过引用与建议审查，但不代表正式质量门槛已经通过。

历史实验从 [评测索引](docs/evaluation.md) 查阅或复现，包含数据规模、分母、配置及限制。工程测试通过、检索命中、引用有效和实际解决问题是不同指标，不互相替代。

## 部署与文档

| 文档 | 内容 |
| --- | --- |
| [本地开发](docs/local-development.md) | 首次配置、AI / 向量开关、Conda、测试、排错 |
| [系统架构](docs/architecture.md) | 模块、处理分支、存储和访问边界 |
| [员工与 IT 工作流](docs/employee-workflow.md) | 状态流转、自动建议与自动审批范围 |
| [模型 API](docs/model-api.md) | 管理台切换、Key 保存和共享管理员风险 |
| [部署指南](docs/deployment.md) | Docker / K3s、生产必填配置、持久化与验收 |
| [评测索引](docs/evaluation.md) | 离线复现、历史实验与质量边界 |
| [部署历史](docs/deployment-status-20261001.md) | 按版本记录的发布与验证证据 |

生产环境必须配置私有会话密钥、管理员密码哈希、Turnstile 与 HTTPS。使用单副本和持久卷保存 SQLite；升级前备份，不把真实 .env、Key 或数据库提交到 Git。生产部署与本地快速启动的配置要求不同，见部署指南。
