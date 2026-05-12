from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date as dt_date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_config
from .crawler import CrawlManager
from .db import Database, encode_json
from .llm import answer_question, fallback_answer, llm_runtime_status, serper_search
from .ranker import rank_items
from .textutils import stable_id
from .translation import SummaryTranslator
from .wiki import compile_wiki_pages


config = load_config()
db = Database(config.db_path)
crawler = CrawlManager(config, db)

DIGEST_SECTIONS = [
    {
        "key": "papers",
        "label": "重点论文",
        "description": "arXiv 与正式研究条目，优先用于判断方法、实验和可深读价值。",
    },
    {
        "key": "official_updates",
        "label": "官方与实验室动态",
        "description": "公司、实验室和中文研究动态，适合快速捕捉能力、产品和研究线索。",
    },
    {
        "key": "code_tools",
        "label": "代码与工具",
        "description": "GitHub 项目、工程工具和可复现实验资源。",
    },
    {
        "key": "discussions",
        "label": "工程讨论",
        "description": "Hacker News 等社区讨论，主要看真实反馈、质疑和替代方案。",
    },
    {
        "key": "signals",
        "label": "外部精选",
        "description": "AIHOT 等外部精选流，作为发现 X / KOL / 媒体动态的二级入口。",
    },
    {
        "key": "other",
        "label": "其他值得扫读",
        "description": "暂不属于以上类别，但仍有一定相关性的近期条目。",
    },
]

HIDDEN_DISPLAY_TAGS = {"rag"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    db.mark_interrupted_crawl_days()
    scheduler_tasks = start_background_crawl_tasks()
    yield
    for task in scheduler_tasks:
        task.cancel()
    if scheduler_tasks:
        await asyncio.gather(*scheduler_tasks, return_exceptions=True)


def start_background_crawl_tasks() -> list[asyncio.Task]:
    crawl_settings = config.settings.get("crawl", {})
    if not crawl_settings.get("scheduler_enabled", True):
        return []
    lock_path = ROOT / "data" / "scheduler.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        if scheduler_lock_is_stale(lock_path):
            try:
                lock_path.unlink()
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
            except OSError:
                return []
        else:
            return []
    except OSError:
        return []

    async def release_lock_when_done(tasks: list[asyncio.Task]) -> None:
        try:
            await asyncio.gather(*tasks)
        finally:
            release_scheduler_lock(lock_path)

    scheduler = asyncio.create_task(crawler.scheduler_loop())
    catchup = asyncio.create_task(crawler.catch_up())
    guard = asyncio.create_task(release_lock_when_done([scheduler, catchup]))
    return [scheduler, catchup, guard]


def scheduler_lock_is_stale(path: Path) -> bool:
    try:
        pid_text = path.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except (OSError, ValueError):
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def release_scheduler_lock(path: Path) -> None:
    try:
        pid_text = path.read_text(encoding="utf-8").strip()
        if pid_text == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


app = FastAPI(title="ResearchRadar", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "researchradar" / "static"), name="static")


class CrawlRequest(BaseModel):
    days: int = 1
    mode: str = "recent"


class FeedbackRequest(BaseModel):
    user_id: str = "default"
    action: str
    note: str | None = None


class ChatRequest(BaseModel):
    user_id: str = "default"
    scope: str = "item"
    item_id: str | None = None
    question: str
    save_note: bool = False
    days: int = 7
    item_date: str = ""


class NoteRequest(BaseModel):
    user_id: str = "default"
    item_id: str | None = None
    title: str
    content: str
    tags: list[str] = []
    importance: int = 3


class WikiCompileRequest(BaseModel):
    user_id: str = "default"
    limit: int = 80


class WebSearchRequest(BaseModel):
    query: str
    num: int = 8


class ProfileCandidateDecisionRequest(BaseModel):
    user_id: str = "default"
    decision: str


class ProfileCandidateGenerateRequest(BaseModel):
    user_id: str = "default"
    limit: int = 12


@app.get("/")
async def index():
    return FileResponse(ROOT / "researchradar" / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "crawler": crawler.status, "stats": db.stats()}


@app.get("/api/llm-status")
async def llm_status():
    return llm_runtime_status(config.settings)


@app.get("/api/profiles")
async def profiles():
    return {"profiles": config.profiles}


@app.get("/api/stats")
async def stats():
    return db.stats()


