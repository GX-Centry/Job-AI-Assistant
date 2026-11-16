import json

import pytest
from fastapi import HTTPException

from job_agent.api.security import get_current_user_id


@pytest.fixture
def configured_keys(monkeypatch):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        json.dumps(
            {
                "token-user-001": "user-001",
                "token-user-002": "user-002",
            }
        ),
    )


def test_first_user_identification(configured_keys):
    assert get_current_user_id("token-user-001") == "user-001"


def test_second_user_identification(configured_keys):
    assert get_current_user_id("token-user-002") == "user-002"


def test_invalid_token_rejected(configured_keys):
    with pytest.raises(HTTPException) as exc:
        get_current_user_id("wrong-token")

    assert exc.value.status_code == 401


def test_missing_token_rejected(configured_keys):
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(None)

    assert exc.value.status_code == 401


def test_missing_configuration_rejected(monkeypatch):
    monkeypatch.delenv("JOB_AGENT_API_KEYS", raising=False)

    with pytest.raises(HTTPException) as exc:
        get_current_user_id("token-user-001")

    assert exc.value.status_code == 503
