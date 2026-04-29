from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    summary TEXT,
                    authors_json TEXT NOT NULL DEFAULT '[]',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    published_at TEXT,
                    collected_at TEXT NOT NULL,
                    source_reliability TEXT,
                    evidence_role TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    search_text TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
                CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
                CREATE INDEX IF NOT EXISTS idx_items_type ON items(source_type);

                CREATE TABLE IF NOT EXISTS crawl_days (
                    target_date TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    items_found INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS source_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_date TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    items_found INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_source_runs_latest
                ON source_runs(source_id, started_at);

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_feedback_user_item
                ON feedback(user_id, item_id);

                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    item_id TEXT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    importance INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    item_id TEXT,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def upsert_items(self, items: Iterable[dict[str, Any]]) -> int:
        rows = list(items)
        if not rows:
            return 0
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO items (
                    id, source_id, source_name, source_type, title, url, summary,
                    authors_json, categories_json, tags_json, published_at,
                    collected_at, source_reliability, evidence_role, metadata_json,
                    search_text
                )
                VALUES (
                    :id, :source_id, :source_name, :source_type, :title, :url, :summary,
                    :authors_json, :categories_json, :tags_json, :published_at,
                    :collected_at, :source_reliability, :evidence_role, :metadata_json,
                    :search_text
                )
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    summary=COALESCE(NULLIF(excluded.summary, ''), items.summary),
                    tags_json=excluded.tags_json,
                    categories_json=excluded.categories_json,
                    metadata_json=excluded.metadata_json,
                    collected_at=excluded.collected_at,
                    search_text=excluded.search_text
                """,
                rows,
            )
            return conn.total_changes - before

    def mark_day_started(self, target_date: date) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO crawl_days(target_date, status, started_at, items_found)
                VALUES (?, 'running', ?, 0)
                ON CONFLICT(target_date) DO UPDATE SET
                    status='running', started_at=excluded.started_at,
                    finished_at=NULL, error=NULL
                """,
                (target_date.isoformat(), ts),
            )

    def mark_day_finished(self, target_date: date, status: str, items_found: int, error: str | None = None) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE crawl_days
                SET status=?, finished_at=?, items_found=?, error=?
                WHERE target_date=?
                """,
                (status, ts, items_found, error, target_date.isoformat()),
            )

    def add_source_run(
        self,
        target_date: date,
        source_id: str,
        status: str,
        started_at: str,
        finished_at: str,
        items_found: int,
        error: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_runs(
                    target_date, source_id, status, started_at, finished_at, items_found, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (target_date.isoformat(), source_id, status, started_at, finished_at, items_found, error),
            )

    def successful_days(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT target_date FROM crawl_days WHERE status='success'").fetchall()
        return {row["target_date"] for row in rows}

    def last_successful_day(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT target_date FROM crawl_days WHERE status='success' ORDER BY target_date DESC LIMIT 1"
            ).fetchone()
        return row["target_date"] if row else None

    def query_items(
        self,
        *,
        days: int = 14,
        q: str = "",
        source_id: str = "",
        source_type: str = "",
        tag: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = []
        params: list[Any] = []
        since = datetime.now(timezone.utc) - timedelta(days=days)
        clauses.append("(published_at IS NULL OR published_at >= ?)")
        params.append(since.isoformat())
        if q:
            clauses.append("search_text LIKE ?")
            params.append(f"%{q.lower()}%")
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        if tag:
            clauses.append("tags_json LIKE ?")
            params.append(f"%{tag}%")
        where = " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS c FROM items{where}", params).fetchone()["c"]
            rows = conn.execute(
                f"""
                SELECT * FROM items
                {where}
                ORDER BY (published_at IS NULL) ASC, COALESCE(published_at, collected_at) DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return [decode_item(row) for row in rows], int(total)

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return decode_item(row) if row else None

    def feedback_for_user(self, user_id: str) -> dict[str, list[str]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT item_id, action FROM feedback WHERE user_id=?", (user_id,)).fetchall()
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row["item_id"], []).append(row["action"])
        return out

    def add_feedback(self, user_id: str, item_id: str, action: str, note: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback(user_id, item_id, action, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, item_id, action, note, datetime.now(timezone.utc).isoformat()),
            )

    def add_note(self, note: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO notes(id, user_id, item_id, title, content, tags_json, importance, created_at)
                VALUES (:id, :user_id, :item_id, :title, :content, :tags_json, :importance, :created_at)
                """,
                note,
            )

    def list_notes(self, user_id: str = "default") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notes WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user_id,)
            ).fetchall()
        return [
            {
                **dict(row),
                "tags": json.loads(row["tags_json"] or "[]"),
            }
            for row in rows
        ]

    def add_conversation(self, conv: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations(id, user_id, scope, item_id, question, answer, created_at)
                VALUES (:id, :user_id, :scope, :item_id, :question, :answer, :created_at)
                """,
                conv,
            )

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            item_count = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
            source_count = conn.execute("SELECT COUNT(DISTINCT source_id) AS c FROM items").fetchone()["c"]
            last_day = conn.execute(
                "SELECT * FROM crawl_days ORDER BY target_date DESC LIMIT 1"
            ).fetchone()
            by_type = conn.execute(
                "SELECT source_type, COUNT(*) AS c FROM items GROUP BY source_type ORDER BY c DESC"
            ).fetchall()
            recent = conn.execute(
                """
                SELECT date(COALESCE(published_at, collected_at)) AS d, COUNT(*) AS c
                FROM items
                GROUP BY d
                ORDER BY d DESC
                LIMIT 14
                """
            ).fetchall()
        return {
            "item_count": item_count,
            "source_count": source_count,
            "last_day": dict(last_day) if last_day else None,
            "by_type": [dict(row) for row in by_type],
            "recent": [dict(row) for row in recent],
        }

    def source_status(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sr.*
                FROM source_runs sr
                JOIN (
                    SELECT source_id, MAX(id) AS max_id
                    FROM source_runs
                    GROUP BY source_id
                ) latest
                ON latest.max_id = sr.id
                ORDER BY sr.source_id
                """
            ).fetchall()
        return [dict(row) for row in rows]


def encode_json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def decode_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["authors"] = json.loads(item.pop("authors_json") or "[]")
    item["categories"] = json.loads(item.pop("categories_json") or "[]")
    item["tags"] = json.loads(item.pop("tags_json") or "[]")
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item
