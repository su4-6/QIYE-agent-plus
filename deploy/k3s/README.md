# K3s 部署清单使用说明

这不是通用的一键安装清单。首次部署请先阅读[生产部署指南](../../docs/deployment.md)，调整自己的域名、Gateway、命名空间引用、TLS、StorageClass 和镜像。当前 BGE 配置入口是 `deploy/overlays/local-bge`，基础目录保留旧 Gemini 配置，不能直接覆盖已有 BGE 服务。

这套清单针对当前服务器上的 K3s、NGINX Gateway Fabric 和
`minipay/minipay-gateway`。应用运行在独立的 `atlas-desk` 命名空间；只有
`HTTPRoute` 放在 `minipay` 命名空间，用 `ReferenceGrant` 安全访问应用的
`ClusterIP` 服务。这样无需新增公网端口，也无需复制通配符 TLS 私钥。

## 发布门槛

- 节点资源须重新测量并保留现有业务余量。历史隔离实验的 384 MiB 上限和 320 MiB 节点余量是当时的验收预算，不是任意服务器的通用条件；公网持续负载须单独验收。
- 镜像已经发布，并在 `kustomization.yaml` 中固定为不可变版本号或 digest。
- `ticket.su46proj.site` DNS 指向当前 Cloudflare 入口。
- 旧模型密钥已撤销，Turnstile 已限制到该子域名。

基础清单保留 Gemini 配置；当前已部署版本使用构建时预加载的本地 BGE，见 `../releases/20261001/internal.yaml`。不要直接将基础清单覆盖到当前服务。
Deployment 仅允许一个副本，并采用 `Recreate`，防止两个实例同时写同一个
SQLite 数据库。

## 创建密钥

在服务器的受限目录复制 `secrets.env.example`，填写真实值并设为仅管理员可读，
然后执行：

```bash
sudo k3s kubectl create namespace atlas-desk --dry-run=client -o yaml | sudo k3s kubectl apply -f -
sudo k3s kubectl -n atlas-desk create secret generic atlas-desk-secrets \
  --from-env-file=/受限目录/atlas-desk-secrets.env \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

密钥文件和生成后的 Secret YAML 都不能进入 Git。

## 应用与验收

下列基础生产清单包含公网路由。当前生产实例已配置 Turnstile，实际无 Secret 清单位于 `../releases/20261001/production.yaml`；重新部署前需要核对私有 Secret 与节点容量。当前实际部署状态见 [部署记录](../../docs/deployment-status-20261001.md)。生产配置齐全后，优先渲染 `../overlays/local-bge` 并核对固定镜像摘要及资源限制，再执行发布。

```bash
sudo k3s kubectl kustomize deploy/overlays/local-bge
# 确认生产 Secret、域名、网关、镜像和节点资源后再应用
sudo k3s kubectl apply -k deploy/overlays/local-bge
sudo k3s kubectl -n atlas-desk rollout status deploy/atlas-desk --timeout=120s
sudo k3s kubectl -n atlas-desk get pod,svc,pvc
sudo k3s kubectl -n minipay get httproute atlas-desk
curl --fail --resolve ticket.su46proj.site:443:127.0.0.1 https://ticket.su46proj.site/health
```

部署后继续验证员工认证与归属、旧访客凭据、管理员登录、审批冲突、审计日志和重启后的
SQLite 数据。若 Pod 接近 384 MiB 限制、节点发生内存压力或现有服务延迟上升，
立即回滚并扩容服务器。

## 临时停止 Atlas 公网服务

以下操作用于停止入口与应用，不是镜像版本回滚。版本回滚应按部署指南恢复记录的 Atlas 镜像与兼容配置；不操作其他业务。

```bash
sudo k3s kubectl -n minipay delete httproute atlas-desk
sudo k3s kubectl -n atlas-desk scale deploy/atlas-desk --replicas=0
```

保留 PVC 以便恢复数据；确认无需恢复后再单独删除数据卷。
