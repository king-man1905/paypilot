"""Session Token Management for PayPilot Frontend Authentication.

Provides secure, short-lived session tokens for the static frontend to authenticate
with the backend without exposing the API key in the browser bundle.

The frontend calls POST /api/v1/auth/session (unauthenticated, CORS-protected) and
receives an HMAC-signed session token. The token encodes: origin, issued-at timestamp,
and a random nonce, signed with the PAYPILOT_API_KEY as the HMAC key.

The backend validates session tokens by verifying the HMAC signature, checking TTL,
and confirming the origin matches the CORS allowlist.

Security properties:
- The PAYPILOT_API_KEY is NEVER transmitted to the frontend.
- Session tokens expire after SESSION_TOKEN_TTL_SECONDS (default: 3600s = 1 hour).
- Tokens are bound to the requesting origin.
- HMAC-SHA256 prevents forgery.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional, Tuple

logger = logging.getLogger("paypilot.security.session")

# Default TTL: 1 hour
SESSION_TOKEN_TTL_SECONDS = int(os.getenv("SESSION_TOKEN_TTL_SECONDS", "3600"))


def _get_signing_key() -> str:
    """Retrieves the HMAC signing key (the PAYPILOT_API_KEY)."""
    from backend.config import get_paypilot_api_key
    return get_paypilot_api_key()


def create_session_token(origin: str) -> Optional[str]:
    """Creates an HMAC-SHA256 signed session token for the given origin.

    Token format: {origin_hash}.{issued_at}.{nonce}.{signature}
    - origin_hash: SHA-256 of the origin (first 16 hex chars)
    - issued_at: Unix timestamp (integer)
    - nonce: 16-byte random hex string
    - signature: HMAC-SHA256 of "{origin_hash}.{issued_at}.{nonce}" using the API key

    Returns:
        Signed session token string, or None if no signing key is configured.
    """
    signing_key = _get_signing_key()
    if not signing_key:
        logger.warning("Cannot create session token: PAYPILOT_API_KEY not configured.")
        return None

    origin_hash = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]
    issued_at = str(int(time.time()))
    nonce = secrets.token_hex(16)

    payload = f"{origin_hash}.{issued_at}.{nonce}"
    signature = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    token = f"pps_{payload}.{signature}"
    logger.info("Session token issued for origin (hash: %s).", origin_hash[:8])
    return token


def validate_session_token(token: str, request_origin: Optional[str] = None) -> Tuple[bool, str]:
    """Validates an HMAC-signed session token.

    Args:
        token: The session token string to validate.
        request_origin: The Origin header of the current request (for binding check).

    Returns:
        Tuple of (is_valid, reason_if_invalid).
    """
    signing_key = _get_signing_key()
    if not signing_key:
        return False, "No signing key configured"

    if not token or not token.startswith("pps_"):
        return False, "Not a session token"

    stripped = token[4:]  # Remove "pps_" prefix
    parts = stripped.split(".")
    if len(parts) != 4:
        return False, "Malformed session token"

    origin_hash, issued_at_str, nonce, provided_sig = parts

    # 1. Verify HMAC signature (constant-time comparison)
    payload = f"{origin_hash}.{issued_at_str}.{nonce}"
    expected_sig = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(provided_sig, expected_sig):
        return False, "Invalid signature"

    # 2. Check TTL
    try:
        issued_at = int(issued_at_str)
    except ValueError:
        return False, "Invalid timestamp"

    elapsed = time.time() - issued_at
    if elapsed < 0 or elapsed > SESSION_TOKEN_TTL_SECONDS:
        return False, "Session token expired"

    # 3. Origin binding check — the Origin header is required, not optional. A request with
    # no Origin header (e.g. a stolen token replayed via curl/Postman/server-to-server,
    # rather than from the browser it was issued to) must be rejected, not silently treated
    # as exempt from origin verification.
    if not request_origin:
        return False, "Missing Origin header"

    expected_origin_hash = hashlib.sha256(
        request_origin.encode("utf-8")
    ).hexdigest()[:16]
    if not hmac.compare_digest(origin_hash, expected_origin_hash):
        return False, "Origin mismatch"

    return True, "valid"
