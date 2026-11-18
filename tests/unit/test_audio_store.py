"""M1-c 语音 blob 存储单元测试:往返、用户边界、会话生命周期。

(会话级 conftest 已把模块默认 DB_PATH 重定向到临时目录;这里再按测试
独立 tmp_path 覆盖,保证用例互不共享数据。)
"""

import pytest

from job_agent.api import audio_store

WAV_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 32


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_store, "DB_PATH", tmp_path / "audio.sqlite")


def test_store_and_get_round_trip():
    record = audio_store.store_audio(
        "user-001",
        "conv-1",
        "voice.wav",
        "audio/wav",
        "wav",
        WAV_BYTES,
    )

    assert record["audio_id"]
    assert record["size"] == len(WAV_BYTES)

    stored = audio_store.get_audio("user-001", record["audio_id"])

    assert stored is not None
    assert stored["payload"] == WAV_BYTES
    assert stored["name"] == "voice.wav"
    assert stored["conversation_id"] == "conv-1"


def test_each_store_gets_distinct_id():
    first = audio_store.store_audio("u", "c", "a.webm", "audio/webm", "webm", b"x")
    second = audio_store.store_audio("u", "c", "a.webm", "audio/webm", "webm", b"x")

    assert first["audio_id"] != second["audio_id"]


@pytest.mark.parametrize(
    ("user_id", "conversation_id", "payload"),
    [
        ("", "conv", b"data"),
        ("   ", "conv", b"data"),
        ("user", "", b"data"),
        ("user", "conv", b""),
    ],
)
def test_rejects_empty_identity_or_payload(user_id, conversation_id, payload):
    with pytest.raises(ValueError):
        audio_store.store_audio(
            user_id, conversation_id, "n", "audio/wav", "wav", payload
        )


def test_get_audio_is_user_scoped():
    record = audio_store.store_audio(
        "user-a", "conv-1", "v.wav", "audio/wav", "wav", b"audio-bytes"
    )

    # 工具调用永远不能越用户读取音频。
    assert audio_store.get_audio("user-b", record["audio_id"]) is None
    assert audio_store.get_audio("user-a", "missing-id") is None


def test_delete_for_session_only_touches_that_session():
    in_a = audio_store.store_audio(
        "user-a", "conv-a", "v1.wav", "audio/wav", "wav", b"one"
    )
    in_b = audio_store.store_audio(
        "user-a", "conv-b", "v2.wav", "audio/wav", "wav", b"two"
    )
    other_user = audio_store.store_audio(
        "user-b", "conv-a", "v3.wav", "audio/wav", "wav", b"three"
    )

    deleted = audio_store.delete_audio_for_session("user-a", "conv-a")

    assert deleted == 1
    assert audio_store.get_audio("user-a", in_a["audio_id"]) is None
    # 同用户其他会话、其他用户同名片段都不受影响。
    assert audio_store.get_audio("user-a", in_b["audio_id"]) is not None
    assert audio_store.get_audio("user-b", other_user["audio_id"]) is not None

    # 幂等:再删同一会话返回 0。
    assert audio_store.delete_audio_for_session("user-a", "conv-a") == 0
