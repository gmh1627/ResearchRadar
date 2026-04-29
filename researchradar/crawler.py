from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .collectors import Collector
from .config import AppConfig
from .db import Database


class CrawlManager:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.collector = Collector(config.settings)
        self.lock = asyncio.Lock()
        self.status = {
            "running": False,
            "message": "idle",
            "last_started_at": None,
            "last_finished_at": None,
        }

    async def catch_up(self) -> None:
        today = datetime.now(ZoneInfo(self.config.timezone)).date()
        successful = self.db.successful_days()
        if not successful:
            start = today - timedelta(days=self.config.initial_backfill_days)
            await self.crawl_range(start, today)
            return

        yesterday = today - timedelta(days=1)
        missing = []
        cursor = min(yesterday, date.fromisoformat(max(successful)) + timedelta(days=1))
        while cursor <= yesterday:
            if cursor.isoformat() not in successful:
                missing.append(cursor)
            cursor += timedelta(days=1)
        if missing:
            await self.crawl_dates(missing)

    async def crawl_range(self, start: date, end: date) -> None:
        days = []
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor += timedelta(days=1)
        await self.crawl_dates(days)

    async def crawl_dates(self, dates: list[date]) -> None:
        async with self.lock:
            self.status.update(
                {
                    "running": True,
                    "message": f"crawling {len(dates)} day(s)",
                    "last_started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            try:
                for target in dates:
                    await self.crawl_day(target)
            finally:
                self.status.update(
                    {
                        "running": False,
                        "message": "idle",
                        "last_finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

    async def crawl_day(self, target: date) -> int:
        self.db.mark_day_started(target)
        total = 0
        errors: list[str] = []
        source_tasks = [self.run_config_source(source, target) for source in self.config.sources if source.get("enabled", True)]
        extra_tasks = [self.run_github(target), self.run_hackernews(target)]
        results = await asyncio.gather(*(source_tasks + extra_tasks), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            total += result
        status = "success" if not errors else "partial"
        self.db.mark_day_finished(target, status, total, "\n".join(errors) if errors else None)
        await asyncio.to_thread(self.translate_recent_summaries)
        return total

    async def run_config_source(self, source: dict, target: date) -> int:
        started = datetime.now(timezone.utc).isoformat()
        result = await self.collector.collect_source(source, target)
        changed = self.db.upsert_items(result.items)
        finished = datetime.now(timezone.utc).isoformat()
        self.db.add_source_run(target, result.source_id, result.status, started, finished, len(result.items), result.error)
        return changed

    async def run_github(self, target: date) -> int:
        started = datetime.now(timezone.utc).isoformat()
        result = await self.collector.collect_github(target)
        changed = self.db.upsert_items(result.items)
        finished = datetime.now(timezone.utc).isoformat()
        self.db.add_source_run(target, result.source_id, result.status, started, finished, len(result.items), result.error)
        return changed

    async def run_hackernews(self, target: date) -> int:
        started = datetime.now(timezone.utc).isoformat()
        result = await self.collector.collect_hackernews(target)
        changed = self.db.upsert_items(result.items)
        finished = datetime.now(timezone.utc).isoformat()
        self.db.add_source_run(target, result.source_id, result.status, started, finished, len(result.items), result.error)
        return changed

    async def scheduler_loop(self) -> None:
        while True:
            await asyncio.sleep(seconds_until_next_run(self.config.timezone, self.config.daily_time))
            target = datetime.now(ZoneInfo(self.config.timezone)).date() - timedelta(days=1)
            await self.crawl_dates([target])

    def translate_recent_summaries(self) -> None:
        try:
            from .translation import translate_missing

            translate_missing(limit=300)
        except Exception:
            pass


def seconds_until_next_run(tz_name: str, hhmm: str) -> float:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max((next_run - now).total_seconds(), 1.0)
