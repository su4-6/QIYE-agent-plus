# `ticket.su46proj.site` 部署清单

## 上线前硬门槛

1. 在模型服务商后台撤销曾进入 Git 历史的旧密钥，创建新密钥；不要把新值提交到仓库。
2. 在 Cloudflare 创建仅允许 `ticket.su46proj.site` 的 Turnstile 小组件。
3. 生成管理员密码哈希：`python -m app.security`。
4. 生成至少 32 字节的随机 `SESSION_SECRET`，完成服务器 `.env`。
5. 检查服务器可用内存、磁盘、80/443 监听和现有容器。启动后可用内存低于 700 MiB 时，将 `EMBEDDING_PROVIDER` 改为 `gemini` 或 `disabled`，重建索引并重新评测。

## 部署方式

当前服务器已经运行 K3s 和 NGINX Gateway Fabric，优先使用
[`deploy/k3s`](../deploy/k3s/README.md) 中的清单复用现有 80/443 入口和通配符证书。
应用使用独立命名空间、ClusterIP 服务和持久卷；跨命名空间路由通过
`ReferenceGrant` 明确授权。服务器资源不足时只准备清单，不执行发布。

Docker Compose 保留为独立服务器或本机验收方案。

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

停写或短暂停服务后复制 `data/tickets.db`，并至少保留最近 7 份。恢复演练必须检查工单、知识文档、向量模型标识和审计日志。切换向量模型后运行 `python -m app.knowledge reindex`，不同模型产生的向量不能混用。
