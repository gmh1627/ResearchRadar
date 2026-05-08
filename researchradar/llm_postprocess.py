from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .llm import LLMCallError, generate_text_sync, resolve_llm_config
from .ranker import SOURCE_AUTHORITY, SOURCE_TIER_BONUS, clamp, recency_signal


LLM_DIMENSIONS = ("relevance", "novelty", "significance", "actionability", "credibility")
PIPELINE_MARKER = "llm_postprocess"
AI_TAGS = [
    "LLM",
    "Agent",
    "Tool Use",
    "Reasoning",
    "Memory",
    "Evaluation",
    "Alignment",
    "RL for LLM",
    "Code Agent",
    "Long Context",
    "Data Synthesis",
    "Inference",
    "Multi-Agent",
    "RAG",
    "Multimodal",
    "Open Source",
    "AI Infrastructure",
]


def run_llm_postprocess(config, db: Database, *, limit: int | None = None, days: int | None = None) -> int:
    settings = config.settings
    llm_cfg = resolve_llm_config(settings)
    post_cfg = settings.get("llm_postprocess", {})
    if not post_cfg.get("enabled", True) or not llm_cfg.api_key:
        return 0

    model = str(post_cfg.get("model") or llm_cfg.model or "gpt-5.5")
    version = str(post_cfg.get("pipeline_version") or "gpt55-v1")
    rows = db.items_for_llm_postprocess(
        days=days or int(post_cfg.get("days", 3)),
        limit=limit or int(post_cfg.get("max_items_per_run", 80)),
        pipeline_version=version,
    )
    if not rows:
        return 0
    min_ai_confidence = float(post_cfg.get("min_ai_confidence", 0.55))
    prefilter_batch_size = max(1, int(post_cfg.get("prefilter_batch_size", 12)))
    analysis_batch_size = max(1, int(post_cfg.get("analysis_batch_size", 6)))

    by_id = {row["id"]: row for row in rows}
    decisions: dict[str, dict[str, Any]] = {}
    for batch in chunks(rows, prefilter_batch_size):
        decisions.update(prefilter_batch(settings, model, batch, version))

    relevant = []
    updates = []
    for row in rows:
        decision = decisions.get(row["id"]) or {}
        is_ai = bool(decision.get("is_ai_related"))
        confidence = score01(decision.get("confidence"), default=0.0)
        if not is_ai or confidence < min_ai_confidence:
            updates.append(apply_non_ai_decision(row, decision, version, model))
            continue
        relevant.append(row)

    for batch in chunks(relevant, analysis_batch_size):
        analyses = analyze_batch(settings, model, batch, version)
        for item_id, analysis in analyses.items():
            row = by_id.get(item_id)
            if row:
                updates.append(apply_analysis(row, analysis, version, model))

    db.update_llm_postprocess(updates)
    return len(updates)


