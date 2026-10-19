import re
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: int
    message: str
    data: T | None = None

    @classmethod
    def success(cls, data: T) -> "ApiResponse[T]":
        return cls(code=0, message="success", data=data)


class ConversationTurn(BaseModel):
    role: Literal["agent", "user"] = Field(..., description="说话人角色：agent=坐席，user=用户")
    content: str = Field(..., min_length=1, max_length=20000, description="对话内容")


class ExtractConfig(BaseModel):
    rec_type: str | None = None
    rec_type_en: str | None = None
    rec_desc: str | None = None
    rec_regx: str | None = None
    extract_mode: str | None = None
    multiple: bool = False
    option: list[dict[str, Any]] | None = None

    @field_validator("rec_regx")
    @classmethod
    def validate_regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"rec_regx 无效: {exc}") from exc
        return value


class IntentRequest(BaseModel):
    type: int | None = None
    conversation: list[ConversationTurn] = Field(default_factory=list, max_length=500)
    previous_ticket: dict[str, Any] | None = None
    call_phone: str | None = None
    extract_config: list[ExtractConfig] | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_config_array(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"extract_config": value}
        return value

    @model_validator(mode="after")
    def validate_total_conversation_size(self) -> "IntentRequest":
        if sum(len(turn.content) for turn in self.conversation) > 200_000:
            raise ValueError("conversation 总长度不能超过 200000 个字符")
        return self


class ExtractFieldResult(BaseModel):
    rec_type: str
    rec_type_en: str
    value: str
    recognized: bool
    confidence: float
    option: list[dict[str, str]] | None = None
