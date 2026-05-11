from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .llm import LLMCallError, generate_text_sync


WIKI_SCHEMA = """# ResearchRadar LLM Wiki Schema

## Layers
- Raw sources: immutable items collected from arXiv, official blogs, GitHub, Hacker News, AIHOT and notes.
- Wiki pages: LLM-maintained markdown pages compiled from sources.
- Log: chronological append-only record of wiki compilation.

## Page Types
- index: catalog of all generated wiki pages.
- overview: current synthesis and open questions.
- concept: topic pages that merge evidence across sources.
- source: source-focused pages for major channels.

## Maintenance Rules
- Preserve source links and item ids as citations.
- Prefer updating an existing page over creating near-duplicate pages.
- Mention uncertainty, contradictions and freshness when evidence is weak.
- Good answers and notes can be filed back into wiki pages.
"""


STOP_TAGS = {"ai", "llm", "rag"}


def compile_wiki_pages(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any] | None,
    items: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
    existing_pages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    deterministic = build_deterministic_pages(
        profile=profile,
        items=items,
        notes=notes,
        conversations=conversations,
        existing_pages=existing_pages or [],
    )
    llm_pages = build_llm_pages(
        settings=settings,
        profile=profile,
        items=items,
        notes=notes,
        conversations=conversations,
        deterministic_pages=deterministic,
        existing_pages=existing_pages or [],
    )
    if llm_pages:
        by_slug = {page["slug"]: page for page in deterministic}
        by_slug.update({page["slug"]: page for page in llm_pages})
        return list(by_slug.values())
    return deterministic


def build_deterministic_pages(
    *,
    profile: dict[str, Any] | None,
    items: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
    existing_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    concept_items = top_concept_items(items)
    source_items = top_source_items(items)
    pages: list[dict[str, Any]] = []
    pages.append(build_overview_page(profile, items, notes, conversations, concept_items))
    pages.extend(build_concept_page(tag, grouped) for tag, grouped in concept_items[:8])
    pages.extend(build_source_page(source_key, grouped) for source_key, grouped in source_items[:6])
    pages.append(build_index_page(pages, existing_pages))
    return pages


def build_llm_pages(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any] | None,
    items: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
    deterministic_pages: list[dict[str, Any]],
    existing_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not items:
        return []
    prompt = {
        "schema": WIKI_SCHEMA,
        "profile": {
            "display_name": (profile or {}).get("display_name"),
            "primary_topics": (profile or {}).get("primary_topics", []),
            "secondary_topics": (profile or {}).get("secondary_topics", []),
        },
        "existing_pages": [
            {
                "slug": page.get("slug"),
                "title": page.get("title"),
                "page_type": page.get("page_type"),
                "summary": page.get("summary"),
            }
            for page in existing_pages[:30]
        ],
        "items": [item_digest(item) for item in items[:28]],
        "notes": [
            {
                "title": note.get("title"),
                "content": truncate_text(note.get("content", ""), 900),
                "tags": note.get("tags", []),
            }
            for note in notes[:10]
        ],
        "recent_questions": [
            {
                "question": conv.get("question"),
                "answer": truncate_text(conv.get("answer", ""), 700),
            }
            for conv in conversations[:8]
        ],
        "deterministic_page_slugs": [page["slug"] for page in deterministic_pages],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You maintain ResearchRadar's persistent LLM Wiki. "
                "Return Chinese markdown pages only as strict JSON. "
                "Use GPT-5.5 style careful synthesis: integrate sources, preserve citations, "
                "flag uncertainty and stale/contradictory claims. Do not invent facts."
            ),
        },
        {
            "role": "user",
            "content": (
                "根据下面材料更新个人 AI 研究 wiki。返回 JSON："
                "{\"pages\":[{\"slug\":\"...\",\"page_type\":\"overview|concept|source|index\","
                "\"title\":\"...\",\"summary\":\"...\",\"content\":\"markdown\","
                "\"tags\":[\"...\"],\"source_item_ids\":[\"...\"]}]}。"
                "最多 6 页，必须包含 overview。slug 用小写字母数字和连字符。\n\n"
                + json.dumps(prompt, ensure_ascii=False)
            ),
        },
    ]
    try:
        text = generate_text_sync(settings, messages, temperature=0.1, max_output_tokens=5000)
        payload = parse_json_object(text)
    except (LLMCallError, Exception):
        return []
    pages = []
    for raw in payload.get("pages", []) or []:
        if not isinstance(raw, dict):
            continue
        page = normalize_page(raw)
        if page:
            pages.append(page)
    return pages[:8]