def prefilter_batch(settings: dict[str, Any], model: str, rows: list[dict[str, Any]], version: str) -> dict[str, dict[str, Any]]:
    prompt = {
        "task": "判断这些信息是否与 AI / ML / LLM / Agent / AI 产品或 AI 行业动态有关。",
        "rules": [
            "只输出 JSON，不要解释。",
            "is_ai_related=false 的典型情况：纯硬件、纯商业、泛科技、招聘、营销、与 AI 无直接关系的公司公告。",
            "如果是 AI 公司官方动态、论文、模型、Agent、开发工具、评测、监管、产业变化，应判 true。",
            "confidence 是 0 到 1。",
        ],
        "items": [compact_item(row) for row in rows],
        "output_schema": {"items": [{"id": "string", "is_ai_related": True, "confidence": 0.0, "reason": "string"}]},
        "pipeline_version": version,
    }
    try:
        content = generate_text_sync(
            postprocess_model_settings(settings, model),
            [
                {"role": "system", "content": "You are an exacting AI-news relevance classifier. Return strict JSON only."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0,
            max_output_tokens=1800,
        )
        data = parse_json_object(content)
        return keyed_items(data)
    except (LLMCallError, Exception):
        return {
            row["id"]: {
                "is_ai_related": True,
                "confidence": 0.6,
                "reason": "LLM prefilter unavailable; kept for downstream scoring.",
            }
            for row in rows
        }


def analyze_batch(settings: dict[str, Any], model: str, rows: list[dict[str, Any]], version: str) -> dict[str, dict[str, Any]]:
    prompt = {
        "task": "为这些 AI 信息生成中文摘要并给五个维度打分。最终质量分不要给，最终分由代码公式计算。",
        "dimensions": {
            "relevance": "与 LLM、Agent、Reasoning、Tool Use、AI research/infra/product 动态的相关度",
            "novelty": "是否新发布、新方法、新数据、新产品、新趋势",
            "significance": "对研究/工程/产业的重要性",
            "actionability": "读者是否能据此试用、复现、跟进、引用或调整判断",
            "credibility": "来源可信度和证据质量；官方/论文/代码高于二手传闻",
        },
        "score_contract": "每个维度必须是 0 到 1 的数字。不要输出最终分。",
        "tag_candidates": AI_TAGS,
        "items": [compact_item(row) for row in rows],
        "output_schema": {
            "items": [
                {
                    "id": "string",
                    "summary_zh": "1-3 句中文摘要，不添加来源没有的信息",
                    "tags": ["string"],
                    "scores": {dimension: 0.0 for dimension in LLM_DIMENSIONS},
                    "reason": "为什么值得或不值得关注",
                    "recommended_action": "下一步建议",
                }
            ]
        },
        "pipeline_version": version,
    }
    try:
        content = generate_text_sync(
            postprocess_model_settings(settings, model),
            [
                {"role": "system", "content": "You are a precise AI research/news analyst. Return strict JSON only."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0,
            max_output_tokens=3200,
        )
        data = parse_json_object(content)
        return keyed_items(data)
    except (LLMCallError, Exception):
        return {}


def apply_non_ai_decision(row: dict[str, Any], decision: dict[str, Any], version: str, model: str) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    metadata[PIPELINE_MARKER] = {
        "version": version,
        "model": model,
        "status": "filtered_non_ai",
        "ai_confidence": score01(decision.get("confidence"), default=0.0),
        "reason": str(decision.get("reason") or ""),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    parts = {dimension: 0.0 for dimension in LLM_DIMENSIONS}
    parts.update({"authority": authority_signal(row), "recency": recency_signal(row), "negative_penalty": 0.0})
    return {
        **row,
        "quality_score": 0.0,
        "score_parts": parts,
        "relevance_reason": "GPT-5.5 预筛判定为非 AI 相关：" + str(decision.get("reason") or "未给出原因"),
        "recommended_action": "忽略。该条目保留入库，但不进入精选排序。",
        "metadata": metadata,
    }


def apply_analysis(row: dict[str, Any], analysis: dict[str, Any], version: str, model: str) -> dict[str, Any]:
    llm_scores = {dimension: score01((analysis.get("scores") or {}).get(dimension), default=0.4) for dimension in LLM_DIMENSIONS}
    parts = score_parts(row, llm_scores)
    final_score = final_quality_score(parts)
    summary_zh = str(analysis.get("summary_zh") or row.get("summary_zh") or row.get("summary") or "").strip()
    tags = merge_tags(row.get("tags", []), analysis.get("tags", []))
    metadata = dict(row.get("metadata") or {})
    metadata[PIPELINE_MARKER] = {
        "version": version,
        "model": model,
        "status": "analyzed",
        "dimensions": llm_scores,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        **row,
        "summary_zh": summary_zh,
        "quality_score": final_score,
        "score_parts": parts,
        "relevance_reason": str(analysis.get("reason") or row.get("relevance_reason") or "").strip(),
        "recommended_action": str(analysis.get("recommended_action") or row.get("recommended_action") or "").strip(),
        "tags": tags,
        "metadata": metadata,
    }


def score_parts(row: dict[str, Any], llm_scores: dict[str, float]) -> dict[str, float]:
    authority = authority_signal(row)
    recency = recency_signal(row)
    trend = trend_signal(row)
    source_adjusted_credibility = clamp(llm_scores["credibility"] * 0.7 + authority * 0.3)
    return {
        "llm_relevance": round(llm_scores["relevance"], 3),
        "llm_novelty": round(llm_scores["novelty"], 3),
        "llm_significance": round(llm_scores["significance"], 3),
        "llm_actionability": round(llm_scores["actionability"], 3),
        "llm_credibility": round(llm_scores["credibility"], 3),
        "relevance": round(llm_scores["relevance"], 3),
        "novelty": round(llm_scores["novelty"], 3),
        "significance": round(llm_scores["significance"], 3),
        "actionability": round(llm_scores["actionability"], 3),
        "credibility": round(source_adjusted_credibility, 3),
        "authority": round(authority, 3),
        "trend": round(trend, 3),
        "recency": round(recency, 3),
        "negative_penalty": 0.0,
    }


def final_quality_score(parts: dict[str, float]) -> float:
    score = (
        0.24 * parts["relevance"]
        + 0.20 * parts["significance"]
        + 0.16 * parts["novelty"]
        + 0.14 * parts["actionability"]
        + 0.12 * parts["credibility"]
        + 0.07 * parts["trend"]
        + 0.05 * parts["recency"]
        + 0.02 * parts["authority"]
    )
    return round(clamp(score), 3)


def authority_signal(row: dict[str, Any]) -> float:
    authority = SOURCE_AUTHORITY.get(row.get("source_id"), 0.6)
    tier = row.get("source_tier") or (row.get("metadata") or {}).get("source_tier")
    authority += SOURCE_TIER_BONUS.get(str(tier or ""), 0.0)
    reliability = row.get("source_reliability")
    if reliability == "high":
        authority += 0.08
    elif reliability == "low":
        authority -= 0.12
    return clamp(authority)


def trend_signal(row: dict[str, Any]) -> float:
    metadata = row.get("metadata") or {}
    if row.get("source_id") == "aihot_public" and metadata.get("aihot_selected"):
        return 0.65
    if row.get("source_id") == "github":
        return clamp(float(metadata.get("stars") or 0) / 2000 + float(metadata.get("forks") or 0) / 400)
    if row.get("source_id") == "hackernews":
        return clamp(float(metadata.get("points") or 0) / 250 + float(metadata.get("comments") or 0) / 150)
    if row.get("evidence_role") in {"primary_research", "official_update"}:
        return 0.6
    return 0.45


def compact_item(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "summary": truncate(row.get("summary") or row.get("summary_zh") or "", 900),
        "source_name": row.get("source_name"),
        "source_type": row.get("source_type"),
        "source_reliability": row.get("source_reliability"),
        "evidence_role": row.get("evidence_role"),
        "source_tier": row.get("source_tier"),
        "tags": row.get("tags", []),
        "categories": row.get("categories", []),
        "published_at": row.get("published_at"),
        "url": row.get("url"),
        "metadata_hint": {
            key: metadata.get(key)
            for key in ["aihot_category_label", "aihot_origin_source", "points", "comments", "stars", "forks"]
            if metadata.get(key) is not None
        },
    }


def postprocess_model_settings(settings: dict[str, Any], model: str) -> dict[str, Any]:
    llm = dict(settings.get("llm", {}))
    llm["default_model"] = model
    llm["model_env"] = "__RESEARCHRADAR_LLM_POSTPROCESS_MODEL_ENV__"
    llm["fallback_models"] = []
    return {**settings, "llm": llm}


def merge_tags(existing: list[Any], proposed: Any) -> list[str]:
    values: list[str] = []
    for tag in list(existing or []) + list(proposed or []):
        value = str(tag).strip()
        if value and value not in values:
            values.append(value)
    return values[:16]


def keyed_items(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []) or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out


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


def score01(value: Any, *, default: float) -> float:
    try:
        return clamp(float(value))
    except (TypeError, ValueError):
        return default


def truncate(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def chunks(values: list[dict[str, Any]], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]
