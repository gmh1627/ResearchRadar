from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    wire_api: str
    api_key_env: str
    base_url_env: str
    model_env: str
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str
    disable_response_storage: bool
    request_timeout: float
    max_model_attempts: int
    max_output_tokens: int


class LLMCallError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:3]) if errors else "LLM call failed")
        self.errors = errors


def llm_runtime_status(settings: dict[str, Any]) -> dict[str, Any]:
    cfg = resolve_llm_config(settings)
    llm = settings.get("llm", {})
    return {
        "configured": bool(cfg.api_key),
        "provider": cfg.provider,
        "wire_api": cfg.wire_api,
        "api_key_env": cfg.api_key_env,
        "api_key_present": bool(cfg.api_key),
        "base_url_env": cfg.base_url_env,
        "base_url": cfg.base_url,
        "model_env": cfg.model_env,
        "model": cfg.model,
        "reasoning_effort": cfg.reasoning_effort,
        "disable_response_storage": cfg.disable_response_storage,
        "chat_timeout_seconds": llm.get("chat_timeout_seconds"),
        "request_timeout_seconds": cfg.request_timeout,
        "max_model_attempts": cfg.max_model_attempts,
    }


async def answer_question(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any],
    item: dict[str, Any] | None,
    question: str,
    related_items: list[dict[str, Any]],
    scope: str = "item",
    context_note: str = "",
) -> str:
    cfg = resolve_llm_config(settings)
    if not cfg.api_key:
        return fallback_answer(
            profile=profile,
            item=item,
            question=question,
            related_items=related_items,
            reason="no_key",
            scope=scope,
            context_note=context_note,
        )

    source_text = await fetch_jina_text(settings, item.get("url")) if item else ""
    context = build_context(profile, item, related_items, source_text=source_text, scope=scope, context_note=context_note)
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
    try:
        content = await generate_text(settings, messages, temperature=0.2)
        if content:
            return content
    except LLMCallError as exc:
        errors = exc.errors
    except Exception as exc:
        errors = [f"{type(exc).__name__}: {str(exc)[:180]}"]

    fallback = fallback_answer(
        profile=profile,
        item=item,
        question=question,
        related_items=related_items,
        reason="llm_unavailable",
        scope=scope,
        context_note=context_note,
    )
    return fallback + "\n\n大模型接口刚才不可用，已自动降级为本地回答。最近的调用错误：" + "；".join(errors[:3])


