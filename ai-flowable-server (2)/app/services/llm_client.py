from functools import lru_cache

from openai import OpenAI

from app.config import get_settings


@lru_cache
def get_llm_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
    )


def warm_up_model() -> None:
    settings = get_settings()
    get_llm_client().chat.completions.create(
        model=settings.llm_model,
        temperature=0,
        messages=[{"role": "user", "content": "Reply OK."}],
        max_tokens=1,
        stream=False,
    )
