# 在线聊天 API(localhost:8000)附件链路冒烟 —— 访问真实 LLM 与在线业务库。
# 自包含:新建"测试后删除"会话 → 图片问答 / 纯语音附件防呆 / 历史回读 →
# 经支持的 DELETE 流程清理该会话(会话行 + 检查点线程),不触碰其他历史数据。
# 不打印 API Key。用法:docker compose exec -T dev python -m scripts.check_live_attachments

import base64
import json
import os
import struct
import zlib

import httpx


def make_image_b64() -> str:
    """上半红、下半蓝的 64x64 假图(与 check_model_multimodal 同源)。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + (bytes((255, 0, 0)) if y < 32 else bytes((0, 0, 255))) * 64
        for y in range(64)
    )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )

    return base64.b64encode(png).decode()


def sse_text(response: httpx.Response) -> str:
    return "".join(
        line[6:]
        for line in response.text.splitlines()
        if line.startswith("data: ") and '"content"' in line
    )


def main() -> None:
    api_key = next(iter(json.loads(os.environ["JOB_AGENT_API_KEYS"])))

    image_b64 = make_image_b64()
    image = {
        "kind": "image",
        "name": "smoke.png",
        "mime": "image/png",
        "size": len(base64.b64decode(image_b64)),
        "data": image_b64,
    }
    audio = {
        "kind": "audio",
        "name": "冒烟语音.wav",
        "mime": "audio/wav",
        "size": 16044,
    }

    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": api_key},
        timeout=120,
    ) as client:
        session = client.post(
            "/sessions",
            json={"title": "附件冒烟(测试后删除)"},
        ).json()
        conversation_id = session["conversation_id"]

        try:
            first = client.post(
                "/chat/stream",
                json={
                    "conversation_id": conversation_id,
                    "message": "这张图的上半和下半分别是什么颜色?",
                    "attachments": [image],
                },
            )
            print("stream(image) http:", first.status_code)
            print("assistant:", sse_text(first)[:200])

            second = client.post(
                "/chat/stream",
                json={
                    "conversation_id": conversation_id,
                    "message": "",
                    "attachments": [audio],
                },
            )
            print("stream(audio-only) http:", second.status_code)
            print("assistant:", sse_text(second)[:200])

            history = client.get(f"/sessions/{conversation_id}/messages").json()
            user_messages = [m for m in history if m["role"] == "user"]

            print("history user texts:", [m["content"] for m in user_messages])
            print(
                "history attachments:",
                [m["attachments"] for m in user_messages],
            )
        finally:
            deleted = client.delete(f"/sessions/{conversation_id}")
            print("cleanup delete http:", deleted.status_code)
            print("sessions left:", len(client.get("/sessions").json()))


if __name__ == "__main__":
    main()
