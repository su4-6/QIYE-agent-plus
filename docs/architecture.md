# 系统架构

系统提供员工服务台和 IT 管理台，共用 FastAPI 接口。员工看到自己的问题、答复和进度；管理员处理人工队列，管理知识、规则、模型连接并查看审计。当前 Web 入口使用演示租户 demo，没有企业租户管理或 SSO。

## 业务与 Agent 分支

~~~mermaid
flowchart TD
    U[员工账号与签名会话] --> API[FastAPI / 归属与 CSRF 检查]
    A[管理员账号与签名会话] --> API
    API --> K{工单类型}
    K -->|服务申请| SP[版本化审批规则]
    SP -->|允许| D[等待 IT 交付 / 员工验收]
    SP -->|禁用| N[拒绝并记录依据]
    SP -->|超范围或敏感| H[人工队列]
    K -->|报修| G[LangGraph 分诊 / 查询改写]
    G --> V[BGE + sqlite-vec]
    V -->|关闭或失败| B[FTS5 BM25]
    V --> E[证据与操作风险判断]
    B --> E
    E --> M{答复模式}
    M -->|只读辅助| L[模型结合最近对话生成追问或建议]
    L --> C[引用 / 敏感内容 / 建议审查]
    C -->|通过| R[员工继续排查与反馈]
    C -->|有限修正仍失败| H
    M -->|正式模式| F[冻结评测发布门槛]
    F -->|未通过| H
    F -->|通过| S[选择句子 ID / 服务端组装原文]
    S --> R
    E -->|高风险或证据不足| H
    R -->|补充| G
    R -->|未解决或主动申请| H
    H --> D
    R -->|确认解决| Z[关闭 / 可重开]
    D -->|验收| Z
    API --> DB[(SQLite WAL / 工单 / 对话 / 知识 / 审计)]
~~~

服务申请审批使用结构化条件和规则版本，不以 RAG 分数或模型自由答复授予权限。批准后由 IT 交付、员工验收；新补充改变范围或重开时需要复核。

故障辅助要求显式开启 LOW_RISK_ASSISTANCE，结合首次描述、最新补充与已尝试步骤生成下一步。事实性操作建议需要知识依据和引用校验，并经独立模型审查，失败最多修正一次；缺信息可追问，无法可靠处理则交接。AI 不执行设备操作、不自动结单。

正式模式默认关闭辅助，保留原句子 ID 协议和评测保护。目前冻结测试未通过 95% 放行精确率目标。线上演示开启辅助，不表示该质量目标已经通过，也没有完整的自动解决率统计。

## 模块入口

| 模块 | 职责 |
| --- | --- |
| app/main.py、schemas.py | 路由、输入校验、页面与访问控制入口 |
| app/agent.py | LangGraph 节点与分支 |
| app/assistance.py、llm.py | 多轮辅助、模型输出与审查 |
| app/knowledge.py、evidence.py | 文档版本、分块、检索与证据策略 |
| app/approvals.py | 服务申请规则与决策 |
| app/repository.py、workflow.py | 工单保存、继续处理、状态与并发版本控制 |
| app/database.py | SQLite 表、迁移、事务和升级备份 |
| app/employees.py、security.py | 员工身份、密码、会话、CSRF、限流与 Turnstile |
| app/model_api.py | 连接配置、加密 Key、端点允许清单与切换审计 |
| app/retrieval_health.py、metrics.py | 向量自检与管理员统计 |

## 检索与存储

BGE 中文模型配合 sqlite-vec 进行有效段落的精确余弦检索；关闭向量、模型失败或索引不可用时回退 FTS5 / BM25。保留 RRF 混合与轻量业务重排作为实验配置，当前没有 ANN 或 Cross-Encoder。

引用记录文档版本、段落 ID 和稳定 chunk_key，并校验租户与有效性。有效引用不自动证明语义支持；证据分数也不是正确概率。逻辑回归多信号实验仍属于离线对照，没有替代在线默认策略。

SQLite 保存账号、工单、对话、知识、规则、模型配置及审计。状态与审计同事务落库；使用条件更新防止重复审批和过期操作。部署为单副本，升级迁移前在线备份，不能仅复制 WAL 模式下的主数据库文件。

## 认证与 API

- 员工工单绑定账号 ID 和租户，不按姓名筛选。所属员工写入需会话与 CSRF。
- 报修创建 API 仍兼容匿名访客，返回随机访问凭据；服务申请必须登录。员工页面按账号使用，旧访客工单凭原凭据访问，不自动迁移归属。
- 管理员签名 Cookie 有限时、HttpOnly 和 SameSite 属性，生产使用 Secure；管理员写入需 CSRF。
- 生产注册、登录与工单提交使用 Turnstile，并实施限流。当前只有一个管理员配置，没有管理员多角色和 MFA。

| 接口 | 权限与用途 |
| --- | --- |
| POST /api/v1/employee/register、login | 员工认证；生产需 Turnstile |
| GET /api/v1/employee/tickets | 当前账号的工单列表 |
| POST /api/v1/tickets | 报修兼容匿名；服务申请需员工会话；登录写入需 CSRF，生产需 Turnstile |
| GET /api/v1/tickets/{id} | 所属员工会话；旧访客记录使用访问凭据 |
| POST /api/v1/tickets/{id}/messages、actions | 所属员工会话与 CSRF，或旧访客凭据兼容 |
| POST /api/v1/admin/login | 配置的管理员账号与密码；生产需 Turnstile |
| POST /api/v1/admin/tickets/{id}/work、approval | 管理员会话与 CSRF |
| GET /api/v1/admin/tickets/{id}/audit-logs | 管理员会话 |
| GET / POST /api/v1/admin/knowledge | 管理员读取 / 导入；写入需 CSRF |
| GET / PUT /api/v1/admin/service-policy | 管理员读取 / 发布规则；写入需 CSRF |
| GET / PUT /api/v1/admin/model-api | 管理员读取 / 切换模型；写入需 CSRF |
| POST /api/v1/admin/model-api/test、restore | 管理员会话与 CSRF；测试会实际调用模型 |
| GET /api/v1/admin/retrieval-metrics | 管理员租户统计，支持 1 / 7 / 30 天 |
| GET /health、/health/live | 数据库与向量状态 / 仅进程存活；不调用付费模型 |

开发 API 文档在 /api/docs，生产关闭。模型配置读取不返回 Key 或密文；加密依赖私有 SESSION_SECRET。共享演示管理员仍能消耗已保存 Key 的额度，见 [模型配置](model-api.md)。

/health 检查真实向量覆盖与运行状态，不在查询时下载模型或调用付费服务。HTTP 200 和进程存活不能替代完整的业务验收。

相关文档：[本地运行](local-development.md)、[员工工作流](employee-workflow.md)、[评测](evaluation.md)、[部署](deployment.md)。