def build_overview_page(
    profile: dict[str, Any] | None,
    items: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
    concept_items: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).date().isoformat()
    top_concepts = [tag for tag, _ in concept_items[:8]]
    lines = [
        "# ResearchRadar Wiki 总览",
        "",
        f"> 更新时间：{now}",
        "",
        "## 当前研究重心",
        "",
        bullet_join((profile or {}).get("primary_topics", [])[:8], "暂无画像主题。"),
        "",
        "## 最近沉淀",
        "",
        f"- 已纳入 {len(items)} 个读过/收藏/问答相关条目。",
        f"- 已纳入 {len(notes)} 条手动笔记和 {len(conversations)} 条问答记录。",
        "- 主要概念：" + ("、".join(top_concepts) if top_concepts else "暂无。"),
        "",
        "## 推荐维护动作",
        "",
        "- 将反复出现的标签沉淀成概念页。",
        "- 对同一方向的论文和代码条目做对照表。",
        "- 对缺少原文阅读的外部精选保留二级来源标记。",
    ]
    return {
        "slug": "overview",
        "page_type": "overview",
        "title": "ResearchRadar Wiki 总览",
        "summary": "长期沉淀的研究主题、资料和下一步维护动作。",
        "content": "\n".join(lines),
        "tags": top_concepts,
        "source_item_ids": [item["id"] for item in items[:20]],
    }


