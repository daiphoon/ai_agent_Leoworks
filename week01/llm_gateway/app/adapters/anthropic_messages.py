from typing import Any, AsyncIterator, Dict, List

from ..errors import GatewayError
from .base import BaseAdapter, PreparedRequest, ProviderEvent


STRUCTURED_TOOL_NAME = "emit_structured_response"


class AnthropicMessagesAdapter(BaseAdapter):
    protocol = "anthropic_messages"

    async def generate(self, request: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        system_parts: List[str] = []
        messages: List[Dict[str, str]] = []
        for message in request.messages:
            if message["role"] == "system":
                system_parts.append(message["content"])
            else:
                messages.append(message)

        body: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": True,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if request.response_format:
            body["tools"] = [
                {
                    "name": STRUCTURED_TOOL_NAME,
                    "description": "Return the final answer using the required JSON schema.",
                    "input_schema": request.response_format["schema"],
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": STRUCTURED_TOOL_NAME}

        headers = {
            "x-api-key": str(self.settings.deepseek_api_key),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        saw_terminal_event = False
        saw_structured_tool = not bool(request.response_format)
        async for raw in self.raw_stream(
            self.settings.anthropic_messages_url, headers, body
        ):
            if raw.kind == "attempt":
                yield raw
                continue
            payload = self.decode_event(raw.data["data"])
            event_type = payload.get("type") or raw.data["event"]
            if event_type == "message_start":
                usage = (payload.get("message") or {}).get("usage") or {}
                yield ProviderEvent("usage", self._usage(usage))
            elif event_type == "content_block_start" and request.response_format:
                block = payload.get("content_block") or {}
                if block.get("type") == "tool_use" and block.get("name") == STRUCTURED_TOOL_NAME:
                    saw_structured_tool = True
                    initial_input = block.get("input")
                    if initial_input:
                        raise GatewayError(
                            "invalid_upstream_response",
                            "结构化工具在流开始时返回了非空 input，无法安全拼接增量",
                            502,
                        )
            elif event_type == "content_block_delta":
                delta = payload.get("delta") or {}
                if not request.response_format and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        yield ProviderEvent("delta", text)
                elif request.response_format and delta.get("type") == "input_json_delta":
                    partial_json = delta.get("partial_json")
                    if isinstance(partial_json, str) and partial_json:
                        yield ProviderEvent("delta", partial_json)
            elif event_type == "message_delta":
                yield ProviderEvent("usage", self._usage(payload.get("usage") or {}))
            elif event_type == "message_stop":
                saw_terminal_event = True
                if not saw_structured_tool:
                    raise GatewayError(
                        "invalid_upstream_response",
                        "上游没有调用结构化输出工具",
                        502,
                    )
                yield ProviderEvent("done")
            elif event_type == "error":
                raise GatewayError(
                    "upstream_stream_error",
                    "上游 Anthropic Messages 流在生成过程中失败",
                    502,
                    retryable=False,
                )
        if not saw_terminal_event:
            raise GatewayError(
                "invalid_upstream_response",
                "上游 Anthropic Messages 流缺少 message_stop",
                502,
            )

    @staticmethod
    def _usage(usage: Dict[str, Any]) -> Dict[str, int]:
        output_details = usage.get("output_tokens_details") or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        result: Dict[str, int] = {}
        if input_tokens is not None:
            result["input_tokens"] = int(input_tokens)
        if usage.get("cache_read_input_tokens") is not None:
            result["cached_input_tokens"] = int(usage["cache_read_input_tokens"])
        if usage.get("cache_creation_input_tokens") is not None:
            result["cache_creation_input_tokens"] = int(
                usage["cache_creation_input_tokens"]
            )
        if output_tokens is not None:
            result["output_tokens"] = int(output_tokens)
        if output_details.get("thinking_tokens") is not None:
            result["reasoning_output_tokens"] = int(output_details["thinking_tokens"])
        if input_tokens is not None and output_tokens is not None:
            result["total_tokens"] = int(input_tokens) + int(output_tokens)
        return result
