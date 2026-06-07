"""
Fábrica de LLM, agnóstica de provedor.

O padrão é Google Gemini (mesmo stack da Franq: Vertex AI / Gemini), mas trocar
para OpenAI ou Anthropic é só ajustar LLM_PROVIDER e a chave no .env.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from . import config


def get_llm() -> BaseChatModel:
    provider = config.LLM_PROVIDER.lower()

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=config.MODEL_NAME,
            temperature=config.TEMPERATURE,
            max_retries=config.MAX_LLM_RETRIES,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=config.MODEL_NAME,
            temperature=config.TEMPERATURE,
            max_retries=config.MAX_LLM_RETRIES,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=config.MODEL_NAME,
            temperature=config.TEMPERATURE,
            max_retries=config.MAX_LLM_RETRIES,
        )

    raise ValueError(
        f"LLM_PROVIDER '{config.LLM_PROVIDER}' inválido. "
        "Use 'google', 'openai' ou 'anthropic'."
    )