def build_concept_page(tag: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    slug = "concept-" + slugify(tag)
    lines = [
        f"# {tag}",
        "",
        "## 目前理解",
        "",
        synthesize_tag_summary(tag, items),
        "",
        "## 相关来源",
        "",
        *item_lines(items[:12]),
        "",
        "## 待追问",
        "",
        "- 这些来源之间是否有可复现的共同假设？",
        "- 是否已有代码、数据或 benchmark 能支撑进一步实验？",
    ]
    return {
        "slug": slug,
        "page_type": "concept",
        "title": tag,
        "summary": f"{tag} 相关的来源、笔记和待追问问题。",
        "content": "\n".join(lines),
        "tags": [tag],
        "source_item_ids": [item["id"] for item in items[:20]],
    }


def build_source_page(source_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    source_name = items[0].get("source_name") or source_key
    lines = [
        f"# {source_name}",
        "",
        "## 来源定位",
        "",
        source_position(items[0]),
        "",
        "## 最近条目",
        "",
        *item_lines(items[:14]),
    ]
    return {
        "slug": "source-" + slugify(source_key),
        "page_type": "source",
        "title": source_name,
        "summary": f"{source_name} 的近期信息沉淀。",
        "content": "\n".join(lines),
        "tags": sorted({tag for item in items[:20] for tag in visible_tags(item.get("tags", []))})[:12],
        "source_item_ids": [item["id"] for item in items[:20]],
    }


def build_index_page(pages: list[dict[str, Any]], existing_pages: list[dict[str, Any]]) -> dict[str, Any]:
    merged = {page["slug"]: page for page in existing_pages}
    merged.update({page["slug"]: page for page in pages})
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in merged.values():
        if page.get("slug") == "index":
            continue
        groups[page.get("page_type") or "other"].append(page)
    lines = ["# Wiki Index", "", "内容导向目录。每次编译会更新。", ""]
    labels = {"overview": "总览", "concept": "概念页", "source": "来源页", "other": "其他"}
    for page_type in ["overview", "concept", "source", "other"]:
        group = sorted(groups.get(page_type, []), key=lambda page: page.get("title", ""))
        if not group:
            continue
        lines.extend([f"## {labels.get(page_type, page_type)}", ""])
        for page in group:
            lines.append(f"- [[{page['slug']}|{page['title']}]]：{page.get('summary', '')}")
        lines.append("")
    return {
        "slug": "index",
        "page_type": "index",
        "title": "Wiki Index",
        "summary": "ResearchRadar wiki 的内容目录。",
        "content": "\n".join(lines).strip(),
        "tags": [],
        "source_item_ids": [],
    }


def top_concept_items(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        for tag in visible_tags(item.get("tags", []))[:8]:
            if tag.strip().lower() in STOP_TAGS:
                continue
            grouped[tag].append(item)
    return sorted(grouped.items(), key=lambda pair: (len(pair[1]), latest_timestamp(pair[1])), reverse=True)


def top_source_items(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[item.get("source_id") or item.get("source_name") or "source"].append(item)
    return sorted(grouped.items(), key=lambda pair: (len(pair[1]), latest_timestamp(pair[1])), reverse=True)


def item_digest(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "source_name": item.get("source_name"),
        "source_type": item.get("source_type"),
        "url": item.get("url"),
        "tags": visible_tags(item.get("tags", []))[:8],
        "summary": truncate_text(item.get("display_summary") or item.get("summary_zh") or item.get("summary") or "", 1000),
    }


def item_lines(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in items:
        title = item.get("title") or "Untitled"
        source = item.get("source_name") or item.get("source_id") or "source"
        url = item.get("url") or ""
        item_id = item.get("id") or ""
        summary = truncate_text(item.get("display_summary") or item.get("summary_zh") or item.get("summary") or "", 150)
        lines.append(f"- [{title}]({url}) `{item_id}` · {source}。{summary}")
    return lines or ["- 暂无来源。"]


def synthesize_tag_summary(tag: str, items: list[dict[str, Any]]) -> str:
    sources = Counter(item.get("source_type") or "other" for item in items)
    source_text = "、".join(f"{k} {v}" for k, v in sources.most_common())
    examples = "；".join((item.get("title") or "")[:80] for item in items[:3])
    return f"这个概念目前由 {len(items)} 个来源支撑，来源类型包括 {source_text or '未知'}。代表性条目：{examples or '暂无'}。"


def source_position(item: dict[str, Any]) -> str:
    role = item.get("evidence_role") or ""
    if role == "primary_research":
        return "正式研究来源，适合支撑方法、实验和深读判断。"
    if role == "official_update":
        return "官方更新来源，适合跟踪产品、模型和实验室动态。"
    if role == "curated_secondary_signal":
        return "外部精选，适合作为早期发现入口，需要打开原始来源核验。"
    return "用户已读或收藏的信息源，适合纳入长期知识沉淀。"


def visible_tags(tags: list[Any]) -> list[str]:
    out = []
    for tag in tags or []:
        text = str(tag).strip()
        if not text or text.lower() in STOP_TAGS:
            continue
        if text not in out:
            out.append(text)
    return out


def bullet_join(values: list[Any], empty: str) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return "\n".join(f"- {value}" for value in cleaned) if cleaned else empty


def latest_timestamp(items: list[dict[str, Any]]) -> str:
    return max(str(item.get("knowledge_at") or item.get("published_at") or item.get("collected_at") or "") for item in items)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:64] or "page"


def normalize_page(raw: dict[str, Any]) -> dict[str, Any] | None:
    slug = slugify(str(raw.get("slug") or raw.get("title") or ""))
    title = str(raw.get("title") or slug).strip()
    content = str(raw.get("content") or "").strip()
    if not slug or not title or not content:
        return None
    page_type = str(raw.get("page_type") or "concept")
    if page_type not in {"index", "overview", "concept", "source"}:
        page_type = "concept"
    return {
        "slug": slug,
        "page_type": page_type,
        "title": title,
        "summary": str(raw.get("summary") or "").strip(),
        "content": content,
        "tags": [str(tag).strip() for tag in raw.get("tags", []) if str(tag).strip()][:16],
        "source_item_ids": [str(item_id).strip() for item_id in raw.get("source_item_ids", []) if str(item_id).strip()][:40],
    }


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def truncate_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."
