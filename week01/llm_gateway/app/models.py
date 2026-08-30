from typing import Any, Dict, List, Literal, Optional, Union

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator


JSONScalar = Union[str, int, float, bool]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class PromptReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    variables: Dict[str, JSONScalar] = Field(default_factory=dict)


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["json_schema"]
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    schema_: Dict[str, Any] = Field(alias="schema")

    @model_validator(mode="after")
    def validate_json_schema(self) -> "ResponseFormat":
        try:
            Draft202012Validator.check_schema(self.schema_)
        except SchemaError as exc:
            raise ValueError("response_format.schema 不是合法的 JSON Schema") from exc
        return self


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    messages: Optional[List[ChatMessage]] = None
    prompt: Optional[PromptReference] = None
    stream: bool = False
    response_format: Optional[ResponseFormat] = None
    max_output_tokens: int = Field(default=1024, ge=1, le=16384)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def require_one_input_source(self) -> "LLMRequest":
        has_messages = bool(self.messages)
        has_prompt = self.prompt is not None
        if has_messages == has_prompt:
            raise ValueError("messages 和 prompt 必须且只能提供一个")
        return self


class TokenDetails(BaseModel):
    cached_tokens: int = 0
    cache_creation_tokens: int = 0


class OutputTokenDetails(BaseModel):
    reasoning_tokens: int = 0


class UsageResponse(BaseModel):
    input_tokens: int = 0
    input_tokens_details: TokenDetails = Field(default_factory=TokenDetails)
    output_tokens: int = 0
    output_tokens_details: OutputTokenDetails = Field(default_factory=OutputTokenDetails)
    total_tokens: int = 0


class LatencyResponse(BaseModel):
    latency_ms: float
    first_token_latency_ms: Optional[float]
    attempts: int
    retries: int


class PromptVersionUsed(BaseModel):
    name: str
    version: str


class LLMResponse(BaseModel):
    id: str
    model: str
    protocol: str
    output_text: str
    usage: UsageResponse
    latency: LatencyResponse
    prompt: Optional[PromptVersionUsed] = None
