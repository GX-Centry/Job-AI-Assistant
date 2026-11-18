"""M1-c:faster-whisper ASR 引擎(服务端本机转写,绝不送 LLM 网关)。

档位(2026-11-18 评测拍板,见 experiments/tool-research/REPORT.md §1/§2):

- ``small`` + CPU + int8:中文样本 9.3s→2.6s(≈3.5× 实时),识别质量达标;
  权重已离线缓存(462MB)。升级口是配置项(``JOB_AGENT_ASR_MODEL`` 可切
  base/large-v3,large-v3 属"以后有 GPU 再切"路线);
- faster-whisper 与 ctranslate2/av 的版本锁定见 requirements.txt
  (av 19 与 faster-whisper 1.2.1 不兼容,REPORT §2 实测坑)。

工程约束:

- **懒 import**:模块导入不触碰 ctranslate2/av(在线环境与 CI 都不需要),
  只有首次真正转写才加载;模型进程级单例(加载成本 ~2s,按配置指纹缓存);
- 时长保护:超过 ``asr_max_seconds`` 的音频只转写前段并显式标记;
- 转写文本上限 ``MAX_TRANSCRIPT_CHARS``(上下文窗口保护,截断显式标记)。
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass

from job_agent.config import Settings, get_settings

MAX_TRANSCRIPT_CHARS = 30000


class AsrError(RuntimeError):
    """引擎加载失败或转写失败 —— 工具层收敛为用户可读文本。"""


@dataclass(frozen=True)
class AsrOutcome:
    text: str
    language: str
    language_probability: float
    duration_seconds: float
    audio_truncated: bool
    text_truncated: bool


_model_lock = threading.Lock()
_model = None
_model_key: tuple | None = None


def _model_fingerprint(settings: Settings) -> tuple:
    return (
        settings.asr_model,
        settings.asr_compute_type,
        str(settings.asr_model_cache_dir),
    )


def get_model_for(settings: Settings | None = None):
    """进程级模型单例(按配置指纹缓存);加载失败 → AsrError。"""

    global _model, _model_key

    settings = settings or get_settings()
    fingerprint = _model_fingerprint(settings)

    with _model_lock:
        if _model is not None and _model_key == fingerprint:
            return _model

        try:
            # 懒 import:faster-whisper/ctranslate2/av 只在真正用到时加载。
            from faster_whisper import WhisperModel

            model = WhisperModel(
                settings.asr_model,
                device="cpu",
                compute_type=settings.asr_compute_type,
                download_root=str(settings.asr_model_cache_dir),
            )
        except Exception as exc:
            raise AsrError(
                f"ASR model {settings.asr_model!r} failed to load "
                f"({type(exc).__name__})",
            ) from exc

        _model = model
        _model_key = fingerprint

        return model


def transcribe_payload(
    payload: bytes,
    model=None,
    settings: Settings | None = None,
) -> AsrOutcome:
    """内存转写(不落盘):webm/wav/mp3/m4a 等由 PyAV 按内容解码。

    ``model`` 可注入(测试用假引擎);缺省取进程级单例。
    """

    if not payload:
        raise AsrError("empty audio payload")

    settings = settings or get_settings()
    model = model or get_model_for(settings)

    try:
        segments, info = model.transcribe(io.BytesIO(payload), vad_filter=True)
    except Exception as exc:
        raise AsrError(f"transcription failed ({type(exc).__name__})") from exc

    max_seconds = settings.asr_max_seconds
    parts: list[str] = []
    truncated_audio = False

    # segments 是惰性生成器:超时长直接停,不必解码整段音频。
    for segment in segments:
        if segment.start >= max_seconds:
            truncated_audio = True
            break
        parts.append(segment.text)

    text = "".join(parts).strip()

    text_truncated = len(text) > MAX_TRANSCRIPT_CHARS

    if text_truncated:
        text = text[:MAX_TRANSCRIPT_CHARS]

    return AsrOutcome(
        text=text,
        language=getattr(info, "language", "?"),
        language_probability=float(getattr(info, "language_probability", 0.0)),
        duration_seconds=float(getattr(info, "duration", 0.0)),
        audio_truncated=truncated_audio,
        text_truncated=text_truncated,
    )
