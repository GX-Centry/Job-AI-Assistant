import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

DB_PATH = Path("data/agent_sessions.sqlite")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New conversation',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, conversation_id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user
        ON sessions(user_id, updated_at)
    """)

    conn.commit()
    return conn


def create_session(
    user_id: str,
    title: str = "New conversation",
) -> dict:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty.")

    conversation_id = str(uuid4())
    now = datetime.now(UTC).isoformat()

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO sessions
            (user_id, conversation_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, conversation_id, title, now, now),
        )
        conn.commit()

        return {
            "conversation_id": conversation_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


def list_sessions(user_id: str) -> list[dict]:
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT conversation_id, title, created_at, updated_at
            FROM sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_session(
    user_id: str,
    conversation_id: str,
) -> dict | None:
    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT conversation_id, title, created_at, updated_at
            FROM sessions
            WHERE user_id = ? AND conversation_id = ?
            """,
            (user_id, conversation_id),
        ).fetchone()

        return dict(row) if row else None
    finally:
        conn.close()


def update_session_activity(
    user_id: str,
    conversation_id: str,
) -> bool:
    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            UPDATE sessions
            SET updated_at = ?
            WHERE user_id = ? AND conversation_id = ?
            """,
            (
                datetime.now(UTC).isoformat(),
                user_id,
                conversation_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_session(
    user_id: str,
    conversation_id: str,
) -> bool:
    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            DELETE FROM sessions
            WHERE user_id = ? AND conversation_id = ?
            """,
            (user_id, conversation_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
