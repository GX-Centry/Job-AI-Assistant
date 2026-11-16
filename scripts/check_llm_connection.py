import time

from langchain_core.messages import HumanMessage

from job_agent.llm.factory import create_chat_model


def main():
    model = create_chat_model()
    start = time.perf_counter()

    try:
        response = model.invoke([HumanMessage(content="请只回复：连接成功")])

    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - start

        print("LLM connection: FAILED")
        print(f"Elapsed: {elapsed:.2f}s")

        current = exc
        seen = set()

        while current is not None and id(current) not in seen:
            seen.add(id(current))

            print("\n" + "=" * 50)
            print("Error type:", type(current).__name__)
            print("Error message:", str(current))

            for attr in (
                "status_code",
                "body",
                "request_id",
                "code",
                "param",
            ):
                value = getattr(current, attr, None)
                if value is not None:
                    print(f"{attr}: {value}")

            current = current.__cause__ or current.__context__

        raise SystemExit(1)

    elapsed = time.perf_counter() - start

    print("LLM connection: SUCCESS")
    print(f"Elapsed: {elapsed:.2f}s")
    print("Model response:", response.content)


if __name__ == "__main__":
    main()
