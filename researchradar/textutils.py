from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup


TAG_KEYWORDS: dict[str, list[str]] = {
    "LLM": ["llm", "large language model", "language model", "foundation model"],
    "Agent": ["agent", "agents", "agentic"],
    "Multi-Agent": ["multi-agent", "multiagent", "multi agent"],
    "Tool Use": ["tool use", "tool-use", "function calling", "tools"],
    "Reasoning": ["reasoning", "chain-of-thought", "cot", "planning"],
    "Memory": ["memory", "long-term memory", "context management", "consolidation"],
    "RAG": ["rag", "retrieval augmented", "retrieval-augmented", "retriever"],
    "Evaluation": ["evaluation", "benchmark", "eval", "leaderboard"],
    "Alignment": ["alignment", "preference", "rlhf", "safety"],
    "RL for LLM": ["reinforcement learning", "rl", "policy optimization", "grpo"],
    "Code Agent": ["code agent", "coding agent", "software engineering", "program repair"],
    "Long Context": ["long context", "context window", "long-context"],
    "Data Synthesis": ["synthetic data", "data synthesis", "distillation"],
    "Inference": ["inference", "serving", "latency", "throughput"],
    "Self-Improvement": ["self-improvement", "self improvement", "reflection", "reflexion"],
    "Future Prediction": ["future prediction", "forecasting", "prediction market"],
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def strip_html(value: str | None, limit: int = 1400) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def normalize_title(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    parsed = parsed._replace(fragment="", query=urlencode(query_pairs, doseq=True))
    return urlunparse(parsed)


def stable_id(*parts: str) -> str:
    raw = "\n".join(part or "" for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def extract_tags(title: str, summary: str = "", categories: list[str] | None = None) -> list[str]:
    haystack = f"{title}\n{summary}".lower()
    tags: list[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword.lower() in haystack for keyword in keywords):
            tags.append(tag)
    for category in categories or []:
        if category in {"cs.AI", "cs.LG", "stat.ML", "cs.CL", "cs.MA"} and "Core AI" not in tags:
            tags.append("Core AI")
    return tags[:12]


def contains_any(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(keyword.lower() in low for keyword in keywords)


def short_reason(title: str, profile: dict) -> str:
    text = title.lower()
    matches = []
    for topic in profile.get("primary_topics", []) + profile.get("secondary_topics", []):
        if topic.lower() in text:
            matches.append(topic)
    if matches:
        return "匹配你的关注主题：" + "、".join(matches[:3])
    return "与当前 AI / ML / Agent 信息池相关，建议快速扫读后决定是否深读。"
