"""M1-c ASR 引擎单元测试:假引擎注入,不加载 faster-whisper、不碰权重。

真实引擎只在冒烟脚本/在线验收里验证;这里钉死转写编排语义:
时长截断、字符截断、空 payload、失败收敛为 AsrError。
"""

import pytest

from job_agent.asr.engine import (
    MAX_TRANSCRIPT_CHARS,
    AsrError,
    transcribe_payload,
)
from job_agent.config import Settings


class FakeSegment:
    def __init__(self, start: float, text: str) -> None:
        self.start = start
        self.text = text


class FakeInfo:
    language = "zh"
    language_probability = 0.91
    duration = 12.5


class BareInfo:
    """缺属性的 info(引擎版本差异容错)。"""


class FakeModel:
    def __init__(self, segments, info=None, fail=None) -> None:
        self.segments = segments
        self.info = info if info is not None else FakeInfo()
        self.fail = fail
        self.calls = []

    def transcribe(self, file_like, vad_filter=False):
        self.calls.append((file_like.read(), vad_filter))

        if self.fail is not None:
            raise self.fail

        # faster-whisper 语义:segments 是惰性生成器,info 即时返回。
        return iter(self.segments), self.info


def settings_with(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_transcribe_joins_segments_and_reports_metadata():
    model = FakeModel([FakeSegment(0.0, "你好"), FakeSegment(1.2, "世界")])

    outcome = transcribe_payload(
        b"RIFFfake-audio", model=model, settings=settings_with()
    )

    # BytesIO 传入 + VAD 常开。
    payload, vad = model.calls[0]
    assert payload == b"RIFFfake-audio"
    assert vad is True

    assert outcome.text == "你好世界"
    assert outcome.language == "zh"
    assert outcome.language_probability == pytest.approx(0.91)
    assert outcome.duration_seconds == pytest.approx(12.5)
    assert not outcome.audio_truncated
    assert not outcome.text_truncated


def test_max_seconds_stops_lazy_segments_and_marks_truncation():
    model = FakeModel(
        [
            FakeSegment(0.0, "第一段"),
            FakeSegment(4.0, "第二段"),
            FakeSegment(9.0, "第三段"),
        ],
    )

    outcome = transcribe_payload(
        b"payload",
        model=model,
        settings=settings_with(asr_max_seconds=5.0),
    )

    assert outcome.text == "第一段第二段"
    assert outcome.audio_truncated
    assert "第三段" not in outcome.text


def test_transcript_char_budget_truncated_flag():
    long_text = "啊" * (MAX_TRANSCRIPT_CHARS + 50)
    model = FakeModel([FakeSegment(0.0, long_text)])

    outcome = transcribe_payload(b"payload", model=model, settings=settings_with())

    assert outcome.text_truncated
    assert len(outcome.text) == MAX_TRANSCRIPT_CHARS


def test_empty_payload_rejected_before_engine():
    with pytest.raises(AsrError, match="empty audio payload"):
        transcribe_payload(b"", model=FakeModel([]), settings=settings_with())


def test_engine_failure_converges_to_asr_error():
    model = FakeModel([], fail=RuntimeError("decode exploded"))

    with pytest.raises(AsrError, match="transcription failed"):
        transcribe_payload(b"payload", model=model, settings=settings_with())


def test_empty_segments_gives_empty_text():
    model = FakeModel([])

    outcome = transcribe_payload(b"payload", model=model, settings=settings_with())

    assert outcome.text == ""


def test_info_defaults_when_attributes_missing():
    model = FakeModel([FakeSegment(0.0, "喂")], info=BareInfo())

    outcome = transcribe_payload(b"payload", model=model, settings=settings_with())

    assert outcome.language == "?"
    assert outcome.language_probability == 0.0
    assert outcome.duration_seconds == 0.0
