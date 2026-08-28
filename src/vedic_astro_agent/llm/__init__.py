"""Optional LLM integration.

The agent is fully functional without any LLM (deterministic planning and
interpretation). With an OpenAI-compatible client, the LLM only *polishes* prose —
every factual claim still has to pass the grounding validator.
"""

from vedic_astro_agent.llm.base import LLMClient, StubLLM
from vedic_astro_agent.llm.openai_compat import OpenAICompatClient, llm_from_env

__all__ = ["LLMClient", "OpenAICompatClient", "StubLLM", "llm_from_env"]
