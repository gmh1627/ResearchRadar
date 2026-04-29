from __future__ import annotations

import os
from typing import Any

import httpx


async def answer_question(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any],
    item: dict[str, Any] | None,
    question: str,
    related_items: list[dict[str, Any]],
) -> str:
    llm = settings.get("llm", {})
    api_key = os.getenv(str(llm.get("api_key_env", "OPENROUTER_API_KEY")))
    base_url = os.getenv(str(llm.get("base_url_env", "OPENROUTER_BASE_URL")))
    if not api_key:
        api_key = os.getenv(str(llm.get("fallback_api_key_env", "OPENAI_API_KEY")))
        base_url = os.getenv(str(llm.get("fallback_base_url_env", "OPENAI_BASE_URL")))
    base_url = (base_url or str(llm.get("default_base_url", "https://openrouter.ai/api/v1"))).rstrip("/")
    model = os.getenv(str(llm.get("model_env", "OPENROUTER_MODEL")), str(llm.get("default_model", "openai/gpt-4o-mini")))
    if not api_key:
        return fallback_answer(profile=profile, item=item, question=question, related_items=related_items)

    source_text = await fetch_jina_text(settings, item.get("url")) if item else ""
    context = build_context(profile, item, related_items, source_text=source_text)
    messages = [
        {
            "role": "system",
            "content": (
                "You are ResearchRadar, a source-grounded AI research assistant. "
                "Answer in Chinese. Use the supplied item/profile context. "
                "If evidence is weak or missing, say so. Do not invent paper results."
            ),
        },
        {
            "role": "user",
            "content": f"用户问题：{question}\n\n上下文：\n{context}",
        },
    ]
    models = unique_models([model, "gpt-5.4", "openai/gpt-4o-mini", "gpt-4o-mini"])
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=90) as client:
        for candidate in models:
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": candidate, "messages": messages, "temperature": 0.2},
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"].strip()
                if content:
                    return content
            except Exception as exc:
                errors.append(f"{candidate}: {type(exc).__name__}")
    fallback = fallback_answer(profile=profile, item=item, question=question, related_items=related_items)
    return fallback + "\n\n大模型接口刚才不可用，已自动降级为本地回答。最近的调用错误：" + "；".join(errors[:3])


async def fetch_jina_text(settings: dict[str, Any], url: str | None) -> str:
    if not url:
        return ""
    jina = settings.get("jina", {})
    reader_url = str(jina.get("reader_url", "https://r.jina.ai/http://"))
    max_chars = int(jina.get("max_chars", 5000))
    api_key = os.getenv(str(jina.get("api_key_env", "JINA_API_KEY")))
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    endpoint = reader_url + url
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=True, headers=headers) as client:
            response = await client.get(endpoint)
            response.raise_for_status()
            return response.text[:max_chars]
    except Exception:
        return ""


async def serper_search(settings: dict[str, Any], query: str, num: int | None = None) -> list[dict[str, Any]]:
    serper = settings.get("serper", {})
    api_key = os.getenv(str(serper.get("api_key_env", "SERPER_API_KEY")))
    if not api_key:
        return []
    url = str(serper.get("search_url", "https://google.serper.dev/search"))
    count = int(num or serper.get("default_results", 8))
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            url,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": count},
        )
        response.raise_for_status()
        payload = response.json()
    return payload.get("organic", [])[:count]


def build_context(
    profile: dict[str, Any],
    item: dict[str, Any] | None,
    related_items: list[dict[str, Any]],
    *,
    source_text: str = "",
) -> str:
    parts = [
        "User profile:",
        f"- primary_topics: {', '.join(profile.get('primary_topics', []))}",
        f"- secondary_topics: {', '.join(profile.get('secondary_topics', []))}",
        f"- negative_topics: {', '.join(profile.get('negative_topics', []))}",
    ]
    if item:
        parts.extend(
            [
                "\nCurrent item:",
                f"- title: {item.get('title')}",
                f"- source: {item.get('source_name')} / {item.get('source_type')}",
                f"- url: {item.get('url')}",
                f"- tags: {', '.join(item.get('tags', []))}",
                f"- summary: {item.get('summary') or '(no summary)'}",
            ]
        )
    if source_text:
        parts.extend(["\nSource text fetched by Jina Reader:", source_text])
    if related_items:
        parts.append("\nRelated recent items:")
        for row in related_items[:6]:
            parts.append(f"- {row.get('title')} ({row.get('source_name')}): {row.get('summary', '')[:260]}")
    return "\n".join(parts)


def fallback_answer(
    *,
    profile: dict[str, Any],
    item: dict[str, Any] | None,
    question: str,
    related_items: list[dict[str, Any]],
) -> str:
    if not item:
        return (
            "当前没有绑定具体条目，也没有配置 LLM API key。你可以先在列表里打开一条论文或博客，"
            "再基于该条目提问；配置 OPENROUTER_API_KEY 后可启用完整大模型问答。"
        )
    tags = "、".join(item.get("tags", [])) or "暂无系统标签"
    topics = "、".join(profile.get("primary_topics", [])[:5])
    summary = item.get("summary") or "该来源没有提供摘要，建议打开原文查看细节。"
    related = "\n".join(f"- {row['title']} ({row['source_name']})" for row in related_items[:3])
    related = related or "- 暂无明显相关条目"
    return f"""当前未配置 LLM API key，因此先给你一个本地、基于来源文本的简要回答。

**条目**：{item.get('title')}

**和你的关系**：你的主关注方向包括 {topics}。系统给这条内容打的标签是：{tags}。如果这些标签与你当前课题吻合，可以先快速读摘要和原文引言。

**已有摘要**：{summary}

**针对你的问题**：{question}

我能确定的是：这条内容来自 {item.get('source_name')}，证据角色是 `{item.get('evidence_role')}`。如果它是论文，建议优先看方法和实验；如果是官方博客，建议检查是否有论文、模型卡、代码或 API 文档链接；如果是社区讨论，建议把它当作工程反馈而不是最终事实。

**相关近期条目**：
{related}

配置 `OPENROUTER_API_KEY`、`OPENROUTER_BASE_URL` 和 `OPENROUTER_MODEL` 后，这里会切换为完整的 source-grounded 大模型回答。"""


def unique_models(models: list[str]) -> list[str]:
    seen = set()
    out = []
    for model in models:
        model = (model or "").strip()
        if model and model not in seen:
            seen.add(model)
            out.append(model)
    return out
