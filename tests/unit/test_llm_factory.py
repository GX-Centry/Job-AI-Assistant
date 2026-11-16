import pytest

from job_agent.config import Settings
from job_agent.llm.factory import create_chat_model


def test_missing_model_config(monkeypatch):
    monkeypatch.delenv(
        "JOB_AGENT_MODEL_PROVIDER",
        raising=False,
    )
    monkeypatch.delenv(
        "JOB_AGENT_MODEL_NAME",
        raising=False,
    )

    with pytest.raises(ValueError):
        create_chat_model(Settings(_env_file=None))


def test_create_chat_model(monkeypatch):
    captured = {}

    def fake_init(**kwargs):
        captured.update(kwargs)
        return "mock_model"

    monkeypatch.setattr(
        "job_agent.llm.factory.init_chat_model",
        fake_init,
    )

    settings = Settings(
        _env_file=None,
        model_provider="openai",
        model_name="test-model",
        model_api_key="test-key",
        model_base_url="https://example.com/v1",
    )

    result = create_chat_model(settings)

    assert result == "mock_model"
    assert captured["model"] == "test-model"
    assert captured["model_provider"] == "openai"
    assert captured["api_key"] == "test-key"
