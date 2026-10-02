# 管理台切换模型 API

登录 `/admin`，打开「模型 API」。默认连接来自部署环境；在线演示配置 MiMo，首次本地配置为无模型。保存后使用管理台选择的连接，恢复默认后重新使用环境配置。

1. 选择 MiMo、DeepSeek、OpenAI、硅基流动国内版或其他 OpenAI 兼容服务，填写供应商给出的模型 ID。
2. 第一次使用该 API 时填入演示专用 Key；已保存的配置留空保留该 API 的 Key。更换兼容服务地址必须填写新 Key，旧 Key 不会被发送到新地址。
3. 「测试连接与 JSON 输出」只发出一次短测试请求，不创建工单、不切换配置。供应商可能计费。此测试只验证连接和结构化输出，不能代表工单答复质量。
4. 「保存并切换」先再次测试，成功后才保存生效。失败保留原配置。新的 AI 请求使用新连接，已开始的草案和审查仍使用同一个连接。
5. 「恢复部署默认」回到环境变量中的模型和 Key，不覆盖环境变量。重启保留管理员的选择；切换记录保存版本、供应商、模型、时间与操作账号，不记录 Key。

在线演示使用共享管理员，全局切换会影响后续演示请求。请只填写演示专用 Key；Key 不回显，使用 Fernet 加密后存入 SQLite，密钥从服务器 `SESSION_SECRET` 派生，不能把会话密钥公开。备份同样需要保护。变更会话密钥后，旧的已保存 Key 需重新填写，或恢复部署默认。员工与匿名访客不能读取或修改模型配置；共享管理员仍能通过模型请求消耗已保存 Key 的额度，加密不防止这类额度使用。自己的私有部署不要公开管理员凭据。

MiMo 官方地址为 `https://api.xiaomimimo.com/v1`；DeepSeek 为 `https://api.deepseek.com/v1`；OpenAI 为 `https://api.openai.com/v1`。其他兼容服务需维护者在 `MODEL_API_ALLOWED_BASE_URLS` 中精确配置允许的 HTTPS 基础地址（逗号分隔），不能在该值中写 Key。网页不能任意指定服务器内网地址；供应商类型绑定官方地址，已配置 Key 不能换地址复用，调用不跟随重定向、不使用环境代理。

支持现有 Chat Completions JSON 流程。常规工单辅助按草案、审查阶段分别限制输出和等待时间，其他服务走原兼容调用。选择模型不会改变风险判断、引用校验、审批规则或员工确认流程，也不会自动执行安装与企业授权。

常规工单辅助默认使用 `mimo-v2.6-flash`，关闭草案与审查的深度思考，分别限制 1800 / 400 输出 tokens；独立审查、引用校验和最多一次修正继续保留。每轮模型调用共享 `SUPPORT_BUDGET_SECONDS` 等待预算（默认40秒，运行时限制在5–60秒），草案单次最多18秒，审查最多12秒，并受剩余预算限制。耗尽预算时不发布未审查建议，进入原人工接管。浏览器提交与跟进设置60秒超时提示，避免无限等待；超时先核对“我的工单”，不要重复提交。计时预算不是供应商响应速度承诺。

MiMo 2.6 Flash 的官方 ID 为 `mimo-v2.6-flash`；型号和深度思考开关见 [MiMo 官方说明](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/text-generation/deep-thinking)。管理台仍允许输入其他兼容型号，不把这一默认值当作对所有模型的质量保证。

来源：[MiMo Chat API](https://mimo.mi.com/docs/en-US/api/chat)、[DeepSeek 首次调用](https://api-docs.deepseek.com/)、[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Fernet](https://cryptography.io/en/latest/fernet/)。

## 硅基流动国内版 · Qwen3-8B

登录管理台 → 模型 API → API 类型选择 **硅基流动（国内版）**。默认地址为 **https://api.siliconflow.cn/v1**，模型 ID 为 **Qwen/Qwen3-8B**（区分大小写）；只填写国内版控制台创建的演示专用 Key，点击「测试连接与 JSON 输出」，成功后「保存并切换」。国际版账号和地址不适用这项配置。Key 不需要写入源代码、README 或聊天。

服务端对该地址的 Qwen3-8B 请求明确发送 **enable_thinking=false**，覆盖连接测试、正式生成、辅助草案、审查和修正。模型提供有依据的下一步，原引用校验、独立审查、限时预算、规则审批与员工确认继续保留。非思考参数见 [硅基流动官方接口](https://docs.siliconflow.cn/docs/api/chat-completions-post)。

保存成功前仍使用原连接；保存成功后新请求使用硅基流动，原 MiMo Key 和历史保留。供应商可能限流、调整模型或收费，请在自己的模型详情核对当前价格和额度。连接成功不代表工单质量或速度已达标，应另用模拟工单检查澄清、补充和针对性建议。此接入已完成模拟 API 与本地测试；真实调用需要维护者或体验者自行配置有效 Key。
