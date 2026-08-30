import json
import unittest
from pathlib import Path
from typing import Dict, List

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


PROJECT_DIR = Path(__file__).resolve().parent.parent


def sse_event(event: str, data: Dict) -> str:
    return "event: {}\ndata: {}\n\n".format(
        event, json.dumps(data, separators=(",", ":"))
    )


class MockDeepSeek:
    def __init__(self) -> None:
        self.requests: List[Dict] = []
        self.retry_attempts = 0
        self.always_fail_attempts = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        row = {
            "path": request.url.path,
            "body": body,
            "authorization": request.headers.get("authorization"),
            "x_api_key": request.headers.get("x-api-key"),
        }
        self.requests.append(row)
        prompt_text = json.dumps(body, ensure_ascii=False)
        if "trigger retry" in prompt_text:
            self.retry_attempts += 1
            if self.retry_attempts < 3:
                return httpx.Response(503, json={"error": {"message": "temporary"}})
        if "always fail" in prompt_text:
            self.always_fail_attempts += 1
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        if request.url.path.endswith("/responses"):
            return self._responses(body)
        if request.url.path.endswith("/messages"):
            return self._messages(body)
        return httpx.Response(404)

    def _responses(self, body: Dict) -> httpx.Response:
        if "text" in body:
            chunks = ['{"answer":"', 'ok","count":2}']
        else:
            chunks = ["pro-", "response"]
        content = sse_event(
            "response.created", {"type": "response.created", "response": {}}
        )
        for chunk in chunks:
            content += sse_event(
                "response.output_text.delta",
                {"type": "response.output_text.delta", "delta": chunk},
            )
        content += sse_event(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "usage": {
                        "input_tokens": 10,
                        "input_tokens_details": {"cached_tokens": 3},
                        "output_tokens": 5,
                        "output_tokens_details": {"reasoning_tokens": 1},
                        "total_tokens": 15,
                    }
                },
            },
        )
        return httpx.Response(
            200, content=content.encode("utf-8"), headers={"content-type": "text/event-stream"}
        )

    def _messages(self, body: Dict) -> httpx.Response:
        content = sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 12,
                        "cache_read_input_tokens": 4,
                        "cache_creation_input_tokens": 2,
                        "output_tokens": 1,
                    }
                },
            },
        )
        if "tools" in body:
            content += sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "name": "emit_structured_response",
                        "input": {},
                    },
                },
            )
            for chunk in ['{"answer":"ok",', '"count":2}']:
                content += sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": chunk},
                    },
                )
        else:
            content += sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            for chunk in ["flash-", "response"]:
                content += sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": chunk},
                    },
                )
        content += sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        )
        content += sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {
                    "output_tokens": 6,
                    "output_tokens_details": {"thinking_tokens": 2},
                },
            },
        )
        content += sse_event("message_stop", {"type": "message_stop"})
        return httpx.Response(
            200, content=content.encode("utf-8"), headers={"content-type": "text/event-stream"}
        )


def make_settings(**overrides) -> Settings:
    values = {
        "deepseek_api_key": "test_key_not_secret",
        "responses_url": "https://mock.deepseek.local/responses",
        "anthropic_messages_url": "https://mock.deepseek.local/anthropic/v1/messages",
        "request_timeout_seconds": 5.0,
        "max_attempts": 3,
        "retry_base_delay_seconds": 0.0,
        "pro_rate_limit_per_minute": 100,
        "flash_rate_limit_per_minute": 100,
        "prompt_store_path": PROJECT_DIR / "prompts" / "templates.json",
        "metrics_history_size": 100,
    }
    values.update(overrides)
    return Settings(**values)


def basic_request(model: str, content: str = "hello") -> Dict:
    return {"model": model, "messages": [{"role": "user", "content": content}]}


SCHEMA_REQUEST = {
    "type": "json_schema",
    "name": "answer",
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["answer", "count"],
        "additionalProperties": False,
    },
}


class GatewayFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.upstream = MockDeepSeek()
        app = create_app(
            make_settings(), transport=httpx.MockTransport(self.upstream)
        )
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)

    def test_01_models_route_to_distinct_protocols(self) -> None:
        pro = self.client.post(
            "/v1/llm/generate", json=basic_request("deepseek-v4-pro")
        )
        flash = self.client.post(
            "/v1/llm/generate", json=basic_request("deepseek-v4-flash")
        )
        self.assertEqual(pro.status_code, 200)
        self.assertEqual(flash.status_code, 200)
        self.assertEqual(pro.json()["protocol"], "openai_responses")
        self.assertEqual(flash.json()["protocol"], "anthropic_messages")
        self.assertEqual(self.upstream.requests[0]["body"]["model"], "deepseek-v4-pro")
        self.assertTrue(self.upstream.requests[0]["authorization"].startswith("Bearer "))
        self.assertEqual(self.upstream.requests[1]["body"]["model"], "deepseek-v4-flash")
        self.assertEqual(self.upstream.requests[1]["x_api_key"], "test_key_not_secret")

    def test_02_streaming_returns_unified_sse(self) -> None:
        for model, expected_delta in (
            ("deepseek-v4-pro", '"delta":"pro-"'),
            ("deepseek-v4-flash", '"delta":"flash-"'),
        ):
            request = basic_request(model)
            request["stream"] = True
            response = self.client.post("/v1/llm/generate", json=request)
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            self.assertIn("event: delta", response.text)
            self.assertIn(expected_delta, response.text)
            self.assertIn("event: usage", response.text)
            self.assertIn("event: done", response.text)

    def test_03_structured_output_works_for_both_protocols(self) -> None:
        for model in ("deepseek-v4-pro", "deepseek-v4-flash"):
            request = basic_request(model)
            request["response_format"] = SCHEMA_REQUEST
            response = self.client.post("/v1/llm/generate", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(json.loads(response.json()["output_text"]), {"answer": "ok", "count": 2})
        pro_body = self.upstream.requests[0]["body"]
        flash_body = self.upstream.requests[1]["body"]
        self.assertEqual(pro_body["text"]["format"]["type"], "json_schema")
        self.assertEqual(flash_body["tool_choice"]["name"], "emit_structured_response")
        self.assertEqual(flash_body["tools"][0]["input_schema"], SCHEMA_REQUEST["schema"])

    def test_04_prompt_version_is_rendered_and_reported(self) -> None:
        request = {
            "model": "deepseek-v4-pro",
            "prompt": {
                "name": "explain_concept",
                "version": "v2",
                "variables": {"concept": "Python 装饰器"},
            },
        }
        response = self.client.post("/v1/llm/generate", json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["prompt"], {"name": "explain_concept", "version": "v2"}
        )
        upstream_input = self.upstream.requests[0]["body"]["input"][0]["content"]
        self.assertIn("Python 装饰器", upstream_input)
        prompt_list = self.client.get("/v1/prompts").json()["templates"]
        self.assertTrue(any(row["name"] == "explain_concept" for row in prompt_list))

    def test_05_observability_records_token_categories_and_ttft(self) -> None:
        response = self.client.post(
            "/v1/llm/generate", json=basic_request("deepseek-v4-pro")
        )
        data = response.json()
        self.assertEqual(data["usage"]["input_tokens_details"]["cached_tokens"], 3)
        self.assertEqual(data["usage"]["output_tokens_details"]["reasoning_tokens"], 1)
        self.assertIsNotNone(data["latency"]["first_token_latency_ms"])
        record = self.client.get("/v1/metrics").json()["records"][0]
        self.assertEqual(record["request_id"], data["id"])
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["usage"]["total_tokens"], 15)

    def test_06_retry_succeeds_on_third_attempt(self) -> None:
        response = self.client.post(
            "/v1/llm/generate",
            json=basic_request("deepseek-v4-pro", "trigger retry"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latency"]["attempts"], 3)
        self.assertEqual(response.json()["latency"]["retries"], 2)

    def test_07_upstream_failure_uses_unified_error(self) -> None:
        response = self.client.post(
            "/v1/llm/generate",
            json=basic_request("deepseek-v4-pro", "always fail"),
        )
        self.assertEqual(response.status_code, 502)
        error = response.json()["error"]
        self.assertEqual(error["code"], "upstream_unavailable")
        self.assertTrue(error["retryable"])
        self.assertTrue(error["request_id"].startswith("req_"))
        self.assertEqual(self.upstream.always_fail_attempts, 3)

    def test_08_unknown_model_uses_unified_error(self) -> None:
        response = self.client.post(
            "/v1/llm/generate", json=basic_request("unknown-model")
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "model_not_supported")


class RateLimitTests(unittest.TestCase):
    def test_09_rate_limits_are_independent_per_model(self) -> None:
        upstream = MockDeepSeek()
        app = create_app(
            make_settings(
                pro_rate_limit_per_minute=1,
                flash_rate_limit_per_minute=1,
            ),
            transport=httpx.MockTransport(upstream),
        )
        with TestClient(app) as client:
            first_pro = client.post(
                "/v1/llm/generate", json=basic_request("deepseek-v4-pro")
            )
            second_pro = client.post(
                "/v1/llm/generate", json=basic_request("deepseek-v4-pro")
            )
            first_flash = client.post(
                "/v1/llm/generate", json=basic_request("deepseek-v4-flash")
            )
        self.assertEqual(first_pro.status_code, 200)
        self.assertEqual(second_pro.status_code, 429)
        self.assertEqual(second_pro.json()["error"]["code"], "rate_limit_exceeded")
        self.assertIn("Retry-After", second_pro.headers)
        self.assertEqual(first_flash.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
