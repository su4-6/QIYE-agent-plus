# Atlas Desk · 企业工单智能处理 Agent

面向公开作品演示的企业 IT 服务台系统。访客提交模拟工单后，系统进行风险分诊、混合检索、证据判断和引用校验；高风险或证据不足的问题进入人工处理。系统不会声称已经自动执行生产、权限或数据变更。

## 已实现能力

- FastAPI API 与响应式中文演示页面。
- LangGraph 编排“分诊 → 查询改写 → 混合召回 → 证据判断 → 生成与引用校验”。
- SQLite FTS5 BM25 与 `sqlite-vec` 中文向量召回，使用 RRF 和业务信号重排。
- 版本化知识库，管理员可导入 TXT、Markdown 和文本 PDF。
- 访客工单访问凭证、管理员签名会话、CSRF 校验、租户过滤、限流和 Turnstile。
- 高风险、证据不足及评测发布保护触发时人工接管，审批状态使用条件更新防止重复处理。
- 支持从 `MIMO_API_KEY` 自动接入 MiMo；模型失败或引用校验失败时转人工。
- 工单、状态变化和审计日志同事务保存。
- Docker/K3s 部署配置、35 项工程测试、60 段模拟语料和 180 条固定评测查询。

## 处理流程

```text
FastAPI 接收工单
→ 服务端确定租户并校验访问边界
→ LangGraph 进行意图识别和风险判断
→ 查询改写
→ FTS5 BM25 与 sqlite-vec 双路召回
→ RRF 合并、去重和轻量重排
→ 判断资料相关度
→ 校准阈值与独立测试发布门槛通过后生成建议并校验引用
→ 高风险、证据不足或发布保护触发时转人工
→ SQLite 保存工单、答案、来源和审计记录
```

架构图见 [docs/architecture.md](docs/architecture.md)，最新实测见 [工程评测报告](docs/engineering-evaluation-20261001.md)，可复制的简历描述见 [简历证据](docs/resume-evidence.md)。

当前校准阈值在独立测试集上未达到 95% 的放行精确率，自动建议发布保护默认开启。提交后会展示资料并等待人工审核；这是实际评测结果触发的保护。MiMo 已完成 30 次真实生成实验，原始答案与语义审查单独保存。

## 本地启动

### Conda 一键启动（推荐）

项目已使用名为 `ticket-agent` 的 Conda 环境时，先进入项目目录，再启动。在你当前电脑上：

```powershell
Set-Location -LiteralPath "C:\Users\hp\Desktop\Agent学习\企业工单智能处理 Agent 系统练习"
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-conda.ps1
```

根据提示设置一个仅用于本机的管理员密码，然后打开
`http://127.0.0.1:8000`。脚本使用独立的 `data/local-verify.db`，关闭窗口或按
`Ctrl+C` 即可停止，不会连接线上 K3s，也不会读取项目现有 `.env` 中的模型密钥。

需要使用 Windows 环境变量中的 `MIMO_API_KEY` 做真实生成时，增加开关：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-conda.ps1 -UseMimo -UseVectors -ImportSimulatedKnowledge
```

`-UseVectors` 开启本地 BGE；`-ImportSimulatedKnowledge` 显式导入 60 个模拟 SOP 并重建向量，不替换管理员文档。首次下载模型需要联网。此次已导入本机 `data/local-verify.db`，共 66 个有效段落。

管理员入口是启动窗口打印的 `/admin` 地址，密码使用本次启动时设置的值。页面会显示模型配置、检索健康和发布保护状态。`-UseMimo` 启用生成器，当前发布保护仍要求人工审核，不保证每张工单都会调用模型。

真实生成对照通过后面的评测命令查看，结果包含每条答案及引用，调用上限明确。旧 `scripts/check-llm.py` 也可作少量模型冒烟检查，**不计入本次已经完成的30次实验**；运行它会另外调用模型。

### Python 虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000`。开发环境不强制 Turnstile；未通过发布门槛或证据不足时进入人工审核；未启用模型且具备合格证据时可返回可追溯知识库资料。

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
| `GET` | `/api/v1/admin/retrieval-metrics?days=7` | 管理员会话，窗口支持1/7/30天 |
| `GET` | `/health` | 数据库及真实向量状态检查，不调用模型 |
| `GET` | `/health/live` | 进程存活检查 |

旧的 `/工单` 和 `/tickets` 只保留提交兼容。旧查询和审批路径已删除，避免绕过新权限层。

## 测试与评测

```powershell
conda run -n ticket-agent python -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\scripts\evaluate-conda.ps1 -Performance
# 可选：每次新实验最多30次MiMo调用；关闭自动重试，每次最多1000输出tokens
powershell -ExecutionPolicy Bypass -File .\scripts\evaluate-conda.ps1 -LiveMimo -Performance
```

最新评测为 60 段模拟语料／180 问，主题分组校准与测试各90问。测试60条可回答查询的 Hit@3：旧关键词40%，BM25 85%，真实向量93.33%，混合86.67%。本地关闭LLM的300次HTTP工作流全部成功；35项工程测试通过。指标定义、失败案例、时延及内存见[报告](docs/engineering-evaluation-20261001.md)。

评测数据库临时隔离，不改现有工单库。模型原始结果可用 `--reuse-generation` 复用，绝不发送LLM请求。历史六段／30问实验保留在[旧评测记录](docs/evaluation-results.md)，不能与新语料混用提升数字。

工单新增 `evidence_score`、`handoff_reason` 和 `request_id`；`confidence` 仅保留兼容别名。评分不是正确概率。向量使用精确余弦扫描，当前未使用 ANN 或 Cross-Encoder。首次升级前自动在线备份SQLite；限流桶按绝对过期时间清理。

## 生产部署

复制 `.env.example` 并填写生产配置。生产启动会强制检查管理员、会话和 Turnstile 配置。完整步骤见 [docs/deployment.md](docs/deployment.md)。

安全提醒：旧 `.env` 曾被 Git 跟踪。部署前必须在服务商后台撤销旧模型密钥并创建新密钥；仅从当前版本删除文件不能消除历史泄露风险。
