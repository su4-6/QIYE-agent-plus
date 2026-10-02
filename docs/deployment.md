# 生产部署指南

本指南面向自行部署的维护者。本地运行使用 [本地指南](local-development.md)；生产必须配置认证、人机验证、HTTPS、持久化和备份。仓库中的域名、Gateway 名称及镜像仓库是演示环境实例值，部署到自己的环境时须调整。

## 必填配置与启动条件

1. Python 3.11 / Docker 或可用的 Kubernetes 集群，足够的磁盘和内存。
2. 私有随机 SESSION_SECRET（至少 32 字节）、ADMIN_USERNAME 与 scrypt ADMIN_PASSWORD_HASH；密码哈希用 python -m app.security 生成。
3. Cloudflare Turnstile site key 和 secret，允许自己的域名；生产启动会校验安全项非空且会话密钥长度足够。腾讯或其他 CDN 不替代此验证配置。
4. APP_ENV=production、持久化 DATABASE_URL，反向代理与 HTTPS。不要把 dev 模式直接暴露公网。
5. AI 需演示专用模型 Key / API 连接；启用多轮辅助需 LOW_RISK_ASSISTANCE=true。服务申请规则审批独立于模型。
6. 选择检索模式：local 使用 BGE，disabled 使用 BM25；不同模型向量不能混用。
7. 真实 .env、Secret、Key 和数据库不得进入 Git 或镜像；曾公开的旧 Key 应在服务商撤销。
8. 部署前核对当前可用资源与业务负载。历史 384 MiB / 1 CPU 隔离启动通过，不代表任意并发都足够；不要为部署 Atlas 停止或清理其他业务。

SQLite 使用单副本、单 worker。K3s Deployment 采用 Recreate 与 PVC，避免多个实例共享写库。正式评测保护仍未达到 95% 目标；开启只读辅助不是正式质量放行。

## Docker Compose：独立服务器

在受限位置配置项目 .env；Compose 会将 APP_ENV 强制设为 production，所以本地快速启动生成的空 Turnstile 配置不足以启动它。

~~~bash
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/health
~~~

现有 Compose 使用普通 Dockerfile 构建，并把 ./data 挂载到 /app/data。BGE 可能在首次初始化下载；需要构建时预加载模型时，用 Dockerfile.bge 构建，再在自己的 Compose 配置中指定该镜像或 Dockerfile。它使用 /opt/atlas-models 预置缓存并设置 HF_HUB_OFFLINE=1，不依赖运行时下载。

Nginx / 网关以 HTTPS 反向代理到 127.0.0.1:8000，不直接公开应用端口。普通 Dockerfile 的启动参数仅信任 127.0.0.1 的代理头，容器桥接时应按实际可信代理地址调整，不能把所有不可信客户端作为代理。代理传递 Host、X-Forwarded-For 与 X-Forwarded-Proto 后，应实际验证客户端 IP、限流和 Secure Cookie。

## K3s：现有演示集群或自建集群

[deploy/k3s](../deploy/k3s/README.md) 的基础清单引用现有 Gateway、域名与命名空间，不能直接用于任意集群。它还保留旧 Gemini 配置。当前 BGE 版本通过 **deploy/overlays/local-bge** 覆盖，部署前必须先渲染并检查镜像、配置、资源与跨命名空间路由。

~~~bash
docker build -f Dockerfile.bge -t YOUR_REGISTRY/atlas-desk:YOUR_VERSION .
kubectl kustomize deploy/overlays/local-bge
~~~

在自己的镜像仓库发布并固定 digest；替换域名、Gateway / 命名空间引用、TLS 和 StorageClass 后，再按 K3s 文档创建私有 Secret 并发布。不要把未经修改的基础清单覆盖到已有 BGE 服务。

当前演示发布镜像、摘要与验证范围见 [部署历史](deployment-status-20261001.md) 最后一个版本记录；静态版本记录不能代替重新核对正在运行的 Pod 和配置。

## 发布验收

- Pod Ready，数据库路径正确，持久卷绑定，日志无重复迁移或模型下载失败。
- /health 数据库正常；BGE 模式向量 ready，BM25 模式向量 disabled，不能仅看 HTTP 200。
- 员工注册、登录、提交和自己的列表；另一账号不能读取或修改前一账号工单。
- 管理员登录、CSRF、接单、回复、补充、解决确认和重开；正常浏览器完成人机验证。
- 模拟申请覆盖自动获批、拒绝、人工审核；获批之后仍需交付和验收。
- 隔离验收真实模型的追问、针对新事实的建议、敏感请求交接；连接测试不替代答复验收。
- 重启后账号、工单、知识与模型配置保留；确认可回滚，监控 OOM、节点内存与现有业务。
- 公开演示管理员只能配低额度演示 Key；私有部署不要公开管理员密码。

## 备份与回滚

应用升级迁移前自动使用 SQLite 在线备份接口，在数据库旁的 backups 目录保存一致快照。维护者另需定期备份与恢复演练，保护数据和 SESSION_SECRET；仅备份 WAL 模式的主文件可能漏数据。

记录升级前镜像 digest、配置与数据库备份。失败时回退 Atlas 镜像与兼容配置，不删除 PVC。若新 schema 不能由旧代码读取，停止 Atlas 后从经过验证的备份恢复；不能在活跃写库时复制覆盖。模型 / 会话密钥改变时同步检查加密 Key 和索引兼容性。

历史容量实验、服务器快照和已发布版本均归档在 [部署历史](deployment-status-20261001.md)，不作为新服务器的通用启动条件。
