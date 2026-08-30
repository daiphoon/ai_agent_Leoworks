import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from ..config import Settings
from ..errors import GatewayError, upstream_status_error


@dataclass(frozen=True)
class PreparedRequest:
    model: str
    messages: List[Dict[str, str]]
    max_output_tokens: int
    temperature: float
    response_format: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class ProviderEvent:
    kind: str
    data: Any = None


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[Tuple[str, str]]:
    event_name = "message"
    data_lines: List[str] = []
    async for line in lines:
        if line == "":
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


class BaseAdapter:
    protocol = "unknown"

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def ensure_configured(self) -> None:
        if not self.settings.deepseek_api_key:
            raise GatewayError(
                "service_not_configured",
                "服务缺少 DEEPSEEK_API_KEY 环境变量",
                503,
                retryable=False,
            )

    async def raw_stream(
        self, url: str, headers: Dict[str, str], body: Dict[str, Any]
    ) -> AsyncIterator[ProviderEvent]:
        self.ensure_configured()
        for attempt in range(1, self.settings.max_attempts + 1):
            yield ProviderEvent("attempt", attempt)
            saw_stream_data = False
            try:
                async with self.client.stream(
                    "POST", url, headers=headers, json=body
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        error = upstream_status_error(response.status_code)
                        if error.retryable and attempt < self.settings.max_attempts:
                            await self._backoff(attempt)
                            continue
                        raise error
                    async for event_name, data in iter_sse(response.aiter_lines()):
                        saw_stream_data = True
                        yield ProviderEvent(
                            "raw", {"event": event_name, "data": data}
                        )
                    return
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if not saw_stream_data and attempt < self.settings.max_attempts:
                    await self._backoff(attempt)
                    continue
                code = "upstream_timeout" if isinstance(exc, httpx.TimeoutException) else "upstream_network_error"
                status = 504 if isinstance(exc, httpx.TimeoutException) else 502
                raise GatewayError(
                    code,
                    "连接上游模型服务失败",
                    status,
                    retryable=not saw_stream_data,
                ) from exc
        raise GatewayError("upstream_unavailable", "上游模型服务暂时不可用", 502, True)

    async def _backoff(self, attempt: int) -> None:
        delay = self.settings.retry_base_delay_seconds * (2 ** (attempt - 1))
        if delay:
            await asyncio.sleep(delay)

    @staticmethod
    def decode_event(data: str) -> Dict[str, Any]:
        try:
            value = json.loads(data)
        except json.JSONDecodeError as exc:
            raise GatewayError(
                "invalid_upstream_response",
                "上游返回了无法解析的流式事件",
                502,
            ) from exc
        if not isinstance(value, dict):
            raise GatewayError(
                "invalid_upstream_response",
                "上游流式事件不是 JSON 对象",
                502,
            )
        return value

    async def generate(self, request: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        raise NotImplementedError
