import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    def update(self, values: Dict[str, int]) -> None:
        for name in asdict(self):
            if name in values and values[name] is not None:
                setattr(self, name, int(values[name]))
        if not values.get("total_tokens") and (self.input_tokens or self.output_tokens):
            self.total_tokens = self.input_tokens + self.output_tokens

    def as_response(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "input_tokens_details": {
                "cached_tokens": self.cached_input_tokens,
                "cache_creation_tokens": self.cache_creation_input_tokens,
            },
            "output_tokens": self.output_tokens,
            "output_tokens_details": {
                "reasoning_tokens": self.reasoning_output_tokens,
            },
            "total_tokens": self.total_tokens,
        }


@dataclass
class CallRecord:
    request_id: str
    timestamp: str
    model: str
    protocol: str
    status: str
    error_code: Optional[str]
    attempts: int
    retries: int
    latency_ms: float
    first_token_latency_ms: Optional[float]
    usage: Dict[str, Any]
    prompt: Optional[Dict[str, str]]


class MetricsStore:
    def __init__(self, max_records: int) -> None:
        self._records: Deque[CallRecord] = deque(maxlen=max_records)
        self._lock = asyncio.Lock()

    async def add(self, record: CallRecord) -> None:
        async with self._lock:
            self._records.append(record)

    async def recent(self, limit: int) -> List[Dict[str, Any]]:
        async with self._lock:
            rows = list(self._records)[-limit:]
        return [asdict(row) for row in reversed(rows)]


class CallTracker:
    def __init__(
        self,
        store: MetricsStore,
        request_id: str,
        model: str,
        protocol: str,
        prompt: Optional[Dict[str, str]],
    ) -> None:
        self.store = store
        self.request_id = request_id
        self.model = model
        self.protocol = protocol
        self.prompt = prompt
        self.usage = TokenUsage()
        self.attempts = 0
        self._started_at = time.perf_counter()
        self._first_token_at: Optional[float] = None
        self._finished = False

    def mark_attempt(self, attempt: int) -> None:
        self.attempts = max(self.attempts, attempt)

    def mark_first_token(self) -> None:
        if self._first_token_at is None:
            self._first_token_at = time.perf_counter()

    async def finish(self, status: str, error_code: Optional[str] = None) -> Dict[str, Any]:
        if self._finished:
            return self.latency_response()
        self._finished = True
        latency = self.latency_response()
        record = CallRecord(
            request_id=self.request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=self.model,
            protocol=self.protocol,
            status=status,
            error_code=error_code,
            attempts=self.attempts,
            retries=max(0, self.attempts - 1),
            latency_ms=latency["latency_ms"],
            first_token_latency_ms=latency["first_token_latency_ms"],
            usage=self.usage.as_response(),
            prompt=self.prompt,
        )
        await self.store.add(record)
        return latency

    def latency_response(self) -> Dict[str, Any]:
        now = time.perf_counter()
        first_token_ms = None
        if self._first_token_at is not None:
            first_token_ms = round((self._first_token_at - self._started_at) * 1000, 3)
        return {
            "latency_ms": round((now - self._started_at) * 1000, 3),
            "first_token_latency_ms": first_token_ms,
            "attempts": self.attempts,
            "retries": max(0, self.attempts - 1),
        }
