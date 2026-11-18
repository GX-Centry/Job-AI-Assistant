"""附件契约测试(M1 首个增量 + M1-a 识别 + M1-b 转译 + M1-c 语音/zip)。

- API 校验:图片必带 base64、解码尺寸/声明尺寸一致、≤5MiB;audio(M1-c)
  可带 data 但魔数必须是真实音频族(伪装 422)、size 与解码一致;file 的
  data 仅当魔数命中可解析通道(pdf/docx/xlsx/pptx/text/**zip**)才收,限额
  按策略表;mime 与 kind 一致;数量 ≤3;空文本+附件合法。
- build_user_message:图片进 image_url block;file+data 就地转译为带来源
  标记的文本 block(extract_status 入元数据,zip 走内存解包重路由);
  audio+data 存 audio_store 拿 audio_id(**内容绝不进模型**,转写走
  transcribe_audio 工具);无 data 的语音/不可解析文件只进元数据。
- graph 防呆:可转写语音(audio+audio_id)与"无法读取"附件分开提示,
  已转译=parsed 的排除;扫描全部人类消息。

不访问真实模型、不触碰 data/。
"""

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from job_agent.api import app as api_module
from job_agent.api import audio_store
from job_agent.api.app import (
    MAX_IMAGE_BYTES,
    Attachment,
    ChatRequest,
    ChatResponse,
    build_user_message,
    create_app,
)
from job_agent.graph import _unreadable_attachment_note

# M1-a 起,图片 data 必须是**真实图片字节**(魔数嗅探强校验),
# 这里用 PNG 签名(8 字节)作为最小合法图片。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# M1-c:audio data 的合法最小音频字节(WAV 头,RIFF+WAVEfmt)。
WAV_MAGIC = b"RIFF\x00\x00\x00\x00WAVEfmt "


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)

    return buffer.getvalue()


VALID_IMAGE = {
    "kind": "image",
    "name": "shot.png",
    "mime": "image/png",
    "size": len(PNG_MAGIC),
    "data": base64.b64encode(PNG_MAGIC).decode(),
}

VALID_AUDIO = {
    "kind": "audio",
    "name": "voice.wav",
    "mime": "audio/wav",
    "size": 1024,
}

VALID_FILE = {
    "kind": "file",
    "name": "resume.pdf",
    "mime": "application/pdf",
    "size": 2048,
}


@pytest.fixture(autouse=True)
def configure_api_keys(monkeypatch):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001"}',
    )


@pytest.fixture
def captured_handler():
    captured: dict = {}

    def handler(request, user_id):
        captured["request"] = request
        captured["user_id"] = user_id

        return ChatResponse(
            conversation_id=request.conversation_id,
            answer="ok",
        )

    handler.captured = captured
    return handler


