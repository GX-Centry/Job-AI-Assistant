"""M1-c:语音附件的内容存储(blob store)。

与 session_store 同一手法:模块级默认路径 + 每次调用短连接(conftest 把
``DB_PATH`` 重定向到临时目录)。

契约(M1-c 拍板):

- 语音附件通过上游校验(魔数=音频族、≤20MiB、size 与解码一致)后,字节
  在这里持久化,``audio_id`` 进消息元数据与历史;转译由模型按需调用
  ``transcribe_audio`` 工具完成(工具按 user_id + audio_id 取回字节);
- 生命周期**挂在会话上**:`DELETE /sessions/{id}` 同时删除该会话全部语音
  blob(与 checkpoint 线程同批清理);不引入独立 TTL —— 会话行还在,blob
  就有意义;
- 只存语音族内容(上游闸门保证);图片/文档字节绝不入这里(转译即用不持久)。
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

DB_PATH = Path("data/agent_audio.sqlite")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audio_blobs (
            audio_id TEXT NOT NULL PRIMARY KEY,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mime TEXT NOT NULL,
            resolved TEXT NOT NULL,
            size INTEGER NOT NULL,
            payload BLOB NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audio_blobs_session
        ON audio_blobs(user_id, conversation_id)
    """)

    conn.commit()
    return conn


def store_audio(
    user_id: str,
    conversation_id: str,
    name: str,
    mime: str,
    resolved: str,
    payload: bytes,
) -> dict:
    if not user_id.strip() or not conversation_id.strip():
        raise ValueError("user_id/conversation_id cannot be empty.")

    if not payload:
        raise ValueError("audio payload cannot be empty.")

    audio_id = uuid4().hex
    now = datetime.now(UTC).isoformat()

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO audio_blobs
            (audio_id, user_id, conversation_id, name, mime, resolved,
             size, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audio_id,
                user_id,
                conversation_id,
                name,
                mime,
                resolved,
                len(payload),
                payload,
                now,
            ),
        )
        conn.commit()

        return {
            "audio_id": audio_id,
            "name": name,
            "mime": mime,
            "resolved": resolved,
            "size": len(payload),
            "created_at": now,
        }
    finally:
        conn.close()


def get_audio(user_id: str, audio_id: str) -> dict | None:
    """按用户边界取 blob —— 工具调用永远不能越用户读取音频。"""

    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT audio_id, user_id, conversation_id, name, mime, resolved,
                   size, payload, created_at
            FROM audio_blobs
            WHERE user_id = ? AND audio_id = ?
            """,
            (user_id, audio_id),
        ).fetchone()

        return dict(row) if row else None
    finally:
        conn.close()


def delete_audio_for_session(
    user_id: str,
    conversation_id: str,
) -> int:
    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            DELETE FROM audio_blobs
            WHERE user_id = ? AND conversation_id = ?
            """,
            (user_id, conversation_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
