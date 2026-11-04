"""Complete conversation history, separate from compressed model memory."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.errors import AppError


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversations(id)
                    ON DELETE CASCADE, user TEXT NOT NULL, assistant TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, request_id TEXT, error TEXT,
                    metrics TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS turn_session ON turns(session_id, created_at, id);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        except sqlite3.Error as exc:
            raise AppError("HISTORY_UNAVAILABLE", "Conversation storage unavailable", 503) from exc
        finally:
            if db is not None:
                db.close()

    def create(self, title: str = "新对话", session_id: str | None = None) -> dict[str, Any]:
        identity = session_id or f"ses_{uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO conversations VALUES (?, ?, 0, ?, ?)",
                (identity, title, now, now),
            )
        return self.get(identity)

    def get(self, session_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise AppError("SESSION_NOT_FOUND", "Conversation not found", 404)
        return dict(row)

    def list(
        self, *, archived: bool = False, query: str = "", offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        with self.connection() as db:
            parameters = (int(archived), f"%{query}%", f"%{query}%")
            condition = """archived=? AND (title LIKE ? OR EXISTS
                (SELECT 1 FROM turns WHERE session_id=conversations.id
                 AND (user LIKE ? OR assistant LIKE ?)))"""
            values = (*parameters, parameters[2])
            count = db.execute(
                f"SELECT count(*) FROM conversations WHERE {condition}", values
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM conversations WHERE {condition} "
                "ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": count, "offset": offset}

    def update(self, session_id: str, title: str | None, archived: bool | None) -> dict[str, Any]:
        self.get(session_id)
        with self.connection() as db:
            db.execute(
                "UPDATE conversations SET title=COALESCE(?, title), "
                "archived=COALESCE(?, archived), updated_at=? WHERE id=?",
                (title, archived, datetime.now(UTC).isoformat(), session_id),
            )
        return self.get(session_id)

    def delete(self, session_id: str) -> None:
        self.get(session_id)
        with self.connection() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (session_id,))

    def begin(self, session_id: str, turn_id: str, user: str, request_id: str | None) -> None:
        self.create(user[:80] or "新对话", session_id)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM turns WHERE id=?", (turn_id,)).fetchone():
                raise AppError(
                    "TURN_EXISTS",
                    "This turn was already submitted; inspect history before retrying",
                    409,
                )
            now = datetime.now(UTC).isoformat()
            if not db.execute(
                "SELECT 1 FROM turns WHERE session_id=? LIMIT 1", (session_id,)
            ).fetchone():
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND title='新对话'",
                    (user[:80] or "新对话", session_id),
                )
            db.execute(
                "UPDATE turns SET status='interrupted', error='Previous process exited', "
                "updated_at=? WHERE session_id=? AND status='running'",
                (now, session_id),
            )
            db.execute(
                "INSERT INTO turns(id,session_id,user,status,request_id,created_at,updated_at) "
                "VALUES(?,?,?,'running',?,?,?)",
                (turn_id, session_id, user, request_id, now, now),
            )
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, session_id))

    def finish(
        self, turn_id: str, content: str, status: str, error: str | None, metrics: dict[str, Any]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            db.execute(
                "UPDATE turns SET assistant=?, status=?, error=?, metrics=?, "
                "updated_at=? WHERE id=?",
                (content, status, error, json.dumps(metrics), now, turn_id),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? "
                "WHERE id=(SELECT session_id FROM turns WHERE id=?)",
                (now, turn_id),
            )

    def turns(self, session_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        self.get(session_id)
        with self.connection() as db:
            total = db.execute(
                "SELECT count(*) FROM turns WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY created_at, id LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item["metrics"])
            items.append(item)
        return {"items": items, "total": total, "offset": offset}

    def export(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY created_at,id", (session_id,)
            ).fetchall()
        return {
            "session": session,
            "turns": [{**dict(row), "metrics": json.loads(row["metrics"])} for row in rows],
        }

    def stats(self) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute("SELECT metrics,status FROM turns").fetchall()
        tokens = peak = longest = 0
        for row in rows:
            metrics = json.loads(row["metrics"])
            usage = metrics.get("usage.update", metrics.get("usage", {})) or {}
            total = sum(
                value
                for key, value in usage.items()
                if key in {"input_tokens", "output_tokens"} and isinstance(value, int)
            )
            tokens += total
            peak = max(peak, total)
            longest = max(longest, metrics.get("duration_ms", 0))
        return {
            "turns": len(rows),
            "tokens": tokens,
            "peak_tokens": peak,
            "longest_ms": longest,
            "completed": sum(row["status"] == "completed" for row in rows),
        }
