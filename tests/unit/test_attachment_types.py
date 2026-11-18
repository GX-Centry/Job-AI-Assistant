"""M1-a 识别/路由层单元测试:魔数嗅探、声明推断、策略表一致性。"""

import io
from zipfile import ZipFile

import pytest

from job_agent.api.app import MAX_IMAGE_BYTES as APP_MAX_IMAGE_BYTES
from job_agent.api.attachment_types import (
    CHANNEL_BY_RESOLVED,
    MAX_AUDIO_BYTES,
    MAX_DOCUMENT_BYTES,
    MAX_IMAGE_BYTES,
    ROUTE_POLICIES,
    channel_of,
    declared_resolved,
    sniff_resolved,
)


def _zip_bytes(*entry_names: str) -> bytes:
    buffer = io.BytesIO()

    with ZipFile(buffer, "w") as archive:
        for entry in entry_names:
            archive.writestr(entry, "<stub/>")

    return buffer.getvalue()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
        (b"GIF89a\x01\x00\x01\x00", "gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
        (b"BM\x36\x00\x00\x00\x00\x00", "bmp"),
        (b"II*\x00\x2c\x00\x00\x00", "tiff"),
        (b"MM\x00\x2a\x00\x00\x00\x00", "tiff"),
        (b"\x00\x00\x01\x00\x01\x00\x20\x20", "ico"),
        (b"\x1a\x45\xdf\xa3\x01\x00\x00\x00", "webm"),
        (b"OggS\x00\x02", "ogg"),
        (b"ID3\x03\x00\x00\x00\x00", "mp3"),
        (b"\xff\xfb\x90\x64\x00\x00\x00\x00", "mp3"),
        (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00", "m4a"),
        # HEIC 与 m4a 同为 ftyp 容器,品牌字段必须把它拉回图片族。
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00", "heif"),
        (b"%PDF-1.7\n%\xb5\xb5", "pdf"),
        (_zip_bytes("word/document.xml"), "docx"),
        (_zip_bytes("xl/workbook.xml"), "xlsx"),
        (_zip_bytes("ppt/presentation.xml"), "pptx"),
        (_zip_bytes("readme.txt"), "zip"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00", "ole2"),
        ("中文纯文本内容,可正常解码。".encode(), "text"),
        (b"\xef\xbb\xbf<h1>hi</h1>", "text"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "text"),
        (b"", "unknown"),
        (b"\x00\x01\x02\x03\xff\xfe\x00\x01\x02\x03", "unknown"),
    ],
)
def test_sniff_magic_table(payload: bytes, expected: str) -> None:
    assert sniff_resolved(payload) == expected


def test_sniff_jpeg_wins_over_mpeg_frame_sync() -> None:
    # JPEG 的 FF D8 FF 同时满足 MPEG 帧同步位模式 —— 判定顺序必须图片优先。
    assert sniff_resolved(b"\xff\xd8\xff\xdb") == "jpeg"


@pytest.mark.parametrize(
    ("resolved", "channel"),
    [
        ("png", "multimodal"),
        ("heif", "multimodal"),
        ("webm", "asr"),
        ("wav", "asr"),
        ("pdf", "document"),
        ("docx", "document"),
        ("ole2", "document"),
        ("text", "text"),
        ("zip", "archive"),
        ("unknown", "none"),
        ("nonsense", "none"),
    ],
)
def test_channel_map(resolved: str, channel: str) -> None:
    assert channel_of(resolved) == channel


@pytest.mark.parametrize(
    ("mime", "name", "expected"),
    [
        ("audio/wav", "voice.wav", "wav"),
        ("application/pdf", "resume.pdf", "pdf"),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "resume.docx",
            "docx",
        ),
        ("text/markdown", "notes.anything", "text"),
        # mime 缺省时靠扩展名兜底;大小写不敏感。
        ("application/octet-stream", "Resume.PDF", "pdf"),
        # 声明 mime 优先于扩展名(识别层语义,M1-c 嗅探仲裁才反转)。
        ("image/png", "photo.gif", "png"),
        ("application/octet-stream", "clip.heic", "heif"),
        ("application/octet-stream", "thing.xyz", "unknown"),
        ("audio/unknown-sub", "no-extension", "unknown"),
    ],
)
def test_declared_resolved(mime: str, name: str, expected: str) -> None:
    assert declared_resolved(mime, name) == expected


def test_policy_table_covers_every_channel_and_caps_are_single_source() -> None:
    for channel in set(CHANNEL_BY_RESOLVED.values()):
        assert channel in ROUTE_POLICIES

    # 限额在识别层与 API(app.py)必须同源同值。
    assert ROUTE_POLICIES["multimodal"].max_bytes == MAX_IMAGE_BYTES
    assert MAX_IMAGE_BYTES == APP_MAX_IMAGE_BYTES
    assert ROUTE_POLICIES["multimodal"].injection == "image_content_block"
    assert ROUTE_POLICIES["none"].injection == "metadata_only"

    # M1-b/M1-c 限额:document 与 archive(zip 整包)同用 20MiB,audio 20MiB。
    assert ROUTE_POLICIES["document"].max_bytes == MAX_DOCUMENT_BYTES
    assert ROUTE_POLICIES["archive"].max_bytes == MAX_DOCUMENT_BYTES
    assert ROUTE_POLICIES["asr"].max_bytes == MAX_AUDIO_BYTES
    assert ROUTE_POLICIES["text"].max_bytes == 1024 * 1024


def test_executor_wiring_status_by_channel() -> None:
    # M1-c 现状:document/text 走轻量链;archive 走内置 zipfile 内存解包;
    # asr 走 transcribe_audio 工具(tool_call 注入,非请求期预处理);
    # multimodal 走既有 image_content_block(executor 字段留 None 是契约现状);
    # none 无执行器(元数据回落)。
    for channel in ("multimodal", "none"):
        assert ROUTE_POLICIES[channel].executor is None

    assert ROUTE_POLICIES["asr"].injection == "tool_call"
    assert "待接线" not in str(ROUTE_POLICIES["asr"].executor)
    assert "faster-whisper" in str(ROUTE_POLICIES["asr"].executor)
    assert "transcribe_audio" in str(ROUTE_POLICIES["asr"].executor)

    assert "待接线" not in str(ROUTE_POLICIES["document"].executor)
    assert "markitdown" in str(ROUTE_POLICIES["document"].executor)
    assert ROUTE_POLICIES["document"].injection == "preprocess_text_block"
    assert ROUTE_POLICIES["text"].injection == "preprocess_text_block"

    assert "zipfile" in str(ROUTE_POLICIES["archive"].executor)
    assert ROUTE_POLICIES["archive"].injection == "preprocess_text_block"


def test_parseable_resolved_gate() -> None:
    # file+data 接收闸口:只有这六个识别类型可携带 data
    # (M1-b 轻量链 + M1-c zip;ole2/图片/音频/unknown 仍拒)。
    from job_agent.api.attachment_types import PARSEABLE_RESOLVED

    assert PARSEABLE_RESOLVED == {"pdf", "docx", "xlsx", "pptx", "text", "zip"}

    # 每个可解析类型的通道限额必须来自策略表(不能是 none 的 0)。
    for resolved in PARSEABLE_RESOLVED:
        assert ROUTE_POLICIES[channel_of(resolved)].max_bytes > 0
