# 第一周作业：LLM 统一模型调用服务

这是一个可运行的 Python/FastAPI 网关。调用方只需要请求一个统一接口，服务会根据 `model` 字段选择不同协议：

| 对外模型名 | 上游协议 | 上游接口 | 鉴权方式 |
| --- | --- | --- | --- |
| `deepseek-v4-pro` | OpenAI Responses API | `/responses` | `Authorization: Bearer ...` |
| `deepseek-v4-flash` | Anthropic Messages API | `/anthropic/v1/messages` | `x-api-key: ...` |

这里的“OpenAI Responses API”表示协议格式，上游仍然是 DeepSeek，不会把请求发送到 OpenAI。

## 已实现能力

- 适配器模式：统一请求/响应，按模型动态路由，隔离鉴权、请求体、SSE 事件和 Token 字段差异。
- 流式输出：统一输出 `delta`、`usage`、`done` 三类 SSE 事件；流中错误使用 `error` 事件。
- 结构化输出：接收统一 `response_format`，并在网关内做最终 JSON Schema 校验。
- 提示词版本：模板存储在 `prompts/templates.json`，支持变量替换、版本引用和模板列表查询。
- 可观测性：记录模型、协议、状态、Token 分类、总延迟、首 Token 延迟、尝试次数和重试次数。
- 韧性：统一错误码；对网络异常、408、409、429 和 5xx 执行指数退避；最多尝试 3 次；两个模型分别限流。

## 关键设计说明

```text
调用方
  │  统一 LLMRequest
  ▼
FastAPI /v1/llm/generate
  ├─ 提示词版本解析
  ├─ 按模型独立限流
  └─ Model Router
       ├─ deepseek-v4-pro   → ResponsesAdapter
       └─ deepseek-v4-flash → AnthropicMessagesAdapter
                │
                ▼
        统一 SSE / JSON 响应 + 观测记录
```

