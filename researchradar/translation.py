from __future__ import annotations

import re
import time
from typing import Any
import json
import os

import httpx
from deep_translator import GoogleTranslator

from .config import load_config
from .db import Database


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def fallback_chinese_summary(row: dict[str, Any]) -> str:
    source = row.get("source_name") or "未知来源"
    source_type = row.get("source_type") or "条目"
    title = row.get("title") or "未命名条目"
    return f"暂无来源摘要。这是一条来自{source}的{source_type}信息，标题为“{title}”。建议打开原文查看细节。"


class SummaryTranslator:
    def __init__(self, settings: dict[str, Any]):
        llm = settings.get("llm", {})
        self.api_key = os.getenv(str(llm.get("api_key_env", "OPENROUTER_API_KEY")))
        self.base_url = os.getenv(str(llm.get("base_url_env", "OPENROUTER_BASE_URL")), str(llm.get("default_base_url", "https://openrouter.ai/api/v1"))).rstrip("/")
        self.model = os.getenv(str(llm.get("model_env", "OPENROUTER_MODEL")), str(llm.get("default_model", "gpt-5.4")))
        self.translator = GoogleTranslator(source="auto", target="zh-CN")

    def translate_row(self, row: dict[str, Any]) -> str:
        summary = (row.get("summary") or "").strip()
        title = (row.get("title") or "").strip()
        text = summary or title
        if not text:
            return fallback_chinese_summary(row)
        if has_cjk(text):
            return text
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 4500:
            text = text[:4500].rsplit(" ", 1)[0]
        try:
            translated = self.translator.translate(text)
            if translated and has_cjk(translated):
                return translated.strip()
        except Exception:
            pass
        return fallback_chinese_summary(row)

    def translate_rows(self, rows: list[dict[str, Any]]) -> list[str]:
        llm_results = self.translate_rows_with_llm(rows)
        if llm_results:
            return llm_results

        texts: list[str] = []
        fallback_indexes: list[int] = []
        for index, row in enumerate(rows):
            summary = (row.get("summary") or "").strip()
            title = (row.get("title") or "").strip()
            text = summary or title
            if not text:
                fallback_indexes.append(index)
                texts.append("")
                continue
            if has_cjk(text):
                texts.append(text)
                continue
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 2200:
                text = text[:2200].rsplit(" ", 1)[0]
            texts.append(text)

        results = ["" for _ in rows]
        to_translate = [(i, text) for i, text in enumerate(texts) if text and not has_cjk(text)]
        for i, text in enumerate(texts):
            if text and has_cjk(text):
                results[i] = text
        for i in fallback_indexes:
            results[i] = fallback_chinese_summary(rows[i])

        if to_translate:
            try:
                translated = self.translator.translate_batch([text for _, text in to_translate])
            except Exception:
                translated = []
            if len(translated) == len(to_translate):
                for (row_index, _), zh in zip(to_translate, translated):
                    results[row_index] = zh.strip() if zh and has_cjk(zh) else fallback_chinese_summary(rows[row_index])
            else:
                for row_index, _ in to_translate:
                    results[row_index] = self.translate_row(rows[row_index])
        return results

    def translate_rows_with_llm(self, rows: list[dict[str, Any]]) -> list[str] | None:
        if not self.api_key:
            return None
        prepared = []
        for row in rows:
            summary = (row.get("summary") or "").strip()
            title = (row.get("title") or "").strip()
            text = summary or title
            if not text:
                prepared.append(fallback_chinese_summary(row))
                continue
            if has_cjk(text):
                prepared.append(text)
                continue
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 1500:
                text = text[:1500].rsplit(" ", 1)[0]
            prepared.append(text)

        if all(has_cjk(text) for text in prepared):
            return prepared

        prompt = (
            "请把下面 JSON 数组中的每一条研究信息摘要翻译成简体中文。"
            "要求：保留技术术语的英文缩写，例如 LLM、Agent、RLHF；不要增加原文没有的信息；"
            "返回严格 JSON，格式为 {\"translations\": [\"...\"]}，数组长度必须相同。\n\n"
            + json.dumps(prepared, ensure_ascii=False)
        )
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are a precise English-to-Chinese translator for AI research texts."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                },
                timeout=120,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = parse_json_object(content)
            translations = data.get("translations", [])
            if len(translations) == len(rows):
                return [
                    str(text).strip() if text and has_cjk(str(text)) else fallback_chinese_summary(row)
                    for row, text in zip(rows, translations)
                ]
        except Exception:
            return None
        return None


def translate_missing(limit: int = 500, batch_size: int = 32, sleep_seconds: float = 0.05) -> int:
    config = load_config()
    db = Database(config.db_path)
    db.initialize()
    translator = SummaryTranslator(config.settings)
    rows = db.items_missing_translation(limit=limit)
    count = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        summaries = translator.translate_rows(batch)
        for row, zh in zip(batch, summaries):
            db.update_summary_zh(row["id"], zh)
            count += 1
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return count


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
