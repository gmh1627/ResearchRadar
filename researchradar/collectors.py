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
                    return CollectResult(source_id, "partial", items, f"rss failed; used fallback: {exc}")
                except Exception as fallback_exc:
                    return CollectResult(source_id, "error", [], f"{exc}; fallback failed: {fallback_exc}")
            return CollectResult(source_id, "error", [], str(exc))

    async def collect_github(self, target: date) -> CollectResult:
        if not self.github.get("enabled", True):
            return CollectResult("github", "skipped", [])
        items: list[dict[str, Any]] = []
        async with self.client() as client:
            items.extend(await self.collect_github_daily_rank(client, target))
            items.extend(await self.collect_github_trending(client, target))
        return CollectResult("github", "success", dedupe_items(items)[: self.max_items])

    async def collect_github_daily_rank(self, client: httpx.AsyncClient, target: date) -> list[dict[str, Any]]:
        branch = str(self.github.get("daily_rank_branch", "main")).strip() or "main"
        url = f"https://raw.githubusercontent.com/OpenGithubs/github-daily-rank/{branch}/README.md"
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        day_label = target.strftime("%Y.%m.%d")
        if day_label not in text:
            return []
        items: list[dict[str, Any]] = []
        detail_sections = split_daily_rank_detail_blocks(text)
        rank_table = parse_daily_rank_table(text)
        for repo, table_row in rank_table.items():
            repo_url = f"https://github.com/{repo}"
            detail = detail_sections.get(repo, {})
            metadata = {
                "stars": table_row.get("stars"),
                "daily_stars": table_row.get("daily_stars"),
                "weekly_stars": detail.get("weekly_stars"),
                "monthly_stars": detail.get("monthly_stars"),
                "rank_source": "github_daily_rank",
                "rank_date": day_label,
                "rank_position": table_row.get("rank"),
            }
            items.append(
                self.make_item(
                    source_id="github",
                    source_name="GitHub",
                    source_type="repo",
                    title=repo,
                    url=repo_url,
                    summary=(detail.get("description") or "").strip(),
                    published_at=parse_datetime(detail.get("created_at")),
                    authors=[repo.split("/", 1)[0]],
                    categories=[],
                    source_reliability="medium",
                    evidence_role="code_signal",
                    metadata=metadata,
                )
            )
        return items

    async def collect_github_trending(self, client: httpx.AsyncClient, target: date) -> list[dict[str, Any]]:
        url = str(self.github.get("trending_url", "https://github.com/trending"))
        response = await client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[dict[str, Any]] = []
        for article in soup.select("article.Box-row"):
            link = article.select_one("h2 a")
            if not link or not link.get("href"):
                continue
            repo = normalize_title(link.get_text(" ", strip=True)).replace(" / ", "/").replace(" ", "")
            repo_url = canonical_url(urljoin(url, link["href"]))
            summary = strip_html((article.select_one("p") or article).get_text(" ", strip=True), limit=700)
            language = text_or_none(article.select_one('[itemprop="programmingLanguage"]'))
            stars = parse_count(text_or_none(article.select_one("a[href$='/stargazers']")))
            forks = parse_count(text_or_none(article.select_one("a[href$='/forks']")))
            today_text = article.get_text(" ", strip=True)
            today_match = re.search(r"([\d,]+)\s+stars\s+today", today_text, re.IGNORECASE)
            metadata = {
                "stars": stars,
                "forks": forks,
                "language": language,
                "daily_stars": parse_count(today_match.group(1)) if today_match else None,
                "rank_source": "github_trending",
                "rank_date": target.isoformat(),
            }
            items.append(
                self.make_item(
                    source_id="github",
                    source_name="GitHub",
                    source_type="repo",
                    title=repo,
                    url=repo_url,
                    summary=summary,
                    published_at=datetime.combine(target, dt_time.min, tzinfo=timezone.utc),
                    authors=[repo.split("/", 1)[0]],
                    categories=[],
                    source_reliability="medium",
                    evidence_role="code_signal",
                    metadata=metadata,
                )
            )
            if len(items) >= self.max_items:
                break
        return items

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
        try:
            core_query = self.build_arxiv_query(source.get("categories", []), [], target)
            items.extend(await self.fetch_arxiv_query(source, core_query))
            conditional_categories = source.get("conditional_categories", [])
            conditional_keywords = source.get("conditional_keywords", [])
            if conditional_categories and conditional_keywords:
                conditional_query = self.build_arxiv_query(conditional_categories, conditional_keywords, target)
                items.extend(await self.fetch_arxiv_query(source, conditional_query))
            items = dedupe_items(items)
            if items:
                return items
        except Exception:
            items = []
        return await self.collect_arxiv_recent_pages(source, target)

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
                if response.status_code == 429:
                    raise RuntimeError("arXiv API rate exceeded")
                response.raise_for_status()
                batch = self.parse_arxiv_feed(response.text, source)
                items.extend(batch)
                if len(batch) < self.arxiv_page_size:
                    break
                start += self.arxiv_page_size
                await asyncio.sleep(3.1)
        return items

    async def collect_arxiv_recent_pages(self, source: dict[str, Any], target: date) -> list[dict[str, Any]]:
        categories = list(dict.fromkeys((source.get("categories", []) or []) + (source.get("conditional_categories", []) or [])))
        items: list[dict[str, Any]] = []
        async with self.client() as client:
            for category in categories:
                response = await client.get(f"https://arxiv.org/list/{category}/pastweek?show=2000")
                response.raise_for_status()
                items.extend(self.parse_arxiv_recent_html(response.text, source, target, category))
        return dedupe_items(items)

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

    def parse_arxiv_recent_html(self, html_text: str, source: dict[str, Any], target: date, category: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        out: list[dict[str, Any]] = []
        for heading in soup.find_all("h3"):
            heading_date = parse_arxiv_heading_date(heading.get_text(" ", strip=True))
            if heading_date != target:
                continue
            dl = heading.find_next_sibling("dl")
            if not dl:
                continue
            dts = dl.find_all("dt", recursive=False)
            dds = dl.find_all("dd", recursive=False)
            for dt, dd in zip(dts, dds):
                abs_link = dt.find("a", href=re.compile(r"^/abs/"))
                if not abs_link:
                    continue
                abs_url = canonical_url(urljoin("https://arxiv.org", abs_link["href"]))
                arxiv_id = abs_link["href"].rsplit("/", 1)[-1]
                pdf_link = dt.find("a", href=re.compile(r"^/pdf/"))
                title = extract_recent_field(dd, "Title:")
                if not title:
                    continue
                authors = [name.strip() for name in extract_recent_field(dd, "Authors:").split(",") if name.strip()]
                comments = extract_recent_field(dd, "Comments:")
                subjects = extract_recent_field(dd, "Subjects:")
                summary = comments or subjects or title
                categories = [part.strip() for part in subjects.split(";") if part.strip()] if subjects else [category]
                out.append(
                    self.make_item(
                        source_id=source["id"],
                        source_name=source["name"],
                        source_type=source.get("source_type", "paper"),
                        title=title,
                        url=abs_url,
                        summary=summary,
                        published_at=datetime.combine(target, dt_time.min, tzinfo=timezone.utc),
                        authors=authors,
                        categories=categories,
                        source_reliability=source.get("reliability", "high"),
                        evidence_role=source.get("evidence_role", "primary_research"),
                        metadata={
                            "arxiv_id": arxiv_id,
                            "pdf_url": canonical_url(urljoin("https://arxiv.org", pdf_link["href"])) if pdf_link else "",
                            "fallback_source": "pastweek_page",
                        },
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
            if url in seen_urls or same_site_noise(url) or page_url_noise(url):
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
        seen_at = isoformat(now_utc())
        return {
            "id": item_id,
            "source_id": source_id,
            "source_name": source_name,
            "source_type": source_type,
            "title": title,
            "url": url,
            "summary": summary,
            "summary_zh": "",
            "authors_json": encode_json([a for a in authors if a]),
            "categories_json": encode_json([c for c in categories if c]),
            "tags_json": encode_json(tags),
            "published_at": isoformat(published_at),
            "collected_at": seen_at,
            "last_seen_at": seen_at,
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


def page_url_noise(url: str) -> bool:
    low = url.lower().rstrip("/")
    return any(
        marker in low
        for marker in [
            "/category/",
            "/categories/",
            "/tag/",
            "/tags/",
            "/topics/",
            "/topic/",
            "/search",
            "?s=",
            "/page/",
        ]
    )


def parse_count(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip().lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([kmb])?", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


def text_or_none(element) -> str | None:
    if not element:
        return None
    text = element.get_text(" ", strip=True)
    return text or None


def first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def parse_arxiv_heading_date(value: str) -> date | None:
    match = re.search(r"([A-Za-z]{3}),\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", value)
    if not match:
        return None
    month_map = {
        "Jan": 1,
        "Feb": 2,
        "Mar": 3,
        "Apr": 4,
        "May": 5,
        "Jun": 6,
        "Jul": 7,
        "Aug": 8,
        "Sep": 9,
        "Oct": 10,
        "Nov": 11,
        "Dec": 12,
    }
    day = int(match.group(2))
    month = month_map.get(match.group(3))
    year = int(match.group(4))
    if not month:
        return None
    return date(year, month, day)


def extract_recent_field(container, label: str) -> str:
    for div in container.find_all("div", class_="list-title"):
        text = div.get_text(" ", strip=True)
        if text.startswith(label):
            return text.replace(label, "", 1).strip()
    for div in container.find_all("div", class_="list-authors"):
        if label == "Authors:":
            names = [a.get_text(" ", strip=True) for a in div.find_all("a")]
            return ", ".join(name for name in names if name)
    for div in container.find_all("div", class_="list-comments"):
        if label == "Comments:":
            return div.get_text(" ", strip=True).replace("Comments:", "", 1).strip()
    for div in container.find_all("div", class_="list-subjects"):
        if label == "Subjects:":
            return div.get_text(" ", strip=True).replace("Subjects:", "", 1).strip()
    return ""


def split_daily_rank_detail_blocks(text: str) -> dict[str, dict[str, str | int | None]]:
    blocks = re.split(r'(?=<h3[^>]*>.*?https://github\.com/)', text)
    details: dict[str, dict[str, str | int | None]] = {}
    for block in blocks:
        repo = first_match(block, r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
        if not repo:
            continue
        details[repo] = {
            "created_at": first_match(block, r"📅\s*开源时间[:：]\s*([^\n<]+)"),
            "description": first_match(block, r"📝\s*项目描述[:：]\s*([^\n<]+)") or "",
            "weekly_stars": parse_count(first_match(block, r"🔺\s*上周增长数量[:：]\s*([^\n<]+)")),
            "monthly_stars": parse_count(first_match(block, r"🔺\s*上月增长数量[:：]\s*([^\n<]+)")),
        }
    return details


def parse_daily_rank_table(text: str) -> dict[str, dict[str, int | None]]:
    rows: dict[str, dict[str, int | None]] = {}
    pattern = re.compile(
        r"\|\s*(?P<rank>\d+)\s*\|\s*\[(?P<repo>[^\]]+)\]\(https://github\.com/[^\)]+\)\|\s*(?P<stars>[^|]+)\|\s*🔺(?P<daily>[^|]+)\|"
    )
    for match in pattern.finditer(text):
        repo = match.group("repo").strip()
        rows[repo] = {
            "rank": int(match.group("rank")),
            "stars": parse_count(match.group("stars")),
            "daily_stars": parse_count(match.group("daily")),
        }
    return rows


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
