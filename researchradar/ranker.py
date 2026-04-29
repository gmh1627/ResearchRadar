from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .textutils import short_reason


SOURCE_AUTHORITY = {
    "arxiv_core": 1.0,
    "openai_blog": 0.95,
    "anthropic_news": 0.95,
    "deepmind_blog": 0.95,
    "meta_ai_blog": 0.9,
    "google_research_blog": 0.9,
    "microsoft_research_blog": 0.88,
    "nvidia_research_blog": 0.85,
    "huggingface_blog": 0.88,
    "berkeley_bair_blog": 0.88,
    "allenai_blog": 0.86,
    "stanford_crfm": 0.86,
    "github": 0.72,
    "hackernews": 0.65,
}

ACTION_TAGS = {"Agent", "Tool Use", "Memory", "RAG", "Evaluation", "Code Agent", "Self-Improvement"}


def rank_items(items: list[dict[str, Any]], profile: dict[str, Any], feedback: dict[str, list[str]]) -> list[dict[str, Any]]:
    ranked = []
    for item in items:
        score, parts = score_item(item, profile, feedback.get(item["id"], []))
        card = {
            **item,
            "score": round(score, 3),
            "score_parts": parts,
            "relevance_reason": build_relevance_reason(item, profile, parts),
            "recommended_action": recommended_action(item, parts),
        }
        ranked.append(card)
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked


def score_item(item: dict[str, Any], profile: dict[str, Any], actions: list[str]) -> tuple[float, dict[str, float]]:
    text = f"{item.get('title', '')}\n{item.get('summary', '')}\n{' '.join(item.get('tags', []))}".lower()
    primary = profile.get("primary_topics", [])
    secondary = profile.get("secondary_topics", [])
    negative = profile.get("negative_topics", [])
    preferred = set(profile.get("preferred_sources", []))

    relevance = keyword_score(text, primary, base=0.2, per_hit=0.18)
    relevance += keyword_score(text, secondary, base=0.0, per_hit=0.09)
    tag_bonus = len(set(item.get("tags", [])) & ACTION_TAGS) * 0.04
    relevance = clamp(relevance + tag_bonus)

    negative_penalty = keyword_score(text, negative, base=0.0, per_hit=0.25)
    authority = SOURCE_AUTHORITY.get(item.get("source_id"), 0.6)
    if item.get("source_id") in preferred:
        authority += 0.08
    authority = clamp(authority)

    trend = trend_signal(item)
    actionability = actionability_signal(item)
    recency = recency_signal(item)
    feedback_boost = 0.0
    if "save" in actions or "deep_read" in actions or "like" in actions:
        feedback_boost -= 0.2
    if "not_relevant" in actions or "ignore" in actions:
        feedback_boost -= 0.5

    score = (
        0.38 * relevance
        + 0.18 * authority
        + 0.14 * trend
        + 0.14 * actionability
        + 0.12 * recency
        - 0.28 * negative_penalty
        + feedback_boost
    )
    parts = {
        "relevance": round(relevance, 3),
        "authority": round(authority, 3),
        "trend": round(trend, 3),
        "actionability": round(actionability, 3),
        "recency": round(recency, 3),
        "negative_penalty": round(negative_penalty, 3),
    }
    return score, parts


def keyword_score(text: str, keywords: list[str], *, base: float, per_hit: float) -> float:
    if not keywords:
        return 0.0
    hits = 0
    for keyword in keywords:
        if keyword.lower() in text:
            hits += 1
    if hits == 0:
        return 0.0
    return clamp(base + hits * per_hit)


def trend_signal(item: dict[str, Any]) -> float:
    meta = item.get("metadata", {})
    if item.get("source_id") == "hackernews":
        points = float(meta.get("points") or 0)
        comments = float(meta.get("comments") or 0)
        return clamp(points / 250 + comments / 150)
    if item.get("source_id") == "github":
        stars = float(meta.get("stars") or 0)
        forks = float(meta.get("forks") or 0)
        return clamp(stars / 2000 + forks / 400)
    if item.get("source_type") == "paper":
        return 0.55
    if item.get("evidence_role") == "official_update":
        return 0.65
    return 0.45


def actionability_signal(item: dict[str, Any]) -> float:
    tags = set(item.get("tags", []))
    score = 0.35
    if item.get("source_id") == "github":
        score += 0.35
    if tags & ACTION_TAGS:
        score += 0.25
    if item.get("metadata", {}).get("pdf_url"):
        score += 0.1
    return clamp(score)


def recency_signal(item: dict[str, Any]) -> float:
    value = item.get("published_at") or item.get("collected_at")
    if not value:
        return 0.4
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        age_days = max((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 0)
        return clamp(1.0 - age_days / 21)
    except Exception:
        return 0.4


def build_relevance_reason(item: dict[str, Any], profile: dict[str, Any], parts: dict[str, float]) -> str:
    if parts["relevance"] >= 0.45:
        return short_reason(item.get("title", ""), profile)
    tags = item.get("tags", [])
    if tags:
        return "系统标签显示它涉及：" + "、".join(tags[:4])
    if item.get("evidence_role") == "official_update":
        return "来自重要公司或实验室官方信息源，适合作为研究动态补充。"
    return "进入近期 AI 信息池，可作为快速浏览项。"


def recommended_action(item: dict[str, Any], parts: dict[str, float]) -> str:
    if item.get("source_type") == "paper" and parts["relevance"] >= 0.45:
        return "先读 abstract 和 method；若与当前课题相关，再加入深读列表。"
    if item.get("source_id") == "github":
        return "查看 README、最近 commit 和 examples，判断是否可作为 baseline 或工具。"
    if item.get("source_id") == "hackernews":
        return "快速看讨论焦点，重点关注工程质疑、替代方案和真实使用反馈。"
    if item.get("evidence_role") == "official_update":
        return "浏览官方发布内容，检查是否关联模型能力、API、论文或开源代码。"
    return "快速扫读并决定收藏或忽略。"


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
