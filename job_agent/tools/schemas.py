from pydantic import BaseModel, ConfigDict, Field


class TextLengthInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    text: str = Field(
        min_length=1,
        max_length=1000,
    )


class TranscribeAudioInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    audio_id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "语音附件的 audio_id(取自附件防呆提示/附件元数据中的 "
            "audio_id=... 字段),不是文件名。"
        ),
    )
