from langchain.chat_models import init_chat_model

from job_agent.config import Settings, get_settings


def create_chat_model(settings: Settings | None = None):
    settings = settings or get_settings()

    if not settings.model_provider or not settings.model_name:
        raise ValueError("MODEL_PROVIDER and MODEL_NAME must be configured.")

    kwargs = {
        "model": settings.model_name,
        "model_provider": settings.model_provider,
        "temperature": settings.model_temperature,
        "timeout": settings.model_timeout_seconds,
        "max_retries": settings.model_max_retries,
    }

    # OpenAI 及 OpenAI 兼容接口
    if settings.model_provider == "openai":
        if settings.model_api_key:
            kwargs["api_key"] = settings.model_api_key.get_secret_value()

        if settings.model_base_url:
            kwargs["base_url"] = settings.model_base_url
            kwargs["use_responses_api"] = False

    return init_chat_model(**kwargs)
