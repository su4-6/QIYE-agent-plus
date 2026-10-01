# 个人主页与 Atlas 项目介绍

- `index.html`：苏启航的个人介绍、学习方向、实习目标和项目列表。
- `atlas-desk/index.html`：Atlas 的处理流程、能力与当前部署状态。

保留个人主页原来的暖白配色、顶部导航、字号、卡片和布局。首屏「查看项目」滚动到项目列表，不跳到单个项目；主页移除 H5 按钮，MiniPay 介绍页继续保留业务入口。

本目录是静态页面源码，可以通过 `python -m http.server 8861 --directory portfolio` 预览。生产页面挂载到已有 landing-personal 的新版本 ConfigMap，镜像和副本数保留；MiniPay H5 和三端后台继续使用原界面。

源码链接指向 `https://github.com/su4-6/QIYE-agent-plus`，默认主分支。Atlas 公网状态以实际部署验收记录为准。这里不存放 API 密钥、管理员密码或会话配置。