@app.get("/api/sources")
async def sources():
    configured = {source["id"]: source for source in config.sources}
    configured["github"] = {
        "id": "github",
        "name": "GitHub",
        "type": "api",
        "homepage": "https://github.com/trending",
        "fallback_url": "https://github.com/OpenGithubs/github-daily-rank",
        "enabled": True,
    }
    configured["hackernews"] = {
        "id": "hackernews",
        "name": "Hacker News",
        "type": "api",
        "homepage": "https://hn.algolia.com/?q=LLM%20agent",
        "enabled": True,
    }
    latest = {row["source_id"]: row for row in db.source_status()}
    rows = []
    for source_id, source in configured.items():
        rows.append({"source": source, "latest": latest.get(source_id)})
    return {"sources": rows}


@app.get("/api/items")
async def items(
    days: int = 14,
    item_date: str = Query("", alias="date"),
    q: str = "",
    source_id: str = "",
    source_type: str = "",
    tag: str = "",
    limit: int = 80,
    offset: int = 0,
    translate_limit: int = 0,
):
    if item_date:
        validate_item_date(item_date)
    rows, total = db.query_items(
        days=max(1, min(days, 3650)),
        item_date=item_date,
        q=q,
        source_id=source_id,
        source_type=source_type,
        tag=tag,
        limit=min(limit, 300),
        offset=offset,
    )
    await ensure_translations(rows, max_count=max(0, min(translate_limit, 100)))
    rows = [with_display_summary(row) for row in rows]
    return {"items": rows, "total": total}


@app.get("/api/dates")
async def available_dates(
    days: int = 365,
    q: str = "",
    source_id: str = "",
    source_type: str = "",
    tag: str = "",
    limit: int = 366,
):
    rows = db.available_dates(
        days=max(0, min(days, 3650)),
        q=q,
        source_id=source_id,
        source_type=source_type,
        tag=tag,
        limit=max(1, min(limit, 1000)),
    )
    return {"dates": rows}


@app.get("/api/items/{item_id}")
async def item_detail(item_id: str, user_id: str = "default"):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    db.record_item_view(user_id, item_id)
    return with_display_summary(item)


@app.get("/api/digest")
async def digest(
    user_id: str = "default",
    days: int = 7,
    limit: int | None = None,
    item_date: str = Query("", alias="date"),
):
    profile = find_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    memory = db.list_profile_memory(user_id)
    profile = effective_profile(profile, memory)
    profile_version = profile_cache_version(profile, memory)
    if item_date:
        validate_item_date(item_date)
    max_items = max(1, min(limit or config.digest_item_count, 120))
    run_date = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    existing_run = None if item_date else db.get_digest_run(user_id, run_date, days, max_items, profile_version)
    if existing_run:
        existing_rows = existing_run["items"]
        existing_items = [with_display_summary(row) for row in existing_rows]
        sections = build_digest_sections(existing_items)
        return {
            "profile": profile,
            "items": existing_items,
            "sections": sections,
            "limit": max_items,
            "candidate_count": int(existing_run["meta"].get("candidate_count") or 0),
            "generated_at": existing_run["meta"]["created_at"],
            "date": "",
            "scope_label": f"近 {days} 天",
            "profile_version": profile_version,
        }
    rows, _ = db.query_items(item_date=item_date, days=days, limit=max(500, max_items * 12))
    ranked = rank_items(rows, profile, db.feedback_for_user(user_id), db.feedback_signals_for_user(user_id))
    db.update_item_scores(ranked)
    min_per_section = int(config.settings.get("ranking", {}).get("digest_min_items_per_section", 4))
    sent_fingerprints = set() if item_date else db.sent_digest_fingerprints(user_id)
    selected = select_digest_items(
        ranked,
        max_items=max_items,
        min_per_section=min_per_section,
        excluded_fingerprints=sent_fingerprints,
    )
    selected = [with_display_summary(row) for row in selected]
    if not item_date:
        db.create_digest_run(
            user_id=user_id,
            run_date=run_date,
            days=days,
            item_limit=max_items,
            profile_version=profile_version,
            candidate_count=len(rows),
            entries=[
                {"item_id": item["id"], "fingerprint": digest_item_fingerprint(item)}
                for item in selected
            ],
        )
    sections = build_digest_sections(selected)
    return {
        "profile": profile,
        "items": selected,
        "sections": sections,
        "limit": max_items,
        "candidate_count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": item_date,
        "scope_label": item_date or f"近 {days} 天",
        "profile_version": profile_version,
    }


@app.post("/api/crawl")
async def crawl(req: CrawlRequest, background: BackgroundTasks):
    days = max(1, min(req.days, 31))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    background.add_task(crawler.crawl_range, start, end, run_postprocess=True)
    return {"accepted": True, "start": start.isoformat(), "end": end.isoformat(), "crawler": crawler.status}


