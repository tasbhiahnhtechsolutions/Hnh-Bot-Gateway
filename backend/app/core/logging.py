"""structlog setup with redaction (plan section 14: no secrets, base64, OTP in logs)."""

import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"

# Any log field whose name contains one of these fragments is replaced entirely.
_SENSITIVE_FRAGMENTS = (
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "secret",
    "password",
    "token",
    "base64",
    "cookie",
    "body",
)

# Short words that would give false positives as substrings (e.g. "footprint"), so they
# must match a whole word of the field name.
_SENSITIVE_WORDS = frozenset({"otp", "message", "text"})

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")

# Secret-looking values are scrubbed even when they sit inside a free-text field.
_VALUE_PATTERNS = (
    re.compile(r"wab_(?:live|test)_[A-Za-z0-9]+_[A-Za-z0-9_-]+"),
    re.compile(r"whsec_[A-Za-z0-9_-]+"),
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS):
        return True
    return not _SENSITIVE_WORDS.isdisjoint(_WORD_SPLIT.split(lowered))


def _scrub_string(value: str) -> str:
    for pattern in _VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def _scrub(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, Mapping):
        return {k: _scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key == "event":
            event_dict[key] = _scrub(event_dict[key])
        else:
            event_dict[key] = _scrub(event_dict[key], key)
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redact_processor,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        # No explicit file: stdout is looked up at log time, so redirected stdout is honoured.
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
