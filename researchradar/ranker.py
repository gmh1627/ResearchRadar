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
    "aihot_public": 0.78,
}

ACTION_TAGS = {"Agent", "Tool Use", "Memory", "Evaluation", "Code Agent", "Self-Improvement"}
AIHOT_TAGS = {"AIHOT线索"}
SOURCE_TIER_BONUS = {
    "T1": 0.12,
    "T1_5": 0.07,
    "T1.5": 0.07,
    "T2": 0.0,
}


def rank_items(
    items: list[dict[str, Any]],
    profile: dict[str, Any],
    feedback: dict[str, list[str]],
    feedback_signals: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    ranked = []
    for item in items:
        score, parts = score_item(item, profile, feedback.get(item["id"], []), feedback_signals or {})
        card = {
            **item,
            "score": round(score, 3),
            "quality_score": round(score, 3),
            "score_parts": parts,
            "relevance_reason": build_relevance_reason(item, profile, parts),
            "recommended_action": recommended_action(item, parts),
        }
        ranked.append(card)
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked


def score_item(
    item: dict[str, Any],
    profile: dict[str, Any],
    actions: list[str],
    feedback_signals: dict[str, dict[str, float]],
) -> tuple[float, dict[str, float]]:
    text = f"{item.get('title', '')}\n{item.get('summary', '')}\n{' '.join(item.get('tags', []))}".lower()
    primary = profile.get("primary_topics", [])
    secondary = profile.get("secondary_topics", [])
    negative = profile.get("negative_topics", [])
    preferred = set(profile.get("preferred_sources", []))
    if llm_filtered_non_ai(item):
        parts = {
            "relevance": 0.0,
            "authority": round(SOURCE_AUTHORITY.get(item.get("source_id"), 0.6), 3),
            "credibility": 0.0,
            "novelty": 0.0,
            "significance": 0.0,
            "research_value": 0.0,
            "trend": 0.0,
            "actionability": 0.0,
            "recency": round(recency_signal(item), 3),
            "personalization": 0.0,
            "negative_penalty": 0.0,
        }
        return 0.0, parts
    llm_dimensions = llm_postprocess_dimensions(item)
    if llm_dimensions:
        return score_item_from_llm_dimensions(item, profile, actions, feedback_signals, text, negative, preferred, llm_dimensions)

    relevance = keyword_score(text, primary, base=0.2, per_hit=0.18)
    relevance += keyword_score(text, secondary, base=0.0, per_hit=0.09)
    tags = set(item.get("tags", []))
    tag_bonus = len(tags & ACTION_TAGS) * 0.04
    relevance = clamp(relevance + tag_bonus)

    negative_penalty = keyword_score(text, negative, base=0.0, per_hit=0.25)
    authority = SOURCE_AUTHORITY.get(item.get("source_id"), 0.6)
    if item.get("source_id") in preferred:
        authority += 0.08
    source_tier = item.get("source_tier") or item.get("metadata", {}).get("source_tier")
    authority += SOURCE_TIER_BONUS.get(str(source_tier or ""), 0.0)
    authority = clamp(authority)

    trend = trend_signal(item)
    actionability = actionability_signal(item)
    novelty = novelty_signal(item)
    significance = significance_signal(item)
    credibility = credibility_signal(item, authority)
    research_value = research_value_signal(item, relevance)
    recency = recency_signal(item)
    personalization = personalization_signal(item, feedback_signals)
    feedback_boost = 0.0
    if "save" in actions or "deep_read" in actions or "like" in actions:
        feedback_boost -= 0.2
    if "not_relevant" in actions or "ignore" in actions:
        feedback_boost -= 0.5

    score = (
        0.30 * relevance
        + 0.14 * credibility
        + 0.12 * novelty
        + 0.12 * significance
        + 0.12 * actionability
        + 0.09 * trend
        + 0.08 * research_value
        + 0.08 * recency
        + 0.08 * personalization
        - 0.28 * negative_penalty
        + feedback_boost
    )
    parts = {
        "relevance": round(relevance, 3),
        "authority": round(authority, 3),
        "credibility": round(credibility, 3),
        "novelty": round(novelty, 3),
        "significance": round(significance, 3),
        "research_value": round(research_value, 3),
        "trend": round(trend, 3),
        "actionability": round(actionability, 3),
        "recency": round(recency, 3),
        "personalization": round(personalization, 3),
        "negative_penalty": round(negative_penalty, 3),
    }
    return score, parts


def score_item_from_llm_dimensions(
    item: dict[str, Any],
    profile: dict[str, Any],
    actions: list[str],
    feedback_signals: dict[str, dict[str, float]],
    text: str,
    negative: list[str],
    preferred: set[str],
    dimensions: dict[str, float],
) -> tuple[float, dict[str, float]]:
    relevance = dimensions["relevance"]
    novelty = dimensions["novelty"]
    significance = dimensions["significance"]
    actionability = dimensions["actionability"]
    authority = SOURCE_AUTHORITY.get(item.get("source_id"), 0.6)
    if item.get("source_id") in preferred:
        authority += 0.08
    source_tier = item.get("source_tier") or item.get("metadata", {}).get("source_tier")
    authority += SOURCE_TIER_BONUS.get(str(source_tier or ""), 0.0)
    authority = clamp(authority)
    credibility = clamp(dimensions["credibility"] * 0.7 + authority * 0.3)
    trend = trend_signal(item)
    recency = recency_signal(item)
    research_value = research_value_signal(item, relevance)
    personalization = personalization_signal(item, feedback_signals)
    negative_penalty = keyword_score(text, negative, base=0.0, per_hit=0.25)
    feedback_boost = 0.0
    if "save" in actions or "deep_read" in actions or "like" in actions:
        feedback_boost -= 0.2
    if "not_relevant" in actions or "ignore" in actions:
        feedback_boost -= 0.5
    score = (
        0.24 * relevance
        + 0.20 * significance
        + 0.16 * novelty
        + 0.14 * actionability
        + 0.12 * credibility
        + 0.07 * trend
        + 0.05 * recency
        + 0.04 * research_value
        + 0.08 * personalization
        - 0.28 * negative_penalty
        + feedback_boost
    )
    parts = {
        "llm_relevance": round(relevance, 3),
        "llm_novelty": round(novelty, 3),
        "llm_significance": round(significance, 3),
        "llm_actionability": round(actionability, 3),
        "llm_credibility": round(dimensions["credibility"], 3),
        "relevance": round(relevance, 3),
        "authority": round(authority, 3),
        "credibility": round(credibility, 3),
        "novelty": round(novelty, 3),
        "significance": round(significance, 3),
        "research_value": round(research_value, 3),
        "trend": round(trend, 3),
        "actionability": round(actionability, 3),
        "recency": round(recency, 3),
        "personalization": round(personalization, 3),
        "negative_penalty": round(negative_penalty, 3),
    }
    return score, parts


def llm_postprocess_dimensions(item: dict[str, Any]) -> dict[str, float] | None:
    post = (item.get("metadata", {}) or {}).get("llm_postprocess") or {}
    if post.get("status") != "analyzed":
        return None
    raw = post.get("dimensions") or {}
    required = ["relevance", "novelty", "significance", "actionability", "credibility"]
    if not all(key in raw for key in required):
        return None
    out = {}
    for key in required:
        try:
            out[key] = clamp(float(raw[key]))
        except (TypeError, ValueError):
            return None
    return out


def llm_filtered_non_ai(item: dict[str, Any]) -> bool:
    post = (item.get("metadata", {}) or {}).get("llm_postprocess") or {}
    return post.get("status") == "filtered_non_ai"


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
    if item.get("source_id") == "aihot_public":
        aihot_score = float(meta.get("aihot_score") or 0)
        if aihot_score:
            return clamp(aihot_score / 100)
        if meta.get("aihot_selected"):
            return 0.65
        return 0.45
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
    if item.get("metadata", {}).get("aihot_reason"):
        score += 0.12
    if item.get("metadata", {}).get("aihot_category"):
        score += 0.08
    return clamp(score)


def novelty_signal(item: dict[str, Any]) -> float:
    text = f"{item.get('title', '')}\n{item.get('summary', '')}".lower()
    markers = [
        "new",
        "novel",
        "release",
        "launch",
        "benchmark",
        "dataset",
        "open-source",
        "open source",
        "state-of-the-art",
        "sota",
        "首次",
        "发布",
        "开源",
        "新模型",
        "新框架",
        "基准",
    ]
    score = 0.35 + sum(0.08 for marker in markers if marker in text)
    if item.get("metadata", {}).get("aihot_score"):
        score += min(float(item["metadata"].get("aihot_score") or 0) / 250, 0.25)
    return clamp(score)


def significance_signal(item: dict[str, Any]) -> float:
    text = f"{item.get('title', '')}\n{item.get('summary', '')}".lower()
    markers = [
        "openai",
        "anthropic",
        "deepmind",
        "google",
        "meta",
        "nvidia",
        "deepseek",
        "qwen",
        "agent",
        "llm",
        "reasoning",
        "infrastructure",
        "evaluation",
        "突破",
        "重大",
        "旗舰",
        "官方",
    ]
    score = 0.3 + sum(0.055 for marker in markers if marker in text)
    if item.get("source_tier") == "T1" or item.get("evidence_role") in {"primary_research", "official_update"}:
        score += 0.16
    return clamp(score)


def credibility_signal(item: dict[str, Any], authority: float) -> float:
    score = authority
    role = item.get("evidence_role")
    reliability = item.get("source_reliability")
    if reliability == "high":
        score += 0.08
    elif reliability == "low":
        score -= 0.12
    if role in {"primary_research", "official_update", "lab_update"}:
        score += 0.08
    elif role == "curated_secondary_signal":
        score -= 0.03
    return clamp(score)


def research_value_signal(item: dict[str, Any], relevance: float) -> float:
    tags = set(item.get("tags", []))
    score = 0.25 + relevance * 0.45
    if item.get("source_type") == "paper":
        score += 0.18
    if item.get("source_id") == "github":
        score += 0.12
    if tags & {"Agent", "Tool Use", "Memory", "Evaluation", "Reasoning", "Code Agent"}:
        score += 0.16
    if item.get("metadata", {}).get("pdf_url"):
        score += 0.08
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


def personalization_signal(item: dict[str, Any], signals: dict[str, dict[str, float]]) -> float:
    if not signals:
        return 0.0
    tags = set(item.get("tags", []))
    source_id = item.get("source_id")
    positive = sum(signals.get("positive_tags", {}).get(tag, 0.0) for tag in tags) * 0.18
    positive += signals.get("positive_sources", {}).get(source_id, 0.0) * 0.12
    negative = sum(signals.get("negative_tags", {}).get(tag, 0.0) for tag in tags) * 0.18
    negative += signals.get("negative_sources", {}).get(source_id, 0.0) * 0.12
    return max(-1.0, min(1.0, positive - negative))


def build_relevance_reason(item: dict[str, Any], profile: dict[str, Any], parts: dict[str, float]) -> str:
    metadata = item.get("metadata", {}) or {}
    post = metadata.get("llm_postprocess") or {}
    if post.get("status") == "filtered_non_ai":
        return "GPT-5.5 预筛判定为非 AI 相关：" + str(post.get("reason") or "未给出原因")
    if post.get("status") == "analyzed" and item.get("relevance_reason"):
        return str(item["relevance_reason"])
    aihot_reason = metadata.get("aihot_reason")
    if aihot_reason:
        return "AIHOT 推荐理由：" + str(aihot_reason)
    if item.get("source_id") == "aihot_public" and metadata.get("aihot_category_label"):
        return "来自 AIHOT 精选 API，分类为：" + str(metadata["aihot_category_label"])
    if parts.get("personalization", 0) >= 0.35:
        tags = item.get("tags", [])
        if tags:
            return "你的历史反馈偏好相似标签：" + "、".join(tags[:4])
    if parts["relevance"] >= 0.45:
        return short_reason(item.get("title", ""), profile)
    tags = item.get("tags", [])
    if tags:
        return "系统标签显示它涉及：" + "、".join(tags[:4])
    if item.get("evidence_role") == "official_update":
        return "来自重要公司或实验室官方信息源，适合作为研究动态补充。"
    if item.get("evidence_role") == "curated_secondary_signal":
        return "来自外部精选信息流，适合作为早期线索，建议打开原始来源核验。"
    return "进入近期 AI 信息池，可作为快速浏览项。"


def recommended_action(item: dict[str, Any], parts: dict[str, float]) -> str:
    post = (item.get("metadata", {}) or {}).get("llm_postprocess", {})
    if post.get("status") == "filtered_non_ai":
        return "忽略。该条目保留入库，但不进入精选排序。"
    if post.get("status") == "analyzed" and item.get("recommended_action"):
        return str(item["recommended_action"])
    if item.get("source_id") == "aihot_public":
        if item.get("metadata", {}).get("aihot_reason"):
            return "把它当作二级线索：先看 AIHOT 摘要和推荐理由，再打开原始链接核验。"
        return "把它当作二级线索：先看 AIHOT 摘要和分类，再打开原始链接核验。"
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
