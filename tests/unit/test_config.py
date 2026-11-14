from job_agent.config import Settings, get_settings


def test_settings_defaults():
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.model_temperature == 0.0


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_APP_ENV", "test")
    monkeypatch.setenv("JOB_AGENT_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"


def test_secret_masking(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_MODEL_API_KEY", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.model_api_key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(settings)


def test_get_settings():
    assert isinstance(get_settings(), Settings)
