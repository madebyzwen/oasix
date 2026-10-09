"""Public structured-logging foundation for the OASIX control plane."""

from oasix.logging.core import (
    ALLOWED_LOG_FIELDS,
    OPTIONAL_LOG_FIELDS,
    REQUIRED_LOG_FIELDS,
    EventDefinition,
    JsonLinesFormatter,
    LogErrorClass,
    LogValidationError,
    StructuredLogger,
    create_structured_logger,
)

__all__ = [
    "ALLOWED_LOG_FIELDS",
    "OPTIONAL_LOG_FIELDS",
    "REQUIRED_LOG_FIELDS",
    "EventDefinition",
    "JsonLinesFormatter",
    "LogErrorClass",
    "LogValidationError",
    "StructuredLogger",
    "create_structured_logger",
]
