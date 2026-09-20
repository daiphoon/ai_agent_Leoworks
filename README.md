# AI Agent 全栈工程师训练营作业

这是按周次整理的课程作业仓库。每个作业保持独立目录、独立 README 和可重复验证方式，避免不同周的代码、依赖与说明混在一起。

## 目录

| 周次 | 作业 | 说明 |
| --- | --- | --- |
| Week 01 | [LLM 统一模型调用服务](week01/llm_gateway/README.md) | DeepSeek V4 Pro/Flash 的两种 API 协议统一网关 |
| Week 02 | [工具治理与权限状态机](week02/tool_governance/README.md) | 高风险转账工具的校验、授权、审批、脱敏与审计演示 |

## 后续作业约定

- 新作业放在 `week02/<作业名>/`、`week03/<作业名>/` 等独立目录。
- 每个作业目录应包含自己的 README、依赖声明、测试或验证脚本，以及仅含假值的 `.env.example`。
- 不把 API Key、Token、Cookie、真实业务数据或本地 `.env` 上传到此公共仓库。
