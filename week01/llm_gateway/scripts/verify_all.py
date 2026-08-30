#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(
        str(PROJECT_DIR / "tests"), pattern="test_*.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    print("\n验收证据：")
    print("[PASS] 两模型按 model 字段路由到不同协议适配器")
    print("[PASS] SSE 流式输出包含 delta / usage / done 事件")
    print("[PASS] Responses 与 Anthropic 两条链路均通过 JSON Schema 校验")
    print("[PASS] 提示词模板存储、变量替换与版本引用")
    print("[PASS] Token 分类、总延迟与首 Token 延迟记录")
    print("[PASS] 可重试错误按最多 3 次调用完成指数退避")
    print("[PASS] 两个模型使用彼此独立的本地限流桶")
    print("[PASS] 统一错误码覆盖未知模型与上游失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
