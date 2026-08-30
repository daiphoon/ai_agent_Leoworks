import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_DIR = Path(__file__).resolve().parent.parent


def _read_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = int(raw_value)
    if value <= 0:
        raise ValueError("{} must be greater than 0".format(name))
    return value


def _read_non_negative_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = float(raw_value)
    if value < 0:
        raise ValueError("{} must be greater than or equal to 0".format(name))
    return value


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: Optional[str] = None
    responses_url: str = "https://api.deepseek.com/responses"
    anthropic_messages_url: str = "https://api.deepseek.com/anthropic/v1/messages"
    request_timeout_seconds: float = 60.0
    max_attempts: int = 3
    retry_base_delay_seconds: float = 0.25
    pro_rate_limit_per_minute: int = 30
    flash_rate_limit_per_minute: int = 60
    prompt_store_path: Path = PROJECT_DIR / "prompts" / "templates.json"
    metrics_history_size: int = 1000

    @classmethod
    def from_env(cls) -> "Settings":
        prompt_path = os.getenv("PROMPT_STORE_PATH")
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
            responses_url=os.getenv(
                "DEEPSEEK_RESPONSES_URL", "https://api.deepseek.com/responses"
            ),
            anthropic_messages_url=os.getenv(
                "DEEPSEEK_ANTHROPIC_MESSAGES_URL",
                "https://api.deepseek.com/anthropic/v1/messages",
            ),
            request_timeout_seconds=_read_non_negative_float(
                "REQUEST_TIMEOUT_SECONDS", 60.0
            ),
            max_attempts=_read_positive_int("MAX_ATTEMPTS", 3),
            retry_base_delay_seconds=_read_non_negative_float(
                "RETRY_BASE_DELAY_SECONDS", 0.25
            ),
            pro_rate_limit_per_minute=_read_positive_int(
                "PRO_RATE_LIMIT_PER_MINUTE", 30
            ),
            flash_rate_limit_per_minute=_read_positive_int(
                "FLASH_RATE_LIMIT_PER_MINUTE", 60
            ),
            prompt_store_path=Path(prompt_path) if prompt_path else PROJECT_DIR / "prompts" / "templates.json",
            metrics_history_size=_read_positive_int("METRICS_HISTORY_SIZE", 1000),
        )
