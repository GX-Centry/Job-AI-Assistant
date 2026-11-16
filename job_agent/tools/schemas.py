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
