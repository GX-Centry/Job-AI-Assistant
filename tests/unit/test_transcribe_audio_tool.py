"""M1-c transcribe_audio 工具单元测试:用户边界、失败收敛、头部标记。

引擎与 blob 全部打桩(名字在 builtin 模块命名空间上打桩 —— 工具按
模块全局查找);真实引擎语义在 asr 引擎单测与在线冒烟里钉。
"""

import pytest

from job_agent.asr.engine import AsrError, AsrOutcome
from job_agent.tools import builtin
from job_agent.tools.builtin import transcribe_audio


def outcome_of(**overrides) -> AsrOutcome:
    values = {
        "text": "转写出的语音内容",
        "language": "zh",
        "language_probability": 0.91,
        "duration_seconds": 3.0,
        "audio_truncated": False,
        "text_truncated": False,
    }
    values.update(overrides)
    return AsrOutcome(**values)


def invoke(audio_id="abc123", config=None):
    if config is None:
        config = {"configurable": {"user_id": "user-001"}}
    return transcribe_audio.invoke({"audio_id": audio_id}, config=config)


def test_schema_exposes_audio_id_not_config():
    # config 由 langchain 注入,绝不能进模型可见的参数表。
    args = transcribe_audio.args

    assert "audio_id" in args
    assert "config" not in args


def test_requires_user_context():
    result = invoke(config={"configurable": {}})

    assert "缺少用户上下文" in result


def test_missing_blob_reports_id_not_stack_trace(monkeypatch):
    calls = []

    def fake_get_audio(user_id, audio_id):
        calls.append((user_id, audio_id))
        # 隐式返回 None:模拟"blob 不存在"。

    monkeypatch.setattr(builtin, "get_audio", fake_get_audio)

    result = invoke(audio_id="gone-id")

    # 必须按用户边界查 —— 不能越用户拿别人的语音。
    assert calls == [("user-001", "gone-id")]
    assert "未找到 audio_id='gone-id'" in result


def test_asr_error_converges_to_readable_text(monkeypatch):
    monkeypatch.setattr(
        builtin,
        "get_audio",
        lambda user_id, audio_id: {"payload": b"x", "name": "v.webm"},
    )

    def fail(*args, **kwargs):
        raise AsrError("ASR model 'small' failed to load (ImportError)")

    monkeypatch.setattr(builtin, "transcribe_payload", fail)

    result = invoke()

    assert "转写失败" in result
    assert "failed to load" in result


def test_empty_transcript_is_explicit(monkeypatch):
    monkeypatch.setattr(
        builtin,
        "get_audio",
        lambda user_id, audio_id: {"payload": b"x", "name": "v.webm"},
    )
    monkeypatch.setattr(
        builtin,
        "transcribe_payload",
        lambda payload: outcome_of(text=""),
    )

    assert "转写结果为空" in invoke()


def test_success_header_and_text(monkeypatch):
    monkeypatch.setattr(
        builtin,
        "get_audio",
        lambda user_id, audio_id: {"payload": b"audio-bytes", "name": "语音.webm"},
    )

    seen = []

    def fake_transcribe(payload):
        seen.append(payload)
        return outcome_of()

    monkeypatch.setattr(builtin, "transcribe_payload", fake_transcribe)

    result = invoke()

    assert seen == [b"audio-bytes"]
    assert "[语音转写 name=语音.webm 语言=zh(0.91) 音频时长=3.0s]" in result
    assert "转写出的语音内容" in result
    assert "仅转写前段" not in result
    assert "转写文本已截断" not in result


def test_truncation_flags_surface_in_header(monkeypatch):
    monkeypatch.setattr(
        builtin,
        "get_audio",
        lambda user_id, audio_id: {"payload": b"x", "name": "v.webm"},
    )
    monkeypatch.setattr(
        builtin,
        "transcribe_payload",
        lambda payload: outcome_of(audio_truncated=True, text_truncated=True),
    )

    result = invoke()

    assert "超长音频仅转写前段" in result
    assert "转写文本已截断" in result


@pytest.mark.parametrize(
    ("outcome_kwargs", "expected"),
    [
        (
            {"language": "en", "language_probability": 0.5, "duration_seconds": 1.0},
            "语言=en(0.50)",
        ),
    ],
)
def test_header_number_formatting(monkeypatch, outcome_kwargs, expected):
    monkeypatch.setattr(
        builtin,
        "get_audio",
        lambda user_id, audio_id: {"payload": b"x", "name": "v.webm"},
    )
    monkeypatch.setattr(
        builtin,
        "transcribe_payload",
        lambda payload: outcome_of(**outcome_kwargs),
    )

    assert expected in invoke()
