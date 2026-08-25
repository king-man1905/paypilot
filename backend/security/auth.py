"""Authentication & Authorization Module for PayPilot API.

Provides API key extraction (via X-API-Key and Authorization: Bearer headers),
constant-time timing-attack-safe validation via secrets.compare_digest,
and role-based access control (analyst vs admin).
"""

import logging
import secrets
from dataclasses import dataclass
from typing import Optional
from fastapi import Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from backend.config import (
    get_paypilot_admin_key,
    get_paypilot_api_key,
    is_auth_required,
)
from backend.observability.metrics import record_error

logger = logging.getLogger("paypilot.security")

# OpenAPI Security Scheme definitions
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    """Represents an authenticated principal in the PayPilot system."""
    client_id: str
    role: str  # 'analyst' or 'admin'


def _extract_credentials(
    x_api_key: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Optional[str]:
    """Extracts raw API key string from X-API-Key or Authorization Bearer header."""
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()

    if authorization and authorization.strip():
        auth_header = authorization.strip()
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        # If an explicit non-bearer auth schema was provided (e.g. Basic ...), do not accept as token
        return None

    return None


def get_current_user(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    required_role: str = "analyst",
) -> AuthenticatedUser:
    """Validates incoming credentials and enforces role-based authorization.

    Args:
        request: FastAPI Request instance.
        x_api_key: Header value for X-API-Key.
        authorization: Header value for Authorization (Bearer).
        required_role: Required access level ('analyst' or 'admin').

    Returns:
        AuthenticatedUser: Validated principal.

    Raises:
        HTTPException(401): Missing or invalid API key.
        HTTPException(403): Insufficient role permission.
    """
    configured_key = get_paypilot_api_key()
    configured_admin_key = get_paypilot_admin_key() or configured_key
    auth_enforced = is_auth_required()

    # 1. If auth is not enforced and no keys are configured (e.g. unconfigured dev mode)
    if not auth_enforced and not configured_key and not configured_admin_key:
        return AuthenticatedUser(client_id="anonymous-dev", role="admin")

    # 2. Extract credential
    token = _extract_credentials(x_api_key=x_api_key, authorization=authorization)
    if not token:
        logger.warning(f"Unauthenticated request to {request.url.path} (missing or malformed auth header).")
        record_error("auth_error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a valid API key via X-API-Key or Authorization: Bearer header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Constant-time timing-attack safe validation
    is_admin = bool(configured_admin_key) and secrets.compare_digest(token, configured_admin_key)
    is_analyst = bool(configured_key) and secrets.compare_digest(token, configured_key)

    if not is_admin and not is_analyst:
        logger.warning(f"Authentication failure on {request.url.path}: invalid API key provided.")
        record_error("auth_error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_role = "admin" if is_admin else "analyst"

    # 4. Role Authorization Check
    if required_role == "admin" and user_role != "admin":
        logger.warning(f"Forbidden access to {request.url.path}: user has role '{user_role}', requires 'admin'.")
        record_error("forbidden_error")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Administrative privileges required to access this resource.",
        )

    client_tag = "admin-client" if user_role == "admin" else "merchant-client"
    request.state.client_id = client_tag
    request.state.role = user_role
    return AuthenticatedUser(client_id=client_tag, role=user_role)


def require_analyst(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthenticatedUser:
    """Dependency requiring analyst (or admin) role."""
    return get_current_user(
        request=request,
        x_api_key=x_api_key,
        authorization=authorization,
        required_role="analyst",
    )


def require_admin(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthenticatedUser:
    """Dependency requiring admin role (e.g. for metrics telemetry)."""
    return get_current_user(
        request=request,
        x_api_key=x_api_key,
        authorization=authorization,
        required_role="admin",
    )
