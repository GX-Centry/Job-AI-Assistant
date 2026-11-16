import json
import os
import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)


def load_api_keys() -> dict[str, str]:
    raw = os.getenv("JOB_AGENT_API_KEYS")

    if not raw:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured.",
        )

    try:
        api_keys = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=503,
            detail="Invalid API authentication configuration.",
        ) from None

    if not isinstance(api_keys, dict) or not api_keys:
        raise HTTPException(
            status_code=503,
            detail="Invalid API authentication configuration.",
        )

    for token, user_id in api_keys.items():
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(user_id, str)
            or not user_id.strip()
        ):
            raise HTTPException(
                status_code=503,
                detail="Invalid API authentication configuration.",
            )

    return api_keys


def get_current_user_id(
    api_key: str | None = Security(api_key_header),
) -> str:
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key.",
        )

    api_keys = load_api_keys()

    for configured_key, user_id in api_keys.items():
        if secrets.compare_digest(
            api_key.encode("utf-8"),
            configured_key.encode("utf-8"),
        ):
            return user_id.strip()

    raise HTTPException(
        status_code=401,
        detail="Invalid API key.",
    )
