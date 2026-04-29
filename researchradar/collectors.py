from __future__ import annotations

import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

from .db import encode_json
from .textutils import (
    canonical_url,
    contains_any,
    extract_tags,
    isoformat,
    normalize_title,
    now_utc,
    parse_datetime,
    stable_id,
    strip_html,
)


@dataclass
class CollectResult:
    source_id: str
    status: str
    items: list[dict[str, Any]]
    error: str | None = None


class Collector:
    def __init__(self, settings: dict[str, Any]):
        crawl = settings.get("crawl", {})
        self.timeout = float(crawl.get("request_timeout_seconds", 25))
        self.max_items = int(crawl.get("max_items_per_source", 80))
        self.user_agent = str(crawl.get("user_agent", "ResearchRadar/0.1"))
        self.arxiv_page_size = int(crawl.get("arxiv_page_size", 200))
        self.arxiv_max_results = int(crawl.get("arxiv_max_results_per_run", 1500))
        self.github = settings.get("github", {})
        self.hackernews = settings.get("hackernews", {})

    async def collect_source(self, source: dict[str, Any], target: date) -> CollectResult:
        source_id = source["id"]
        try:
            if not source.get("enabled", True):
                return CollectResult(source_id, "skipped", [])
            kind = source.get("type")
            if kind == "arxiv":
                items = await self.collect_arxiv(source, target)
            elif kind == "rss":
                items = await self.collect_rss(source, target)
            elif kind == "page":
                items = await self.collect_page(source, target)
            else:
                return CollectResult(source_id, "error", [], f"unknown source type: {kind}")
            return CollectResult(source_id, "success", items)
        except Exception as exc:
            fallback_url = source.get("fallback_url")
            if fallback_url and source.get("type") == "rss":
                try:
                    fallback_source = {**source, "type": "page", "url": fallback_url}
                    items = await self.collect_page(fallback_source, target)
                    return CollectResult(source_id, "success", items, f"rss failed; used fallback: {exc}")
                except Exception as fallback_exc:
                    return CollectResult(source_id, "error", [], f"{exc}; fallback failed: {fallback_exc}")
            return CollectResult(source_id, "error", [], str(exc))

    async def collect_github(self, target: date) -> CollectResult:
        if not self.github.get("enabled", True):
            return CollectResult("github", "skipped", [])
        queries = self.github.get("keywords", [])[:3] or ["llm agent"]
        items: list[dict[str, Any]] = []
        async with self.client() as client:
            for keyword in queries:
                query = f"{keyword} pushed:>={target.isoformat()}"
                response = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": 20},
                )
                if response.status_code == 403:
                    raise RuntimeError("GitHub API rate limit or access denied")
                response.raise_for_status()
                payload = response.json()
                for repo in payload.get("items", []):
                    updated_at = parse_datetime(repo.get("updated_at"))
                    items.append(
                        self.make_item(
                            source_id="github",
                            source_name="GitHub",
                            source_type="repo",
                            title=repo.get("full_name") or repo.get("name") or "GitHub repository",
                            url=repo.get("html_url", ""),
                            summary=repo.get("description") or "",
                            published_at=updated_at,
                            authors=[repo.get("owner", {}).get("login", "")],
                            categories=[],
                            source_reliability="medium",
                            evidence_role="code_signal",
                            metadata={
                                "stars": repo.get("stargazers_count"),
                                "forks": repo.get("forks_count"),
                                "language": repo.get("language"),
                                "keyword": keyword,
                            },
                        )
                    )
        return CollectResult("github", "success", dedupe_items(items)[: self.max_items])

    async def collect_hackernews(self, target: date) -> CollectResult:
        if not self.hackernews.get("enabled", True):
            return CollectResult("hackernews", "skipped", [])
        keywords = self.hackernews.get("keywords", [])[:5] or ["LLM", "agent", "AI"]
        start_ts = int(datetime.combine(target, dt_time.min, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.combine(target, dt_time.max, tzinfo=timezone.utc).timestamp())
        items: list[dict[str, Any]] = []
        async with self.client() as client:
            for keyword in keywords:
                response = await client.get(
                    "https://hn.algolia.com/api/v1/search_by_date",
                    params={
                        "query": keyword,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{start_ts},created_at_i<{end_ts}",
                        "hitsPerPage": 20,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                for hit in payload.get("hits", []):
                    url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                    published_at = parse_datetime(hit.get("created_at"))
                    title = hit.get("title") or hit.get("story_title") or "Hacker News discussion"
                    summary = f"HN points: {hit.get('points', 0)}, comments: {hit.get('num_comments', 0)}"
                    items.append(
                        self.make_item(
                            source_id="hackernews",
                            source_name="Hacker News",
                            source_type="discussion",
                            title=title,
                            url=url,
                            summary=summary,
                            published_at=published_at,
                            authors=[hit.get("author", "")],
                            categories=[],
                            source_reliability="medium",
                            evidence_role="engineering_discussion",
                            metadata={
                                "hn_id": hit.get("objectID"),
                                "points": hit.get("points"),
                                "comments": hit.get("num_comments"),
                                "keyword": keyword,
                                "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                            },
                        )
                    )
        return CollectResult("hackernews", "success", dedupe_items(items)[: self.max_items])

    async def collect_arxiv(self, source: dict[str, Any], target: date) -> list[dict[str, Any]]:
        items = []
        core_query = self.build_arxiv_query(source.get("categories", []), [], target)
        items.extend(await self.fetch_arxiv_query(source, core_query))
        conditional_categories = source.get("conditional_categories", [])
        conditional_keywords = source.get("conditional_keywords", [])
        if conditional_categories and conditional_keywords:
            conditional_query = self.build_arxiv_query(conditional_categories, conditional_keywords, target)
            items.extend(await self.fetch_arxiv_query(source, conditional_query))
        return dedupe_items(items)

    def build_arxiv_query(self, categories: list[str], keywords: list[str], target: date) -> str:
        category_query = " OR ".join(f"cat:{category}" for category in categories)
        start = target.strftime("%Y%m%d") + "0000"
        end = target.strftime("%Y%m%d") + "2359"
        date_query = f"submittedDate:[{start} TO {end}]"
        if not keywords:
            return f"({category_query}) AND {date_query}"
        safe_keywords = []
        for keyword in keywords:
            if " " in keyword:
                safe_keywords.append(f'all:"{keyword}"')
            else:
                safe_keywords.append(f"all:{keyword}")
        keyword_query = " OR ".join(safe_keywords)
        return f"({category_query}) AND ({keyword_query}) AND {date_query}"

    async def fetch_arxiv_query(self, source: dict[str, Any], query: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start = 0
        async with self.client() as client:
            while start < self.arxiv_max_results:
                response = await client.get(
                    "https://export.arxiv.org/api/query",
                    params={
                        "search_query": query,
                        "start": start,
                        "max_results": self.arxiv_page_size,
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                )
                response.raise_for_status()
                batch = self.parse_arxiv_feed(response.text, source)
                items.extend(batch)
                if len(batch) < self.arxiv_page_size:
                    break
                start += self.arxiv_page_size
                await asyncio.sleep(3.1)
        return items

    def parse_arxiv_feed(self, xml_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        out: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", ns):
            title = normalize_title(text_of(entry, "atom:title", ns))
            summary = strip_html(text_of(entry, "atom:summary", ns), limit=2200)
            published = parse_datetime(text_of(entry, "atom:published", ns))
            authors = [normalize_title(text_of(author, "atom:name", ns)) for author in entry.findall("atom:author", ns)]
            links = entry.findall("atom:link", ns)
            abs_url = ""
            pdf_url = ""
            for link in links:
                href = link.attrib.get("href", "")
                rel = link.attrib.get("rel", "")
                title_attr = link.attrib.get("title", "")
                if rel == "alternate":
                    abs_url = href
                if title_attr == "pdf":
                    pdf_url = href
            categories = [cat.attrib.get("term", "") for cat in entry.findall("atom:category", ns)]
            arxiv_id = text_of(entry, "atom:id", ns).rsplit("/", 1)[-1]
            out.append(
                self.make_item(
                    source_id=source["id"],
                    source_name=source["name"],
                    source_type=source.get("source_type", "paper"),
                    title=title,
                    url=abs_url or text_of(entry, "atom:id", ns),
                    summary=summary,
                    published_at=published,
                    authors=authors,
                    categories=categories,
                    source_reliability=source.get("reliability", "high"),
                    evidence_role=source.get("evidence_role", "primary_research"),
                    metadata={"arxiv_id": arxiv_id, "pdf_url": pdf_url},
                )
            )
        return out

    async def collect_rss(self, source: dict[str, Any], target: date) -> list[dict[str, Any]]:
        async with self.client() as client:
            response = await client.get(source["url"])
            response.raise_for_status()
        entries = parse_feed(response.text)
        items = []
        for entry in entries:
            published = entry.get("published_at")
            if published and published.date() != target:
                continue
            items.append(
                self.make_item(
                    source_id=source["id"],
                    source_name=source["name"],
                    source_type=source.get("source_type", "blog"),
                    title=entry["title"],
                    url=entry["url"],
                    summary=entry.get("summary", ""),
                    published_at=published,
                    authors=entry.get("authors", []),
                    categories=[],
                    source_reliability=source.get("reliability", "high"),
                    evidence_role=source.get("evidence_role", "official_update"),
                    metadata={"feed_url": source["url"]},
                )
            )
        return dedupe_items(items)[: self.max_items]

    async def collect_page(self, source: dict[str, Any], target: date) -> list[dict[str, Any]]:
        async with self.client() as client:
            response = await client.get(source["url"])
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[dict[str, Any]] = []
        containers = soup.find_all(["article", "li", "div", "section"], limit=800)
        seen_urls: set[str] = set()
        for container in containers:
            anchor = container.find("a", href=True)
            if not anchor:
                continue
            title = normalize_title(anchor.get_text(" ", strip=True))
            if not plausible_title(title):
                heading = container.find(["h1", "h2", "h3", "h4"])
                if heading:
                    title = normalize_title(heading.get_text(" ", strip=True))
            if not plausible_title(title):
                continue
            url = canonical_url(urljoin(source["url"], anchor["href"]))
            if url in seen_urls or same_site_noise(url):
                continue
            seen_urls.add(url)
            published = extract_time(container)
            if published and published.date() != target:
                continue
            paragraph = container.find("p")
            summary = strip_html(paragraph.get_text(" ", strip=True) if paragraph else "", limit=700)
            items.append(
                self.make_item(
                    source_id=source["id"],
                    source_name=source["name"],
                    source_type=source.get("source_type", "blog"),
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=published,
                    authors=[],
                    categories=[],
                    source_reliability=source.get("reliability", "medium"),
                    evidence_role=source.get("evidence_role", "official_update"),
                    metadata={"page_url": source["url"], "undated": published is None},
                )
            )
            if len(items) >= self.max_items:
                break
        return dedupe_items(items)

    def make_item(
        self,
        *,
        source_id: str,
        source_name: str,
        source_type: str,
        title: str,
        url: str,
        summary: str,
        published_at: datetime | None,
        authors: list[str],
        categories: list[str],
        source_reliability: str,
        evidence_role: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        url = canonical_url(url)
        title = normalize_title(title)
        summary = strip_html(summary, limit=2200)
        tags = extract_tags(title, summary, categories)
        item_id = stable_id(source_id, metadata.get("arxiv_id") or url or title)
        search_text = f"{title}\n{summary}\n{' '.join(tags)}\n{' '.join(categories)}".lower()
        return {
            "id": item_id,
            "source_id": source_id,
            "source_name": source_name,
            "source_type": source_type,
            "title": title,
            "url": url,
            "summary": summary,
            "authors_json": encode_json([a for a in authors if a]),
            "categories_json": encode_json([c for c in categories if c]),
            "tags_json": encode_json(tags),
            "published_at": isoformat(published_at),
            "collected_at": isoformat(now_utc()),
            "source_reliability": source_reliability,
            "evidence_role": evidence_role,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
            "search_text": search_text,
        }

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept": "application/rss+xml, application/xml, text/html, */*"},
        )


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries: list[dict[str, Any]] = []
    if root.tag.lower().endswith("rss") or root.find(".//channel") is not None:
        for item in root.findall(".//item"):
            title = normalize_title(child_text(item, "title"))
            link = child_text(item, "link") or child_text(item, "guid")
            if not title or not link:
                continue
            summary = child_text(item, "description") or child_text(item, "summary")
            published = parse_datetime(child_text(item, "pubDate") or child_text(item, "date"))
            authors = [child_text(item, "author") or child_text(item, "creator")]
            entries.append(
                {
                    "title": title,
                    "url": canonical_url(link),
                    "summary": strip_html(summary, limit=1600),
                    "published_at": published,
                    "authors": [a for a in authors if a],
                }
            )
        return entries

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = normalize_title(text_of(entry, "atom:title", ns))
        link = ""
        for link_el in entry.findall("atom:link", ns):
            if link_el.attrib.get("href") and link_el.attrib.get("rel", "alternate") == "alternate":
                link = link_el.attrib["href"]
                break
        link = link or text_of(entry, "atom:id", ns)
        if not title or not link:
            continue
        summary = text_of(entry, "atom:summary", ns) or text_of(entry, "atom:content", ns)
        published = parse_datetime(text_of(entry, "atom:published", ns) or text_of(entry, "atom:updated", ns))
        authors = [normalize_title(text_of(author, "atom:name", ns)) for author in entry.findall("atom:author", ns)]
        entries.append(
            {
                "title": title,
                "url": canonical_url(link),
                "summary": strip_html(summary, limit=1600),
                "published_at": published,
                "authors": authors,
            }
        )
    return entries


def child_text(parent: ET.Element, name: str) -> str:
    found = parent.find(name)
    if found is not None and found.text:
        return found.text.strip()
    for child in parent:
        if child.tag.endswith(name) and child.text:
            return child.text.strip()
    return ""


def text_of(parent: ET.Element, path: str, ns: dict[str, str]) -> str:
    found = parent.find(path, ns)
    return found.text.strip() if found is not None and found.text else ""


def plausible_title(title: str) -> bool:
    if not title:
        return False
    if len(title) < 16 or len(title) > 180:
        return False
    bad = {"privacy", "terms", "cookies", "subscribe", "contact", "careers", "login", "sign in"}
    low = title.lower()
    return not any(low == word or low.startswith(word + " ") for word in bad)


def same_site_noise(url: str) -> bool:
    low = url.lower()
    return any(
        part in low
        for part in [
            "trust.anthropic.com",
            "#newsletter",
            "/privacy",
            "/terms",
            "/careers",
            "/contact",
            "/legal",
            "/security-and-compliance",
            "/responsible-disclosure",
            "/consumer-health",
            "/trust",
        ]
    )


def extract_time(container) -> datetime | None:
    time_el = container.find("time")
    if time_el:
        for attr in ["datetime", "dateTime"]:
            if time_el.get(attr):
                parsed = parse_datetime(time_el[attr])
                if parsed:
                    return parsed
        parsed = parse_datetime(time_el.get_text(" ", strip=True))
        if parsed:
            return parsed
    text = container.get_text(" ", strip=True)
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        y, m, d = [int(part) for part in match.groups()]
        return datetime(y, m, d, tzinfo=timezone.utc)
    return None


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.get("id") or item.get("url") or item.get("title")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