@app.post("/api/items/{item_id}/feedback")
async def feedback(item_id: str, req: FeedbackRequest):
    if not db.get_item(item_id):
        raise HTTPException(status_code=404, detail="item not found")
    if req.action not in {"like", "save", "deep_read", "ignore", "not_relevant"}:
        raise HTTPException(status_code=400, detail="invalid action")
    db.add_feedback(req.user_id, item_id, req.action, req.note)
    return {"ok": True}


@app.delete("/api/items/{item_id}/feedback")
async def delete_feedback(item_id: str, user_id: str = "default", action: str | None = None):
    removed = db.remove_feedback(user_id, item_id, action)
    return {"ok": True, "removed": removed}


@app.get("/api/notes")
async def list_notes(user_id: str = "default"):
    return {"notes": db.list_notes(user_id)}


@app.get("/api/conversations")
async def list_conversations(user_id: str = "default", limit: int = 80):
    return {"conversations": db.list_conversations(user_id, limit=max(1, min(limit, 200)))}


@app.get("/api/knowledge")
async def knowledge(user_id: str = "default"):
    profile = find_profile(user_id)
    memory = db.list_profile_memory(user_id)
    effective = effective_profile(profile, memory)
    stats = filter_knowledge_stats(db.knowledge_stats(user_id), effective)
    saved_items = [with_display_summary(row) for row in db.list_feedback_items(user_id, ["save", "deep_read"], limit=80)]
    return {
        "profile": effective,
        "stats": stats,
        "items": saved_items,
        "notes": db.list_notes(user_id),
        "conversations": db.list_conversations(user_id, limit=60),
        "wiki_pages": db.list_wiki_pages(user_id),
        "wiki_log": db.list_wiki_log(user_id, limit=20),
        "profile_candidates": db.list_profile_update_candidates(user_id, statuses=("pending",)),
        "profile_memory": memory,
    }


@app.get("/api/knowledge/search")
async def knowledge_search(user_id: str = "default", q: str = "", limit: int = 60):
    rows = db.search_knowledge(user_id=user_id, q=q, limit=max(1, min(limit, 120)))
    for row in rows:
        if row.get("item"):
            row["item"] = with_display_summary(row["item"])
    return {"results": rows}


@app.post("/api/profile-candidates/generate")
async def generate_profile_candidates(req: ProfileCandidateGenerateRequest):
    created = db.generate_profile_update_candidates(req.user_id, limit=max(1, min(req.limit, 40)))
    return {
        "ok": True,
        "created": created,
        "profile_candidates": db.list_profile_update_candidates(req.user_id, statuses=("pending",)),
        "profile_memory": db.list_profile_memory(req.user_id),
    }


@app.post("/api/profile-candidates/{candidate_id}")
async def decide_profile_candidate(candidate_id: str, req: ProfileCandidateDecisionRequest):
    if req.decision not in {"accept", "reject"}:
        raise HTTPException(status_code=400, detail="decision must be accept or reject")
    candidate = db.decide_profile_candidate(req.user_id, candidate_id, req.decision)
    if not candidate:
        raise HTTPException(status_code=404, detail="candidate not found")
    return {
        "ok": True,
        "candidate": candidate,
        "profile_candidates": db.list_profile_update_candidates(req.user_id, statuses=("pending",)),
        "profile_memory": db.list_profile_memory(req.user_id),
    }


@app.get("/api/knowledge/graph")
async def knowledge_graph(user_id: str = "default", limit: int = 80):
    items = [with_display_summary(row) for row in db.knowledge_graph_items(user_id, limit=max(10, min(limit, 160)))]
    return build_knowledge_graph(items)


@app.post("/api/knowledge/compile")
async def compile_knowledge(req: WikiCompileRequest):
    profile = find_profile(req.user_id)
    memory = db.list_profile_memory(req.user_id)
    profile = effective_profile(profile, memory)
    items = [with_display_summary(row) for row in db.knowledge_graph_items(req.user_id, limit=max(20, min(req.limit, 160)))]
    notes = db.list_notes(req.user_id)
    conversations = db.list_conversations(req.user_id, limit=40)
    existing = db.list_wiki_pages(req.user_id)
    pages = await asyncio.to_thread(
        compile_wiki_pages,
        settings=config.settings,
        profile=profile,
        items=items,
        notes=notes,
        conversations=conversations,
        existing_pages=existing,
    )
    changed = db.upsert_wiki_pages(req.user_id, pages, event_title="compile knowledge wiki")
    return {"ok": True, "changed": changed, "pages": db.list_wiki_pages(req.user_id), "wiki_log": db.list_wiki_log(req.user_id)}


