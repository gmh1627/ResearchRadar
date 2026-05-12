from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge_fts_available = True

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

                CREATE TABLE IF NOT EXISTS wiki_pages (
                    user_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    page_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    source_item_ids_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, slug)
                );

                CREATE INDEX IF NOT EXISTS idx_wiki_pages_user_type
                ON wiki_pages(user_id, page_type, updated_at);

                CREATE TABLE IF NOT EXISTS wiki_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_wiki_log_user_created
                ON wiki_log(user_id, created_at);

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
                    profile_version TEXT NOT NULL DEFAULT '',
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
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

                CREATE TABLE IF NOT EXISTS profile_update_candidates (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    update_type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_id TEXT,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_profile_update_candidates_user_status
                ON profile_update_candidates(user_id, status, created_at);

                CREATE TABLE IF NOT EXISTS profile_memory (
                    user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    memory_value TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    source_candidate_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, memory_key, memory_value)
                );

                CREATE INDEX IF NOT EXISTS idx_profile_memory_user_key
                ON profile_memory(user_id, memory_key);
                """
            )
            ensure_column(conn, "items", "summary_zh", "TEXT")
            ensure_column(conn, "items", "last_seen_at", "TEXT")
            ensure_column(conn, "items", "source_tier", "TEXT")
            ensure_column(conn, "items", "quality_score", "REAL")
            ensure_column(conn, "items", "score_parts_json", "TEXT NOT NULL DEFAULT '{}'")
            ensure_column(conn, "items", "relevance_reason", "TEXT")
            ensure_column(conn, "items", "recommended_action", "TEXT")
            ensure_column(conn, "digest_runs", "profile_version", "TEXT NOT NULL DEFAULT ''")
            self.migrate_digest_runs_profile_version(conn)
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_runs_unique_profile
                ON digest_runs(user_id, run_date, days, item_limit, profile_version)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_digest_runs_user_date
                ON digest_runs(user_id, run_date, created_at)
                """
            )
            conn.execute(
                """
                UPDATE items
                SET last_seen_at=collected_at
                WHERE last_seen_at IS NULL OR last_seen_at = ''
                """
            )
            self.backfill_item_fingerprints(conn)
            self.ensure_knowledge_fts(conn)

    def migrate_digest_runs_profile_version(self, conn: sqlite3.Connection) -> None:
        indexes = conn.execute("PRAGMA index_list(digest_runs)").fetchall()
        old_unique = False
        for index in indexes:
            if not index["unique"]:
                continue
            cols = [row["name"] for row in conn.execute(f"PRAGMA index_info({index['name']})").fetchall()]
            if cols == ["user_id", "run_date", "days", "item_limit"]:
                old_unique = True
                break
        if not old_unique:
            return
        conn.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE IF NOT EXISTS digest_runs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                run_date TEXT NOT NULL,
                days INTEGER NOT NULL,
                item_limit INTEGER NOT NULL,
                profile_version TEXT NOT NULL DEFAULT '',
                candidate_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            INSERT INTO digest_runs_new(
                id, user_id, run_date, days, item_limit, profile_version, candidate_count, created_at
            )
            SELECT id, user_id, run_date, days, item_limit, COALESCE(profile_version, ''), candidate_count, created_at
            FROM digest_runs;
            DROP TABLE digest_runs;
            ALTER TABLE digest_runs_new RENAME TO digest_runs;
            PRAGMA foreign_keys=ON;
            """
        )

    def ensure_knowledge_fts(self, conn: sqlite3.Connection) -> None:
        try:
            table = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
            ).fetchone()
            existing_sql = str(table["sql"] or "").lower() if table else ""
            if "content=''" in existing_sql or 'content=""' in existing_sql:
                conn.execute("DROP TABLE knowledge_fts")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    user_id UNINDEXED,
                    kind UNINDEXED,
                    ref_id UNINDEXED,
                    title,
                    body,
                    tags,
                    created_at UNINDEXED,
                    tokenize='unicode61'
                )
                """
            )
            row = conn.execute("SELECT COUNT(*) AS c FROM knowledge_fts").fetchone()
            if int(row["c"] or 0) == 0:
                self.rebuild_knowledge_fts(conn)
            self.knowledge_fts_available = True
        except sqlite3.DatabaseError:
            self.knowledge_fts_available = False

    def rebuild_knowledge_fts(self, conn: sqlite3.Connection, user_id: str | None = None) -> None:
        try:
            if user_id:
                conn.execute("DELETE FROM knowledge_fts WHERE user_id=?", (user_id,))
            else:
                conn.execute("DELETE FROM knowledge_fts")
            self.index_knowledge_items(conn, user_id=user_id)
            self.index_notes(conn, user_id=user_id)
            self.index_conversations(conn, user_id=user_id)
            self.index_wiki_pages(conn, user_id=user_id)
        except sqlite3.DatabaseError:
            self.knowledge_fts_available = False

    def upsert_knowledge_fts(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        kind: str,
        ref_id: str,
        title: str,
        body: str,
        tags: list[Any] | str | None = None,
        created_at: str = "",
    ) -> None:
        if not self.knowledge_fts_available or not user_id or not kind or not ref_id:
            return
        tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags or "")
        try:
            conn.execute(
                "DELETE FROM knowledge_fts WHERE user_id=? AND kind=? AND ref_id=?",
                (user_id, kind, ref_id),
            )
            conn.execute(
                """
                INSERT INTO knowledge_fts(user_id, kind, ref_id, title, body, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    kind,
                    ref_id,
                    str(title or ""),
                    str(body or ""),
                    tag_text,
                    str(created_at or datetime.now(timezone.utc).isoformat()),
                ),
            )
        except sqlite3.DatabaseError:
            self.knowledge_fts_available = False

    def delete_knowledge_fts(self, conn: sqlite3.Connection, user_id: str, kind: str, ref_id: str) -> None:
        if not self.knowledge_fts_available:
            return
        try:
            conn.execute(
                "DELETE FROM knowledge_fts WHERE user_id=? AND kind=? AND ref_id=?",
                (user_id, kind, ref_id),
            )
        except sqlite3.DatabaseError:
            self.knowledge_fts_available = False

    def index_knowledge_items(self, conn: sqlite3.Connection, user_id: str | None = None) -> None:
        params: list[Any] = []
        user_clause = ""
        if user_id:
            user_clause = " AND COALESCE(v.user_id, f.user_id, c.user_id, n.user_id)=?"
            params.append(user_id)
        rows = conn.execute(
            f"""
            SELECT COALESCE(v.user_id, f.user_id, c.user_id, n.user_id) AS user_id,
                   i.*,
                   MAX(COALESCE(v.viewed_at, f.created_at, c.created_at, n.created_at, i.collected_at)) AS knowledge_at
            FROM items i
            LEFT JOIN item_views v ON v.item_id=i.id
            LEFT JOIN feedback f ON f.item_id=i.id AND f.action IN ('save', 'deep_read', 'like')
            LEFT JOIN conversations c ON c.item_id=i.id
            LEFT JOIN notes n ON n.item_id=i.id
            WHERE COALESCE(v.user_id, f.user_id, c.user_id, n.user_id) IS NOT NULL{user_clause}
            GROUP BY COALESCE(v.user_id, f.user_id, c.user_id, n.user_id), i.id
            """,
            params,
        ).fetchall()
        for row in rows:
            self.index_knowledge_item(conn, row["user_id"], row["id"], created_at=row["knowledge_at"] or row["collected_at"])

    def index_knowledge_item(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        item_id: str,
        *,
        created_at: str = "",
    ) -> None:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return
        tags = json.loads(row["tags_json"] or "[]")
        body = "\n".join(
            [
                row["summary_zh"] or "",
                row["summary"] or "",
                row["relevance_reason"] or "",
                row["recommended_action"] or "",
                row["search_text"] or "",
            ]
        )
        self.upsert_knowledge_fts(
            conn,
            user_id=user_id,
            kind="item",
            ref_id=row["id"],
            title=row["title"],
            body=body,
            tags=tags,
            created_at=created_at or row["collected_at"],
        )

    def index_notes(self, conn: sqlite3.Connection, user_id: str | None = None) -> None:
        params: list[Any] = []
        clause = ""
        if user_id:
            clause = "WHERE user_id=?"
            params.append(user_id)
        rows = conn.execute(f"SELECT * FROM notes {clause}", params).fetchall()
        for row in rows:
            self.upsert_knowledge_fts(
                conn,
                user_id=row["user_id"],
                kind="note",
                ref_id=row["id"],
                title=row["title"],
                body=row["content"],
                tags=json.loads(row["tags_json"] or "[]"),
                created_at=row["created_at"],
            )

    def index_conversations(self, conn: sqlite3.Connection, user_id: str | None = None) -> None:
        params: list[Any] = []
        clause = ""
        if user_id:
            clause = "WHERE user_id=?"
            params.append(user_id)
        rows = conn.execute(f"SELECT * FROM conversations {clause}", params).fetchall()
        for row in rows:
            self.upsert_knowledge_fts(
                conn,
                user_id=row["user_id"],
                kind="conversation",
                ref_id=row["id"],
                title=row["question"],
                body=row["answer"],
                tags=[row["scope"]],
                created_at=row["created_at"],
            )

    def index_wiki_pages(self, conn: sqlite3.Connection, user_id: str | None = None) -> None:
        params: list[Any] = []
        clause = ""
        if user_id:
            clause = "WHERE user_id=?"
            params.append(user_id)
        rows = conn.execute(f"SELECT * FROM wiki_pages {clause}", params).fetchall()
        for row in rows:
            tags = json.loads(row["tags_json"] or "[]")
            self.upsert_knowledge_fts(
                conn,
                user_id=row["user_id"],
                kind="wiki",
                ref_id=row["slug"],
                title=row["title"],
                body="\n".join([row["summary"] or "", row["content"] or ""]),
                tags=tags,
                created_at=row["updated_at"],
            )

    def generate_profile_update_candidates(self, user_id: str, *, limit: int = 12) -> int:
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
            existing_rows = conn.execute(
                """
                SELECT update_type, lower(topic) AS topic
                FROM profile_update_candidates
                WHERE user_id=? AND status IN ('pending', 'accepted')
                """,
                (user_id,),
            ).fetchall()
            memory_rows = conn.execute(
                """
                SELECT memory_key, lower(memory_value) AS memory_value
                FROM profile_memory
                WHERE user_id=?
                """,
                (user_id,),
            ).fetchall()

            existing = {(row["update_type"], row["topic"]) for row in existing_rows}
            existing.update((memory_to_update_type(row["memory_key"]), row["memory_value"]) for row in memory_rows)

            tag_pos: dict[str, float] = {}
            tag_neg: dict[str, float] = {}
            source_pos: dict[str, float] = {}
            source_neg: dict[str, float] = {}
            for row in rows:
                action = row["action"]
                if action in {"like", "save", "deep_read"}:
                    tag_bucket = tag_pos
                    source_bucket = source_pos
                    weight = 1.6 if action == "deep_read" else 1.0
                elif action in {"ignore", "not_relevant"}:
                    tag_bucket = tag_neg
                    source_bucket = source_neg
                    weight = 1.6 if action == "not_relevant" else 1.0
                else:
                    continue
                for tag in json.loads(row["tags_json"] or "[]"):
                    tag = normalize_profile_value(tag)
                    if not tag or tag.lower() in GENERIC_PROFILE_TAGS:
                        continue
                    tag_bucket[tag] = tag_bucket.get(tag, 0.0) + weight
                source_id = normalize_profile_value(row["source_id"])
                if source_id:
                    source_bucket[source_id] = source_bucket.get(source_id, 0.0) + weight

            now = datetime.now(timezone.utc).isoformat()
            candidates: list[dict[str, Any]] = []
            for tag, score in sorted(tag_pos.items(), key=lambda item: item[1], reverse=True):
                if score < 2.0:
                    continue
                add_profile_candidate(
                    candidates,
                    user_id=user_id,
                    topic=tag,
                    update_type="increase_interest",
                    reason=f"最近多次收藏、深读或标记有用，累计正反馈 {score:.1f}。",
                    evidence_id=f"feedback:positive_tag:{tag}",
                    confidence=min(0.95, 0.55 + score * 0.08),
                    existing=existing,
                    created_at=now,
                )
            for tag, score in sorted(tag_neg.items(), key=lambda item: item[1], reverse=True):
                if score < 2.0:
                    continue
                add_profile_candidate(
                    candidates,
                    user_id=user_id,
                    topic=tag,
                    update_type="add_negative",
                    reason=f"最近多次忽略或标记不相关，累计负反馈 {score:.1f}。",
                    evidence_id=f"feedback:negative_tag:{tag}",
                    confidence=min(0.95, 0.55 + score * 0.08),
                    existing=existing,
                    created_at=now,
                )
            for source_id, score in sorted(source_pos.items(), key=lambda item: item[1], reverse=True):
                if score < 2.5:
                    continue
                add_profile_candidate(
                    candidates,
                    user_id=user_id,
                    topic=source_id,
                    update_type="prefer_source",
                    reason=f"这个来源近期获得较多正反馈，累计 {score:.1f}。",
                    evidence_id=f"feedback:positive_source:{source_id}",
                    confidence=min(0.9, 0.5 + score * 0.07),
                    existing=existing,
                    created_at=now,
                )
            for source_id, score in sorted(source_neg.items(), key=lambda item: item[1], reverse=True):
                if score < 3.0:
                    continue
                add_profile_candidate(
                    candidates,
                    user_id=user_id,
                    topic=source_id,
                    update_type="decrease_source",
                    reason=f"这个来源近期多次被忽略或标记不相关，累计 {score:.1f}。",
                    evidence_id=f"feedback:negative_source:{source_id}",
                    confidence=min(0.9, 0.5 + score * 0.07),
                    existing=existing,
                    created_at=now,
                )

            candidates = candidates[: max(1, limit)]
            if not candidates:
                return 0
            before = conn.total_changes
            conn.executemany(
                """
                INSERT OR IGNORE INTO profile_update_candidates(
                    id, user_id, topic, update_type, reason, evidence_id,
                    confidence, status, created_at, decided_at
                )
                VALUES (
                    :id, :user_id, :topic, :update_type, :reason, :evidence_id,
                    :confidence, 'pending', :created_at, NULL
                )
                """,
                candidates,
            )
            return conn.total_changes - before

    def list_profile_update_candidates(
        self, user_id: str = "default", statuses: tuple[str, ...] = ("pending",)
    ) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        clause = ""
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clause = f" AND status IN ({placeholders})"
            params.extend(statuses)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM profile_update_candidates
                WHERE user_id=?{clause}
                ORDER BY status ASC, confidence DESC, created_at DESC
                LIMIT 60
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_profile_memory(self, user_id: str = "default") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM profile_memory
                WHERE user_id=?
                ORDER BY memory_key ASC, weight DESC, created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def profile_memory_version(self, user_id: str = "default") -> str:
        rows = self.list_profile_memory(user_id)
        if not rows:
            return ""
        payload = [
            [
                row.get("memory_key") or "",
                row.get("memory_value") or "",
                round(float(row.get("weight") or 0), 4),
                row.get("created_at") or "",
            ]
            for row in rows
        ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def decide_profile_candidate(self, user_id: str, candidate_id: str, decision: str) -> dict[str, Any] | None:
        status = "accepted" if decision == "accept" else "rejected"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM profile_update_candidates
                WHERE user_id=? AND id=?
                """,
                (user_id, candidate_id),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE profile_update_candidates
                SET status=?, decided_at=?
                WHERE user_id=? AND id=?
                """,
                (status, now, user_id, candidate_id),
            )
            candidate = dict(row)
            candidate["status"] = status
            candidate["decided_at"] = now
            if status == "accepted":
                memory_key = update_type_to_memory_key(candidate["update_type"])
                conn.execute(
                    """
                    INSERT INTO profile_memory(
                        user_id, memory_key, memory_value, weight, source_candidate_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, memory_key, memory_value) DO UPDATE SET
                        weight=max(profile_memory.weight, excluded.weight),
                        source_candidate_id=excluded.source_candidate_id
                    """,
                    (
                        user_id,
                        memory_key,
                        candidate["topic"],
                        float(candidate["confidence"] or 0.7),
                        candidate["id"],
                        now,
                    ),
                )
            return candidate

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

    def mark_interrupted_crawl_days(self) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        message = "Interrupted before the previous crawl finished; marked during server startup."
        with self.connect() as conn:
            before = conn.total_changes
            conn.execute(
                """
                UPDATE crawl_days
                SET status='interrupted',
                    finished_at=?,
                    error=COALESCE(NULLIF(error, ''), ?)
                WHERE status='running'
                """,
                (ts, message),
            )
            return conn.total_changes - before

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

    def covered_days(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT target_date
                FROM crawl_days
                WHERE status IN ('success', 'partial')
                  AND items_found > 0
                """
            ).fetchall()
        return {row["target_date"] for row in rows}

    def dates_with_source_items(self, source_id: str, start: date, end: date, *, min_count: int = 1) -> set[str]:
        date_expr = "date(COALESCE(published_at, collected_at))"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {date_expr} AS target_date
                FROM items
                WHERE source_id=?
                  AND {date_expr} BETWEEN ? AND ?
                GROUP BY target_date
                HAVING COUNT(*) >= ?
                """,
                (source_id, start.isoformat(), end.isoformat(), max(1, min_count)),
            ).fetchall()
        return {row["target_date"] for row in rows if row["target_date"]}

    def latest_successful_source_dates(self, source_id: str, start: date, end: date) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sr.target_date
                FROM source_runs sr
                JOIN (
                    SELECT target_date, MAX(id) AS max_id
                    FROM source_runs
                    WHERE source_id=?
                      AND target_date BETWEEN ? AND ?
                    GROUP BY target_date
                ) latest
                ON latest.max_id = sr.id
                WHERE sr.status='success'
                """,
                (source_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        return {row["target_date"] for row in rows if row["target_date"]}

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

    def items_for_llm_postprocess(self, *, days: int = 3, limit: int = 80, pipeline_version: str = "") -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM items
                WHERE COALESCE(published_at, collected_at) >= ?
                  AND (
                    json_extract(metadata_json, '$.llm_postprocess.version') IS NULL
                    OR json_extract(metadata_json, '$.llm_postprocess.version') <> ?
                    OR json_extract(metadata_json, '$.llm_postprocess.status') = 'error'
                    OR json_extract(metadata_json, '$.llm_postprocess.status') = 'article_read_pending_analysis'
                    OR (
                      COALESCE(summary_zh, '') = ''
                      AND COALESCE(summary, '') <> ''
                    )
                  )
                ORDER BY
                  CASE WHEN source_type IN ('blog', 'cn_community') THEN 0 ELSE 1 END,
                  CASE WHEN COALESCE(summary_zh, '') = '' AND COALESCE(summary, '') <> '' THEN 0 ELSE 1 END,
                  (published_at IS NULL) ASC,
                  COALESCE(published_at, collected_at) DESC
                LIMIT ?
                """,
                (since.isoformat(), pipeline_version, max(1, limit)),
            ).fetchall()
        return [decode_item(row) for row in rows]

    def update_llm_postprocess(self, items: Iterable[dict[str, Any]]) -> None:
        rows = []
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            tags = item.get("tags", [])
            categories = item.get("categories", [])
            metadata = item.get("metadata", {})
            rows.append(
                (
                    item.get("summary", ""),
                    item.get("summary_zh", ""),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(categories, ensure_ascii=False),
                    item.get("quality_score"),
                    json.dumps(item.get("score_parts") or {}, ensure_ascii=False),
                    item.get("relevance_reason", ""),
                    item.get("recommended_action", ""),
                    json.dumps(metadata, ensure_ascii=False),
                    build_search_text(item.get("title", ""), item.get("summary", ""), tags, categories),
                    item_id,
                )
            )
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                UPDATE items
                SET summary=?,
                    summary_zh=?,
                    tags_json=?,
                    categories_json=?,
                    quality_score=?,
                    score_parts_json=?,
                    relevance_reason=?,
                    recommended_action=?,
                    metadata_json=?,
                    search_text=?
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

    def taste_feedback_rows(self, user_id: str, limit: int = 600) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    f.action,
                    f.created_at AS feedback_created_at,
                    i.id,
                    i.source_id,
                    i.source_name,
                    i.source_type,
                    i.title,
                    i.tags_json,
                    i.quality_score,
                    i.score_parts_json,
                    i.metadata_json
                FROM feedback f
                JOIN items i ON i.id = f.item_id
                WHERE f.user_id=?
                ORDER BY f.created_at DESC
                LIMIT ?
                """,
                (user_id, max(1, min(limit, 2000))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["score_parts"] = json.loads(item.pop("score_parts_json") or "{}")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            out.append(item)
        return out

    def add_feedback(self, user_id: str, item_id: str, action: str, note: str | None = None) -> None:
        with self.connect() as conn:
            created_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO feedback(user_id, item_id, action, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, item_id, action, note, created_at),
            )
            if action in {"like", "save", "deep_read"}:
                self.index_knowledge_item(conn, user_id, item_id, created_at=created_at)

    def remove_feedback(self, user_id: str, item_id: str, action: str | None = None) -> int:
        with self.connect() as conn:
            before = conn.total_changes
            if action:
                conn.execute("DELETE FROM feedback WHERE user_id=? AND item_id=? AND action=?", (user_id, item_id, action))
            else:
                conn.execute("DELETE FROM feedback WHERE user_id=? AND item_id=?", (user_id, item_id))
            row = conn.execute(
                """
                SELECT 1
                FROM feedback
                WHERE user_id=? AND item_id=? AND action IN ('like', 'save', 'deep_read')
                LIMIT 1
                """,
                (user_id, item_id),
            ).fetchone()
            if row:
                self.index_knowledge_item(conn, user_id, item_id)
            else:
                self.delete_knowledge_fts(conn, user_id, "item", item_id)
            return conn.total_changes - before

    def record_item_view(self, user_id: str, item_id: str) -> None:
        with self.connect() as conn:
            viewed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO item_views(user_id, item_id, viewed_at, view_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id, item_id) DO UPDATE SET
                    viewed_at=excluded.viewed_at,
                    view_count=item_views.view_count + 1
                """,
                (user_id, item_id, viewed_at),
            )
            self.index_knowledge_item(conn, user_id, item_id, created_at=viewed_at)

    def get_digest_run(
        self,
        user_id: str,
        run_date: str,
        days: int,
        item_limit: int,
        profile_version: str = "",
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            run = conn.execute(
                """
                SELECT *
                FROM digest_runs
                WHERE user_id=? AND run_date=? AND days=? AND item_limit=? AND profile_version=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, run_date, days, item_limit, profile_version),
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
        profile_version: str = "",
        candidate_count: int,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            existing_run = conn.execute(
                """
                SELECT id
                FROM digest_runs
                WHERE user_id=? AND run_date=? AND days=? AND item_limit=? AND profile_version=?
                LIMIT 1
                """,
                (user_id, run_date, days, item_limit, profile_version),
            ).fetchone()
            if not existing_run:
                conn.execute(
                    """
                    INSERT INTO digest_runs(user_id, run_date, days, item_limit, profile_version, candidate_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, run_date, days, item_limit, profile_version, candidate_count, created_at),
                )
            run = conn.execute(
                """
                SELECT *
                FROM digest_runs
                WHERE user_id=? AND run_date=? AND days=? AND item_limit=? AND profile_version=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, run_date, days, item_limit, profile_version),
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
            self.upsert_knowledge_fts(
                conn,
                user_id=note["user_id"],
                kind="note",
                ref_id=note["id"],
                title=note["title"],
                body=note["content"],
                tags=json.loads(note["tags_json"] or "[]") if isinstance(note.get("tags_json"), str) else note.get("tags_json"),
                created_at=note["created_at"],
            )
            if note.get("item_id"):
                self.index_knowledge_item(conn, note["user_id"], note["item_id"], created_at=note["created_at"])

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
            row = conn.execute(
                "SELECT item_id FROM conversations WHERE user_id=? AND id=?",
                (user_id, conversation_id),
            ).fetchone()
            before = conn.total_changes
            conn.execute("DELETE FROM conversations WHERE user_id=? AND id=?", (user_id, conversation_id))
            self.delete_knowledge_fts(conn, user_id, "conversation", conversation_id)
            if row and row["item_id"]:
                self.index_knowledge_item(conn, user_id, row["item_id"])
            return conn.total_changes - before

    def delete_note(self, user_id: str, note_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT item_id FROM notes WHERE user_id=? AND id=?", (user_id, note_id)).fetchone()
            before = conn.total_changes
            conn.execute("DELETE FROM notes WHERE user_id=? AND id=?", (user_id, note_id))
            self.delete_knowledge_fts(conn, user_id, "note", note_id)
            if row and row["item_id"]:
                self.index_knowledge_item(conn, user_id, row["item_id"])
            return conn.total_changes - before

    def upsert_wiki_pages(self, user_id: str, pages: list[dict[str, Any]], *, event_title: str = "") -> int:
        if not pages:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for page in pages:
            slug = str(page.get("slug") or "").strip()
            title = str(page.get("title") or "").strip()
            content = str(page.get("content") or "").strip()
            if not slug or not title or not content:
                continue
            rows.append(
                {
                    "user_id": user_id,
                    "slug": slug,
                    "page_type": str(page.get("page_type") or "concept"),
                    "title": title,
                    "summary": str(page.get("summary") or "").strip(),
                    "content": content,
                    "tags_json": encode_json(page.get("tags") or []),
                    "source_item_ids_json": encode_json(page.get("source_item_ids") or []),
                    "updated_at": now,
                }
            )
        if not rows:
            return 0
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO wiki_pages(
                    user_id, slug, page_type, title, summary, content,
                    tags_json, source_item_ids_json, updated_at
                )
                VALUES (
                    :user_id, :slug, :page_type, :title, :summary, :content,
                    :tags_json, :source_item_ids_json, :updated_at
                )
                ON CONFLICT(user_id, slug) DO UPDATE SET
                    page_type=excluded.page_type,
                    title=excluded.title,
                    summary=excluded.summary,
                    content=excluded.content,
                    tags_json=excluded.tags_json,
                    source_item_ids_json=excluded.source_item_ids_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            if event_title:
                conn.execute(
                    """
                    INSERT INTO wiki_log(user_id, event_type, title, detail, created_at)
                    VALUES (?, 'compile', ?, ?, ?)
                    """,
                    (user_id, event_title, f"updated {len(rows)} wiki pages", now),
                )
            self.index_wiki_pages(conn, user_id=user_id)
            return conn.total_changes - before

    def list_wiki_pages(self, user_id: str = "default") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_pages
                WHERE user_id=?
                ORDER BY
                    CASE page_type
                        WHEN 'index' THEN 0
                        WHEN 'overview' THEN 1
                        WHEN 'concept' THEN 2
                        WHEN 'source' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC,
                    title ASC
                """,
                (user_id,),
            ).fetchall()
        return [decode_wiki_page(row) for row in rows]

    def get_wiki_page(self, user_id: str, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM wiki_pages WHERE user_id=? AND slug=?",
                (user_id, slug),
            ).fetchone()
        return decode_wiki_page(row) if row else None

    def list_wiki_log(self, user_id: str = "default", limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_log
                WHERE user_id=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def search_knowledge(self, user_id: str = "default", q: str = "", limit: int = 80) -> list[dict[str, Any]]:
        q = q.strip().lower()
        if not q:
            return []
        if self.knowledge_fts_available:
            fts_results = self.search_knowledge_fts(user_id=user_id, q=q, limit=limit)
            if fts_results:
                return fts_results
        return self.search_knowledge_like(user_id=user_id, q=q, limit=limit)

    def search_knowledge_fts(self, user_id: str = "default", q: str = "", limit: int = 80) -> list[dict[str, Any]]:
        query = fts_query(q)
        if not query:
            return []
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT rowid, user_id, kind, ref_id, title, body, tags, created_at,
                           bm25(knowledge_fts, 8.0, 1.0, 1.3) AS rank
                    FROM knowledge_fts
                    WHERE user_id=? AND knowledge_fts MATCH ?
                    ORDER BY rank ASC, created_at DESC
                    LIMIT ?
                    """,
                    (user_id, query, max(1, min(limit * 3, 240))),
                ).fetchall()
        except sqlite3.DatabaseError:
            self.knowledge_fts_available = False
            return []

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        with self.connect() as conn:
            for row in rows:
                key = (row["kind"], row["ref_id"])
                if key in seen:
                    continue
                seen.add(key)
                result = self.knowledge_result_for_fts_row(conn, row, q)
                if result:
                    results.append(result)
                if len(results) >= limit:
                    break
        return results

    def knowledge_result_for_fts_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        q: str,
    ) -> dict[str, Any] | None:
        kind = row["kind"]
        ref_id = row["ref_id"]
        score = round(abs(float(row["rank"] or 0)), 3)
        if kind == "item":
            item_row = conn.execute("SELECT * FROM items WHERE id=?", (ref_id,)).fetchone()
            if not item_row:
                return None
            item = decode_item(item_row)
            return {
                "kind": "item",
                "id": item["id"],
                "title": item["title"],
                "subtitle": item.get("source_name") or item.get("source_id") or "条目",
                "snippet": knowledge_snippet(row["body"] or item.get("summary_zh") or item.get("summary") or "", q),
                "created_at": row["created_at"] or item.get("collected_at"),
                "rank": score,
                "search_mode": "fts",
                "item": item,
            }
        if kind == "note":
            note_row = conn.execute(
                """
                SELECT n.*, i.title AS item_title, i.url AS item_url, i.source_name AS item_source_name
                FROM notes n
                LEFT JOIN items i ON i.id=n.item_id
                WHERE n.user_id=? AND n.id=?
                """,
                (row["user_id"], ref_id),
            ).fetchone()
            if not note_row:
                return None
            note = {**dict(note_row), "tags": json.loads(note_row["tags_json"] or "[]")}
            return {
                "kind": "note",
                "id": note["id"],
                "title": note["title"],
                "subtitle": note.get("item_title") or "知识笔记",
                "snippet": knowledge_snippet(note.get("content") or row["body"] or "", q),
                "created_at": note["created_at"],
                "rank": score,
                "search_mode": "fts",
                "note": note,
            }
        if kind == "conversation":
            conv_row = conn.execute(
                """
                SELECT c.*, i.title AS item_title, i.url AS item_url, i.source_name AS item_source_name
                FROM conversations c
                LEFT JOIN items i ON i.id=c.item_id
                WHERE c.user_id=? AND c.id=?
                """,
                (row["user_id"], ref_id),
            ).fetchone()
            if not conv_row:
                return None
            conv = dict(conv_row)
            return {
                "kind": "conversation",
                "id": conv["id"],
                "title": conv["question"],
                "subtitle": conv.get("item_title") or "研究问答",
                "snippet": knowledge_snippet(conv.get("answer") or row["body"] or "", q),
                "created_at": conv["created_at"],
                "rank": score,
                "search_mode": "fts",
                "conversation": conv,
            }
        if kind == "wiki":
            page_row = conn.execute(
                "SELECT * FROM wiki_pages WHERE user_id=? AND slug=?",
                (row["user_id"], ref_id),
            ).fetchone()
            if not page_row:
                return None
            page = decode_wiki_page(page_row)
            return {
                "kind": "wiki",
                "id": page["slug"],
                "title": page["title"],
                "subtitle": page["page_type"],
                "snippet": knowledge_snippet(page.get("summary") or page.get("content") or row["body"] or "", q),
                "created_at": page["updated_at"],
                "rank": score,
                "search_mode": "fts",
                "wiki_page": page,
            }
        return None

    def search_knowledge_like(self, user_id: str = "default", q: str = "", limit: int = 80) -> list[dict[str, Any]]:
        needle = f"%{q}%"
        item_limit = max(10, min(limit, 120))
        note_limit = max(8, min(limit // 2, 60))
        conv_limit = max(8, min(limit // 2, 60))
        wiki_limit = max(8, min(limit // 2, 60))
        results: list[dict[str, Any]] = []
        with self.connect() as conn:
            item_rows = conn.execute(
                """
                SELECT i.*,
                       COALESCE(MAX(v.viewed_at), MAX(f.created_at), MAX(c.created_at), MAX(n.created_at), i.collected_at) AS knowledge_at
                FROM items i
                LEFT JOIN item_views v ON v.item_id=i.id AND v.user_id=?
                LEFT JOIN feedback f ON f.item_id=i.id AND f.user_id=?
                LEFT JOIN conversations c ON c.item_id=i.id AND c.user_id=?
                LEFT JOIN notes n ON n.item_id=i.id AND n.user_id=?
                WHERE (v.item_id IS NOT NULL OR f.item_id IS NOT NULL OR c.item_id IS NOT NULL OR n.item_id IS NOT NULL)
                  AND (
                    i.search_text LIKE ?
                    OR lower(COALESCE(i.summary_zh, '')) LIKE ?
                    OR lower(COALESCE(i.title, '')) LIKE ?
                  )
                GROUP BY i.id
                ORDER BY knowledge_at DESC
                LIMIT ?
                """,
                (user_id, user_id, user_id, user_id, needle, needle, needle, item_limit),
            ).fetchall()
            note_rows = conn.execute(
                """
                SELECT n.*, i.title AS item_title, i.url AS item_url, i.source_name AS item_source_name
                FROM notes n
                LEFT JOIN items i ON i.id=n.item_id
                WHERE n.user_id=?
                  AND (
                    lower(n.title) LIKE ?
                    OR lower(n.content) LIKE ?
                    OR lower(n.tags_json) LIKE ?
                    OR lower(COALESCE(i.title, '')) LIKE ?
                  )
                ORDER BY n.created_at DESC
                LIMIT ?
                """,
                (user_id, needle, needle, needle, needle, note_limit),
            ).fetchall()
            conv_rows = conn.execute(
                """
                SELECT c.*, i.title AS item_title, i.url AS item_url, i.source_name AS item_source_name
                FROM conversations c
                LEFT JOIN items i ON i.id=c.item_id
                WHERE c.user_id=?
                  AND (
                    lower(c.question) LIKE ?
                    OR lower(c.answer) LIKE ?
                    OR lower(COALESCE(i.title, '')) LIKE ?
                  )
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                (user_id, needle, needle, needle, conv_limit),
            ).fetchall()
            wiki_rows = conn.execute(
                """
                SELECT *
                FROM wiki_pages
                WHERE user_id=?
                  AND (
                    lower(title) LIKE ?
                    OR lower(summary) LIKE ?
                    OR lower(content) LIKE ?
                    OR lower(tags_json) LIKE ?
                  )
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, needle, needle, needle, needle, wiki_limit),
            ).fetchall()

        for row in item_rows:
            item = decode_item(row)
            results.append(
                {
                    "kind": "item",
                    "id": item["id"],
                    "title": item["title"],
                    "subtitle": item.get("source_name") or item.get("source_id") or "条目",
                    "snippet": knowledge_snippet(item.get("display_summary") or item.get("summary_zh") or item.get("summary") or "", q),
                    "created_at": row["knowledge_at"] or item.get("collected_at"),
                    "item": item,
                }
            )
        for row in note_rows:
            note = {
                **dict(row),
                "tags": json.loads(row["tags_json"] or "[]"),
            }
            results.append(
                {
                    "kind": "note",
                    "id": note["id"],
                    "title": note["title"],
                    "subtitle": note.get("item_title") or "知识笔记",
                    "snippet": knowledge_snippet(note.get("content") or "", q),
                    "created_at": note["created_at"],
                    "note": note,
                }
            )
        for row in conv_rows:
            conv = dict(row)
            results.append(
                {
                    "kind": "conversation",
                    "id": conv["id"],
                    "title": conv["question"],
                    "subtitle": conv.get("item_title") or "研究问答",
                    "snippet": knowledge_snippet(conv.get("answer") or "", q),
                    "created_at": conv["created_at"],
                    "conversation": conv,
                }
            )
        for row in wiki_rows:
            page = decode_wiki_page(row)
            results.append(
                {
                    "kind": "wiki",
                    "id": page["slug"],
                    "title": page["title"],
                    "subtitle": page["page_type"],
                    "snippet": knowledge_snippet(page.get("summary") or page.get("content") or "", q),
                    "created_at": page["updated_at"],
                    "wiki_page": page,
                }
            )
        results.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return results[:limit]

    def add_conversation(self, conv: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations(id, user_id, scope, item_id, question, answer, created_at)
                VALUES (:id, :user_id, :scope, :item_id, :question, :answer, :created_at)
                """,
                conv,
            )
            self.upsert_knowledge_fts(
                conn,
                user_id=conv["user_id"],
                kind="conversation",
                ref_id=conv["id"],
                title=conv["question"],
                body=conv["answer"],
                tags=[conv["scope"]],
                created_at=conv["created_at"],
            )
            if conv.get("item_id"):
                self.index_knowledge_item(conn, conv["user_id"], conv["item_id"], created_at=conv["created_at"])

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


def build_search_text(title: str, summary: str, tags: list[Any], categories: list[Any]) -> str:
    return f"{title}\n{summary}\n{' '.join(str(tag) for tag in tags)}\n{' '.join(str(category) for category in categories)}".lower()


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


def decode_wiki_page(row: sqlite3.Row) -> dict[str, Any]:
    page = dict(row)
    page["tags"] = json.loads(page.pop("tags_json") or "[]")
    page["source_item_ids"] = json.loads(page.pop("source_item_ids_json") or "[]")
    return page


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


GENERIC_PROFILE_TAGS = {"ai", "llm", "rag", "open source", "aihot精选", "论文/研究"}


def normalize_profile_value(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def update_type_to_memory_key(update_type: str) -> str:
    return {
        "increase_interest": "interest",
        "add_negative": "negative",
        "prefer_source": "preferred_source",
        "decrease_source": "deprioritized_source",
    }.get(update_type, "interest")


def memory_to_update_type(memory_key: str) -> str:
    return {
        "interest": "increase_interest",
        "negative": "add_negative",
        "preferred_source": "prefer_source",
        "deprioritized_source": "decrease_source",
    }.get(memory_key, "increase_interest")


def add_profile_candidate(
    candidates: list[dict[str, Any]],
    *,
    user_id: str,
    topic: str,
    update_type: str,
    reason: str,
    evidence_id: str,
    confidence: float,
    existing: set[tuple[str, str]],
    created_at: str,
) -> None:
    topic = normalize_profile_value(topic)
    key = (update_type, topic.lower())
    if not topic or key in existing:
        return
    existing.add(key)
    digest = hashlib.sha1("|".join([user_id, update_type, topic.lower()]).encode("utf-8")).hexdigest()[:16]
    candidates.append(
        {
            "id": f"profile-{digest}",
            "user_id": user_id,
            "topic": topic,
            "update_type": update_type,
            "reason": reason,
            "evidence_id": evidence_id,
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "created_at": created_at,
        }
    )


def knowledge_snippet(text: Any, q: str, limit: int = 260) -> str:
    text = re_collapse_whitespace(str(text or ""))
    if not text:
        return ""
    q = q.strip().lower()
    if q:
        index = text.lower().find(q)
        if index >= 0:
            start = max(0, index - 70)
            end = min(len(text), index + len(q) + limit - 90)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            return prefix + text[start:end].strip() + suffix
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def re_collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def fts_query(value: str) -> str:
    terms = []
    for term in re.findall(r"[\w\u4e00-\u9fff]+", value.lower()):
        if len(term) < 2 and not re.search(r"[\u4e00-\u9fff]", term):
            continue
        if len(term) > 48:
            term = term[:48]
        terms.append(term)
    if not terms:
        return ""
    return " OR ".join(f'"{term}"*' for term in terms[:8])
