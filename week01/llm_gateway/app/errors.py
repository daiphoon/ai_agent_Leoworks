from typing import Any, Dict, Optional


class GatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self, request_id: str) -> Dict[str, Any]:
        error: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": request_id,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}


def upstream_status_error(status_code: int) -> GatewayError:
    if status_code == 429:
        return GatewayError(
            "upstream_rate_limited",
            "上游模型服务限流，请稍后重试",
            502,
            retryable=True,
            details={"upstream_status": status_code},
        )
    if status_code in (408, 409) or status_code >= 500:
        return GatewayError(
            "upstream_unavailable",
            "上游模型服务暂时不可用",
            502,
            retryable=True,
            details={"upstream_status": status_code},
        )
    return GatewayError(
        "upstream_rejected_request",
        "上游模型服务拒绝了请求",
        502,
        retryable=False,
        details={"upstream_status": status_code},
    )
