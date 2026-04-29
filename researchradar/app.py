from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_config
from .crawler import CrawlManager
from .db import Database, encode_json
from .llm import answer_question, serper_search
from .ranker import rank_items
from .textutils import stable_id
from .translation import SummaryTranslator


config = load_config()
db = Database(config.db_path)
crawler = CrawlManager(config, db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    asyncio.create_task(crawler.catch_up())
    asyncio.create_task(crawler.scheduler_loop())
    yield


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


class NoteRequest(BaseModel):
    user_id: str = "default"
    item_id: str | None = None
    title: str
    content: str
    tags: list[str] = []
    importance: int = 3


class WebSearchRequest(BaseModel):
    query: str
    num: int = 8


@app.get("/")
async def index():
    return FileResponse(ROOT / "researchradar" / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "crawler": crawler.status, "stats": db.stats()}


@app.get("/api/profiles")
async def profiles():
    return {"profiles": config.profiles}


@app.get("/api/stats")
async def stats():
    return db.stats()


@app.get("/api/sources")
async def sources():
    configured = {source["id"]: source for source in config.sources}
    configured["github"] = {"id": "github", "name": "GitHub", "type": "api", "enabled": True}
    configured["hackernews"] = {"id": "hackernews", "name": "Hacker News", "type": "api", "enabled": True}
    latest = {row["source_id"]: row for row in db.source_status()}
    rows = []
    for source_id, source in configured.items():
        rows.append({"source": source, "latest": latest.get(source_id)})
    return {"sources": rows}


@app.get("/api/items")
async def items(
    days: int = 14,
    q: str = "",
    source_id: str = "",
    source_type: str = "",
    tag: str = "",
    limit: int = 80,
    offset: int = 0,
):
    rows, total = db.query_items(
        days=days,
        q=q,
        source_id=source_id,
        source_type=source_type,
        tag=tag,
        limit=min(limit, 300),
        offset=offset,
    )
    await ensure_translations(rows, max_count=30)
    rows = [with_display_summary(row) for row in rows]
    return {"items": rows, "total": total}


@app.get("/api/items/{item_id}")
async def item_detail(item_id: str):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return with_display_summary(item)


@app.get("/api/digest")
async def digest(user_id: str = "default", days: int = 7, limit: int | None = None):
    profile = find_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    rows, _ = db.query_items(days=days, limit=400)
    ranked = rank_items(rows, profile, db.feedback_for_user(user_id))
    await ensure_translations(ranked[: (limit or config.digest_item_count)], max_count=30)
    ranked = [with_display_summary(row) for row in ranked]
    return {
        "profile": profile,
        "items": ranked[: (limit or config.digest_item_count)],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/crawl")
async def crawl(req: CrawlRequest, background: BackgroundTasks):
    days = max(1, min(req.days, 31))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    background.add_task(crawler.crawl_range, start, end)
    return {"accepted": True, "start": start.isoformat(), "end": end.isoformat(), "crawler": crawler.status}


@app.post("/api/items/{item_id}/feedback")
async def feedback(item_id: str, req: FeedbackRequest):
    if not db.get_item(item_id):
        raise HTTPException(status_code=404, detail="item not found")
    if req.action not in {"like", "save", "deep_read", "ignore", "not_relevant"}:
        raise HTTPException(status_code=400, detail="invalid action")
    db.add_feedback(req.user_id, item_id, req.action, req.note)
    return {"ok": True}


@app.get("/api/notes")
async def list_notes(user_id: str = "default"):
    return {"notes": db.list_notes(user_id)}


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


@app.post("/api/chat")
async def chat(req: ChatRequest):
    profile = find_profile(req.user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    item = db.get_item(req.item_id) if req.item_id else None
    related, _ = db.query_items(days=21, q=" ".join((item or {}).get("tags", [])[:2]), limit=8)
    try:
        answer = await answer_question(
            settings=config.settings,
            profile=profile,
            item=item,
            question=req.question,
            related_items=related,
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
        "scope": req.scope,
        "item_id": req.item_id,
        "question": req.question,
        "answer": answer,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.add_conversation(conv)
    if req.save_note:
        title = f"Research note: {(item or {}).get('title', req.question)[:80]}"
        db.add_note(
            {
                "id": stable_id(req.user_id, title, conv["id"]),
                "user_id": req.user_id,
                "item_id": req.item_id,
                "title": title,
                "content": answer,
                "tags_json": encode_json((item or {}).get("tags", [])),
                "importance": 3,
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


def with_display_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary_zh = (item.get("summary_zh") or "").strip()
    if not summary_zh:
        if item.get("summary"):
            summary_zh = "中文摘要生成中，请稍后刷新。"
        else:
            summary_zh = "暂无来源摘要，建议打开原文查看细节。"
    return {**item, "display_summary": summary_zh}


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
