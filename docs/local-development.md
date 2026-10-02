# 本地开发与启动

适用于首次克隆仓库的开发者。从仓库根目录执行命令；以下配置不依赖项目作者的电脑、服务器或现有 Conda 环境。

## 环境要求

- 推荐 Python 3.11（Docker 镜像运行版本），Git，以及可联网安装依赖的 pip。
- SQLite 需支持 FTS5；向量模式另需能加载 sqlite-vec 扩展。使用官方 Python 发行版和 `requirements.txt` 中的依赖。
- 基础模式不需要云账号、模型 Key、Turnstile、GPU、Docker 或 Conda。
- AI 模式需要服务商 Key 和可访问的 API；BGE 模式第一次需联网下载模型。

安装与启动命令见 [README](../README.md#本地快速启动)。命令直接使用虚拟环境中的 Python，无需修改 PowerShell 执行策略或激活环境。

## 配置如何生效

`scripts/setup-local.py` 从 `.env.example` 创建 `.env`，生成随机 `SESSION_SECRET` 和 scrypt 密码哈希。密码输入不回显，也不写入明文。已有文件时退出，不覆盖原配置。

已有 `.env` 请检查以下变量：

| 变量 | 本地要求或含义 |
| --- | --- |
| `APP_ENV` | `dev`；本地不强制 Turnstile |
| `DATABASE_URL` | SQLite **文件路径**，默认 `data/tickets.db`，不是 `sqlite:///...` |
| `SESSION_SECRET` | 私有随机值，至少 32 字节；签名会话和加密模型 Key 依赖此值 |
| `ADMIN_USERNAME` | 本地默认 `admin` |
| `ADMIN_PASSWORD_HASH` | scrypt 哈希，不能填写明文密码 |
| `LLM_PROVIDER` | 首次配置 `disabled`；AI 可用 `mimo` 或 `auto` |
| `LOW_RISK_ASSISTANCE` | `true` 启用只读多轮 AI；`false` 保留正式评测保护 |
| `AUTO_APPROVE_LOW_RISK` | `true` 允许低风险辅助免人工预审，不代表授予权限 |
| `EMBEDDING_PROVIDER` | `disabled` 为 BM25；`local` 为 BGE 向量 |
| `MAX_LLM_DAILY` | 每日模型调用额度，默认 100；生成、审查和连接测试都消耗额度 |
| `SUPPORT_BUDGET_SECONDS` | 每轮 AI 辅助的共享模型调用等待预算，默认40秒；不改变引用和独立审查要求 |

手动生成会话密钥与密码哈希：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
.\.venv\Scripts\python.exe -m app.security
```

Linux / macOS 用 `.venv/bin/python` 替换解释器路径。分别将输出保存到 `.env` 的 `SESSION_SECRET`、`ADMIN_PASSWORD_HASH`；不要分享或提交输出。手动设置密码也应不少于 8 位。配置改变后重启。

进程环境变量优先于 `.env`，不会被文件覆盖。管理台保存的模型连接又优先于部署默认连接；回到环境中的 Key 或关闭模型前，先恢复部署默认。改变 `SESSION_SECRET` 会使旧会话失效，并使原加密模型 Key 无法解密，应重新配置 Key 或恢复默认。

## 知识库与数据

服务首次启动会创建表并导入基础规程。显式导入模拟 SOP、蓝屏和打印机自助文档：

```powershell
.\.venv\Scripts\python.exe scripts/import-demo.py --without-vectors
```

首次在服务启动前导入时，先执行 `python -m app.knowledge` 初始化基础规程，再执行上述导入命令。重复导入跳过已存在的对应文档，不替换管理员上传内容；这个命令不下载向量模型。启用 `EMBEDDING_PROVIDER=local` 后执行 `python -m app.knowledge reindex` 为有效段落重建向量。向量模型变更也必须重建，旧模型向量不能混用。

账号、工单、知识、审批规则和管理台模型配置保存在 SQLite 文件。停止服务不会清空数据。需要全新数据时，在 `.env` 中选择新的 `DATABASE_URL` 并保留旧文件，不为排错删除业务库。

## AI 模式与验收

1. 完成基础启动并导入模拟知识。
2. 按 README 设置 `LOW_RISK_ASSISTANCE=true` 和自己的演示 Key，或从管理台配置模型 API。
3. 重启后检查 `/health`：`llm_enabled=true` 仅表示已配置，不证明服务商连接成功。
4. 管理台测试连接成功只证明 API 与 JSON 输出可用，会产生真实请求和费用。
5. 员工提交“打印机提示缺纸”，补充“纸盒是空的”，检查回复是否基于新事实；尝试后确认解决或申请 IT。
6. 另一个员工账号应不能从“我的工单”或直接访问看到前一个账号的工单。

服务申请可在无模型模式验收：符合允许清单和条件时自动获批，禁用项目拒绝，超范围交人工。获批后仍需 IT 交付、员工验收，不把批准当作实际完成。

## Conda 可选启动

Conda 是另一种环境工具，不是项目必需条件。首次使用先创建环境并安装依赖：

```powershell
conda create -n ticket-agent python=3.11 -y
conda run -n ticket-agent python -m pip install -r requirements.txt
conda run --no-capture-output -n ticket-agent python scripts/setup-local.py
conda run -n ticket-agent python -m app.knowledge
conda run -n ticket-agent python scripts/import-demo.py --without-vectors
conda run --no-capture-output -n ticket-agent python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

已有环境不要重复创建；使用其他名称时替换 `ticket-agent`。

`scripts/run-local-conda.ps1` 另提供 **仅限本机的隔离验证** 入口：默认不加载项目 `.env`，使用 `data/local-verify.db`，关闭模型和向量，启动时设置本次管理员密码。此入口使用固定的本地验证会话密钥，不能用于公网或私有 Key 的长期保存。以前在此库中保存过模型覆盖配置时，应先恢复部署默认，否则数据库配置仍可能启用模型。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-conda.ps1 -ImportSimulatedKnowledge
```

`-EnvironmentName` 指定已有环境，`-Port 8010` 避开端口冲突。`-UseMimo` 要求当前进程环境已设置 `MIMO_API_KEY`，并启用只读辅助；`-UseVectors` 开启本地 BGE。真实 AI 请求可能计费，这些开关不是无模型启动所必需的。

## 运行工程测试

先建立临时目录，再运行 unittest。测试模拟模型调用、使用独立数据库，不操作部署服务器：

```powershell
New-Item -ItemType Directory -Force work | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Linux / macOS：

```bash
mkdir -p work
.venv/bin/python -m unittest discover -s tests -v
```

Conda 对应命令是 `conda run -n ticket-agent python -m unittest discover -s tests -v`。不要把生产库用于测试或评测。检索和生成实验见 [评测说明](evaluation.md)。

## 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 找不到模块 | 用启动时同一个环境的 `python -m pip install -r requirements.txt` 安装依赖 |
| 管理员无法登录 | 核对当前地址、账号、密码哈希和会话密钥；本地没有统一默认密码 |
| 配置改了不生效 | 重启；检查同名进程环境变量，以及管理台模型覆盖配置 |
| 端口 8000 被占用 | 改用 `--port 8010` 并访问对应地址，避免进入旧进程 |
| AI 一直转人工 | 检查辅助开关、模型连接/额度、知识导入；敏感请求或审查失败仍转 IT |
| 健康状态 `degraded` | 查看 `vector` 状态，确认下载与索引完成；HTTP 200 不等于向量可用 |
| 离线仍下载模型 | 确认 `EMBEDDING_PROVIDER=disabled` 未被环境变量覆盖 |
| `no such module: fts5` | 换用带 FTS5 的 SQLite / Python 发行版，这是必需能力 |
| 因生产配置缺失而无法启动 | 本地使用 `APP_ENV=dev`；公网按部署文档配置安全项 |
| 看不到另一人的工单 | 员工仅能查看自己的工单，跨账号不可见是预期行为 |
