# 个人主页与 Atlas 部署记录

2026-10-01。本次范围以最新确认的要求为准：保留原主页风格，重写个人介绍，增加 Atlas 项目与介绍页；MiniPay H5、商户端、运营端、管理端使用原界面。

## 已发布的页面

- https://su46proj.site/ ：首屏介绍苏启航的学习方向、实践方式和求职目标。「查看项目」滚动到包含 MiniPay、Atlas 的项目列表；移除首屏 H5 按钮。
- https://su46proj.site/atlas-desk/index.html ：沿用原主页的配色、导航、卡片与排版，介绍工单分诊、检索、引用校验、人工接管与管理能力。
- https://pay.su46proj.site/ ：恢复改版前的原始介绍。H5 与三个后台入口、原页面内容保持一致。

首页的关于我、技术栈、联系信息与页脚围绕本人重新整理。MiniPay 以团队项目中的后端与部署实践呈现；Atlas 为独立项目。后续项目的位置保留。

验收覆盖 320、390、768、1024、1440、1920、2443、2560px，包含真实点击、标题可见性、直接访问联系锚点、项目往返和原 MiniPay 入口。公网文件与本地验收文件 SHA256 一致。普通、不带查询参数的主页也已核验。腾讯 CDN 部分节点曾返回旧内容，已提交四个 URL 的缓存刷新。刷新后在普通 URL 上复验 390、1440、2443px 的真实点击和页面往返，此前 41 项检查通过；本次增加 Atlas 工单入口与真实新标签页跳转验证，最终 45 项检查通过，四个 URL 缓存刷新均完成。

## Atlas 的实际状态

Atlas 已在服务器 K3s 的独立 `atlas-desk` 命名空间运行，1 个 Pod，内存上限 384MiB。使用用户指定环境变量中的 MiMo，模型 `mimo-v2.5-pro`；旧 Gemini 密钥没有传入服务器。

镜像为 `docker.io/suqihang/atlas-desk:20261001-bge.1`，部署固定摘要：

```
sha256:8dd94bfd4f6d8c4505efe4b84356007d9038719b064237add41a2cc63a2c501f
```

内网健康检查通过：数据库正常，BGE / sqlite-vec 向量检索就绪，66/66 分块可用，MiMo 已配置。管理员登录、知识读取、模拟高风险工单转人工、无访问令牌拒绝读取（404）和有效令牌读取均通过。验收使用模拟工单，不调用付费模型生成。

**公网入口已开放：https://ticket.su46proj.site/ ，管理员页面为 `/admin`。** 已通过 Cloudflare 插件创建仅允许该域名的 managed Turnstile，并将配置写入 Atlas 的 Kubernetes Secret；应用使用 `APP_ENV=production`。新增 HTTPRoute 的 Accepted / ResolvedRefs 均为 True。工单域名复用原 Cloudflare 通配符 DNS 指向服务器；主页与 MiniPay 仍走原腾讯 CDN，没有更改原 DNS / CDN 设置。

公网 HTTPS 健康、生产 widget 配置、无验证令牌拒绝提交、匿名管理员拒绝访问、管理员登录、Secure / HttpOnly / SameSite=Strict Cookie、工单列表、知识读取、审批 CSRF 保护、模拟工单审批与审计、匿名工单拒绝读取和退出登录共 13 项检查通过。验收未调用付费模型生成。自动化浏览器未完成 managed Turnstile 挑战；2026-10-01 用户已在正常浏览器确认“成功创建工单”，补齐真实人机验证与访客提交验收。该项证据来自用户确认，非自动化浏览器结果。没有替换成测试密钥或绕过验证。

生产转换仅更新 Atlas Secret / ConfigMap / Deployment，并新增 Atlas HTTPRoute / ReferenceGrant。原有工作负载规格、镜像及 PVC 身份核对一致。转换前后节点可用内存快照为 484 / 609 MiB；这不是公网持续负载测试。当前无 Secret 的生产清单和检查结果保存在 `deploy/releases/20261001/production.yaml`、`atlas-production-deployment.json`、`atlas-public-smoke.json`；`internal.yaml` 保留为先前内网部署记录。

管理员密码、会话密钥及模型密钥只留在私密配置和 Kubernetes Secret 中，不进入交付包、截图、公开页面或镜像。

## 原环境与瘦身边界

本次 Atlas 部署前后，原有 25 个 Deployment / StatefulSet / DaemonSet 的规格和镜像保持一致，原有 PVC 的 UID 保持一致。最终另与瘦身前的 MiniPay 基线核对，所有原 MiniPay 镜像均一致，原服务就绪，K3s `/readyz` 正常。没有删除原镜像或数据卷，也没有执行全局镜像清理。

此前已按授权完成的瘦身仍保留：

- 三个 BFF 的 JVM 堆调整为 `-Xms16m -Xmx96m`。
- identity、wallet、payment、miling 的 JVM 堆调整为 `-Xms24m -Xmx128m`；保留各自附加参数和原容器内存上限。
- maintenance-page、landing-personal、landing-pay 的资源请求调整为 16Mi、上限 64Mi；原镜像和副本数保留。
- K3s 服务增加 Go 运行时软内存控制：`GOMEMLIMIT=512MiB`、`GOGC=50`，文件为 `/etc/systemd/system/k3s.service.d/60-codex-memory.conf`。控制面及原服务验证通过。最新用户要求后没有继续修改 K3s 服务配置。

MySQL、Redis、RabbitMQ、Seata、网关、四个业务前端和原持久化数据均保留。最终验收时 Atlas 工作集约 302MiB、节点可用内存约 502MiB，原服务均 Ready；详细状态保存在本地 `deployment-final-state.json`，该读数是一次部署验收快照，不代表长期压力测试结果。

## 回退与后续

若 Atlas 占用异常，只停止新增服务：`kubectl scale deployment/atlas-desk --replicas=0 -n atlas-desk`，保留 PVC。该命令不修改原 MiniPay 服务。如需关闭公网入口，只移除本次新增的 `minipay/atlas-desk` HTTPRoute，不操作原网关或其他路由。

静态主页的原始 ConfigMap 未覆盖，原页面挂载与内容备份保存在本地私密工作记录中。回退只恢复 landing-personal / landing-pay 的 html 挂载，不换镜像。

JVM 与静态服务原资源回退文件仍在 本地 `server-slimming/rollback/` 交付目录；应只针对上述实际调整的服务使用，先核对现状。K3s 的软内存控制如需回退，先核对上述独有文件内容，再移除该文件并重新加载服务。交付不自动执行回退。

早期全站改版的 H5 / B 端预览和压缩包不属于本次发布范围。以当前审核页、本文与上线记录为准。

## 主分支更新

Atlas 的 11 个已有提交和本次启动自检、主页源码、非密钥部署清单与记录已经快进合入 `main` 并正常推送，没有强制覆盖历史。本地已切换到 `main`，主页和 Atlas 介绍中的源码链接指向默认主分支。48 项工程测试通过，`.env`、私密配置、令牌和临时工作目录未加入新提交。
