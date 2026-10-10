"""Closed OpenAI-compatible request and response subset for Phase B.3.1."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

type ModelIdentifier = Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
type MessageContent = Annotated[StrictStr, StringConstraints(max_length=262_144)]
type FinishReason = Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]


class _ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )


class ChatMessage(_ApiModel):
    role: Literal["system", "user", "assistant"]
    content: MessageContent


class ChatCompletionRequest(_ApiModel):
    """Supported non-streaming request subset; B.4 activates stream=true."""

    model: ModelIdentifier
    messages: Annotated[tuple[ChatMessage, ...], Field(min_length=1, max_length=256)] = Field(
        repr=False
    )
    stream: StrictBool = False
    temperature: Annotated[StrictFloat, Field(ge=0.0, le=2.0)] | None = None
    top_p: Annotated[StrictFloat, Field(gt=0.0, le=1.0)] | None = None
    max_tokens: Annotated[StrictInt, Field(ge=1, le=1_000_000)] | None = None

    @field_validator("messages", mode="before")
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class AssistantMessage(_ApiModel):
    role: Literal["assistant"]
    content: MessageContent | None


class ChatCompletionChoice(_ApiModel):
    index: Annotated[StrictInt, Field(ge=0)]
    message: AssistantMessage
    finish_reason: FinishReason | None


class TokenUsage(_ApiModel):
    prompt_tokens: Annotated[StrictInt, Field(ge=0)]
    completion_tokens: Annotated[StrictInt, Field(ge=0)]
    total_tokens: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def validate_total(self) -> TokenUsage:
        if self.prompt_tokens + self.completion_tokens != self.total_tokens:
            raise ValueError("token total must equal prompt plus completion")
        return self


class ChatCompletionResponse(_ApiModel):
    id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
    object: Literal["chat.completion"]
    created: Annotated[StrictInt, Field(ge=0)]
    model: ModelIdentifier
    choices: Annotated[tuple[ChatCompletionChoice, ...], Field(min_length=1, max_length=128)]
    usage: TokenUsage | None = None
    system_fingerprint: Annotated[StrictStr, StringConstraints(max_length=255)] | None = None

    @field_validator("choices", mode="before")
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class ChatCompletionDelta(_ApiModel):
    role: Literal["assistant"] | None = None
    content: MessageContent | None = None


class ChatCompletionChunkChoice(_ApiModel):
    index: Annotated[StrictInt, Field(ge=0)]
    delta: ChatCompletionDelta
    finish_reason: FinishReason | None

    @model_validator(mode="after")
    def require_delta_or_finish(self) -> ChatCompletionChunkChoice:
        if self.delta.role is None and self.delta.content is None and self.finish_reason is None:
            raise ValueError("stream choice must contain a delta or finish reason")
        return self


class ChatCompletionChunk(_ApiModel):
    """Validated OpenAI-compatible streaming chunk subset."""

    id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
    object: Literal["chat.completion.chunk"]
    created: Annotated[StrictInt, Field(ge=0)]
    model: ModelIdentifier
    choices: Annotated[tuple[ChatCompletionChunkChoice, ...], Field(min_length=1, max_length=128)]
    system_fingerprint: Annotated[StrictStr, StringConstraints(max_length=255)] | None = None

    @field_validator("choices", mode="before")
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value