@app.post("/api/notes")
async def create_note(req: NoteRequest):
    note = {
        "id": stable_id(req.user_id, req.item_id or "", req.title, datetime.now(timezone.utc).isoformat()),
        "user_id": req.user_id,
        "item_id": req.item_id,
        "title": req.title,
        "content": req.content,
        "tags_json": encode_json(req.tags),
        "importance": req.importance,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.add_note(note)
    return {"ok": True, "note": note}


@app.delete("/api/notes/{note_id}")
async def delete_note(note_id: str, user_id: str = "default"):
    removed = db.delete_note(user_id, note_id)
    return {"ok": True, "removed": removed}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user_id: str = "default"):
    removed = db.delete_conversation(user_id, conversation_id)
    return {"ok": True, "removed": removed}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    profile = find_profile(req.user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    profile = effective_profile(profile, db.list_profile_memory(req.user_id))
    item = db.get_item(req.item_id) if req.item_id else None
    scope = req.scope if req.scope in {"item", "digest", "knowledge"} else "item"
    if req.item_date:
        validate_item_date(req.item_date)
    related, context_note = chat_context(req.user_id, profile, item, scope=scope, days=req.days, item_date=req.item_date)
    chat_timeout = float(config.settings.get("llm", {}).get("chat_timeout_seconds", 30))
    try:
        answer = await asyncio.wait_for(
            answer_question(
                settings=config.settings,
                profile=profile,
                item=item,
                question=req.question,
                related_items=related,
                scope=scope,
                context_note=context_note,
            ),
            timeout=max(8, chat_timeout),
        )
    except asyncio.TimeoutError:
        answer = (
            fallback_answer(
                profile=profile,
                item=item,
                question=req.question,
                related_items=related,
                reason="timeout",
                scope=scope,
                context_note=context_note,
            )
            + "\n\n外部模型或原文读取超过了本次等待上限，已先返回本地降级回答。你可以稍后重试获取更完整的大模型分析。"
        )
    except Exception as exc:
        answer = (
            "大模型接口暂时不可用，但页面不会再中断。\n\n"
            f"错误类型：{type(exc).__name__}\n"
            "你可以稍后重试，或者先打开原文查看。"
        )
    conv = {
        "id": stable_id(req.user_id, req.item_id or "", req.question, datetime.now(timezone.utc).isoformat()),
        "user_id": req.user_id,
        "scope": scope,
        "item_id": req.item_id,
        "question": req.question,
        "answer": answer,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.add_conversation(conv)
    if req.save_note:
        note = distilled_chat_note(
            user_id=req.user_id,
            item=item,
            scope=scope,
            question=req.question,
            answer=answer,
            related_items=related,
            context_note=context_note,
            conversation_id=conv["id"],
        )
        db.add_note(
            {
                "id": note["id"],
                "user_id": note["user_id"],
                "item_id": note["item_id"],
                "title": note["title"],
                "content": note["content"],
                "tags_json": encode_json(note["tags"]),
                "importance": note["importance"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return {"answer": answer, "conversation": conv}


@app.post("/api/web-search")
async def web_search(req: WebSearchRequest):
    results = await serper_search(config.settings, req.query, req.num)
    return {"results": results}


def find_profile(user_id: str) -> dict[str, Any] | None:
    for profile in config.profiles:
        if profile.get("user_id") == user_id:
            return profile
    return config.profiles[0] if config.profiles and user_id == "default" else None


def chat_context(
    user_id: str,
    profile: dict[str, Any],
    item: dict[str, Any] | None,
    *,
    scope: str,
    days: int,
    item_date: str = "",
) -> tuple[list[dict[str, Any]], str]:
    if scope == "digest":
        profile = effective_profile(profile, db.list_profile_memory(user_id))
        rows, _ = db.query_items(item_date=item_date, days=max(1, min(days, 60)), limit=420)
        ranked = rank_items(rows, profile, db.feedback_for_user(user_id), db.feedback_signals_for_user(user_id))
        selected = select_digest_items(
            ranked,
            max_items=min(config.digest_item_count, 36),
            min_per_section=int(config.settings.get("ranking", {}).get("digest_min_items_per_section", 4)),
            excluded_fingerprints=set(),
        )
        selected = [with_display_summary(row) for row in selected]
        sections = build_digest_sections(selected)
        return selected[:18], digest_context_text(sections, selected, days, item_date=item_date)

    if scope == "knowledge":
        items = [with_display_summary(row) for row in db.knowledge_graph_items(user_id, limit=60)]
        notes = db.list_notes(user_id)[:20]
        conversations = db.list_conversations(user_id, limit=20)
        wiki_pages = db.list_wiki_pages(user_id)[:20]
        memory = db.list_profile_memory(user_id)
        return items[:18], knowledge_context_text(items, notes, conversations, wiki_pages, memory)

    related, _ = db.query_items(days=21, q=" ".join((item or {}).get("tags", [])[:2]), limit=8)
    return related, ""


def digest_context_text(
    sections: list[dict[str, Any]],
    items: list[dict[str, Any]],
    days: int,
    *,
    item_date: str = "",
) -> str:
    lines = [
        f"Digest window: {item_date if item_date else f'last {days} days'}.",
        f"Selected items: {len(items)}.",
    ]
    for section in sections:
        lines.append(f"\nSection: {section['label']} ({section['count']})")
        for item in section["items"][:5]:
            lines.append(
                "- "
                + str(item.get("title") or "Untitled")
                + f" | score={format_context_score(item.get('score'))}"
                + f" | source={item.get('source_name')}"
                + f" | reason={item.get('relevance_reason') or ''}"
            )
    return "\n".join(lines)[:9000]


def knowledge_context_text(
    items: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
    wiki_pages: list[dict[str, Any]],
    memory: list[dict[str, Any]],
) -> str:
    lines = [
        f"Knowledge items: {len(items)}. Notes: {len(notes)}. Conversations: {len(conversations)}. Wiki pages: {len(wiki_pages)}.",
    ]
    if memory:
        lines.append("\nAccepted profile memory:")
        for row in memory[:20]:
            lines.append(f"- {row['memory_key']}: {row['memory_value']} ({format_context_score(row.get('weight'))})")
    if wiki_pages:
        lines.append("\nWiki pages:")
        for page in wiki_pages[:10]:
            lines.append(f"- {page['title']} [{page['page_type']}]: {page.get('summary') or page.get('content', '')[:180]}")
    if notes:
        lines.append("\nRecent notes:")
        for note in notes[:10]:
            lines.append(f"- {note['title']}: {str(note.get('content') or '')[:220]}")
    if conversations:
        lines.append("\nRecent Q&A:")
        for conv in conversations[:8]:
            lines.append(f"- Q: {conv.get('question')} | A: {str(conv.get('answer') or '')[:220]}")
    if items:
        lines.append("\nKnowledge-linked items:")
        for item in items[:14]:
            lines.append(f"- {item.get('title')} ({item.get('source_name')}): {str(item.get('display_summary') or '')[:220]}")
    return "\n".join(lines)[:10000]


def format_context_score(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return str(round(number * 100 if number <= 1 else number))


def effective_profile(profile: dict[str, Any], memory_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    merged = {**(profile or {})}
    fields = {
        "interest": "primary_topics",
        "negative": "negative_topics",
        "preferred_source": "preferred_sources",
        "deprioritized_source": "deprioritized_sources",
    }
    for row in memory_rows or []:
        key = fields.get(str(row.get("memory_key") or ""))
        value = str(row.get("memory_value") or "").strip()
        if not key or not value:
            continue
        values = list(merged.get(key) or [])
        if value not in values:
            values.append(value)
        merged[key] = values
    return merged


def profile_cache_version(profile: dict[str, Any], memory_rows: list[dict[str, Any]] | None = None) -> str:
    payload = {
        "user_id": profile.get("user_id"),
        "primary_topics": profile.get("primary_topics", []),
        "secondary_topics": profile.get("secondary_topics", []),
        "negative_topics": profile.get("negative_topics", []),
        "preferred_sources": profile.get("preferred_sources", []),
        "deprioritized_sources": profile.get("deprioritized_sources", []),
        "memory": [
            {
                "key": row.get("memory_key"),
                "value": row.get("memory_value"),
                "weight": round(float(row.get("weight") or 0), 4),
                "created_at": row.get("created_at"),
            }
            for row in (memory_rows or [])
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def distilled_chat_note(
    *,
    user_id: str,
    item: dict[str, Any] | None,
    scope: str,
    question: str,
    answer: str,
    related_items: list[dict[str, Any]],
    context_note: str,
    conversation_id: str,
) -> dict[str, Any]:
    source_title = (item or {}).get("title") or scope_note_source_title(scope)
    tags = note_tags_for_chat(scope, item, related_items)
    title = f"{scope_note_prefix(scope)}：{truncate_plain(question or source_title, 64)}"
    item_lines = []
    if item:
        item_lines.append(f"- `{item.get('id')}` {item.get('title')} ({item.get('source_name')})")
    for row in related_items[:8]:
        item_lines.append(f"- `{row.get('id')}` {row.get('title')} ({row.get('source_name')})")
    context_excerpt = truncate_plain(context_note, 900)
    answer_excerpt = truncate_answer_for_note(answer)
    content_parts = [
        "# 蒸馏笔记",
        "",
        f"## 问题",
        "",
        question.strip(),
        "",
        "## 结论摘要",
        "",
        answer_excerpt,
    ]
    if item_lines:
        content_parts.extend(["", "## 相关来源", "", *dedupe_preserve_order(item_lines)[:10]])
    if context_excerpt:
        content_parts.extend(["", "## 上下文摘录", "", context_excerpt])
    content_parts.extend(["", "## 追溯", "", f"- scope: `{scope}`", f"- conversation: `{conversation_id}`"])
    return {
        "id": stable_id(user_id, scope, question, conversation_id),
        "user_id": user_id,
        "item_id": (item or {}).get("id") if scope == "item" else None,
        "title": title,
        "content": "\n".join(content_parts).strip(),
        "tags": tags,
        "importance": 4 if scope in {"digest", "knowledge"} else 3,
    }


def truncate_answer_for_note(answer: str, limit: int = 1400) -> str:
    text = answer.strip()
    lines = [line.rstrip() for line in text.splitlines()]
    useful = []
    for line in lines:
        if line.strip().startswith("如果你希望每次都拿到完整的大模型回答"):
            break
        useful.append(line)
    text = "\n".join(useful).strip() or answer.strip()
    return truncate_plain(text, limit)


def note_tags_for_chat(scope: str, item: dict[str, Any] | None, related_items: list[dict[str, Any]]) -> list[str]:
    tags = [f"scope:{scope}", "chat-note"]
    if item:
        tags.extend(item.get("tags", [])[:8])
    if scope in {"digest", "knowledge"}:
        for row in related_items[:8]:
            tags.extend(row.get("tags", [])[:4])
    return dedupe_preserve_order(str(tag) for tag in tags if str(tag).strip())[:14]


def scope_note_prefix(scope: str) -> str:
    return {"digest": "日报追问", "knowledge": "知识库追问"}.get(scope, "条目追问")


def scope_note_source_title(scope: str) -> str:
    return {"digest": "当前日报", "knowledge": "知识库"}.get(scope, "条目")


def truncate_plain(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "..."


def dedupe_preserve_order(values) -> list[str]:
    out = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def build_digest_sections(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = {section["key"]: [] for section in DIGEST_SECTIONS}
    for item in items:
        grouped[digest_section_key(item)].append(item)
    return [
        {
            **section,
            "items": grouped[section["key"]],
            "count": len(grouped[section["key"]]),
        }
        for section in DIGEST_SECTIONS
        if grouped[section["key"]]
    ]


def select_digest_items(
    ranked: list[dict[str, Any]],
    *,
    max_items: int,
    min_per_section: int,
    excluded_fingerprints: set[str] | None = None,
) -> list[dict[str, Any]]:
    grouped = {section["key"]: [] for section in DIGEST_SECTIONS}
    for item in ranked:
        grouped[digest_section_key(item)].append(item)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_fingerprints: set[str] = set(excluded_fingerprints or set())

    def add(item: dict[str, Any]) -> None:
        item_id = item.get("id")
        fingerprint = digest_item_fingerprint(item)
        if len(selected) >= max_items or not item_id or item_id in selected_ids or fingerprint in selected_fingerprints:
            return
        selected.append(item)
        selected_ids.add(item_id)
        selected_fingerprints.add(fingerprint)

    if min_per_section > 0:
        for section in DIGEST_SECTIONS:
            for item in grouped[section["key"]][:min_per_section]:
                add(item)

    for item in ranked:
        add(item)
        if len(selected) >= max_items:
            break

    return sorted(selected, key=lambda row: row.get("score", 0), reverse=True)


def filter_knowledge_stats(stats: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    negative_terms = set(HIDDEN_DISPLAY_TAGS)
    if profile:
        negative_terms.update(str(term).strip().lower() for term in profile.get("negative_topics", []) if str(term).strip())
    filtered_tags = [
        row
        for row in stats.get("top_tags", [])
        if str(row.get("tag", "")).strip().lower() not in negative_terms
    ]
    return {**stats, "top_tags": filtered_tags}


def build_knowledge_graph(items: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    tag_to_items: dict[str, list[str]] = {}
    source_to_items: dict[str, list[str]] = {}
    topic_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    for item in items:
        item_id = item["id"]
        tags = visible_tags(item.get("tags", []))
        signal = int(item.get("view_count") or 0) + int(item.get("feedback_count") or 0) * 2
        signal += int(item.get("conversation_count") or 0) * 2 + int(item.get("note_count") or 0) * 2
        nodes.append(
            {
                "id": item_id,
                "label": graph_label(item),
                "title": item.get("title"),
                "source_name": item.get("source_name"),
                "source_type": item.get("source_type"),
                "url": item.get("url"),
                "tags": tags,
                "score": item.get("score"),
                "weight": min(34, 12 + signal * 3 + len(tags)),
                "timestamp": item.get("knowledge_at") or item.get("display_timestamp"),
                "node_type": "item",
            }
        )
        for tag in tags[:8]:
            tag_to_items.setdefault(tag, []).append(item_id)
            topic_counts[tag] = topic_counts.get(tag, 0) + 1
        source_key = item.get("source_id") or item.get("source_name") or "source"
        source_to_items.setdefault(source_key, []).append(item_id)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1

    top_tags = sorted(tag_to_items.items(), key=lambda pair: len(pair[1]), reverse=True)[:14]
    top_sources = sorted(source_to_items.items(), key=lambda pair: len(pair[1]), reverse=True)[:10]
    hub_ids: set[str] = set()

    for tag, ids in top_tags:
        hub_id = "topic:" + graph_slug(tag)
        hub_ids.add(hub_id)
        nodes.append(
            {
                "id": hub_id,
                "label": tag,
                "title": tag,
                "source_name": "概念",
                "source_type": "topic",
                "node_type": "topic",
                "tags": [tag],
                "weight": min(44, 18 + len(ids) * 1.7),
                "count": len(ids),
            }
        )
        for item_id in ids[:28]:
            add_graph_edge(edges, hub_id, item_id, "tag", tag, weight=2.8)

    for source, ids in top_sources:
        label = source_label(source, items)
        hub_id = "source:" + graph_slug(source)
        hub_ids.add(hub_id)
        nodes.append(
            {
                "id": hub_id,
                "label": label,
                "title": label,
                "source_name": "来源",
                "source_type": "source_hub",
                "node_type": "source",
                "tags": [],
                "weight": min(38, 15 + len(ids) * 1.25),
                "count": len(ids),
            }
        )
        for item_id in ids[:20]:
            add_graph_edge(edges, hub_id, item_id, "source", label, weight=1.4)

    for item in items:
        item_tags = [tag for tag in visible_tags(item.get("tags", []))[:5] if topic_counts.get(tag, 0) >= 2]
        for a, b in zip(item_tags, item_tags[1:]):
            source = "topic:" + graph_slug(a)
            target = "topic:" + graph_slug(b)
            if source in hub_ids and target in hub_ids:
                add_graph_edge(edges, source, target, "co_topic", f"{a} / {b}", weight=0.7)

    return {
        "nodes": nodes,
        "edges": list(edges.values()),
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "tag_count": len(tag_to_items),
            "topic_count": len(top_tags),
            "source_count": len(top_sources),
        },
    }


def add_graph_edge(edges: dict[tuple[str, str], dict[str, Any]], source: str, target: str, kind: str, label: str, weight: float) -> None:
    if source == target:
        return
    key = tuple(sorted((source, target)))
    if key not in edges:
        edges[key] = {"source": source, "target": target, "weight": 0.0, "reasons": []}
    edges[key]["weight"] += weight
    if len(edges[key]["reasons"]) < 3:
        edges[key]["reasons"].append({"kind": kind, "label": label})


def graph_label(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "Untitled")
    authors = item.get("authors") or []
    year = ""
    timestamp = item.get("published_at") or item.get("collected_at") or ""
    if timestamp:
        year = timestamp[:4]
    if authors:
        surname = str(authors[0]).split()[-1].strip(",")
        return f"{surname}, {year}" if year else surname
    return title[:34] + ("..." if len(title) > 34 else "")


def graph_slug(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:64] or "node"


def source_label(source: str, items: list[dict[str, Any]]) -> str:
    for item in items:
        if (item.get("source_id") or item.get("source_name") or "source") == source:
            return str(item.get("source_name") or source)
    return str(source)


def digest_section_key(item: dict[str, Any]) -> str:
    source_id = item.get("source_id")
    source_type = item.get("source_type")
    evidence_role = item.get("evidence_role")
    if source_type == "paper" or evidence_role == "primary_research":
        return "papers"
    if source_id == "github" or source_type == "repo" or evidence_role == "code_signal":
        return "code_tools"
    if source_id == "hackernews" or source_type == "discussion" or evidence_role == "engineering_discussion":
        return "discussions"
    if source_id == "aihot_public" or source_type == "signal" or evidence_role == "curated_secondary_signal":
        return "signals"
    if source_type in {"blog", "cn_community"} or evidence_role in {"official_update", "lab_update", "cn_research_update"}:
        return "official_updates"
    return "other"


def digest_item_fingerprint(item: dict[str, Any]) -> str:
    title = re.sub(r"\s+", " ", str(item.get("title") or "").strip().lower())
    source_type = str(item.get("source_type") or "")
    source_id = str(item.get("source_id") or "")
    url = canonical_digest_url(item.get("url"))
    if source_type in {"blog", "discussion", "cn_community", "signal"}:
        return f"url:{url}" if url else f"title:{title}"
    if source_id == "github" and url:
        return f"github:{url}"
    if url:
        return f"url:{url}"
    return f"title:{title}"


def canonical_digest_url(url: Any) -> str:
    value = str(url or "").strip().lower().rstrip("/")
    if not value:
        return ""
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    value = re.sub(r"[?#].*$", "", value)
    value = value.replace("/index.html", "").rstrip("/")
    if "/category/" in value:
        value = value.split("/category/", 1)[0]
    if "/tags/" in value:
        value = value.split("/tags/", 1)[0]
    if "/tag/" in value:
        value = value.split("/tag/", 1)[0]
    return value


def validate_item_date(value: str) -> None:
    try:
        dt_date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


def with_display_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary_zh = (item.get("summary_zh") or "").strip()
    if not summary_zh:
        if item.get("summary"):
            summary_zh = "中文摘要生成中，请稍后刷新。"
        else:
            summary_zh = "暂无来源摘要，建议打开原文查看细节。"
    date_value = item.get("published_at") or item.get("collected_at")
    date_kind = infer_date_kind(item)
    return {
        **item,
        "score": item.get("quality_score"),
        "tags": visible_tags(item.get("tags", [])),
        "display_summary": summary_zh,
        "date_kind": date_kind,
        "display_timestamp": date_value,
        "evidence_links": build_evidence_links(item),
    }


def visible_tags(tags: list[Any] | tuple[Any, ...] | None) -> list[str]:
    values: list[str] = []
    for tag in tags or []:
        value = str(tag).strip()
        if value and value.lower() not in HIDDEN_DISPLAY_TAGS:
            values.append(value)
    return values


def build_evidence_links(item: dict[str, Any]) -> list[dict[str, str]]:
    metadata = item.get("metadata", {}) or {}
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, url: str | None) -> None:
        if not url:
            return
        normalized = url.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        links.append({"label": label, "url": normalized})

    source_label = {
        "paper": "原文",
        "repo": "代码仓库",
        "discussion": "讨论",
        "blog": "来源",
        "cn_community": "来源",
    }.get(item.get("source_type"), "来源")
    add(source_label, item.get("url"))
    add("AIHOT 页", metadata.get("aihot_page"))
    add("PDF", metadata.get("pdf_url"))
    if metadata.get("arxiv_id"):
        add("arXiv", f"https://arxiv.org/abs/{metadata['arxiv_id']}")
    add("HN", metadata.get("hn_url"))
    return links


def infer_date_kind(item: dict[str, Any]) -> str:
    if not item.get("published_at"):
        return "discovered"
    if item.get("source_id") == "github":
        return "updated"
    return "published"


async def ensure_translations(rows: list[dict[str, Any]], max_count: int = 30) -> None:
    missing = [row for row in rows if not (row.get("summary_zh") or "").strip()][:max_count]
    if not missing:
        return

    def translate_and_update() -> None:
        translator = SummaryTranslator(config.settings)
        for start in range(0, len(missing), 8):
            batch = missing[start : start + 8]
            summaries = translator.translate_rows(batch)
            for row, zh in zip(batch, summaries):
                row["summary_zh"] = zh
                db.update_summary_zh(row["id"], zh)

    await asyncio.to_thread(translate_and_update)
