# Atlas Desk · 企业工单智能处理 Agent

面向公开作品演示的企业 IT 服务台系统。访客提交模拟工单后，系统进行风险分诊、混合检索、证据判断和引用校验；高风险或证据不足的问题进入人工处理。系统不会声称已经自动执行生产、权限或数据变更。

## 已实现能力

- FastAPI API 与响应式中文演示页面。
- LangGraph 编排“分诊 → 查询改写 → 混合召回 → 证据判断 → 生成与引用校验”。
- SQLite FTS5 BM25 与 `sqlite-vec` 中文向量召回，使用 RRF 和业务信号重排。
- 版本化知识库，管理员可导入 TXT、Markdown 和文本 PDF。
- 访客工单访问凭证、管理员签名会话、CSRF 校验、租户过滤、限流和 Turnstile。
- 高风险及低置信度人工接管，审批状态使用条件更新防止重复处理。
- 支持从 `MIMO_API_KEY` 自动接入 MiMo；模型失败或引用校验失败时转人工。
- 工单、状态变化和审计日志同事务保存。
- Docker Compose 部署和 30 条固定工单检索评测。

## 处理流程

```text
FastAPI 接收工单
→ 服务端确定租户并校验访问边界
→ LangGraph 进行意图识别和风险判断
→ 查询改写
→ FTS5 BM25 与 sqlite-vec 并行召回
→ RRF 合并、去重和轻量重排
→ 判断资料相关度
→ 有依据时生成建议并校验引用
→ 高风险或证据不足时转人工
→ SQLite 保存工单、答案、来源和审计记录
```

架构图见 [docs/architecture.md](docs/architecture.md)，本次实测见 [docs/evaluation-results.md](docs/evaluation-results.md)。

## 本地启动

### Conda 一键启动（推荐）

项目已使用名为 `ticket-agent` 的 Conda 环境时，在 PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-conda.ps1
```

根据提示设置一个仅用于本机的管理员密码，然后打开
`http://127.0.0.1:8000`。脚本使用独立的 `data/local-verify.db`，关闭窗口或按
`Ctrl+C` 即可停止，不会连接线上 K3s，也不会读取项目现有 `.env` 中的模型密钥。

需要使用 Windows 环境变量中的 `MIMO_API_KEY` 做真实生成时，增加开关：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-conda.ps1 -UseMimo
```

管理员首页会显示当前生成模型；也可运行 `conda run -n ticket-agent python
scripts/check-llm.py` 做不含真实业务数据的模型与引用冒烟检查。

### Python 虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000`。开发环境不强制 Turnstile；没有大模型密钥时会返回有引用的知识库答复。

首次使用本地向量模型会下载约 90 MB 的 `BAAI/bge-small-zh-v1.5`。如需主动重建向量：

```powershell
python -m app.knowledge reindex
```

## 主要接口

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| `POST` | `/api/v1/tickets` | 公开，生产环境要求 Turnstile |
| `GET` | `/api/v1/tickets/{id}` | 工单访问凭证 |
| `POST` | `/api/v1/admin/login` | 管理员密码 |
| `GET` | `/api/v1/admin/tickets` | 管理员会话 |
| `POST` | `/api/v1/admin/tickets/{id}/approval` | 管理员会话 + CSRF |
| `GET` | `/api/v1/admin/tickets/{id}/audit-logs` | 管理员会话 |
| `GET/POST` | `/api/v1/admin/knowledge` | 管理员会话，写操作加 CSRF |
| `GET` | `/health` | 健康检查 |

旧的 `/工单` 和 `/tickets` 只保留提交兼容。旧查询和审批路径已删除，避免绕过新权限层。

## 测试与评测

```powershell
python -m unittest discover -s tests -v
python -m evaluation.run
```

固定评测集包含 30 条模拟工单。当前本机结果：原关键词检索 Recall@3 为 93.33%，混合检索为 100%；中位检索延迟 10.30 ms，P95 为 11.90 ms。数据规模扩大、模型变化或部署到服务器后必须重新运行，不应将这组数字外推为生产性能。

## 生产部署

复制 `.env.example` 并填写生产配置。生产启动会强制检查管理员、会话和 Turnstile 配置。完整步骤见 [docs/deployment.md](docs/deployment.md)。

安全提醒：旧 `.env` 曾被 Git 跟踪。部署前必须在服务商后台撤销旧模型密钥并创建新密钥；仅从当前版本删除文件不能消除历史泄露风险。
