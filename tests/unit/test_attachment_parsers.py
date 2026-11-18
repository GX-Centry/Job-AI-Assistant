"""M1-b 执行器链单元测试:现场构造的真实最小文件离线解析、
显式失败态、解码回退与截断标记(全部无网络、无模型)。

文件夹具与 eval-env 探针(experiments/tool-research/probe_markitdown_m1b.py)
同源:手工构造的最小 PDF/docx + openpyxl/python-pptx 现场生成。
"""

import io
import zipfile

import pytest

from job_agent.api.attachment_parsers import (
    ARCHIVE_MAX_COMPRESSION_RATIO,
    ARCHIVE_MAX_ENTRIES,
    ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES,
    MAX_EXTRACTED_CHARS,
    ParseFailure,
    decode_text_bytes,
    parse_archive,
    parse_attachment,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

MINI_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 400]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 42>>stream\n"
    b"BT /F1 24 Tf 40 300 Td (Hello M1B) Tj ET\n"
    b"endstream\nendobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
)


def mini_docx_bytes() -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels"
 ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType=
"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns=
"http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type=
"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="word/document.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>你好 M1B 文档通道</w:t></w:r></w:p></w:body>
</w:document>""",
        )

    return buffer.getvalue()


def mini_xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "岗位"
    sheet["B1"] = "产品经理"

    buffer = io.BytesIO()
    workbook.save(buffer)

    return buffer.getvalue()


def mini_pptx_bytes() -> bytes:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "M1B 标题"
    slide.placeholders[1].text_frame.text = "M1B 正文"

    buffer = io.BytesIO()
    presentation.save(buffer)

    return buffer.getvalue()


def test_pdf_extracted() -> None:
    outcome = parse_attachment(MINI_PDF, "pdf")

    assert not outcome.truncated
    assert "Hello M1B" in outcome.text


def test_docx_extracted() -> None:
    outcome = parse_attachment(mini_docx_bytes(), "docx")

    assert not outcome.truncated
    assert "你好 M1B 文档通道" in outcome.text


def test_xlsx_markdown_table_cells() -> None:
    outcome = parse_attachment(mini_xlsx_bytes(), "xlsx")

    assert "岗位" in outcome.text
    assert "产品经理" in outcome.text


def test_pptx_slide_text() -> None:
    outcome = parse_attachment(mini_pptx_bytes(), "pptx")

    assert "M1B 标题" in outcome.text
    assert "M1B 正文" in outcome.text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("中文纯文本内容".encode(), "中文纯文本内容"),
        ("带 BOM 的文本".encode("utf-8-sig"), "带 BOM 的文本"),
        ("中文旧编码回退 gbk".encode("gbk"), "中文旧编码回退 gbk"),
        ("utf-16 BOM 文本".encode("utf-16"), "utf-16 BOM 文本"),
    ],
)
def test_text_decode_chain(payload: bytes, expected: str) -> None:
    outcome = parse_attachment(payload, "text")

    assert outcome.text == expected
    assert not outcome.truncated


def test_text_undecodable_is_explicit_failure() -> None:
    # 0x80 不是 gbk 引导字节;无 BOM 禁走裸 utf-16 —— 必须显式失败,
    # 绝不静默替换出乱码冒充可读文本。
    with pytest.raises(ParseFailure):
        decode_text_bytes(b"ABC\x80\x80\x80\x80EX")


def test_truncation_is_marked() -> None:
    payload = ("啊" * (MAX_EXTRACTED_CHARS + 100)).encode()

    outcome = parse_attachment(payload, "text")

    assert outcome.truncated
    assert len(outcome.text) == MAX_EXTRACTED_CHARS


def test_empty_document_is_failure_not_empty_success() -> None:
    # 嗅探可解析但抽不出任何文本(空 docx)→ 显式失败,不进"已解析"。
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body/></w:document>",
        )
        archive.writestr("[Content_Types].xml", "<Types/>")

    with pytest.raises(ParseFailure):
        parse_attachment(buffer.getvalue(), "docx")


@pytest.mark.parametrize("resolved", ["ole2", "zip", "png", "webm", "unknown"])
def test_unwired_resolved_types_rejected(resolved: str) -> None:
    with pytest.raises(ParseFailure):
        parse_attachment(b"whatever bytes here", resolved)


# ==================================================
# M1-c:archive(zip)执行器与护栏(内存解包,永不落盘)
# ==================================================


def zip_container(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)

    return buffer.getvalue()


def test_archive_members_rerouted_by_magic_with_skipped_listing() -> None:
    payload = zip_container(
        {
            "doc.docx": mini_docx_bytes(),
            "note.txt": "成员文本内容".encode(),
            "photo.png": PNG_MAGIC,
        },
    )

    outcome = parse_archive(payload)

    assert not outcome.truncated
    assert outcome.members_parsed == 2
    assert "【成员 doc.docx】" in outcome.text
    assert "你好 M1B 文档通道" in outcome.text
    assert "成员文本内容" in outcome.text
    # 图片成员在 archive 通道没有执行器:跳过必须点名,绝不猜测内容。
    assert len(outcome.members_skipped) == 1
    assert "photo.png" in outcome.members_skipped[0]


def test_archive_skips_nested_archives() -> None:
    payload = zip_container(
        {
            "inner.zip": zip_container({"x.txt": b"nested"}),
            "ok.txt": "外层可转译".encode(),
        },
    )

    outcome = parse_archive(payload)

    assert "外层可转译" in outcome.text
    assert any(
        "inner.zip" in item and "嵌套" in item for item in outcome.members_skipped
    )


def test_archive_with_only_unsupported_members_is_explicit_failure() -> None:
    payload = zip_container({"photo.png": PNG_MAGIC, "movie.webm": b"\x1a\x45\xdf\xa3"})

    with pytest.raises(ParseFailure) as exc:
        parse_archive(payload)

    # 失败详情必须点名被跳过成员(可诊断性)。
    assert "photo.png" in str(exc.value)
    assert "movie.webm" in str(exc.value)


def test_corrupt_archive_is_explicit_failure() -> None:
    with pytest.raises(ParseFailure):
        parse_archive(b"this is not a zip file at all")


def test_archive_guard_limits_match_approved_policy() -> None:
    # 2026-11-18 用户拍板预设:条目 ≤200、解压总量 ≤200MiB、压缩比 ≤200。
    assert ARCHIVE_MAX_ENTRIES == 200
    assert ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES == 200 * 1024 * 1024


def test_archive_entry_count_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = zip_container({"a.txt": b"a", "b.txt": b"b"})

    monkeypatch.setattr(
        "job_agent.api.attachment_parsers.ARCHIVE_MAX_ENTRIES",
        1,
    )

    with pytest.raises(ParseFailure, match="entries"):
        parse_archive(payload)


def test_archive_total_uncompressed_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = zip_container({"a.txt": b"x" * 500, "b.txt": b"y" * 500})

    monkeypatch.setattr(
        "job_agent.api.attachment_parsers.ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES",
        100,
    )

    with pytest.raises(ParseFailure, match="uncompressed total"):
        parse_archive(payload)


def test_archive_bomb_member_skipped_by_ratio() -> None:
    # 全零成员:deflate 后极小,压缩比远超上限 —— 单条跳过但必须点名。
    payload = zip_container(
        {
            "zeros.bin": b"\x00" * 200_000,
            "ok.txt": "唯一可转译成员".encode(),
        },
    )

    outcome = parse_archive(payload)

    assert "唯一可转译成员" in outcome.text
    assert any(
        "zeros.bin" in item and "压缩比" in item for item in outcome.members_skipped
    )
    assert ARCHIVE_MAX_COMPRESSION_RATIO == 200


def test_archive_shared_char_budget_truncates_and_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "job_agent.api.attachment_parsers.MAX_EXTRACTED_CHARS",
        10,
    )

    payload = zip_container(
        {
            "a.txt": b"A" * 40,
            "b.txt": b"B" * 40,
        },
    )

    outcome = parse_archive(payload)

    assert outcome.truncated
    # 预算耗尽即停:第二个成员不再解包注入。
    assert outcome.members_parsed == 1
    assert "B" * 40 not in outcome.text
    assert MAX_EXTRACTED_CHARS == 30000


def test_archive_member_over_channel_cap_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # text 族成员超 text 限额 → 跳过点名,不硬解。限额来自策略表
    # (frozen dataclass 构造时固化),所以要替换表项而不是改常量。
    from job_agent.api import attachment_types

    monkeypatch.setitem(
        attachment_types.ROUTE_POLICIES,
        "text",
        attachment_types.RoutePolicy("text", None, 8, "preprocess_text_block"),
    )

    payload = zip_container(
        {
            "big.txt": b"x" * 40,
            "ok.txt": b"small",
        },
    )

    outcome = parse_archive(payload)

    assert "small" in outcome.text
    assert any("big.txt" in item and "限额" in item for item in outcome.members_skipped)
