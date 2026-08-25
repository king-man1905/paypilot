"""Mock NVIDIA LLM and Evaluation Environment Patcher for PayPilot.

Ensures that evaluation benchmarks run 100% offline, deterministically,
and without making external network calls to the NVIDIA API.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


class MockChatNVIDIA(SimpleChatModel):
    """Deterministic offline Mock LLM emulating NVIDIA Llama 3.3 70B."""

    model_name: str = "meta/llama-3.3-70b-instruct (mocked)"
    temperature: float = 0.0

    def _call(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> str:
        system_text = str(messages[0].content if messages else "").lower()
        last_msg = str(messages[-1].content if messages else "").lower()
        full_text = " ".join(str(m.content) for m in messages).lower()

        # 1. Recovery Agent Executive Briefing Call
        if "executive synthesis" in system_text or "executive decision briefing" in full_text or "ranked actions" in full_text:
            return (
                "EXECUTIVE DIAGNOSIS & REVENUE RECOVERY BRIEFING\n"
                "=================================================\n\n"
                "DIAGNOSIS & BUSINESS HEALTH\n"
                "---------------------------\n"
                "Comprehensive diagnostic audit confirms realized revenue of INR 50,092,576.66 at an 81.71% payment success rate, "
                "with INR 12,654,909.17 in observed gross failed volume and INR 3,488,251.64 in recoverable opportunity.\n\n"
                "EVIDENCE & OBSERVED LEAKAGES\n"
                "----------------------------\n"
                "• Mobile checkout conversion rate lags at 80.66% vs Desktop 85.11% (4.45% mobile conversion gap).\n"
                "• Payment failure rate is 18.29%, driven by Netbanking friction (21.57%) and UPI bank timeouts.\n"
                "• Fashion category refund anomaly stands at 17.99% against an 8.24% baseline.\n\n"
                "PRIORITIZED RECOVERY ACTIONS\n"
                "----------------------------\n"
                "1. P1: Streamline Mobile Checkout UX with 1-Click UPI Intent & Autofill (Estimated Impact: INR 2,589,659.65 | Urgency: High | Effort: Medium)\n"
                "2. P2: Execute Multi-Point Payment Reliability Program to Achieve +3.0% Success Uplift (Estimated Impact: INR 1,839,235.50 | Urgency: High | Effort: Medium)\n"
                "3. P3: Deploy Dynamic Gateway Routing & Intelligent Auto-Retry for UPI / Bank Timeouts (Estimated Impact: INR 1,241,965.81 | Urgency: High | Effort: Low)\n"
                "4. P4: Implement Pre-Purchase Sizing Verification & Return Controls for Fashion (Estimated Impact: INR 412,195.05 | Urgency: Medium | Effort: Medium)\n\n"
                "EXPECTED REVENUE UPSIDE\n"
                "-----------------------\n"
                "Estimated Recoverable Opportunity : INR 3,488,251.64\n"
                "What-If Potential Uplift (+3%)     : +INR 1,839,235.50 net revenue uplift.\n\n"
                "EXECUTIVE RECOMMENDATION\n"
                "------------------------\n"
                "Management should immediately authorize and execute the P1 Mobile Checkout UX Streamline initiative "
                "to eliminate mobile funnel friction and capture high-intent shoppers."
            )

        # 2. Aggregator Synthesis Call
        if "aggregator" in system_text or "cross-functional" in full_text or "synthesize" in system_text:
            return (
                "Cross-functional multi-agent synthesis confirms that revenue decline is primarily driven by "
                "friction in mobile checkout funnels (4.33% mobile conversion gap) and elevated Netbanking "
                "failure rates (21.57%), with a secondary refund anomaly in Fashion (17.99%)."
            )

        # 3. Supervisor Intent Routing Call
        if "supervisor" in system_text or "routing" in system_text or "classify" in system_text:
            query_prompt = last_msg
            if any(k in query_prompt for k in ["payment", "upi", "netbanking", "debit", "credit card", "bank timeout", "gateway"]):
                return json.dumps({
                    "intent": "payment",
                    "confidence": 0.95,
                    "reasoning": "Query asks about payment methods, gateway failures, or UPI transaction drop-offs."
                })
            elif any(k in query_prompt for k in ["mobile", "checkout", "desktop", "device", "funnel", "android", "ios", "tablet"]):
                return json.dumps({
                    "intent": "checkout",
                    "confidence": 0.95,
                    "reasoning": "Query focuses on checkout device conversion rates, funnel drops, or mobile UX."
                })
            elif any(k in query_prompt for k in ["refund", "customer", "cohort", "vip", "fashion", "category", "product category"]):
                return json.dumps({
                    "intent": "customer",
                    "confidence": 0.95,
                    "reasoning": "Query asks about customer cohorts, product categories, or refund anomalies."
                })
            elif any(k in query_prompt for k in ["what if", "what-if", "simulate", "uplift", "% success"]):
                return json.dumps({
                    "intent": "what_if",
                    "confidence": 0.95,
                    "reasoning": "Query asks for scenario simulation or hypothetical revenue uplift."
                })
            else:
                return json.dumps({
                    "intent": "revenue",
                    "confidence": 0.95,
                    "reasoning": "Query asks about holistic revenue decrease, root causes, or recovery priorities."
                })

        return "PayPilot Mock LLM deterministic response."

    @property
    def _llm_type(self) -> str:
        return "mock_nvidia"


def get_mock_llm(
    model: Optional[str] = None,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> MockChatNVIDIA:
    """Returns an offline deterministic MockChatNVIDIA instance."""
    return MockChatNVIDIA(temperature=temperature)


import contextlib
from unittest.mock import patch


@contextlib.contextmanager
def patch_offline_evaluation_llm():
    """Context manager patching all LLM entrypoints to use MockChatNVIDIA."""
    with patch("backend.agents.llm_factory.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.supervisor.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.aggregator.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.recovery_agent.get_llm", side_effect=get_mock_llm):
        yield


def get_mock_llm_info() -> Dict[str, Any]:
    """Returns metadata for the offline mock evaluation provider."""
    return {
        "provider": "mock_nvidia",
        "active_provider": "MOCK/OFFLINE",
        "configured_provider": "nvidia (mocked)",
        "model": "meta/llama-3.3-70b-instruct",
        "active_model": "meta/llama-3.3-70b-instruct (mocked)",
        "is_llm_active": True,
        "is_live_llm": False,
        "nvidia_key_present": True,
        "status_reason": "Evaluation mode: Offline Mock NVIDIA LLM active (No external network calls)",
    }

