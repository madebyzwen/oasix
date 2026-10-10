"""Authenticated, lease-protected interactive LLM path."""

from oasix.llm.errors import (
    InferenceLeaseAcquireError,
    InferenceLeaseReleaseError,
    InferenceLeaseRenewalError,
    InferenceOverloadedError,
    LlmConfigurationError,
    LlmPathError,
    LlmReadinessError,
    LlmRequestError,
    LlmUpstreamError,
    LlmUpstreamProtocolError,
    LlmUpstreamTimeoutError,
)
from oasix.llm.gateway import (
    MAX_CLIENT_REQUEST_BYTES,
    create_configured_llm_gateway_app,
    create_llm_gateway_app,
)
from oasix.llm.leases import (
    InferenceLeaseRegistry,
    InferenceLeaseSession,
    PersistentInferenceLeaseRegistry,
)
from oasix.llm.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from oasix.llm.service import InferenceAdmission, InferenceConcurrencyGate, LlmProxyService
from oasix.llm.transport import (
    MAX_STREAM_EVENT_BYTES,
    MAX_STREAM_RESPONSE_BYTES,
    MAX_UPSTREAM_RESPONSE_BYTES,
    HttpLlmUpstream,
)

__all__ = [
    "ChatCompletionChunk",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "HttpLlmUpstream",
    "InferenceAdmission",
    "InferenceConcurrencyGate",
    "InferenceLeaseAcquireError",
    "InferenceLeaseRegistry",
    "InferenceLeaseReleaseError",
    "InferenceLeaseRenewalError",
    "InferenceLeaseSession",
    "InferenceOverloadedError",
    "LlmConfigurationError",
    "LlmPathError",
    "LlmProxyService",
    "LlmReadinessError",
    "LlmRequestError",
    "LlmUpstreamError",
    "LlmUpstreamProtocolError",
    "LlmUpstreamTimeoutError",
    "MAX_CLIENT_REQUEST_BYTES",
    "MAX_STREAM_EVENT_BYTES",
    "MAX_STREAM_RESPONSE_BYTES",
    "MAX_UPSTREAM_RESPONSE_BYTES",
    "PersistentInferenceLeaseRegistry",
    "create_configured_llm_gateway_app",
    "create_llm_gateway_app",
]
