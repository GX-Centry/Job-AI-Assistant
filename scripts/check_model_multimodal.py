# 探测当前配置的聊天 LLM 是否支持图片 / 语音输入(访问真实 LLM)。
# 素材全部由标准库在运行时生成(纯色半幅图 = 假图片;440Hz 正弦音 = 假音频),
# 不使用任何真实用户内容,也不打印载荷本身。
# 用法:docker compose exec -T dev python -m scripts.check_model_multimodal

import base64
import io
import math
import struct
import wave
import zlib

from langchain_core.messages import HumanMessage

from job_agent.llm.factory import create_chat_model


def make_png(width: int = 64, height: int = 64) -> bytes:
    """上半红色、下半蓝色的无文字位图(纯 stdlib PNG)。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    top = bytes((255, 0, 0)) * width
    bottom = bytes((0, 0, 255)) * width
    raw = b"".join(
        b"\x00" + (top if y < height // 2 else bottom) for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def make_tone_wav(seconds: float = 0.5, freq: float = 440.0) -> bytes:
    """短正弦音 WAV(并非语音,用于判断音频通道是否被接受)。"""
    rate = 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
                for i in range(n)
            )
        )
    return buf.getvalue()


def run_probe(
    model, label: str, parts: list, must_contain: list[str], discard_marker: str = ""
) -> str:
    """must_contain:全部出现才算真读懂;discard_marker:出现即证明内容被静默丢弃。"""
    print(f"--- {label} ---")
    try:
        response = model.invoke([HumanMessage(content=parts)])
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本,按异常判能力
        message = str(exc).replace("\n", " ")[:300]
        print(f"ERROR: {type(exc).__name__}: {message}")
        print(f"VERDICT[{label}]: REJECTED (通道不被接受)\n")
        return "rejected"

    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    lowered = str(content).lower()
    print(f"RESPONSE: {str(content)[:200]!r}")
    if discard_marker and discard_marker.lower() in lowered:
        print(f"VERDICT[{label}]: DISCARDED (接口未报错,但内容被静默丢弃)\n")
        return "discarded"
    if all(token in lowered for token in must_contain):
        print(f"VERDICT[{label}]: SUPPORTED (接受且内容与素材相符)\n")
        return "supported"
    print(f"VERDICT[{label}]: ACCEPTED (接受但回答与素材预期不符,需人工判断)\n")
    return "accepted_unclear"


def main():
    model = create_chat_model()

    # 基线:纯文本必须可用,否则后面的结论无意义。
    baseline = model.invoke([HumanMessage(content="回复两个字:可以")])
    print(f"baseline text ok: {str(baseline.content)[:50]!r}\n")

    png_b64 = base64.b64encode(make_png()).decode()
    run_probe(
        model,
        "image(vision)",
        [
            {
                "type": "text",
                "text": "这张图的上半部分和下半部分分别是什么颜色?各用一个词回答。",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{png_b64}"},
            },
        ],
        must_contain=["红", "蓝"],
    )

    wav_b64 = base64.b64encode(make_tone_wav()).decode()
    run_probe(
        model,
        "audio(input_audio)",
        [
            {
                "type": "text",
                "text": "如果你根本没有收到任何音频输入,只回复 NO_AUDIO;否则用一句话描述这段音频。",
            },
            {"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}},
        ],
        must_contain=["hz", "声", "音"],  # 真收到音频时大概率描述音色/频率
        discard_marker="NO_AUDIO",
    )


if __name__ == "__main__":
    main()