async def generate_text(
    settings: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float | None = 0.2,
    max_output_tokens: int | None = None,
) -> str:
    cfg = resolve_llm_config(settings)
    if not cfg.api_key:
        raise LLMCallError(["missing API key"])
    errors: list[str] = []
    timeout = httpx.Timeout(
        cfg.request_timeout,
        connect=min(8.0, cfg.request_timeout),
        read=cfg.request_timeout,
        write=min(8.0, cfg.request_timeout),
        pool=5.0,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        for model in candidate_models(settings, cfg):
            for wire_api in candidate_wire_apis(cfg):
                try:
                    if wire_api == "responses":
                        content = await call_responses_api(client, cfg, model, messages, temperature, max_output_tokens)
                    else:
                        content = await call_chat_stream_api(client, cfg, model, messages, temperature, max_output_tokens)
                    if content.strip():
                        return content.strip()
                    errors.append(f"{model}/{wire_api}: empty response")
                except httpx.HTTPStatusError as exc:
                    detail = exc.response.text[:260].replace("\n", " ")
                    errors.append(f"{model}/{wire_api}: HTTP {exc.response.status_code} {detail}")
                except Exception as exc:
                    errors.append(f"{model}/{wire_api}: {type(exc).__name__} {str(exc)[:180]}")
    raise LLMCallError(errors)


def generate_text_sync(
    settings: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float | None = 0.2,
    max_output_tokens: int | None = None,
) -> str:
    cfg = resolve_llm_config(settings)
    if not cfg.api_key:
        raise LLMCallError(["missing API key"])
    errors: list[str] = []
    timeout = httpx.Timeout(
        cfg.request_timeout,
        connect=min(8.0, cfg.request_timeout),
        read=cfg.request_timeout,
        write=min(8.0, cfg.request_timeout),
        pool=5.0,
    )
    with httpx.Client(timeout=timeout) as client:
        for model in candidate_models(settings, cfg):
            for wire_api in candidate_wire_apis(cfg):
                try:
                    if wire_api == "responses":
                        content = call_responses_api_sync(client, cfg, model, messages, temperature, max_output_tokens)
                    else:
                        content = call_chat_stream_api_sync(client, cfg, model, messages, temperature, max_output_tokens)
                    if content.strip():
                        return content.strip()
                    errors.append(f"{model}/{wire_api}: empty response")
                except httpx.HTTPStatusError as exc:
                    detail = exc.response.text[:260].replace("\n", " ")
                    errors.append(f"{model}/{wire_api}: HTTP {exc.response.status_code} {detail}")
                except Exception as exc:
                    errors.append(f"{model}/{wire_api}: {type(exc).__name__} {str(exc)[:180]}")
    raise LLMCallError(errors)


async def call_responses_api(
    client: httpx.AsyncClient,
    cfg: LLMConfig,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_output_tokens: int | None,
) -> str:
    response = await client.post(
        api_endpoint(cfg.base_url, "/responses"),
        headers=auth_headers(cfg.api_key),
        json=responses_payload(cfg, model, messages, temperature, max_output_tokens),
    )
    response.raise_for_status()
    return extract_response_text(response.json())


def call_responses_api_sync(
    client: httpx.Client,
    cfg: LLMConfig,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_output_tokens: int | None,
) -> str:
    response = client.post(
        api_endpoint(cfg.base_url, "/responses"),
        headers=auth_headers(cfg.api_key),
        json=responses_payload(cfg, model, messages, temperature, max_output_tokens),
    )
    response.raise_for_status()
    return extract_response_text(response.json())


async def call_chat_stream_api(
    client: httpx.AsyncClient,
    cfg: LLMConfig,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_output_tokens: int | None,
) -> str:
    payload = chat_payload(cfg, model, messages, temperature, max_output_tokens)
    content_parts: list[str] = []
    async with client.stream(
        "POST",
        api_endpoint(cfg.base_url, "/chat/completions"),
        headers=auth_headers(cfg.api_key),
        json=payload,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            collect_chat_stream_line(line, content_parts)
    return "".join(content_parts)


def call_chat_stream_api_sync(
    client: httpx.Client,
    cfg: LLMConfig,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_output_tokens: int | None,
) -> str:
    payload = chat_payload(cfg, model, messages, temperature, max_output_tokens)
    content_parts: list[str] = []
    with client.stream(
        "POST",
        api_endpoint(cfg.base_url, "/chat/completions"),
        headers=auth_headers(cfg.api_key),
        json=payload,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            collect_chat_stream_line(line, content_parts)
    return "".join(content_parts)


def responses_payload(
    cfg: LLMConfig,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    instructions, input_text = split_messages_for_responses(messages)
    payload: dict[str, Any] = {
        "model": model,
        "input": input_text,
        "stream": False,
        "max_output_tokens": max_output_tokens or cfg.max_output_tokens,
    }
    if instructions:
        payload["instructions"] = instructions
    if temperature is not None:
        payload["temperature"] = temperature
    if cfg.reasoning_effort:
        payload["reasoning"] = {"effort": cfg.reasoning_effort}
    if cfg.disable_response_storage:
        payload["store"] = False
    return payload


def chat_payload(
    cfg: LLMConfig,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_completion_tokens": max_output_tokens or cfg.max_output_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    # The Haoxiang reference client only sends chat reasoning_effort for values
    # known to be accepted by that gateway.
    if cfg.reasoning_effort in {"low", "medium", "high"}:
        payload["reasoning_effort"] = cfg.reasoning_effort
    return payload


def collect_chat_stream_line(line: str, content_parts: list[str]) -> None:
    if not line.startswith("data:"):
        return
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return
    payload = json.loads(data)
    choices = payload.get("choices") or []
    if not choices:
        return
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if content:
        content_parts.append(str(content))


def extract_response_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    parts: list[str] = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, str):
                parts.append(content)
            elif content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(str(content["text"]))
    if parts:
        return "".join(parts)
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        if message.get("content"):
            return str(message["content"])
    return ""


def split_messages_for_responses(messages: list[dict[str, str]]) -> tuple[str, str]:
    instructions: list[str] = []
    inputs: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = str(message.get("content", ""))
        if role in {"system", "developer"}:
            instructions.append(content)
        else:
            inputs.append(content if role == "user" else f"{role}: {content}")
    return "\n\n".join(instructions), "\n\n".join(inputs)


def resolve_llm_config(settings: dict[str, Any]) -> LLMConfig:
    llm = settings.get("llm", {})
    api_key_env = str(llm.get("api_key_env", "OPENAI_API_KEY"))
    api_key, used_api_key_env = first_env(api_key_env, llm.get("fallback_api_key_envs"), llm.get("fallback_api_key_env"))
    base_url_env = str(llm.get("base_url_env", "OPENAI_BASE_URL"))
    base_url, used_base_url_env = first_env(base_url_env, llm.get("fallback_base_url_envs"), llm.get("fallback_base_url_env"))
    model_env = str(llm.get("model_env", "OPENAI_MODEL"))
    model = os.getenv(model_env) or str(llm.get("default_model", "gpt-5.4"))
    return LLMConfig(
        provider=str(llm.get("provider", "openai")),
        wire_api=str(llm.get("wire_api", "responses")),
        api_key_env=used_api_key_env or api_key_env,
        base_url_env=used_base_url_env or base_url_env,
        model_env=model_env,
        api_key=api_key or "",
        base_url=(base_url or str(llm.get("default_base_url", "https://api.openai.com"))).rstrip("/"),
        model=model,
        reasoning_effort=str(llm.get("reasoning_effort", "")),
        disable_response_storage=as_bool(llm.get("disable_response_storage", True)),
        request_timeout=max(5.0, float(llm.get("request_timeout_seconds", 60))),
        max_model_attempts=max(1, int(llm.get("max_model_attempts", 1))),
        max_output_tokens=max(64, int(llm.get("max_output_tokens", 4096))),
    )


def first_env(primary: str, fallback_list: Any = None, fallback_single: Any = None) -> tuple[str, str | None]:
    env_names = [primary] + as_list(fallback_list) + as_list(fallback_single)
    for env_name in env_names:
        if not env_name:
            continue
        value = os.getenv(str(env_name))
        if value:
            return value, str(env_name)
    return "", None


def candidate_models(settings: dict[str, Any], cfg: LLMConfig) -> list[str]:
    llm = settings.get("llm", {})
    return unique_models([cfg.model] + [str(model) for model in as_list(llm.get("fallback_models"))])[: cfg.max_model_attempts]


def candidate_wire_apis(cfg: LLMConfig) -> list[str]:
    preferred = cfg.wire_api.strip().lower()
    if preferred in {"responses", "response"}:
        return ["responses", "chat_stream"]
    if preferred in {"chat_stream", "chat_completions", "chat"}:
        return ["chat_stream", "responses"]
    return ["responses", "chat_stream"]


def api_endpoint(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    prefix = "" if base.endswith("/v1") else "/v1"
    return f"{base}{prefix}{path}"


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def fetch_jina_text(settings: dict[str, Any], url: str | None) -> str:
    if not url:
        return ""
    jina = settings.get("jina", {})
    reader_url = str(jina.get("reader_url", "https://r.jina.ai/http://"))
    max_chars = int(jina.get("max_chars", 5000))
    request_timeout = max(3.0, float(jina.get("timeout_seconds", 6)))
    api_key = os.getenv(str(jina.get("api_key_env", "JINA_API_KEY")))
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    endpoint = reader_url + url
    try:
        timeout = httpx.Timeout(
            request_timeout,
            connect=min(5.0, request_timeout),
            read=request_timeout,
            write=min(5.0, request_timeout),
            pool=3.0,
        )
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
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
    scope: str = "item",
    context_note: str = "",
) -> str:
    parts = [
        f"Conversation scope: {scope}",
        "User profile:",
        f"- primary_topics: {', '.join(profile.get('primary_topics', []))}",
        f"- secondary_topics: {', '.join(profile.get('secondary_topics', []))}",
        f"- negative_topics: {', '.join(profile.get('negative_topics', []))}",
    ]
    if context_note:
        parts.extend(["\nScope context:", context_note])
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
    reason: str = "no_key",
    scope: str = "item",
    context_note: str = "",
) -> str:
    reason_text = {
        "no_key": "当前服务没有读到 OpenAI API key，因此先给你一个本地、基于来源文本的简要回答。",
        "timeout": "外部模型或原文读取超过本次等待上限，因此先给你一个本地、基于来源文本的简要回答。",
        "llm_unavailable": "外部模型接口暂时不可用，因此先给你一个本地、基于来源文本的简要回答。",
    }.get(reason, "当前先给你一个本地、基于来源文本的简要回答。")
    if not item:
        if scope in {"digest", "knowledge"}:
            related = "\n".join(f"- {row.get('title')} ({row.get('source_name')})" for row in related_items[:8])
            related = related or "- 暂无可用条目"
            context = f"\n\n**当前上下文**：{context_note[:900]}" if context_note else ""
            return f"""{reason_text}

**问题**：{question}{context}

**可用条目**：
{related}

当前是本地降级回答，只能依据页面已有摘要、收藏、笔记和问答记录给出粗略判断。完整模型可用后，适合追问“今天应该深读哪几篇”“最近某个方向有什么变化”“哪些内容证据较弱”这类综合问题。"""
        return f"{reason_text}\n\n当前没有绑定具体条目。你可以先在列表里打开一条论文或博客，再基于该条目提问。"
    tags = "、".join(item.get("tags", [])) or "暂无系统标签"
    topics = "、".join(profile.get("primary_topics", [])[:5])
    summary = item.get("summary") or "该来源没有提供摘要，建议打开原文查看细节。"
    related = "\n".join(f"- {row['title']} ({row['source_name']})" for row in related_items[:3])
    related = related or "- 暂无明显相关条目"
    return f"""{reason_text}

**条目**：{item.get('title')}

**和你的关系**：你的主关注方向包括 {topics}。系统给这条内容打的标签是：{tags}。如果这些标签与你当前课题吻合，可以先快速读摘要和原文引言。

**已有摘要**：{summary}

**针对你的问题**：{question}

我能确定的是：这条内容来自 {item.get('source_name')}，证据角色是 `{item.get('evidence_role')}`。如果它是论文，建议优先看方法和实验；如果是官方博客，建议检查是否有论文、模型卡、代码或 API 文档链接；如果是社区讨论，建议把它当作工程反馈而不是最终事实。

**相关近期条目**：
{related}

如果你希望每次都拿到完整的大模型回答，请检查 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 和额度状态。"""


def unique_models(models: list[str]) -> list[str]:
    seen = set()
    out = []
    for model in models:
        model = (model or "").strip()
        if model and model not in seen:
            seen.add(model)
            out.append(model)
    return out
