import json
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()


def main():
    raw_api_keys = os.getenv("JOB_AGENT_API_KEYS")

    if not raw_api_keys:
        raise RuntimeError("JOB_AGENT_API_KEYS not found. Please check your .env file.")

    api_keys = json.loads(raw_api_keys)

    token = next(iter(api_keys))

    if not token.isascii():
        raise ValueError("API Key must contain ASCII characters only.")

    headers = {"X-API-Key": token}

    with httpx.Client(
        base_url="http://localhost:8000",
        timeout=120,
    ) as client:
        session = client.post(
            "/sessions",
            headers=headers,
            json={"title": "Streaming test"},
        )
        session.raise_for_status()

        conversation_id = session.json()["conversation_id"]

        start = time.perf_counter()

        event_name = ""
        token_events = 0
        token_chars = 0

        with client.stream(
            "POST",
            "/chat/stream",
            headers=headers,
            json={
                "conversation_id": conversation_id,
                "message": "请用中文介绍 Python 的三个优点。",
            },
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                elapsed = time.perf_counter() - start

                if line.startswith("event:"):
                    event_name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    if event_name == "token":
                        token_events += 1

                        payload = json.loads(line[len("data:") :].strip())
                        content = payload.get("content", "")
                        token_chars += len(content)

                        if token_events <= 5 or token_events % 10 == 0:
                            preview = content[:30]
                            print(
                                f"t+{elapsed:.2f}s token #{token_events}: {preview!r}",
                                flush=True,
                            )
                    else:
                        print(
                            f"t+{elapsed:.2f}s {line}",
                            flush=True,
                        )

        print()
        print(f"token_events={token_events} token_chars={token_chars}")

        if token_events > 1:
            print("VERDICT: incremental SSE token events received (real streaming)")
        elif token_events == 1:
            print("VERDICT: single token event (no real streaming)")
        else:
            print("VERDICT: no token events")


if __name__ == "__main__":
    main()
