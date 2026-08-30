from typing import Any, AsyncIterator, Dict, List

from ..errors import GatewayError
from .base import BaseAdapter, PreparedRequest, ProviderEvent


class OpenAIResponsesAdapter(BaseAdapter):
    protocol = "openai_responses"

    async def generate(self, request: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        system_parts: List[str] = []
        input_messages: List[Dict[str, str]] = []
        for message in request.messages:
            if message["role"] == "system":
                system_parts.append(message["content"])
            else:
                input_messages.append(message)

        body: Dict[str, Any] = {
            "model": request.model,
            "input": input_messages,
            "stream": True,
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if system_parts:
            body["instructions"] = "\n\n".join(system_parts)
        if request.response_format:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.response_format["name"],
                    "schema": request.response_format["schema"],
                }
            }

        headers = {
            "Authorization": "Bearer {}".format(self.settings.deepseek_api_key),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        saw_terminal_event = False
        async for raw in self.raw_stream(self.settings.responses_url, headers, body):
            if raw.kind == "attempt":
                yield raw
                continue
            payload = self.decode_event(raw.data["data"])
            event_type = payload.get("type") or raw.data["event"]
            if event_type == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    yield ProviderEvent("delta", delta)
            elif event_type in ("response.completed", "response.incomplete"):
                response = payload.get("response") or {}
                usage = self._usage(response.get("usage") or {})
                yield ProviderEvent("usage", usage)
                saw_terminal_event = True
                yield ProviderEvent("done")
            elif event_type in ("response.failed", "error"):
                raise GatewayError(
                    "upstream_stream_error",
                    "上游 Responses 流在生成过程中失败",
                    502,
                    retryable=False,
                )
        if not saw_terminal_event:
            raise GatewayError(
                "invalid_upstream_response",
                "上游 Responses 流缺少结束事件",
                502,
            )

    @staticmethod
    def _usage(usage: Dict[str, Any]) -> Dict[str, int]:
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "cached_input_tokens": input_details.get("cached_tokens", 0),
            "cache_creation_input_tokens": input_details.get("cache_write_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "reasoning_output_tokens": output_details.get("reasoning_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
