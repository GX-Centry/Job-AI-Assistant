# 验证模型服务端是否真正支持流式输出。
# 直接调用 ChatModel.stream(),按到达时间打印 chunk,判断是逐 token 返回还是整段返回。

import time

from langchain_core.messages import HumanMessage

from job_agent.llm.factory import create_chat_model

PROMPT = "请用中文写一段约200字的话,介绍Python的三个优点。"


def extract_text(content) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )

    return ""


def main():
    model = create_chat_model()

    start = time.perf_counter()

    first_chunk_at = None
    last_chunk_at = start

    chunk_count = 0
    char_count = 0
    max_gap = 0.0

    samples = []

    for chunk in model.stream([HumanMessage(content=PROMPT)]):
        now = time.perf_counter()

        if first_chunk_at is None:
            first_chunk_at = now
        else:
            max_gap = max(max_gap, now - last_chunk_at)

        last_chunk_at = now

        text = extract_text(chunk.content)

        if not text:
            continue

        chunk_count += 1
        char_count += len(text)

        if chunk_count <= 5 or chunk_count % 10 == 0:
            samples.append((now - start, text))

    total_span = last_chunk_at - start

    print(f"chunks={chunk_count} chars={char_count}")

    if first_chunk_at is not None:
        print(f"first_chunk_latency={first_chunk_at - start:.2f}s")
    else:
        print("first_chunk_latency=N/A")

    print(f"stream_span={total_span:.2f}s max_inter_chunk_gap={max_gap:.2f}s")

    for elapsed, text in samples:
        print(f"  t+{elapsed:.2f}s {text!r}")

    print()

    if chunk_count > 1 and max_gap > 0.1:
        print("VERDICT: STREAMING SUPPORTED (incremental chunks over time)")
    elif chunk_count == 1:
        print("VERDICT: SINGLE CHUNK - server likely buffers the full completion")
    else:
        print("VERDICT: NO TEXT CHUNKS RECEIVED")


if __name__ == "__main__":
    main()
