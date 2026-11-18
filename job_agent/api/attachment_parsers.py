"""M1-b 第二层:文档/文本转译执行器链(纯离线:无网络、无模型、不落盘)。

轻量链选型(2026-11-18 定,见 experiments/tool-research/DESIGN §3):

- ``text``:内置解码链 —— UTF-16(BOM)→ UTF-8(-sig)→ GBK,全不中即显式失败;
- ``pdf/docx/xlsx/pptx``:markitdown 纯 Python 转换链(API 与离线行为由
  eval-env 探针钉死:experiments/tool-research/probe_markitdown_m1b.py);
- 其余识别类型(ole2/zip/图片/音频/unknown)不在 ``PARSEABLE_RESOLVED``,
  上游校验层已拒 data,这里再兜底拒绝。

红线(DESIGN-routing-layer.md §6):

- 失败态必须显式:解码失败/转换异常/抽出为空 → ``ParseFailure``,
  绝不用"部分文本"冒充全文;
- 截断必须标记:超过 ``MAX_EXTRACTED_CHARS`` 截断并置 ``truncated``,
  调用方负责把标记写进来源标记块;
- 产物不含来源标记本身 —— 标记由消息构造层(build_user_message)统一拼接。

已知宽容度:pdfminer 对损坏但 ``%PDF`` 开头的文件会宽容产出乱码文本而非
抛错(实测)—— 这类内容会带"转译可能有格式损失"标记进消息,不伪装精确。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from zipfile import BadZipFile, ZipFile

from markitdown import MarkItDown

from job_agent.api.attachment_types import (
    MAX_DOCUMENT_BYTES,
    ROUTE_POLICIES,
    channel_of,
    sniff_resolved,
)

# 注入消息的转译文本字符上限(上下文窗口保护;超限截断并显式标记)。
MAX_EXTRACTED_CHARS = 30000

# M1-c zip 护栏(2026-11-18 用户拍板预设):内存解包**永不落盘**(成员只进
# 内存,天然没有 zip-slip 面),以下护栏把 zip bomb/恶意条目挡在转译之前。
ARCHIVE_MAX_ENTRIES = 200
ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
ARCHIVE_MAX_COMPRESSION_RATIO = 200

_EXT_BY_RESOLVED = {
    "pdf": ".pdf",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "pptx": ".pptx",
}


class ParseFailure(ValueError):
    """显式失败态:无法抽出可解码文本,或该类型没有已接线执行器。"""


@dataclass(frozen=True)
class ParseOutcome:
    text: str
    truncated: bool


def decode_text_bytes(payload: bytes) -> str:
    """text 族解码链;全部失败 → ParseFailure(不是静默替换)。"""

    if payload[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return payload.decode("utf-16")

    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    try:
        return payload.decode("gbk")
    except UnicodeDecodeError as exc:
        raise ParseFailure(
            "text payload is not decodable as utf-8/utf-16/gbk",
        ) from exc


def parse_attachment(payload: bytes, resolved: str) -> ParseOutcome:
    """把已嗅探为 PARSEABLE 的字节转成文本;失败一律 ParseFailure。"""

    if resolved == "text":
        text = decode_text_bytes(payload)
    elif resolved in _EXT_BY_RESOLVED:
        try:
            converted = MarkItDown(show_errors=False).convert(
                io.BytesIO(payload),
                file_extension=_EXT_BY_RESOLVED[resolved],
            )
        except Exception as exc:  # markitdown 异常族嘈杂,统一收敛为显式失败态。
            raise ParseFailure(
                f"document conversion failed ({type(exc).__name__})",
            ) from exc
        text = converted.markdown or ""
    else:
        raise ParseFailure(
            f"resolved type {resolved!r} has no wired executor",
        )

    text = text.strip()

    if not text:
        raise ParseFailure("no text content extracted")

    truncated = len(text) > MAX_EXTRACTED_CHARS

    if truncated:
        text = text[:MAX_EXTRACTED_CHARS]

    return ParseOutcome(text=text, truncated=truncated)


# ==================================================
# M1-c:archive(zip)执行器
# ==================================================


@dataclass(frozen=True)
class ArchiveOutcome:
    text: str
    truncated: bool
    members_parsed: int
    members_skipped: list[str] = field(default_factory=list)


def parse_archive(payload: bytes) -> ArchiveOutcome:
    """把嗅探为 zip 的字节在**内存中**解包,成员按同一识别/转译链重路由。

    护栏(拍板预设):条目 > ``ARCHIVE_MAX_ENTRIES`` 或解压总量 >
    ``ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES`` 直接整体失败(bomb 特征不逐条
    陪跑);单条压缩比超 ``ARCHIVE_MAX_COMPRESSION_RATIO`` 只跳过该条并记名。
    加密条目、嵌套压缩包/ole2、非 PARSEABLE 族、超同族限额的条目一律跳过并
    记名(绝不猜测内容)。无任何成员可转译 → ParseFailure(显式失败态)。
    成员字节与解压产物只存在于本次调用内存,不落盘、不持久化。
    """

    try:
        archive = ZipFile(io.BytesIO(payload))
    except (BadZipFile, OSError) as exc:
        raise ParseFailure(f"archive unreadable ({type(exc).__name__})") from exc

    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]

        if len(entries) > ARCHIVE_MAX_ENTRIES:
            raise ParseFailure(
                f"archive has more than {ARCHIVE_MAX_ENTRIES} entries",
            )

        total_uncompressed = sum(info.file_size for info in entries)

        if total_uncompressed > ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ParseFailure(
                "archive uncompressed total exceeds "
                f"{ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES} bytes",
            )

        parts: list[str] = []
        skipped: list[str] = []
        budget = MAX_EXTRACTED_CHARS
        truncated = False

        for info in entries:
            name = info.filename

            if info.flag_bits & 0x1:
                skipped.append(f"{name}(加密条目,已跳过)")
                continue

            if info.file_size > MAX_DOCUMENT_BYTES:
                skipped.append(f"{name}(超过 {MAX_DOCUMENT_BYTES} 字节,已跳过)")
                continue

            ratio = info.file_size / max(info.compress_size, 1)

            if ratio > ARCHIVE_MAX_COMPRESSION_RATIO:
                skipped.append(f"{name}(压缩比 {ratio:.0f} 超限,已跳过)")
                continue

            try:
                member = archive.read(info)
            except (BadZipFile, RuntimeError, OSError):
                skipped.append(f"{name}(读取失败,已跳过)")
                continue

            resolved = sniff_resolved(member)

            if resolved in ("zip", "ole2"):
                skipped.append(f"{name}(嵌套压缩包/老式格式,未处理)")
                continue

            if channel_of(resolved) not in ("document", "text"):
                skipped.append(f"{name}({resolved},不可转译)")
                continue

            member_limit = ROUTE_POLICIES[channel_of(resolved)].max_bytes

            if len(member) > member_limit:
                skipped.append(f"{name}(超过 {member_limit} 字节限额,已跳过)")
                continue

            try:
                outcome = parse_attachment(member, resolved)
            except ParseFailure:
                skipped.append(f"{name}(转译失败或无文本)")
                continue

            member_note = "(成员文本超限已截断)" if outcome.truncated else ""

            section = f"【成员 {name}{member_note}】\n{outcome.text}"

            if len(section) > budget:
                # 预算耗尽:截断当前成员并如实标记(后续成员不再解包 ——
                # 上下文预算已到顶,继续解包只会膨胀内存而无注入价值)。
                parts.append(section[: max(budget, 0)])
                truncated = True
                break

            parts.append(section)
            budget -= len(section)

        if not parts:
            detail = ";".join(skipped) if skipped else "无成员"

            raise ParseFailure(f"archive has no transcribable member: {detail}")

        return ArchiveOutcome(
            text="\n\n".join(parts),
            truncated=truncated,
            members_parsed=len(parts),
            members_skipped=skipped,
        )
