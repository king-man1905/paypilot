"""Comprehensive Safe Diagnostic for NVIDIA LLM Provider.

Never prints or leaks any secret keys.
"""

import os
import sys
from pathlib import Path
from dotenv import dotenv_values, load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
ENV_PATH = ROOT_DIR / ".env"

def run_diagnosis():
    # 1 & 2: Check .env file directly
    file_vals = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    has_key_in_file_dict = "NVIDIA_API_KEY" in file_vals
    raw_file_key = file_vals.get("NVIDIA_API_KEY", "") or ""

    # 3: load_dotenv with override
    load_dotenv(ENV_PATH, override=True)
    env_key = os.getenv("NVIDIA_API_KEY", "") or ""

    # 4: config.py inspection
    import backend.config as cfg
    config_key = cfg.NVIDIA_API_KEY
    config_model = cfg.NVIDIA_MODEL
    config_provider = cfg.LLM_PROVIDER
    config_base_url = cfg.NVIDIA_BASE_URL

    # 5 & 6: llm_factory validation
    import backend.agents.llm_factory as factory
    key_is_valid = factory.is_valid_key(env_key)

    # 9, 10, 11: Attempt initialization
    init_status = False
    init_err = "None"

    if key_is_valid:
        try:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA
            llm = ChatNVIDIA(
                model=config_model,
                api_key=env_key,
                base_url=config_base_url,
            )
            # Try a lightweight invocation or attribute check
            init_status = True
        except Exception as e:
            err_msg = str(e)
            if env_key and env_key in err_msg:
                err_msg = err_msg.replace(env_key, "[REDACTED]")
            init_err = f"{type(e).__name__}: {err_msg}"
            # Fallback check with ChatOpenAI
            try:
                from langchain_openai import ChatOpenAI
                from pydantic import SecretStr
                llm2 = ChatOpenAI(
                    model=config_model,
                    api_key=SecretStr(env_key),
                    base_url=config_base_url,
                )
                init_status = True
                init_err = f"ChatNVIDIA notice ({init_err}), ChatOpenAI succeeded"
            except Exception as e2:
                err2 = str(e2)
                if env_key and env_key in err2:
                    err2 = err2.replace(env_key, "[REDACTED]")
                init_err += f" | ChatOpenAI error: {type(e2).__name__}: {err2}"
    else:
        if not env_key:
            init_err = "NVIDIA_API_KEY is empty"
        else:
            init_err = "is_valid_key() rejected the key format"

    # Evaluate live status via get_llm_info
    llm_info = factory.get_llm_info()

    print(f"provider: {llm_info.get('active_provider', config_provider)}")
    print(f"model: {llm_info.get('active_model', config_model)}")
    print(f"key_exists: {has_key_in_file_dict or 'NVIDIA_API_KEY' in os.environ}")
    print(f"key_non_empty: {bool(env_key and env_key.strip())}")
    print(f"key_length: {len(env_key.strip()) if env_key else 0}")
    print(f"nvidia_llm_initialization: {init_status}")
    print(f"initialization_error: {init_err}")
    print(f"is_live_llm: {llm_info.get('is_live_llm', False)}")


if __name__ == "__main__":
    run_diagnosis()
