import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from job_agent.api.audio_store import get_audio
from job_agent.asr.engine import AsrError, transcribe_payload
from job_agent.tools.schemas import TextLengthInput, TranscribeAudioInput

logger = logging.getLogger(__name__)


@tool(args_schema=TextLengthInput)
def text_length(text: str) -> int:
    """Calculate the number of characters in text."""
    return len(text)


@tool(args_schema=TranscribeAudioInput)
def transcribe_audio(audio_id: str, config: RunnableConfig) -> str:
    """把用户语音附件转写为文本(服务端本机 faster-whisper,内容不经 LLM 网关)。

    仅当用户消息的附件防呆提示里出现 ``audio_id=...`` 时调用,参数就是那个 id。
    转写结果以工具消息进入对话;在此之前**不要**声称听过音频。
    """

    # config 由 langchain 注入(schema 不含该参数),user_id 贯穿整个请求链路。
    user_id = ((config or {}).get("configurable") or {}).get("user_id")

    if not user_id:
        return "错误:缺少用户上下文,无法定位语音附件。"

    record = get_audio(user_id, audio_id)

    if record is None:
        return (
            f"错误:未找到 audio_id={audio_id!r} 的语音附件"
            "(可能已随会话删除,或 id 不正确)。"
        )

    try:
        outcome = transcribe_payload(record["payload"])
    except AsrError as exc:
        logger.warning(
            "audio transcription failed: audio_id=%s err=%s",
            audio_id,
            exc,
        )
        return f"错误:转写失败:{exc}"

    if not outcome.text:
        return "转写结果为空:该音频没有清晰语音(可能是静音或纯噪声)。"

    header = (
        f"[语音转写 name={record['name']} 语言={outcome.language}"
        f"({outcome.language_probability:.2f}) 音频时长={outcome.duration_seconds:.1f}s"
    )

    if outcome.audio_truncated:
        header += " · 超长音频仅转写前段"

    header += "]"

    if outcome.text_truncated:
        header += "(转写文本已截断)"

    return f"{header}\n{outcome.text}"
