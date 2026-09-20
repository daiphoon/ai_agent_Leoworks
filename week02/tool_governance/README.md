# Week 02：工具治理与权限状态机

本作业实现一个教学用工具治理框架，并完成高风险 `transfer` 转账工具接入。所有数据和副作用均为内存中的模拟数据；默认离线演示不会调用真实 Shell、银行系统或模型服务。

## 已覆盖的治理链路

- Pydantic 严格参数校验，拒绝额外字段注入。
- 执行期权限状态机：deny 规则、plan 只读、工具白名单、RBAC、业务预检与一次性审批。
- 高风险转账的参数绑定审批、超时保护和结果脱敏。
- 审计记录，以及非幂等写操作超时后的 `TIMEOUT_UNKNOWN` 表达。

## 运行

需要 Python 3.11+（本作业使用 `StrEnum` 和 `asyncio.timeout`）。

```bash
cd week02/tool_governance
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python tool_governance_demo.py
```

可选的 `--agent` 模式才会访问 DeepSeek。可参照 `.env.example` 在终端环境中设置真实 `DEEPSEEK_API_KEY`；不要提交 `.env` 或密钥。

## 文件说明

- `tool_governance_demo.py`：已完成的作业源码。课程原始 TODO 注释被保留，方便对照六项任务。
- `tests/test_tool_governance.py`：转账链路的五项验收测试。
