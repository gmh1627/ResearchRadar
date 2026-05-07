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
                    summary_zh TEXT,
                    authors_json TEXT NOT NULL DEFAULT '[]',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    published_at TEXT,
                    collected_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    source_reliability TEXT,
                    evidence_role TEXT,
                    source_tier TEXT,
                    quality_score REAL,
                    score_parts_json TEXT NOT NULL DEFAULT '{}',
                    relevance_reason TEXT,
                    recommended_action TEXT,
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

                CREATE TABLE IF NOT EXISTS item_views (
                    user_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    viewed_at TEXT NOT NULL,
                    view_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(user_id, item_id)
                );

                CREATE TABLE IF NOT EXISTS digest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    run_date TEXT NOT NULL,
                    days INTEGER NOT NULL,
                    item_limit INTEGER NOT NULL,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, run_date, days, item_limit)
                );

                CREATE TABLE IF NOT EXISTS digest_entries (
                    run_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY(run_id, position),
                    FOREIGN KEY(run_id) REFERENCES digest_runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_digest_runs_user_date
                ON digest_runs(user_id, run_date, created_at);

                CREATE INDEX IF NOT EXISTS idx_digest_entries_fingerprint
                ON digest_entries(fingerprint);

                CREATE TABLE IF NOT EXISTS item_fingerprints (
                    fingerprint TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_item_fingerprints_item
                ON item_fingerprints(item_id);
                """
            )
            ensure_column(conn, "items", "summary_zh", "TEXT")
            ensure_column(conn, "items", "last_seen_at", "TEXT")
            ensure_column(conn, "items", "source_tier", "TEXT")
            ensure_column(conn, "items", "quality_score", "REAL")
            ensure_column(conn, "items", "score_parts_json", "TEXT NOT NULL DEFAULT '{}'")
            ensure_column(conn, "items", "relevance_reason", "TEXT")
            ensure_column(conn, "items", "recommended_action", "TEXT")
            conn.execute(
                """
                UPDATE items
                SET last_seen_at=collected_at
                WHERE last_seen_at IS NULL OR last_seen_at = ''
                """
            )
            self.backfill_item_fingerprints(conn)

    def upsert_items(self, items: Iterable[dict[str, Any]]) -> int:
        rows = list(items)
        if not rows:
            return 0
        rows = self.filter_new_items(rows)
        if not rows:
            return 0
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO items (
                    id, source_id, source_name, source_type, title, url, summary, summary_zh,
                    authors_json, categories_json, tags_json, published_at,
                    collected_at, last_seen_at, source_reliability, evidence_role, source_tier,
                    quality_score, score_parts_json, relevance_reason, recommended_action,
                    metadata_json, search_text
                )
                VALUES (
                    :id, :source_id, :source_name, :source_type, :title, :url, :summary, :summary_zh,
                    :authors_json, :categories_json, :tags_json, :published_at,
                    :collected_at, :last_seen_at, :source_reliability, :evidence_role, :source_tier,
                    :quality_score, :score_parts_json, :relevance_reason, :recommended_action,
                    :metadata_json, :search_text
                )
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    summary=COALESCE(NULLIF(excluded.summary, ''), items.summary),
                    summary_zh=COALESCE(NULLIF(items.summary_zh, ''), excluded.summary_zh),
                    tags_json=excluded.tags_json,
                    categories_json=excluded.categories_json,
                    source_tier=COALESCE(excluded.source_tier, items.source_tier),
                    metadata_json=excluded.metadata_json,
                    last_seen_at=excluded.last_seen_at,
                    search_text=excluded.search_text
                """,
                rows,
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO item_fingerprints(
                    fingerprint, item_id, source_id, source_type, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["content_fingerprint"],
                        item["id"],
                        item["source_id"],
                        item["source_type"],
                        item["collected_at"],
                    )
                    for item in rows
                    if item.get("content_fingerprint")
                ],
            )
            return conn.total_changes - before

    def filter_new_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            return []
        incoming: list[dict[str, Any]] = []
        seen_in_batch: set[str] = set()
        for item in items:
            fingerprint = content_fingerprint(item)
            item["content_fingerprint"] = fingerprint
            if fingerprint and fingerprint in seen_in_batch:
                continue
            if fingerprint:
                seen_in_batch.add(fingerprint)
            incoming.append(item)
        fingerprints = [item["content_fingerprint"] for item in incoming if item.get("content_fingerprint")]
        existing: set[str] = set()
        if fingerprints:
            placeholders = ", ".join("?" for _ in fingerprints)
            with self.connect() as conn:
                rows = conn.execute(
                    f"SELECT fingerprint FROM item_fingerprints WHERE fingerprint IN ({placeholders})",
                    fingerprints,
                ).fetchall()
            existing = {row["fingerprint"] for row in rows}
        return [item for item in incoming if not item.get("content_fingerprint") or item["content_fingerprint"] not in existing]

    def backfill_item_fingerprints(self, conn: sqlite3.Connection) -> None:
        existing = conn.execute("SELECT COUNT(*) AS c FROM item_fingerprints").fetchone()["c"]
        if existing:
            return
        rows = conn.execute("SELECT * FROM items ORDER BY COALESCE(published_at, collected_at) ASC, id ASC").fetchall()
        seen: set[str] = set()
        payload: list[tuple[str, str, str, str, str]] = []
        for row in rows:
            item = decode_item(row)
            fingerprint = content_fingerprint(item)
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            payload.append(
                (
                    fingerprint,
                    item["id"],
                    item["source_id"],
                    item["source_type"],
                    item.get("collected_at") or datetime.now(timezone.utc).isoformat(),
                )
            )
        if payload:
            conn.executemany(
                """
                INSERT OR IGNORE INTO item_fingerprints(
                    fingerprint, item_id, source_id, source_type, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                payload,
            )

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

    def source_run_exists(
        self,
        target_date: date,
        source_id: str,
        statuses: tuple[str, ...] = ("success", "partial"),
    ) -> bool:
        if not statuses:
            return False
        placeholders = ", ".join("?" for _ in statuses)
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT 1
                FROM source_runs
                WHERE target_date=? AND source_id=? AND status IN ({placeholders})
                LIMIT 1
                """,
                [target_date.isoformat(), source_id, *statuses],
            ).fetchone()
        return row is not None

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
        item_date: str = "",
        q: str = "",
        source_id: str = "",
        source_type: str = "",
        tag: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = []
        params: list[Any] = []
        date_expr = "date(COALESCE(published_at, collected_at))"
        if item_date:
            clauses.append(f"{date_expr} = ?")
            params.append(item_date)
        else:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            clauses.append("COALESCE(published_at, collected_at) >= ?")
            params.append(since.isoformat())
        append_search_filter(clauses, params, q)
        append_multi_filter(clauses, params, "source_id", source_id)
        append_multi_filter(clauses, params, "source_type", source_type)
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

    def available_dates(
        self,
        *,
        days: int = 365,
        q: str = "",
        source_id: str = "",
        source_type: str = "",
        tag: str = "",
        limit: int = 366,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if days > 0:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            clauses.append("COALESCE(published_at, collected_at) >= ?")
            params.append(since.isoformat())
        append_search_filter(clauses, params, q)
        append_multi_filter(clauses, params, "source_id", source_id)
        append_multi_filter(clauses, params, "source_type", source_type)
        if tag:
            clauses.append("tags_json LIKE ?")
            params.append(f"%{tag}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        date_expr = "date(COALESCE(published_at, collected_at))"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {date_expr} AS date, COUNT(*) AS count
                FROM items
                {where}
                GROUP BY date
                HAVING count > 0
                ORDER BY date DESC
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return decode_item(row) if row else None

    def update_item_scores(self, items: Iterable[dict[str, Any]]) -> None:
        rows = []
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            rows.append(
                (
                    item.get("quality_score", item.get("score")),
                    json.dumps(item.get("score_parts") or {}, ensure_ascii=False),
                    item.get("relevance_reason"),
                    item.get("recommended_action"),
                    item_id,
                )
            )
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                UPDATE items
                SET quality_score=?, score_parts_json=?, relevance_reason=?, recommended_action=?
                WHERE id=?
                """,
                rows,
            )

    def feedback_for_user(self, user_id: str) -> dict[str, list[str]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT item_id, action FROM feedback WHERE user_id=?", (user_id,)).fetchall()
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row["item_id"], []).append(row["action"])
        return out

    def feedback_signals_for_user(self, user_id: str) -> dict[str, dict[str, float]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.action, i.source_id, i.tags_json
                FROM feedback f
                JOIN items i ON i.id = f.item_id
                WHERE f.user_id=?
                """,
                (user_id,),
            ).fetchall()
        signals = {
            "positive_tags": {},
            "negative_tags": {},
            "positive_sources": {},
            "negative_sources": {},
        }
        for row in rows:
            action = row["action"]
            if action in {"like", "save", "deep_read"}:
                tag_bucket = signals["positive_tags"]
                source_bucket = signals["positive_sources"]
                weight = 1.4 if action == "deep_read" else 1.0
            elif action in {"ignore", "not_relevant"}:
                tag_bucket = signals["negative_tags"]
                source_bucket = signals["negative_sources"]
                weight = 1.4 if action == "not_relevant" else 1.0
            else:
                continue
            for tag in json.loads(row["tags_json"] or "[]"):
                tag_bucket[tag] = tag_bucket.get(tag, 0.0) + weight
            source_id = row["source_id"]
            source_bucket[source_id] = source_bucket.get(source_id, 0.0) + weight
        return signals

    def add_feedback(self, user_id: str, item_id: str, action: str, note: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback(user_id, item_id, action, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, item_id, action, note, datetime.now(timezone.utc).isoformat()),
            )

    def remove_feedback(self, user_id: str, item_id: str, action: str | None = None) -> int:
        with self.connect() as conn:
            before = conn.total_changes
            if action:
                conn.execute("DELETE FROM feedback WHERE user_id=? AND item_id=? AND action=?", (user_id, item_id, action))
            else:
                conn.execute("DELETE FROM feedback WHERE user_id=? AND item_id=?", (user_id, item_id))
            return conn.total_changes - before

    def record_item_view(self, user_id: str, item_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO item_views(user_id, item_id, viewed_at, view_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id, item_id) DO UPDATE SET
                    viewed_at=excluded.viewed_at,
                    view_count=item_views.view_count + 1
                """,
                (user_id, item_id, datetime.now(timezone.utc).isoformat()),
            )

    def get_digest_run(self, user_id: str, run_date: str, days: int, item_limit: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            run = conn.execute(
                """
                SELECT *
                FROM digest_runs
                WHERE user_id=? AND run_date=? AND days=? AND item_limit=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, run_date, days, item_limit),
            ).fetchone()
            if not run:
                return None
            rows = conn.execute(
                """
                SELECT i.*, de.position
                FROM digest_entries de
                JOIN items i ON i.id = de.item_id
                WHERE de.run_id=?
                ORDER BY de.position ASC
                """,
                (run["id"],),
            ).fetchall()
        return {
            "meta": dict(run),
            "items": [decode_item(row) for row in rows],
        }

    def sent_digest_fingerprints(self, user_id: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT de.fingerprint
                FROM digest_entries de
                JOIN digest_runs dr ON dr.id = de.run_id
                WHERE dr.user_id=?
                """,
                (user_id,),
            ).fetchall()
        return {str(row["fingerprint"]).strip() for row in rows if str(row["fingerprint"]).strip()}

    def create_digest_run(
        self,
        *,
        user_id: str,
        run_date: str,
        days: int,
        item_limit: int,
        candidate_count: int,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO digest_runs(user_id, run_date, days, item_limit, candidate_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, run_date, days, item_limit) DO NOTHING
                """,
                (user_id, run_date, days, item_limit, candidate_count, created_at),
            )
            run = conn.execute(
                """
                SELECT *
                FROM digest_runs
                WHERE user_id=? AND run_date=? AND days=? AND item_limit=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, run_date, days, item_limit),
            ).fetchone()
            if not run:
                raise RuntimeError("failed to create digest run")
            existing = conn.execute(
                "SELECT COUNT(*) AS c FROM digest_entries WHERE run_id=?",
                (run["id"],),
            ).fetchone()["c"]
            if not existing:
                conn.executemany(
                    """
                    INSERT INTO digest_entries(run_id, position, item_id, fingerprint)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (run["id"], index, entry["item_id"], entry["fingerprint"])
                        for index, entry in enumerate(entries)
                    ],
                )
        return dict(run)

    def items_missing_translation(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, summary, source_type, source_name
                FROM items
                WHERE (summary_zh IS NULL OR summary_zh = '')
                ORDER BY (summary IS NULL OR summary = '') ASC, COALESCE(published_at, collected_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_summary_zh(self, item_id: str, summary_zh: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE items SET summary_zh=? WHERE id=?", (summary_zh, item_id))

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
                """
                SELECT n.*, i.title AS item_title, i.url AS item_url, i.source_name AS item_source_name
                FROM notes n
                LEFT JOIN items i ON i.id = n.item_id
                WHERE n.user_id=?
                ORDER BY n.created_at DESC
                LIMIT 100
                """,
                (user_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "tags": json.loads(row["tags_json"] or "[]"),
            }
            for row in rows
        ]

    def list_feedback_items(
        self, user_id: str = "default", actions: list[str] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        selected_actions = actions or ["save", "deep_read"]
        placeholders = ", ".join("?" for _ in selected_actions)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT i.*, f.action AS feedback_action, MAX(f.created_at) AS feedback_created_at
                FROM feedback f
                JOIN items i ON i.id = f.item_id
                WHERE f.user_id=? AND f.action IN ({placeholders})
                GROUP BY i.id, f.action
                ORDER BY feedback_created_at DESC
                LIMIT ?
                """,
                [user_id, *selected_actions, limit],
            ).fetchall()
        out = []
        for row in rows:
            item = decode_item(row)
            item["feedback_action"] = row["feedback_action"]
            item["feedback_created_at"] = row["feedback_created_at"]
            out.append(item)
        return out

    def list_conversations(self, user_id: str = "default", limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, i.title AS item_title, i.url AS item_url, i.source_name AS item_source_name
                FROM conversations c
                LEFT JOIN items i ON i.id = c.item_id
                WHERE c.user_id=?
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_conversation(self, user_id: str, conversation_id: str) -> int:
        with self.connect() as conn:
            before = conn.total_changes
            conn.execute("DELETE FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id))
            return conn.total_changes - before

    def delete_note(self, user_id: str, note_id: str) -> int:
        with self.connect() as conn:
            before = conn.total_changes
            conn.execute("DELETE FROM notes WHERE user_id=? AND id=?", (user_id, note_id))
            return conn.total_changes - before

    def knowledge_graph_items(self, user_id: str = "default", limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*, MAX(COALESCE(v.viewed_at, f.created_at, c.created_at, n.created_at, i.collected_at)) AS knowledge_at,
                       COALESCE(MAX(v.view_count), 0) AS view_count,
                       COUNT(DISTINCT f.id) AS feedback_count,
                       COUNT(DISTINCT c.id) AS conversation_count,
                       COUNT(DISTINCT n.id) AS note_count
                FROM items i
                LEFT JOIN item_views v ON v.item_id=i.id AND v.user_id=?
                LEFT JOIN feedback f ON f.item_id=i.id AND f.user_id=? AND f.action IN ('save', 'deep_read', 'like')
                LEFT JOIN conversations c ON c.item_id=i.id AND c.user_id=?
                LEFT JOIN notes n ON n.item_id=i.id AND n.user_id=?
                WHERE v.item_id IS NOT NULL OR f.item_id IS NOT NULL OR c.item_id IS NOT NULL OR n.item_id IS NOT NULL
                GROUP BY i.id
                ORDER BY knowledge_at DESC
                LIMIT ?
                """,
                (user_id, user_id, user_id, user_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = decode_item(row)
            item["knowledge_at"] = row["knowledge_at"]
            item["view_count"] = row["view_count"]
            item["feedback_count"] = row["feedback_count"]
            item["conversation_count"] = row["conversation_count"]
            item["note_count"] = row["note_count"]
            out.append(item)
        return out

    def knowledge_stats(self, user_id: str = "default") -> dict[str, Any]:
        with self.connect() as conn:
            saved = conn.execute(
                """
                SELECT COUNT(DISTINCT item_id) AS c
                FROM feedback
                WHERE user_id=? AND action='save'
                """,
                (user_id,),
            ).fetchone()["c"]
            deep_read = conn.execute(
                """
                SELECT COUNT(DISTINCT item_id) AS c
                FROM feedback
                WHERE user_id=? AND action='deep_read'
                """,
                (user_id,),
            ).fetchone()["c"]
            notes = conn.execute("SELECT COUNT(*) AS c FROM notes WHERE user_id=?", (user_id,)).fetchone()["c"]
            conversations = conn.execute(
                "SELECT COUNT(*) AS c FROM conversations WHERE user_id=?", (user_id,)
            ).fetchone()["c"]
            tag_rows = conn.execute(
                """
                SELECT i.tags_json
                FROM feedback f
                JOIN items i ON i.id = f.item_id
                WHERE f.user_id=? AND f.action IN ('save', 'deep_read', 'like')
                """,
                (user_id,),
            ).fetchall()
        tag_counts: dict[str, int] = {}
        for row in tag_rows:
            for tag in json.loads(row["tags_json"] or "[]"):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tags = [
            {"tag": tag, "count": count}
            for tag, count in sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:10]
        ]
        return {
            "saved": int(saved),
            "deep_read": int(deep_read),
            "notes": int(notes),
            "conversations": int(conversations),
            "top_tags": top_tags,
        }

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


def ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(row["name"] == column for row in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def append_multi_filter(clauses: list[str], params: list[Any], column: str, value: str) -> None:
    values = split_csv(value)
    if not values:
        return
    if len(values) == 1:
        clauses.append(f"{column} = ?")
        params.append(values[0])
        return
    placeholders = ", ".join("?" for _ in values)
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(values)


def append_search_filter(clauses: list[str], params: list[Any], q: str) -> None:
    q = q.strip().lower()
    if not q:
        return
    needle = f"%{q}%"
    clauses.append(
        """
        (
            search_text LIKE ?
            OR lower(COALESCE(summary_zh, '')) LIKE ?
            OR lower(COALESCE(title, '')) LIKE ?
        )
        """
    )
    params.extend([needle, needle, needle])


def decode_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["authors"] = json.loads(item.pop("authors_json") or "[]")
    item["categories"] = json.loads(item.pop("categories_json") or "[]")
    item["tags"] = json.loads(item.pop("tags_json") or "[]")
    item["score_parts"] = json.loads(item.pop("score_parts_json", "{}") or "{}")
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


def content_fingerprint(item: dict[str, Any]) -> str:
    source_id = str(item.get("source_id") or "").strip().lower()
    source_type = str(item.get("source_type") or "").strip().lower()
    title = normalize_fingerprint_text(item.get("title"))
    url = normalize_fingerprint_url(item.get("url"))
    metadata = item.get("metadata")
    if metadata is None and item.get("metadata_json"):
        try:
            metadata = json.loads(item["metadata_json"] or "{}")
        except Exception:
            metadata = {}
    metadata = metadata or {}

    if source_id == "github":
        return f"github:{url or title}"
    if source_id == "arxiv_core":
        arxiv_id = str(metadata.get("arxiv_id") or "").strip().lower()
        return f"arxiv:{arxiv_id or url or title}"
    if source_type in {"blog", "discussion", "cn_community", "signal"}:
        return f"url:{url}" if url else f"text:{title}"
    if url:
        return f"url:{url}"
    return f"text:{title}"


def normalize_fingerprint_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.split())


def normalize_fingerprint_url(value: Any) -> str:
    url = str(value or "").strip().lower()
    if not url:
        return ""
    url = url.replace("https://", "").replace("http://", "")
    if url.startswith("www."):
        url = url[4:]
    url = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    for marker in ["/category/", "/categories/", "/tag/", "/tags/", "/topics/", "/topic/"]:
        if marker in url:
            url = url.split(marker, 1)[0].rstrip("/")
    if url.endswith("/index.html"):
        url = url[: -len("/index.html")]
    return url
