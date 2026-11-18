"""M1-a 第一层:附件内容类型识别(魔数嗅探)+ 路由策略表。

纯函数模块,无 IO、无第三方依赖:

- ``sniff_resolved``:由**内容字节**判定类型(权威信号,高于声明 mime/扩展名);
- ``declared_resolved``:无 data(仅元数据)附件的声明 mime/扩展名推断;
- ``channel_of`` + ``ROUTE_POLICIES``:识别类型 -> 通道 -> 执行器/限额/注入策略。

M1-b/M1-c 状态:multimodal(图片进模型)、**document/text(轻量链
markitdown + 内置解码,见 attachment_parsers.py)**、**archive(zip 内存解包
重路由,永不落盘,限额护栏见 attachment_parsers.ARCHIVE_*)**均走请求期
预处理;file 附件 data 仅当魔数命中 ``PARSEABLE_RESOLVED``(含 zip)时被接收
(可转译的解析即用;zip 成员字节不持久)。**asr 通道 M1-c 已接
faster-whisper(small/int8,服务端本机转写)**:audio data 被接收后字节持久化
在 ``audio_store``(挂会话生命周期),转写由模型按需调用 ``transcribe_audio``
工具完成 —— 注入方式是 tool_call 而非预处理文本块。ole2(老式 Office)/
图片伪装/音频伪装进 file 通道/unknown 仍拒 data(无消费方不预收字节)。

本表与 ``frontend/src/utils/attachments.js`` 的 JS 镜像表必须同源维护,
两侧各自有测试钉死条目内容;跨端一致性靠限额常量对齐测试保证。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from zipfile import BadZipFile, ZipFile

# 限额单一出处(app.py 引用;前端 JS 镜像表由前端测试钉死同值)。
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # M1-b 启用时生效
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # M1-c 启用时生效
MAX_TEXT_BYTES = 1024 * 1024  # M1-b 启用时生效

IMAGE_TYPES = frozenset(
    {"png", "jpeg", "gif", "webp", "bmp", "tiff", "ico", "heif"},
)

# HEIC/HEIF 同样是 ISO-BMFF(ftyp)容器 —— 不细分品牌会被误判成 m4a 音频。
_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"heim", b"heif", b"mif1", b"msf1", b"hevx"},
)
AUDIO_TYPES = frozenset({"webm", "ogg", "wav", "mp3", "m4a", "aac", "flac"})
DOCUMENT_TYPES = frozenset({"pdf", "docx", "xlsx", "pptx", "doc", "xls", "ppt"})

# 已接执行器的识别类型:file 附件 data 只在这些类型下被接收(上游校验层的
# 闸口)。M1-c 起 zip 入列:archive 执行器内存解包重路由,永不落盘;老式
# Office(ole2)/图片/音频/未知在 file 通道仍拒 data(无消费方不预收)。
PARSEABLE_RESOLVED = frozenset({"pdf", "docx", "xlsx", "pptx", "text", "zip"})

CHANNEL_BY_RESOLVED: dict[str, str] = {
    **dict.fromkeys(IMAGE_TYPES, "multimodal"),
    **dict.fromkeys(AUDIO_TYPES, "asr"),
    **dict.fromkeys(DOCUMENT_TYPES, "document"),
    "text": "text",
    "ole2": "document",
    "zip": "archive",
    "unknown": "none",
}


@dataclass(frozen=True)
class RoutePolicy:
    channel: str
    executor: str | None  # None = 识别/路由已就位,执行器未接线。
    max_bytes: int
    injection: (
        str  # image_content_block | metadata_only | tool_call | preprocess_text_block
    )


ROUTE_POLICIES: dict[str, RoutePolicy] = {
    "multimodal": RoutePolicy(
        "multimodal",
        None,
        MAX_IMAGE_BYTES,
        "image_content_block",
    ),
    # M1-c:audio+data 持久化在 audio_store(挂会话生命周期),转写由模型
    # 按需调用 transcribe_audio 工具(faster-whisper small/int8,本机执行)。
    "asr": RoutePolicy(
        "asr",
        "faster-whisper(small/int8,transcribe_audio 工具按需转写)",
        MAX_AUDIO_BYTES,
        "tool_call",
    ),
    "document": RoutePolicy(
        "document",
        "markitdown[pdf,docx,pptx,xlsx]",
        MAX_DOCUMENT_BYTES,
        "preprocess_text_block",
    ),
    "text": RoutePolicy(
        "text",
        "内置解码(utf-8/utf-16/gbk)",
        MAX_TEXT_BYTES,
        "preprocess_text_block",
    ),
    # M1-c zip 政策(用户拍板预设):内存解包永不落盘(无 zip-slip 面),
    # 条目/总量/压缩比护栏与成员重路由见 attachment_parsers.parse_archive。
    "archive": RoutePolicy(
        "archive",
        "内置 zipfile(内存解包,不落盘,限额护栏见 parse_archive)",
        MAX_DOCUMENT_BYTES,
        "preprocess_text_block",
    ),
    "none": RoutePolicy("none", None, 0, "metadata_only"),
}


def channel_of(resolved: str) -> str:
    return CHANNEL_BY_RESOLVED.get(resolved, "none")


def sniff_resolved(data: bytes) -> str:
    """按内容字节判定类型;判不出的返回 ``unknown``(回落元数据通道)。"""

    head = data[:16]

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"

    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"

    if head[:4] == b"RIFF":
        form_type = data[8:12]
        if form_type == b"WEBP":
            return "webp"
        if form_type == b"WAVE":
            return "wav"
        return "riff"

    if head.startswith(b"BM"):
        return "bmp"

    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"

    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"

    if head[:4] == b"\x1a\x45\xdf\xa3":  # EBML:webm/mkv
        return "webm"

    if head[:4] == b"OggS":
        return "ogg"

    if head[:3] == b"ID3":
        return "mp3"

    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"  # MPEG 帧同步(无 ID3 标签)

    if len(data) >= 12 and data[4:8] == b"ftyp":
        if data[8:12] in _HEIF_BRANDS:
            return "heif"
        return "m4a"

    if head.startswith(b"%PDF"):
        return "pdf"

    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _zip_resolved(data)

    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole2"

    return "text" if _looks_like_text(data) else "unknown"


def declared_resolved(mime: str, name: str) -> str:
    """无 data 附件的降级推断:声明 mime 优先,扩展名次之。"""

    lowered = mime.lower().strip()

    resolved = _MIME_TO_RESOLVED.get(lowered)
    if resolved:
        return resolved

    if lowered.startswith("text/"):
        return "text"

    suffix = f".{name.rsplit('.', 1)[-1].lower()}" if "." in name else ""

    return _EXT_TO_RESOLVED.get(suffix, "unknown")


def _zip_resolved(data: bytes) -> str:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except (BadZipFile, NotImplementedError, OSError):
        return "zip"

    if any(entry.startswith("word/") for entry in names):
        return "docx"

    if any(entry.startswith("xl/") for entry in names):
        return "xlsx"

    if any(entry.startswith("ppt/") for entry in names):
        return "pptx"

    return "zip"


def _looks_like_text(data: bytes) -> bool:
    probe = data[:512]

    if not probe:
        return False

    if probe.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True

    # 探针可能切在多字节字符中间:回退 1~3 字节再试,避免长中文文本
    # 被误判为 unknown(与前端 JS 镜像同法)。
    for trim in range(4):
        if trim >= len(probe):
            break

        candidate = probe[:-trim] if trim else probe

        try:
            candidate.decode("utf-8")

            return True
        except UnicodeDecodeError:
            continue

    return False


_MIME_TO_RESOLVED = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/x-icon": "ico",
    "image/heic": "heif",
    "image/heif": "heif",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/flac": "flac",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}

_EXT_TO_RESOLVED = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".ico": "ico",
    ".heic": "heif",
    ".heif": "heif",
    ".webm": "webm",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".wav": "wav",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".aac": "aac",
    ".flac": "flac",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".doc": "doc",
    ".xls": "xls",
    ".ppt": "ppt",
    ".zip": "zip",
    ".md": "text",
    ".txt": "text",
    ".csv": "text",
    ".html": "text",
    ".htm": "text",
}
