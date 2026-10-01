# `ticket.su46proj.site` 部署清单

## 上线前硬门槛

1. 在模型服务商后台撤销曾进入 Git 历史的旧密钥，创建新密钥；不要把新值提交到仓库。
2. 在 Cloudflare 创建仅允许 `ticket.su46proj.site` 的 Turnstile 小组件。
3. 生成管理员密码哈希：`python -m app.security`。
4. 生成至少 32 字节的随机 `SESSION_SECRET`，完成服务器 `.env`。
5. 检查服务器可用内存、磁盘、80/443 监听和现有容器。本地 BGE 已在 384 MiB / 1 CPU / 单 worker 下通过隔离容量验证。内网部署使用 384 MiB 容器上限和至少 320 MiB 节点保留余量，启动前 MemAvailable 至少 704 MiB；启动过程中监测余量，不足只停止 Atlas。公网流量的容量验收仍需单独执行，不能把内网启动证明当作持续服务能力。容量不足就停止发布；不静默切换检索模型或关闭向量，换模型必须另行重建索引与评测。
6. 检查 evaluation/policy.json 的发布门槛。当前独立测试未达到95%的自动放行精确率，应用默认提供人工审核流程；不得将校准集结果当作生产自动处理准确率。

## 部署方式

当前服务器已经运行 K3s 和 NGINX Gateway Fabric，优先使用
[`deploy/k3s`](../deploy/k3s/README.md) 中的清单复用现有 80/443 入口和通配符证书。
应用使用独立命名空间、ClusterIP 服务和持久卷；跨命名空间路由通过
`ReferenceGrant` 明确授权。服务器资源不足时只准备清单，不执行发布。

Docker Compose 保留为独立服务器或本机验收方案。

### 保持当前 BGE 检索的候选镜像

使用独立的 `Dockerfile.bge` 在构建时预下载 BGE，避免第一次线上请求下载模型。
`deploy/overlays/local-bge` 覆盖旧清单中的 Gemini 配置，并增加启动探针；原来的 Dockerfile 和基础清单保留。

```bash
docker build -f Dockerfile.bge -t atlas-desk:review-bge .
kubectl kustomize deploy/overlays/local-bge
```

镜像已发布到 `docker.io/suqihang/atlas-desk:20261001-assistance.2`，overlay 固定已验证的 digest。这个 overlay 包含生产路由，只有生产密钥配置齐全并完成公网容量检查后才能应用。当前内网部署的无 Secret 清单保存在 `deploy/releases/20261001/internal.yaml`，不包含 HTTPRoute。
已有知识库启动时只做本地向量生成与 SQLite 向量距离自检，不重写知识库，不调用大模型或远程向量服务。
工程测试覆盖重启自检、失败降级和不对远程模式新增启动调用。

本次本地 Linux 镜像在 384 MiB / 1 CPU / 单 worker、swap 禁用时导入 66 个模拟片段，处理 40 张模拟工单（并发 4），40/40 返回 201，无 OOM。
工作集约 343 MiB；cgroup 峰值达到 384 MiB，包含可回收缓存，不能只用工作集数值当作上线预算。
此验证关闭 LLM、使用独立临时数据卷，既不是线上负载证明，也不代表真实模型答复质量。

服务器已经完成授权的 MiniPay JVM / 静态服务瘦身；Atlas 在独立命名空间运行，已配置 MiMo，BGE / sqlite-vec 自检通过。内网管理员登录、知识读取、高风险工单转人工和工单令牌访问均通过。Atlas 部署前后原有 25 个工作负载规格、镜像及原 PVC UID 保持一致。已通过 Cloudflare 插件配置生产 Turnstile 并添加 Atlas 公网路由；公网 API 与模拟审批验收通过，用户已确认正常浏览器真实人机验证与工单创建成功。完整状态见 [部署记录](deployment-status-20261001.md)。

## Docker Compose 部署与验证

```bash
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/health
```

Nginx 为 `ticket.su46proj.site` 配置独立虚拟主机并反向代理到 `127.0.0.1:8000`。应用端口不要直接暴露到公网；TLS 证书和 Cloudflare DNS 生效后，再执行公开页面、工单提交、访问凭证、管理员审批和审计日志的端到端检查。
代理必须传递 `Host`、`X-Real-IP`、`X-Forwarded-For` 和 `X-Forwarded-Proto`；应用只信任来自本机 Nginx 的代理头，限流才会按真实访客 IP 生效。

## 备份

升级迁移前，应用使用 SQLite 在线备份接口生成一致快照，保存到数据库所在目录的 backups 子目录。日常备份也应使用在线备份接口并至少保留最近7份，避免仅复制处于WAL模式的主数据库文件。恢复演练检查工单、知识文档、向量模型标识和审计日志。切换向量模型后运行 `python -m app.knowledge reindex`，不同模型产生的向量不能混用。

员工/IT 工作流版本及原镜像、旧工单保留验证见 [部署状态](deployment-status-20261001.md) 与 [工作流说明](employee-workflow.md)。

最新版本启用 `LOW_RISK_ASSISTANCE=true` 的个人只读辅助；`ADMIN_USERNAME=admin`，密码哈希与会话/Turnstile/MiMo密钥沿用原Secret。仅更新Atlas，迁移前备份SQLite，不清理原镜像、PVC或MiniPay工作负载。评测策略与历史报告保持原值，工程和真实模型验收单独记录。
