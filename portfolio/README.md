# 个人主页与 Atlas 项目介绍

- `index.html`：苏启航的个人介绍、学习方向、实习目标和项目列表。
- `atlas-desk/index.html`：Atlas 的处理流程、能力与当前部署状态。

个人主页采用暖白配色、顶部导航与项目卡片。首屏「查看项目」滚动到项目列表，项目介绍页提供对应体验入口。

本目录是静态页面源码。从仓库根目录运行 `python -m http.server 8861 --directory portfolio`，访问 `http://127.0.0.1:8861/` 预览。它不是 FastAPI 的员工服务台或管理台，不包含工单后端。作者的生产静态页面通过已有 landing-personal 服务的 ConfigMap 发布；其他部署可使用普通静态托管。

源码链接指向 `https://github.com/su4-6/QIYE-agent-plus` 主分支。Atlas 介绍页包含公开的演示管理员账号与专用密码，用于体验共享演示；不是私有部署的默认密码。本目录不存放模型 Key、云服务凭据或会话密钥，部署到其他环境时须调整链接和演示账号说明。
