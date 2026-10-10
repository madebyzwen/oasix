from __future__ import annotations

import json
import uuid
from io import StringIO

from oasix.llm import LlmTelemetry
from oasix.llm.models import TokenUsage
from oasix.logging import LogErrorClass, create_structured_logger
from oasix.worker import WorkerReadinessTiming


def _telemetry(
    values: list[int],
) -> tuple[LlmTelemetry, StringIO]:
    clock = iter(values)
    stream = StringIO()
    logger = create_structured_logger("llm_gateway", stream=stream)
    return LlmTelemetry(logger, monotonic_ns=lambda: next(clock)), stream


def test_success_event_contains_available_correlations_timings_and_usage() -> None:
    telemetry, output = _telemetry([1_000_000, 8_000_000, 13_000_000])
    request_id = str(uuid.uuid4())
    lease_id = str(uuid.uuid4())
    span = telemetry.start(request_id, "worker-primary")

    span.record_lease(lease_id)
    span.record_readiness(WorkerReadinessTiming(5, 3))
    span.record_first_token()
    span.record_usage(TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18))
    span.succeed()

    payload = json.loads(output.getvalue())
    assert payload == {
        "timestamp": payload["timestamp"],
        "level": "INFO",
        "component": "llm_gateway",
        "event_code": "llm.request_completed",
        "message": "LLM request completed.",
        "request_id": request_id,
        "worker_id": "worker-primary",
        "lease_id": lease_id,
        "request_duration_ms": 12,
        "wake_latency_ms": 3,
        "readiness_latency_ms": 5,
        "time_to_first_token_ms": 7,
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "completion_status": "succeeded",
    }


def test_missing_metrics_are_omitted_and_external_start_is_used() -> None:
    telemetry, output = _telemetry([21_000_000])
    span = telemetry.start(
        str(uuid.uuid4()),
        "worker-primary",
        started_at=1_000_000,
    )

    span.record_readiness(None)
    span.record_usage(None)
    span.succeed()

    payload = json.loads(output.getvalue())
    assert payload["request_duration_ms"] == 20
    for unavailable in (
        "lease_id",
        "wake_latency_ms",
        "readiness_latency_ms",
        "time_to_first_token_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "error_class",
        "error_code",
    ):
        assert unavailable not in payload


def test_failure_uses_only_fixed_category_and_code_without_sensitive_data() -> None:
    telemetry, output = _telemetry([1_000_000, 2_000_000])
    sentinel = "SENTINEL-PRIVATE-PROMPT-AND-CREDENTIAL"
    span = telemetry.start(str(uuid.uuid4()), "worker-primary")

    span.fail(LogErrorClass.TIMEOUT, "llm.upstream_timeout")

    serialized = output.getvalue()
    payload = json.loads(serialized)
    assert payload["completion_status"] == "failed"
    assert payload["error_class"] == "TIMEOUT"
    assert payload["error_code"] == "llm.upstream_timeout"
    assert sentinel not in serialized
    assert sentinel not in repr(span)
    assert "prompt" not in payload
    assert "response" not in payload


def test_first_terminal_outcome_wins_and_emits_exactly_one_event() -> None:
    telemetry, output = _telemetry([1_000_000, 2_000_000])
    span = telemetry.start(str(uuid.uuid4()), "worker-primary")

    span.cancel()
    span.fail(LogErrorClass.LLM, "llm.request_failed")
    span.succeed()

    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_code"] == "llm.request_cancelled"
    assert payload["completion_status"] == "cancelled"
    assert "error_class" not in payload
    assert "error_code" not in payload
