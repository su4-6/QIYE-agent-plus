# 测试与评测说明

工程测试、检索评测、真实模型样本和部署验收分别验证不同问题，不合并成一个“AI 正确率”。

## 工程测试

运行方法见 [本地开发](local-development.md#运行工程测试)。测试覆盖员工归属、权限、状态、审批、上下文校验、向量降级、备份和模型配置。2026-10-02 模型配置版本记录 89 项通过，详见 [部署历史](deployment-status-20261001.md)；新增改动应以实际运行结果为准。

## 离线检索评测

从仓库根目录执行，Windows 使用 `.\.venv\Scripts\python.exe`，Linux / macOS 使用 `.venv/bin/python`：

```powershell
# 复算冻结预留集；需本地 BGE 缓存，首次可能下载；不请求付费模型，不重调阈值
.\.venv\Scripts\python.exe -m evaluation.reproduce_heldout --output evaluation/results/heldout-reproduction

# 完整检索评测；需要本地 BGE 模型/缓存，可能产生首次下载
.\.venv\Scripts\python.exe -m evaluation.run --output evaluation/results/local-review
```

评测使用临时数据库，不写业务工单库。第二条命令未指定 `--publish-policy`，不会更新 `evaluation/policy.json`。重复实验使用不同输出目录，并保留分母、环境、方法和限制。

`scripts/evaluate-conda.ps1` 是既有实验快捷入口，**包含 `--publish-policy`，会写入发布策略**，不是日常启动或无改动复核命令。

## 真实模型实验

`evaluation.run --live-mimo` 会调用实际 MiMo，历史协议每次实验最多 30 次调用，并保存使用量和账本。使用自己的演示 Key、独立输出目录，先检查费用预算。`--reuse-generation` 复用已有结果；预留集复现命令不调用模型。

该实验验证正式模式的句子 ID 协议，不等同于员工辅助模式的完整多轮评测。新增知识、多轮 AI 与模型切换的少量实测不代表整体正确率或自动解决率。

## 历史报告索引

| 报告 | 范围 |
| --- | --- |
| [旧评测记录](evaluation-results.md) | 早期小语料与生成实验 |
| [工程与检索评测](engineering-evaluation-20261001.md) | 原 180 问分组评测、对照与消融 |
| [复测快照](review-followup-20261001.md) | b8dcbf3 版本的边界与负例 |
| [新预留集与协议实验](new-heldout-20261001.md) | 冻结后新增 60 题、多信号实验与 10 次协议样本 |
| [部署历史](deployment-status-20261001.md) | 分版本的工程、浏览器、隔离模型与发布验收 |

原 180 问中的 60 条可回答测试查询：旧关键词 Hit@3 为 40%，BM25 为 85%，向量为 93.33%，混合为 86.67%。后续新增 60 题中的 40 条可回答查询，向量 Hit@3 为 95%；这是另一组样本，不能混报。

正式模式的 95% **放行精确率目标仍未通过**。命中来源、引用结构合法、答案与问题相关、员工实际解决问题需要不同证据。当前工作流尚无完整的独立质量评测，不宣称“自动解决率 95%”。