@pytest.fixture
def client(captured_handler, monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    return TestClient(create_app(captured_handler))


@pytest.fixture
def conversation_id(client):
    response = client.post(
        "/sessions",
        json={"title": "attachments"},
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 201

    return response.json()["conversation_id"]


def post_chat(client, conversation_id, *, message="看图", attachments=None):
    body = {
        "message": message,
        "conversation_id": conversation_id,
    }

    if attachments is not None:
        body["attachments"] = attachments

    return client.post(
        "/chat",
        json=body,
        headers={"X-API-Key": "test-token"},
    )


# ==================================================
# API 校验
# ==================================================


def test_all_three_kinds_accepted_and_reach_handler(
    client, conversation_id, captured_handler
):
    response = post_chat(
        client,
        conversation_id,
        attachments=[VALID_IMAGE, VALID_AUDIO, VALID_FILE],
    )

    assert response.status_code == 200

    request = captured_handler.captured["request"]

    assert [attachment.kind for attachment in request.attachments] == [
        "image",
        "audio",
        "file",
    ]
    assert request.attachments[0].data == VALID_IMAGE["data"]
    assert request.attachments[1].data is None
    assert request.attachments[2].data is None


def test_pure_attachment_message_without_text_accepted(client, conversation_id):
    response = post_chat(
        client,
        conversation_id,
        message="",
        attachments=[VALID_AUDIO],
    )

    assert response.status_code == 200


def test_empty_message_without_attachments_rejected(client, conversation_id):
    response = post_chat(client, conversation_id, message="", attachments=[])

    assert response.status_code == 422


def test_image_without_data_rejected(client, conversation_id):
    broken = {k: v for k, v in VALID_IMAGE.items() if k != "data"}

    response = post_chat(client, conversation_id, attachments=[broken])

    assert response.status_code == 422


def test_image_invalid_base64_rejected(client, conversation_id):
    broken = {**VALID_IMAGE, "data": "not!!!base64!!!"}

    response = post_chat(client, conversation_id, attachments=[broken])

    assert response.status_code == 422


def test_image_oversize_rejected(client, conversation_id):
    big = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()

    response = post_chat(
        client,
        conversation_id,
        attachments=[{**VALID_IMAGE, "data": big, "size": MAX_IMAGE_BYTES + 1}],
    )

    assert response.status_code == 422


def test_image_declared_size_must_match_decoded(client, conversation_id):
    response = post_chat(
        client,
        conversation_id,
        attachments=[{**VALID_IMAGE, "size": 1}],
    )

    assert response.status_code == 422


def test_audio_data_must_be_real_audio_magic(client, conversation_id):
    # M1-c:audio data 被接收,但魔数必须是真实音频族 —— 伪装直接 422。
    cases = (
        {**VALID_AUDIO, "data": base64.b64encode(PNG_MAGIC).decode(), "size": 8},
        {**VALID_AUDIO, "data": base64.b64encode(b"plain text").decode()},
        # 声明 size 与解码字节不一致。
        {
            **VALID_AUDIO,
            "data": base64.b64encode(WAV_MAGIC).decode(),
            "size": 999,
        },
    )

    for attachment in cases:
        response = post_chat(client, conversation_id, attachments=[attachment])

        assert response.status_code == 422


def test_file_channel_rejects_audio_and_image_magic(client, conversation_id):
    # file 通道不收音频/图片字节(asr/multimodal 不在 file 闸口内)→ 422。
    cases = (
        {
            **VALID_FILE,
            "data": base64.b64encode(
                b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1f",
            ).decode(),
            "size": 12,
        },
        {**VALID_FILE, "data": base64.b64encode(PNG_MAGIC).decode(), "size": 8},
    )

    for attachment in cases:
        response = post_chat(client, conversation_id, attachments=[attachment])

        assert response.status_code == 422


def test_audio_with_real_audio_data_accepted(client, conversation_id, captured_handler):
    payload = WAV_MAGIC + b"\x00" * 16

    response = post_chat(
        client,
        conversation_id,
        attachments=[
            {
                **VALID_AUDIO,
                "size": len(payload),
                "data": base64.b64encode(payload).decode(),
            }
        ],
    )

    assert response.status_code == 200
    assert captured_handler.captured["request"].attachments[0].data


def test_file_with_zip_data_accepted(client, conversation_id, captured_handler):
    payload = zip_bytes({"readme.txt": b"zip channel M1-c"})

    response = post_chat(
        client,
        conversation_id,
        attachments=[
            {
                **VALID_FILE,
                "name": "pack.zip",
                "mime": "application/zip",
                "size": len(payload),
                "data": base64.b64encode(payload).decode(),
            }
        ],
    )

    assert response.status_code == 200
    assert captured_handler.captured["request"].attachments[0].data


def test_file_with_parseable_data_accepted(client, conversation_id, captured_handler):
    payload = "中文纯文本简历内容".encode()

    response = post_chat(
        client,
        conversation_id,
        attachments=[
            {
                **VALID_FILE,
                "name": "resume.txt",
                "mime": "text/plain",
                "size": len(payload),
                "data": base64.b64encode(payload).decode(),
            }
        ],
    )

    assert response.status_code == 200
    assert captured_handler.captured["request"].attachments[0].data


def test_file_data_size_cap(client, conversation_id):
    from job_agent.api.attachment_types import MAX_TEXT_BYTES

    payload = "啊".encode() * (MAX_TEXT_BYTES // 3 + 1)  # >1MiB 的 text 族

    response = post_chat(
        client,
        conversation_id,
        attachments=[
            {
                **VALID_FILE,
                "name": "huge.txt",
                "mime": "text/plain",
                "size": len(payload),
                "data": base64.b64encode(payload).decode(),
            }
        ],
    )

    assert response.status_code == 422


def test_mime_must_match_kind(client, conversation_id):
    swapped = [
        {**VALID_IMAGE, "mime": "audio/png"},
        {**VALID_AUDIO, "mime": "image/wav"},
    ]

    for attachment in swapped:
        response = post_chat(client, conversation_id, attachments=[attachment])

        assert response.status_code == 422


def test_attachment_count_bounded(client, conversation_id):
    four = [{**VALID_AUDIO, "name": f"v{i}.wav"} for i in range(4)]

    response = post_chat(client, conversation_id, attachments=four)

    assert response.status_code == 422


def test_blank_name_rejected(client, conversation_id):
    response = post_chat(
        client,
        conversation_id,
        attachments=[{**VALID_AUDIO, "name": "   "}],
    )

    assert response.status_code == 422


def test_image_with_non_image_magic_rejected(client, conversation_id):
    impostors = (
        # webm 容器伪装 PNG 扩展名与 mime。
        {
            **VALID_IMAGE,
            "data": base64.b64encode(
                b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1f",
            ).decode(),
            "size": 12,
        },
        # 纯文本伪装 PNG。
        {
            **VALID_IMAGE,
            "data": base64.b64encode(b"This is definitely not an image.").decode(),
            "size": 32,
        },
    )

    for attachment in impostors:
        response = post_chat(client, conversation_id, attachments=[attachment])

        assert response.status_code == 422
        assert "magic bytes" in response.text


def test_image_with_heic_ftyp_accepted_as_image(client, conversation_id):
    # HEIC 与 m4a 同为 ftyp 容器:品牌字段细分后必须仍走图片通道。
    heic = {
        **VALID_IMAGE,
        "name": "photo.heic",
        "mime": "image/heic",
        "data": base64.b64encode(
            b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00",
        ).decode(),
        "size": 16,
    }

    response = post_chat(client, conversation_id, attachments=[heic])

    assert response.status_code == 200


# ==================================================
# build_user_message
# ==================================================


def request_for(message, attachments=()):
    return ChatRequest(
        message=message,
        conversation_id="conv-1",
        attachments=list(attachments),
    )


def build(request):
    # M1-c 起 build_user_message 需要 user/conversation 上下文
    # (语音 blob 挂会话生命周期)。
    return build_user_message(request, "user-001", "conv-1")


def test_plain_text_message_unchanged():
    message = build(request_for("hello"))

    assert isinstance(message, HumanMessage)
    assert message.content == "hello"
    assert "attachments" not in message.additional_kwargs


def test_image_becomes_content_block_others_metadata_only():
    message = build(
        request_for(
            "看图",
            [
                Attachment.model_validate(VALID_IMAGE),
                Attachment.model_validate(VALID_AUDIO),
                Attachment.model_validate(VALID_FILE),
            ],
        ),
    )

    blocks = message.content

    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": "看图"}
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"] == (
        f"data:image/png;base64,{VALID_IMAGE['data']}"
    )
    assert len(blocks) == 2

    metadata = message.additional_kwargs["attachments"]

    assert [item["kind"] for item in metadata] == ["image", "audio", "file"]
    assert all("data" not in item for item in metadata)


def test_attachment_only_message_gets_placeholder_text():
    message = build(
        request_for("", [Attachment.model_validate(VALID_AUDIO)]),
    )

    assert message.content[0]["type"] == "text"
    assert message.content[0]["text"]
    assert message.additional_kwargs["attachments"][0]["kind"] == "audio"


# ==================================================
# M1-b:file+data 就地转译(策略 A,字节不持久)
# ==================================================


def mini_docx_bytes(
    body: str = "<w:p><w:r><w:t>你好 M1B 文档通道</w:t></w:r></w:p>",
) -> bytes:
    import io
    import zipfile

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
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            "<w:document "
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>",
        )

    return buffer.getvalue()


def file_attachment(name: str, payload: bytes) -> Attachment:
    return Attachment.model_validate(
        {
            "kind": "file",
            "name": name,
            "mime": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            "size": len(payload),
            "data": base64.b64encode(payload).decode(),
        },
    )


def test_file_with_docx_data_becomes_parsed_text_block():
    payload = mini_docx_bytes()

    message = build(request_for("", [file_attachment("简历.docx", payload)]))

    blocks = message.content

    assert isinstance(blocks, list)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    # 来源标记块:转译文本必须自带出处(DESIGN §6 红线)。
    assert blocks[0]["text"].startswith("[文档解析自 简历.docx")
    assert "你好 M1B 文档通道" in blocks[0]["text"]

    entry = message.additional_kwargs["attachments"][0]

    assert entry["extract_status"] == "parsed"
    assert "data" not in entry


def test_empty_document_parse_failure_marks_failed_and_keeps_note():
    # 嗅探为 docx 但抽不出文本 → 显式失败态:不出 block、元数据 failed、
    # 防呆提示保留 —— 绝不假装"已解析"。
    payload = mini_docx_bytes(body="")

    message = build(request_for("看看", [file_attachment("empty.docx", payload)]))

    assert message.content == [{"type": "text", "text": "看看"}]
    assert message.additional_kwargs["attachments"][0]["extract_status"] == "failed"

    note = _unreadable_attachment_note([message])

    assert note is not None
    assert "empty.docx" in note
    assert "无法读取" in note


def test_note_excludes_parsed_document_attachments():
    parsed = HumanMessage(
        content="看下简历",
        additional_kwargs={
            "attachments": [
                {
                    "kind": "file",
                    "name": "ok.docx",
                    "mime": "application/x",
                    "size": 10,
                    "extract_status": "parsed",
                },
                {"kind": "audio", "name": "voice.wav", "mime": "audio/wav", "size": 30},
            ]
        },
    )

    note = _unreadable_attachment_note([parsed])

    assert note is not None
    assert "voice.wav" in note
    assert "ok.docx" not in note


# ==================================================
# M1-c:语音 data 持久化(audio_id)与 zip 内存解包
# ==================================================


def audio_attachment(payload: bytes) -> Attachment:
    return Attachment.model_validate(
        {
            "kind": "audio",
            "name": "voice.wav",
            "mime": "audio/wav",
            "size": len(payload),
            "data": base64.b64encode(payload).decode(),
        },
    )


def test_audio_data_persisted_gets_audio_id_content_not_in_model():
    payload = WAV_MAGIC + b"\x00" * 32

    message = build(request_for("听一下", [audio_attachment(payload)]))

    entry = message.additional_kwargs["attachments"][0]

    assert entry["audio_id"]
    assert "data" not in entry

    record = audio_store.get_audio("user-001", entry["audio_id"])

    assert record is not None
    assert record["payload"] == payload
    assert record["conversation_id"] == "conv-1"

    # 内容绝不进模型:文本 block 里不许出现 base64 语音字节。
    texts = [block["text"] for block in message.content if block.get("type") == "text"]

    assert all("base64" not in text for text in texts)

    note = _unreadable_attachment_note([message])

    assert note is not None
    assert f"audio_id={entry['audio_id']}" in note
    assert "transcribe_audio" in note


def test_audio_without_data_stays_metadata_only():
    message = build(request_for("", [Attachment.model_validate(VALID_AUDIO)]))

    entry = message.additional_kwargs["attachments"][0]

    assert "audio_id" not in entry

    note = _unreadable_attachment_note([message])

    assert "无法读取" in note
    assert "transcribe_audio" not in note


def test_zip_file_becomes_parsed_archive_block():
    payload = zip_bytes(
        {
            "简历.docx": mini_docx_bytes(),
            "note.txt": "附注:期望薪资面议".encode(),
        },
    )

    message = build(request_for("看看压缩包", [file_attachment("pack.zip", payload)]))

    blocks = message.content

    assert blocks[0] == {"type": "text", "text": "看看压缩包"}

    text = blocks[1]["text"]

    assert text.startswith("[解包自 pack.zip")
    assert "【成员 简历.docx】" in text
    assert "你好 M1B 文档通道" in text
    assert "附注:期望薪资面议" in text

    entry = message.additional_kwargs["attachments"][0]

    assert entry["extract_status"] == "parsed"
    assert "data" not in entry


def test_zip_skips_nested_and_unsupported_members_but_lists_them():
    payload = zip_bytes(
        {
            "inner.zip": zip_bytes({"x.txt": b"inner"}),
            "photo.png": PNG_MAGIC,
            "ok.txt": "可转译成员".encode(),
        },
    )

    message = build(request_for("", [file_attachment("mixed.zip", payload)]))

    text = message.content[0]["text"]

    assert "可转译成员" in text
    # 被跳过的成员必须在标记里点名 —— 绝不静默消失。
    assert "未转译条目 2 个" in text
    assert "inner.zip" in text
    assert "photo.png" in text
    assert message.additional_kwargs["attachments"][0]["extract_status"] == "parsed"


def test_zip_all_members_skipped_marks_failed():
    # 注意:成员必须真正不可转译 —— 短小二进制若恰好是合法 UTF-8 会被
    # 嗅探成 text(text 嗅探按内容解码判定),这里用真实魔数挡路。
    payload = zip_bytes(
        {
            "photo.png": PNG_MAGIC,
            "movie.webm": b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1f",
        },
    )

    message = build(request_for("看看", [file_attachment("imgs.zip", payload)]))

    assert message.content == [{"type": "text", "text": "看看"}]
    assert message.additional_kwargs["attachments"][0]["extract_status"] == "failed"

    note = _unreadable_attachment_note([message])

    assert note is not None
    assert "imgs.zip" in note


# ==================================================
# graph 防呆提示
# ==================================================


def test_note_none_without_unreadable_attachments():
    plain = HumanMessage(content="hi")

    image_only = HumanMessage(
        content="hi",
        additional_kwargs={
            "attachments": [
                {"kind": "image", "name": "a.png", "mime": "image/png", "size": 1}
            ]
        },
    )

    assert (
        _unreadable_attachment_note([plain, image_only, AIMessage(content="ok")])
        is None
    )


def test_note_lists_unreadable_attachments_from_any_turn():
    old = HumanMessage(
        content="早先传过录音",
        additional_kwargs={
            "attachments": [
                {"kind": "audio", "name": "old.wav", "mime": "audio/wav", "size": 10},
            ]
        },
    )

    note = _unreadable_attachment_note(
        [old, AIMessage(content="好"), HumanMessage(content="你听到刚才的录音了吗?")]
    )

    assert note is not None
    assert "old.wav" in note
    assert "无法读取" in note


def test_note_offers_transcribe_tool_for_audio_with_id():
    transcribable = HumanMessage(
        content="听这段",
        additional_kwargs={
            "attachments": [
                {
                    "kind": "audio",
                    "name": "voice.webm",
                    "mime": "audio/webm",
                    "size": 4096,
                    "audio_id": "a1b2c3",
                },
            ]
        },
    )

    note = _unreadable_attachment_note([transcribable])

    assert note is not None
    assert "voice.webm(audio_id=a1b2c3)" in note
    assert "transcribe_audio" in note
    # 可转写≠不可读:不能出现"无法读取"措辞,也不许模型声称听过。
    assert "无法读取" not in note
    assert "绝不声称听过" in note


def test_note_separates_transcribable_from_unreadable():
    mixed = HumanMessage(
        content="混合附件",
        additional_kwargs={
            "attachments": [
                {
                    "kind": "audio",
                    "name": "new.webm",
                    "mime": "audio/webm",
                    "size": 10,
                    "audio_id": "id-1",
                },
                {"kind": "audio", "name": "old.wav", "mime": "audio/wav", "size": 10},
                {
                    "kind": "file",
                    "name": "bad.docx",
                    "mime": "application/x",
                    "size": 5,
                    "extract_status": "failed",
                },
            ]
        },
    )

    note = _unreadable_attachment_note([mixed])

    assert "new.webm(audio_id=id-1)" in note
    assert "transcribe_audio" in note
    assert "old.wav" in note
    assert "bad.docx" in note
    assert "无法读取" in note
