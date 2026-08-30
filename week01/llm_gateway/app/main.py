import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .adapters import AnthropicMessagesAdapter, OpenAIResponsesAdapter
from .config import Settings
from .errors import GatewayError
from .metrics import MetricsStore
from .models import LLMRequest, LLMResponse
from .prompts import PromptStore
from .rate_limit import PerModelRateLimiter
from .service import LLMService


def create_app(
    settings: Optional[Settings] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> FastAPI:
    active_settings = settings or Settings.from_env()
    http_client = httpx.AsyncClient(
        timeout=active_settings.request_timeout_seconds,
        transport=transport,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await http_client.aclose()

    app = FastAPI(
        title="LLM 统一模型调用服务",
        version="1.0.0",
        lifespan=lifespan,
    )
    prompt_store = PromptStore(active_settings.prompt_store_path)
    metrics_store = MetricsStore(active_settings.metrics_history_size)
    adapters = {
        "deepseek-v4-pro": OpenAIResponsesAdapter(http_client, active_settings),
        "deepseek-v4-flash": AnthropicMessagesAdapter(http_client, active_settings),
    }
    rate_limiter = PerModelRateLimiter(
        {
            "deepseek-v4-pro": active_settings.pro_rate_limit_per_minute,
            "deepseek-v4-flash": active_settings.flash_rate_limit_per_minute,
        }
    )
    service = LLMService(adapters, prompt_store, metrics_store, rate_limiter)
    app.state.service = service

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = "req_{}".format(uuid.uuid4().hex)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        headers = None
        if exc.status_code == 429:
            headers = {
                "Retry-After": str(exc.details.get("retry_after_seconds", 1))
            }
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.as_dict(request.state.request_id),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {"location": list(error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        error = GatewayError(
            "invalid_request",
            "请求参数校验失败",
            422,
            details={"fields": fields},
        )
        return JSONResponse(
            status_code=422, content=error.as_dict(request.state.request_id)
        )

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "models": {
                "deepseek-v4-pro": "openai_responses",
                "deepseek-v4-flash": "anthropic_messages",
            },
        }

    @app.get("/v1/prompts")
    async def prompts() -> dict:
        return {"templates": prompt_store.list_templates()}

    @app.get("/v1/metrics")
    async def metrics(limit: int = 100) -> dict:
        safe_limit = max(1, min(limit, 1000))
        records = await metrics_store.recent(safe_limit)
        return {"count": len(records), "records": records}

    @app.post("/v1/llm/generate", response_model=LLMResponse)
    async def generate(request: Request, body: LLMRequest):
        service.ensure_model_and_configuration(body.model)
        await service.admit(body.model)
        if body.stream:
            return StreamingResponse(
                service.stream(body, request.state.request_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        return await service.execute(body, request.state.request_id)

    return app


app = create_app()
