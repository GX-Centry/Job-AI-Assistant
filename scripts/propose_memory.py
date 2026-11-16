from job_agent.llm.factory import create_chat_model
from job_agent.memory.extractor import extract_memory_candidate
from job_agent.memory.manager import get_memory, save_memory
from job_agent.memory.store import open_memory_store

DB_PATH = "data/agent_memory.sqlite"
USER_ID = "demo-user-001"


def main():
    print("请输入用户自己的对话内容。")
    print("不要输入密码、API Key 或其他敏感信息。")

    user_text = input("User: ").strip()

    if not user_text:
        raise ValueError("Input cannot be empty.")

    candidate = extract_memory_candidate(
        create_chat_model(),
        [user_text],
    )

    if not candidate.should_save:
        print("No durable memory candidate found.")
        return

    print("\nMemory candidate")
    print("Key:", candidate.key)
    print("Value:", candidate.value)
    print("Source:", candidate.source_quote)
    print("Reason:", candidate.reason)

    with open_memory_store(DB_PATH) as store:
        profile = (
            get_memory(
                store,
                USER_ID,
                "profile",
            )
            or {}
        )

        if candidate.key in profile:
            print("Existing value:", profile[candidate.key])

        confirmation = input("\nType SAVE to confirm persistence: ").strip()

        if confirmation != "SAVE":
            print("Memory not saved.")
            return

        profile[candidate.key] = candidate.value

        save_memory(
            store,
            USER_ID,
            "profile",
            profile,
        )

    print("Memory saved successfully.")


if __name__ == "__main__":
    main()
