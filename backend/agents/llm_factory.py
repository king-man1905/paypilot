"""NVIDIA LLM Factory Module.

Provides a unified interface for NVIDIA API (the sole external LLM provider)
with deterministic fallback mode when API keys are absent or invalid.
"""

import logging
import os
from typing import Any, Dict, Optional
from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import (
    LLM_PROVIDER,
    NVIDIA_API_KEY,
    NVIDIA_MODEL,
    NVIDIA_BASE_URL,
    LLM_REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)


def is_valid_key(key: Optional[str]) -> bool:
    """Checks if an API key is present, non-empty, and is not a placeholder."""
    if not key or not isinstance(key, str):
        return False
    stripped = key.strip()
    if not stripped:
        return False
    if stripped.startswith("your_") or stripped.startswith("<") or "placeholder" in stripped.lower():
        return False
    return True


def _wrap_traced_llm(raw_llm: BaseChatModel, provider: str, model: str) -> BaseChatModel:
    """Wraps invoke/ainvoke on the ChatModel instance with distributed tracing while preserving class type."""
    if raw_llm is None:
        return None

    orig_invoke = raw_llm.invoke
    orig_ainvoke = getattr(raw_llm, "ainvoke", None)

    def traced_invoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
        from backend.observability.tracing import trace_span
        meta = {"provider": provider, "model": model}
        with trace_span("llm.generate", component="llm", metadata=meta):
            return orig_invoke(input, config=config, **kwargs)

    try:
        object.__setattr__(raw_llm, "invoke", traced_invoke)
    except Exception:
        try:
            raw_llm.__dict__["invoke"] = traced_invoke
        except Exception:
            pass

    if orig_ainvoke:
        async def traced_ainvoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
            from backend.observability.tracing import trace_span
            meta = {"provider": provider, "model": model}
            with trace_span("llm.generate", component="llm", metadata=meta):
                return await orig_ainvoke(input, config=config, **kwargs)
        try:
            object.__setattr__(raw_llm, "ainvoke", traced_ainvoke)
        except Exception:
            try:
                raw_llm.__dict__["ainvoke"] = traced_ainvoke
            except Exception:
                pass

    return raw_llm



def get_llm(
    model: Optional[str] = None,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> Optional[BaseChatModel]:
    """Returns an initialized LangChain ChatModel connected to the NVIDIA API.

    Args:
        model: Optional model name override (defaults to NVIDIA_MODEL).
        temperature: Sampling temperature (0.0 for deterministic analysis).
        api_key: Optional explicit API key override.
        provider: Kept for backwards compatibility (supports 'nvidia' or None).

    Returns:
        BaseChatModel instance, or None if no valid NVIDIA API key is available.
    """
    if provider is not None and provider.strip().lower() not in ["nvidia", "auto"]:
        logger.warning(f"Unsupported LLM provider '{provider}'. Only 'nvidia' is supported.")
        return None

    # Determine active API key
    if api_key is not None:
        active_key = api_key.strip()
    else:
        active_key = os.getenv("NVIDIA_API_KEY", NVIDIA_API_KEY or "").strip()

    if not is_valid_key(active_key):
        logger.info("No valid NVIDIA_API_KEY configured. Operating in deterministic fallback mode.")
        return None

    target_model = (model or os.getenv("NVIDIA_MODEL", NVIDIA_MODEL or "nvidia/nemotron-3.5-lightning-30b-a3b")).strip()
    base_url = (os.getenv("NVIDIA_BASE_URL", NVIDIA_BASE_URL or "https://integrate.api.nvidia.com/v1")).strip()
    timeout_sec = float(os.getenv("LLM_REQUEST_TIMEOUT", LLM_REQUEST_TIMEOUT))

    # Preferred implementation: ChatNVIDIA from langchain_nvidia_ai_endpoints
    try:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
        raw_llm = ChatNVIDIA(
            model=target_model,
            api_key=active_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout_sec,
        )
        logger.info(f"Initialized ChatNVIDIA with model '{target_model}'.")
        return _wrap_traced_llm(raw_llm, provider="nvidia", model=target_model)
    except Exception as e1:
        logger.warning(f"ChatNVIDIA initialization notice ({e1}), attempting ChatOpenAI with NVIDIA endpoint...")
        try:
            from langchain_openai import ChatOpenAI
            raw_llm = ChatOpenAI(
                model=target_model,
                api_key=active_key,
                base_url=base_url,
                temperature=temperature,
                timeout=timeout_sec,
                max_retries=1,
            )
            logger.info(f"Initialized ChatOpenAI with NVIDIA endpoint and model '{target_model}'.")
            return _wrap_traced_llm(raw_llm, provider="nvidia", model=target_model)
        except Exception as e2:
            logger.error(f"Failed to initialize NVIDIA LLM via ChatOpenAI: {e2}")
            return None




def get_llm_info() -> Dict[str, Any]:
    """Returns metadata about the currently configured LLM provider and status.

    Never exposes secret API keys.
    """
    active_key = os.getenv("NVIDIA_API_KEY", NVIDIA_API_KEY or "").strip()
    has_nvidia = is_valid_key(active_key)
    active_llm = get_llm(temperature=0.0)

    provider_name = "nvidia" if active_llm is not None else "deterministic_fallback"
    model_name = os.getenv("NVIDIA_MODEL", NVIDIA_MODEL or "nvidia/nemotron-3.5-lightning-30b-a3b") if active_llm is not None else "none"

    if active_llm is not None:
        status_reason = "NVIDIA Live LLM active"
    elif not has_nvidia:
        status_reason = "NVIDIA_API_KEY not configured or empty in .env"
    else:
        status_reason = "ChatNVIDIA initialization failed"

    return {
        "provider": provider_name,
        "active_provider": provider_name,
        "configured_provider": "nvidia",
        "model": model_name,
        "active_model": model_name,
        "is_llm_active": active_llm is not None,
        "is_live_llm": active_llm is not None,
        "nvidia_key_present": has_nvidia,
        "status_reason": status_reason,
    }
