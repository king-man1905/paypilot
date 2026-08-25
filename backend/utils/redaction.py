"""Redaction and Data Sanitization Utilities for PayPilot.

Safely scrubs API keys, bearer tokens, passwords, and raw secrets from
strings, dictionaries, and user queries before audit logging or error formatting.
"""

import re
from typing import Any, Dict, List, Union

# Regex patterns matching secret key tokens and authorization headers
SECRET_PATTERNS = [
    # NVIDIA API Keys (nvapi-...)
    re.compile(r"nvapi-[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    # OpenAI / generic secret keys (sk-...)
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    # PayPilot test/prod keys (paypilot-...)
    re.compile(r"paypilot-[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    # Bearer tokens in text
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE),
    # Basic auth tokens in text
    re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{8,}", re.IGNORECASE),
    # X-API-Key or API key patterns in query strings / key-value pairs
    re.compile(r"((?:api[_-]?key|secret|token|password)\s*[:=]\s*)['\"]?([A-Za-z0-9_\-]{6,})['\"]?", re.IGNORECASE),
]

SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "x-api-key",
    "authorization",
    "auth",
    "bearer",
    "token",
    "password",
    "secret",
    "nvidia_api_key",
    "paypilot_api_key",
    "paypilot_admin_key",
    "redis_url",
}


def redact_sensitive_text(text: str) -> str:
    """Masks secret patterns and credentials from a text string.

    Args:
        text: Input string potentially containing sensitive tokens.

    Returns:
        str: Redacted string safe for logging and audit trails.
    """
    if not text or not isinstance(text, str):
        return "" if text is None else str(text)

    # Database URL password masking: ://user:password@host -> ://user:***@host
    redacted = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", text)

    for pattern in SECRET_PATTERNS:
        if "Bearer" in pattern.pattern:
            redacted = pattern.sub(r"\1[REDACTED_TOKEN]", redacted)
        elif "Basic" in pattern.pattern:
            redacted = pattern.sub(r"\1[REDACTED_TOKEN]", redacted)
        elif "api[_-]?key" in pattern.pattern:
            redacted = pattern.sub(r"\1[REDACTED_SECRET]", redacted)
        else:
            redacted = pattern.sub("[REDACTED_SECRET]", redacted)

    return redacted



def redact_sensitive_dict(data: Union[Dict[str, Any], List[Any], Any]) -> Any:
    """Recursively masks values associated with sensitive dictionary keys.

    Args:
        data: Dictionary, list, or primitive data structure.

    Returns:
        Scrubbed data structure with sensitive fields replaced by '[REDACTED]'.
    """
    if isinstance(data, dict):
        scrubbed: Dict[str, Any] = {}
        for k, v in data.items():
            norm_k = str(k).strip().lower().replace("_", "").replace("-", "")
            if any(sens.replace("_", "").replace("-", "") in norm_k for sens in SENSITIVE_KEY_NAMES):
                scrubbed[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                scrubbed[k] = redact_sensitive_dict(v)
            elif isinstance(v, str):
                scrubbed[k] = redact_sensitive_text(v)
            else:
                scrubbed[k] = v
        return scrubbed

    elif isinstance(data, list):
        return [redact_sensitive_dict(item) for item in data]

    elif isinstance(data, str):
        return redact_sensitive_text(data)

    return data


def summarize_query_safely(query: str, max_chars: int = 80) -> str:
    """Constructs a safe, non-sensitive summary representation of a merchant query.

    Args:
        query: Raw incoming query string.
        max_chars: Maximum allowable character length for the summary.

    Returns:
        str: Sanitized, bounded query summary without private credentials.
    """
    if not query:
        return "empty_query"

    cleaned = " ".join(query.strip().split())
    redacted = redact_sensitive_text(cleaned)

    if len(redacted) > max_chars:
        return f"{redacted[:max_chars]}... [truncated]"

    return redacted
