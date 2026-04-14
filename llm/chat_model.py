"""ChatOpenAI configured from settings (LiteLLM / OpenAI-compatible proxy)."""

from langchain_openai import ChatOpenAI

from settings import settings


def get_chat_model(*, temperature: float = 0.1) -> ChatOpenAI:
    """Return a chat model bound to `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`."""
    key = settings.llm_api_key.strip() or "not-set"
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=key,
        model=settings.llm_model,
        temperature=temperature,
    )
