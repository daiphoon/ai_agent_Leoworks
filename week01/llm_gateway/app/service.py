import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator, ValidationError

from .adapters.base import BaseAdapter, PreparedRequest
from .errors import GatewayError
from .metrics import CallTracker, MetricsStore
from .models import LLMRequest
from .prompts import PromptStore
from .rate_limit import PerModelRateLimiter


def encode_sse(event: str, data: Dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return "event: {}\ndata: {}\n\n".format(event, payload).encode("utf-8")


class LLMService:
    def __init__(
        self,
        adapters: Dict[str, BaseAdapter],
        prompt_store: PromptStore,
        metrics_store: MetricsStore,
        rate_limiter: PerModelRateLimiter,
    ) -> None:
        self.adapters = adapters
        self.prompt_store = prompt_store
        self.metrics_store = metrics_store
        self.rate_limiter = rate_limiter

    def ensure_model_and_configuration(self, model: str) -> BaseAdapter:
        adapter = self.adapters.get(model)
        if adapter is None:
            raise GatewayError(
                "model_not_supported",
                "不支持模型 {}".format(model),
                404,
                details={"supported_models": sorted(self.adapters)},
            )
        adapter.ensure_configured()
        return adapter

    async def admit(self, model: str) -> None:
        allowed, retry_after = await self.rate_limiter.acquire(model)
        if not allowed:
            raise GatewayError(
                "rate_limit_exceeded",
                "该模型的本地调用频率已超限",
                429,
                retryable=True,
                details={"model": model, "retry_after_seconds": retry_after},
            )

    def prepare(self, request: LLMRequest) -> Tuple[PreparedRequest, Optional[Dict[str, str]]]:
        prompt_used: Optional[Dict[str, str]] = None
        if request.prompt is not None:
            rendered = self.prompt_store.render(
                request.prompt.name, request.prompt.version, request.prompt.variables
            )
            messages = [{"role": "user", "content": rendered.text}]
            prompt_used = {"name": rendered.name, "version": rendered.version}
        else:
            messages = [message.model_dump() for message in request.messages or []]

        response_format = None
        if request.response_format is not None:
            response_format = request.response_format.model_dump(by_alias=True)
        prepared = PreparedRequest(
            model=request.model,
            messages=messages,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            response_format=response_format,
        )
        return prepared, prompt_used

    async def execute(self, request: LLMRequest, request_id: str) -> Dict[str, Any]:
        adapter = self.ensure_model_and_configuration(request.model)
        prepared, prompt_used = self.prepare(request)
        tracker = CallTracker(
            self.metrics_store,
            request_id,
            request.model,
            adapter.protocol,
            prompt_used,
        )
        chunks: List[str] = []
        try:
            async for event in adapter.generate(prepared):
                if event.kind == "attempt":
                    tracker.mark_attempt(event.data)
                elif event.kind == "delta":
                    tracker.mark_first_token()
                    chunks.append(event.data)
                elif event.kind == "usage":
                    tracker.usage.update(event.data)
            output_text = self._validated_output(chunks, prepared.response_format)
            latency = await tracker.finish("success")
            return {
                "id": request_id,
                "model": request.model,
                "protocol": adapter.protocol,
                "output_text": output_text,
                "usage": tracker.usage.as_response(),
                "latency": latency,
                "prompt": prompt_used,
            }
        except GatewayError as exc:
            await tracker.finish("error", exc.code)
            raise
        except asyncio.CancelledError:
            await tracker.finish("cancelled", "client_cancelled")
            raise

    async def stream(self, request: LLMRequest, request_id: str) -> AsyncIterator[bytes]:
        adapter = self.ensure_model_and_configuration(request.model)
        prepared, prompt_used = self.prepare(request)
        tracker = CallTracker(
            self.metrics_store,
            request_id,
            request.model,
            adapter.protocol,
            prompt_used,
        )
        structured_chunks: List[str] = []
        try:
            async for provider_event in adapter.generate(prepared):
                if provider_event.kind == "attempt":
                    tracker.mark_attempt(provider_event.data)
                elif provider_event.kind == "usage":
                    tracker.usage.update(provider_event.data)
                elif provider_event.kind == "delta":
                    tracker.mark_first_token()
                    if prepared.response_format:
                        structured_chunks.append(provider_event.data)
                    else:
                        yield encode_sse(
                            "delta",
                            {
                                "id": request_id,
                                "model": request.model,
                                "delta": provider_event.data,
                            },
                        )
            if prepared.response_format:
                validated = self._validated_output(
                    structured_chunks, prepared.response_format
                )
                yield encode_sse(
                    "delta",
                    {"id": request_id, "model": request.model, "delta": validated},
                )
            latency = await tracker.finish("success")
            yield encode_sse(
                "usage",
                {"id": request_id, "usage": tracker.usage.as_response()},
            )
            yield encode_sse(
                "done",
                {
                    "id": request_id,
                    "model": request.model,
                    "protocol": adapter.protocol,
                    "latency": latency,
                    "prompt": prompt_used,
                },
            )
        except GatewayError as exc:
            await tracker.finish("error", exc.code)
            yield encode_sse("error", exc.as_dict(request_id))
        except asyncio.CancelledError:
            await tracker.finish("cancelled", "client_cancelled")
            raise

    @staticmethod
    def _validated_output(
        chunks: List[str], response_format: Optional[Dict[str, Any]]
    ) -> str:
        output_text = "".join(chunks)
        if not response_format:
            return output_text
        try:
            value = json.loads(output_text)
            Draft202012Validator(response_format["schema"]).validate(value)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise GatewayError(
                "structured_output_invalid",
                "上游返回内容未通过 JSON Schema 校验",
                502,
                retryable=False,
            ) from exc
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