DeepSeek 官方当前确认两个模型都支持 Responses 与 Anthropic 协议，默认地址分别是 `https://api.deepseek.com` 和 `https://api.deepseek.com/anthropic`。详见 [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。

DeepSeek 的 Anthropic 兼容说明中，`output_config` 当前只支持 `effort`，不支持 Anthropic 原生的 `output_config.format`。因此本项目对 Flash 的结构化输出采用 Anthropic 工具调用：把 JSON Schema 放进 `input_schema`，强制调用 `emit_structured_response`，最后由网关再次校验。详见 [DeepSeek Anthropic API Compatibility](https://api-docs.deepseek.com/guides/anthropic_api/)。

结构化请求与 `stream=true` 可以同时使用，但网关会先收集完整 JSON、完成 Schema 校验，再发出一个完整的 `delta`。这是为了保证调用方不会收到半截或非法 JSON。普通文本请求仍然逐块输出。

## 目录结构

```text
llm_gateway/
├── app/
│   ├── adapters/                 # 两种协议适配器和 SSE 解析
│   ├── main.py                   # FastAPI 入口与路由
│   ├── models.py                 # 统一请求/响应模型
│   ├── service.py                # 路由、结构化校验和统一输出
│   ├── prompts.py                # 提示词版本读取与变量替换
│   ├── metrics.py                # Token 与延迟观测
│   └── rate_limit.py             # 按模型独立限流
├── prompts/templates.json        # 提示词版本存储
├── scripts/verify_all.py         # 全功能验证入口
├── tests/test_gateway.py         # Mock 上游集成测试
├── .env.example                  # 只含假值的配置示例
└── requirements.txt
```

## 启动指南

要求 Python 3.9 或更高版本。

```bash
cd week01/llm_gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env.local
```

在本地编辑 `.env.local`，只替换下面这一项：

```dotenv
DEEPSEEK_API_KEY=你的真实_DeepSeek_API_Key
```

`.env.local` 已被 `.gitignore` 忽略。不要把真实密钥写进 `.env.example`、README、测试、日志或 Git 提交。

载入环境变量并启动：

```bash
set -a
source .env.local
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/health
```

交互式接口文档：<http://127.0.0.1:8000/docs>

## 统一请求格式

`messages` 和 `prompt` 必须且只能提供一个：

```json
{
  "model": "deepseek-v4-pro",
  "messages": [
    {"role": "system", "content": "你是一个简洁的助手。"},
    {"role": "user", "content": "解释什么是适配器模式。"}
  ],
  "stream": false,
  "max_output_tokens": 1024,
  "temperature": 0.7
}
```

## curl 示例

### 1. Pro：Responses API 非流式调用

```bash
curl -sS http://127.0.0.1:8000/v1/llm/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v4-pro",
    "messages": [
      {"role": "user", "content": "用一句话解释适配器模式。"}
    ]
  }'
```

响应中的 `protocol` 应为 `openai_responses`。

### 2. Flash：Anthropic Messages API 流式调用

`-N` 用于关闭 curl 的输出缓冲，便于看到逐块到达的事件。

```bash
curl -N http://127.0.0.1:8000/v1/llm/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v4-flash",
    "messages": [
      {"role": "user", "content": "分三点说明统一模型网关的价值。"}
    ],
    "stream": true
  }'
```

统一 SSE 形态：

```text
event: delta
data: {"id":"req_...","model":"deepseek-v4-flash","delta":"..."}

event: usage
data: {"id":"req_...","usage":{...}}

event: done
data: {"id":"req_...","protocol":"anthropic_messages","latency":{...}}
```

### 3. 结构化输出

下面示例要求返回对象必须包含字符串 `answer` 和整数 `confidence`。把模型名改成 `deepseek-v4-flash` 可以验证 Anthropic 适配器的结构化链路。

```bash
curl -sS http://127.0.0.1:8000/v1/llm/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v4-pro",
    "messages": [
      {"role": "user", "content": "Python 中的 list 是可变对象吗？"}
    ],
    "response_format": {
      "type": "json_schema",
      "name": "concept_answer",
      "schema": {
        "type": "object",
        "properties": {
          "answer": {"type": "string"},
          "confidence": {"type": "integer", "minimum": 0, "maximum": 100}
        },
        "required": ["answer", "confidence"],
        "additionalProperties": false
      }
    }
  }'
```

结构化内容位于响应的 `output_text` 字段中；该字段本身是一个合法 JSON 字符串。

### 4. 提示词版本引用

```bash
curl -sS http://127.0.0.1:8000/v1/llm/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v4-flash",
    "prompt": {
      "name": "explain_concept",
      "version": "v2",
      "variables": {"concept": "Python 装饰器"}
    }
  }'
```

查询可用模板、版本和变量：

```bash
curl -s http://127.0.0.1:8000/v1/prompts
```

新增版本时，只需要在 `prompts/templates.json` 对应模板下增加 `v3` 等记录，重启服务后生效。

### 5. 可观测数据

```bash
curl -s 'http://127.0.0.1:8000/v1/metrics?limit=20'
```

每条记录包含：

- `input_tokens`、`cached_tokens`、`cache_creation_tokens`；
- `output_tokens`、`reasoning_tokens`、`total_tokens`；
- `latency_ms`、`first_token_latency_ms`；
- `attempts`、`retries`、状态和统一错误码。

当前观测记录保存在进程内存中，默认最多 1000 条；服务重启后清空。这满足课程验证，生产环境应改接持久化日志或指标系统。

### 6. 限流与 429

默认 Pro 每分钟 30 次、Flash 每分钟 60 次。可在 `.env.local` 分别设置：

```dotenv
PRO_RATE_LIMIT_PER_MINUTE=1
FLASH_RATE_LIMIT_PER_MINUTE=2
```

重启后连续调用同一模型，超限会返回 HTTP 429、`Retry-After` 响应头和统一错误：

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "该模型的本地调用频率已超限",
    "retryable": true,
    "request_id": "req_...",
    "details": {
      "model": "deepseek-v4-pro",
      "retry_after_seconds": 60
    }
  }
}
```

## 自动验证

验证脚本使用 Mock 上游，不消耗 DeepSeek Token，也不需要真实 API Key：

```bash
source .venv/bin/activate
python scripts/verify_all.py
```

它覆盖以下证据：

1. 两个模型动态路由到不同协议和鉴权头；
2. SSE `delta / usage / done`；
3. 两条协议的结构化输出和 JSON Schema 校验；
4. 模板存储、变量替换和版本引用；
5. Token 分类、总延迟和首 Token 延迟；
6. 前两次 503、第三次成功的重试路径；
7. 两个模型互不影响的本地限流；
8. 未知模型与上游失败的统一错误码。

## 重试与错误边界

- 最多尝试 3 次，包含第一次调用，因此最多执行 2 次“再次尝试”。
- 退避间隔默认约为 `0.25s → 0.5s`，可用环境变量调整。
- 仅对适合重试的网络错误、408、409、429、5xx 重试；普通 4xx 不重试。
- SSE 已经收到上游事件后不再自动重试，避免向调用方重复输出内容。
- 本地限流直接返回 429，不会把超限请求发送到上游。
- 上游错误正文不会原样返回，避免把内部信息或请求内容泄露给调用方。

## 验收状态说明

- 已验证：Mock 上游下的完整协议适配、六大功能和错误路径。
- 未验证：使用真实 DeepSeek 账户的线上调用，因为仓库中没有也不应包含真实 API Key。
- 完成线上验收时，先在本地设置 `DEEPSEEK_API_KEY`，再依次执行上面的 Pro、Flash、结构化、模板和指标 curl 示例。真实调用会产生 Token 费用。
